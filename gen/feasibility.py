#!/usr/bin/env python3
"""M0 feasibility gate for the ADC generator.

Given a generator spec and a PDK capability profile, compute the unit
capacitor, array size, area, noise budget and settling-limited sample rate,
and return a status that never claims signoff.

Design equations (differential, bottom-plate sampled binary CDAC):

  kT/C        C_tot >= 12 kT 2^(2N) / V_FS^2            (SNR ~ 6.02N+1.76)
  mismatch    sigma_Cu/Cu <= DNL_target / 2^((N-1)/2)    (1-sigma MSB step)
              sigma_Cu/Cu  = A_C / sqrt(area_um2),  A_C in %*um
  layout      Cu >= min_geom_area * density
  parasitic   C_tot >= headroom * C_parasitic

  settling    t_bit = ln(2^(N+1)) * Ron * C_tot
              fs_max = 1 / ((N+1) * (t_bit + t_comp))

Every number whose PDK input carries "source": "assumption" is reported as
unverified and downgrades the status.

The other three architectures dispatch from the same entry point and each
have their own sizing rule (see the per-block comment headers):

  arch   sizing / limit                       timing
  -----  -----------------------------------  ---------------------------
  sar    array Cu = max(kT/C, mismatch, ...)  (N+1) bit trials
  ramp   single hold cap, kT/C at Nyquist     f_clk / (2^N + overhead)
  sdm    sampling cap, kT/C relaxed by OSR    OSR * fs_out modulator clock
  vco    no capacitor at all                  (f_max - f_min) / 2^N
"""

from __future__ import annotations

import json
import math
import os
import sys

K_B = 1.380649e-23
T_DEFAULT = 300.15
DEFAULT_HEADROOM = 8.0


# --------------------------------------------------------------------------
# io helpers
# --------------------------------------------------------------------------

def expand(path):
    return os.path.expandvars(os.path.expanduser(path))


def load(path):
    with open(expand(path), "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump(obj):
    return json.dumps(obj, indent=2, sort_keys=False)


# --------------------------------------------------------------------------
# sizing
# --------------------------------------------------------------------------

def pick_cap(profile, name):
    """Resolve a cap by device name first, then by class (mim/mom/moscap).

    The class fallback is what lets one spec run on every PDK: the spec asks
    for a capacitor class, the profile decides which device implements it.
    If the class is absent, the profile's default_cap is substituted and the
    caller is told, rather than failing the whole row.
    """
    for cap in profile["cap_options"]:
        if cap["name"] == name:
            return cap, None
    for cap in profile["cap_options"]:
        if cap["kind"] == name:
            return cap, None
    default = profile.get("default_cap")
    for cap in profile["cap_options"]:
        if cap["name"] == default or cap["kind"] == default:
            return cap, f"cap '{name}' unavailable, substituted '{cap['name']}' ({cap['kind']})"
    names = ", ".join(f"{c['name']}({c['kind']})" for c in profile["cap_options"])
    raise KeyError(f"cap '{name}' not in {profile['id']}; available: {names}")


ARCHITECTURES = ("sar", "ramp", "sdm", "vco", "dual_slope", "flash",
                 "pipeline", "ti")


def evaluate(spec, profile, headroom=DEFAULT_HEADROOM, temperature=T_DEFAULT):
    """Dispatch on spec["arch"].  A profile that does not carry the block an
    architecture needs raises KeyError, which the callers turn into
    FAIL_UNCHARACTERIZED (same contract as a missing cap class)."""
    arch = str(spec.get("arch", "sar")).lower()
    if arch == "sar":
        return evaluate_sar(spec, profile, headroom, temperature)
    if arch == "ramp":
        return evaluate_ramp(spec, profile, headroom, temperature)
    if arch == "sdm":
        return evaluate_sdm(spec, profile, headroom, temperature)
    if arch == "vco":
        return evaluate_vco(spec, profile, headroom, temperature)
    if arch == "dual_slope":
        return evaluate_dual_slope(spec, profile, headroom, temperature)
    if arch == "flash":
        return evaluate_flash(spec, profile, headroom, temperature)
    if arch == "pipeline":
        return evaluate_pipeline(spec, profile, headroom, temperature)
    if arch == "ti":
        return evaluate_ti(spec, profile, headroom, temperature)
    raise KeyError(f"architecture '{arch}' is not implemented; "
                   f"known: {', '.join(ARCHITECTURES)}")


def _opamp(profile):
    """Shared OTA block (E2); falls back to the older sdm GBW scalar.

    The fallback is deliberate: it keeps every existing profile runnable, but
    marks the block as an assumption so no result is upgraded on it.
    """
    block = profile.get("opamp")
    if block:
        return block
    sdm = profile.get("sdm") or {}
    return {
        "gain_db": 60.0,
        "gbw_mhz": float(sdm.get("integrator_gbw_mhz") or 100.0),
        "noise_uv_rms": 100.0,
        "swing_v": float(profile.get("vdd") or 1.2),
        "source": "assumption",
        "note": "synthesised from sdm.integrator_gbw_mhz; no opamp block",
    }


def evaluate_sar(spec, profile, headroom=DEFAULT_HEADROOM, temperature=T_DEFAULT):
    n = int(spec["resolution"])
    n_codes = 1 << n
    vfs = float(spec["input"]["vfs"])
    fs_target = float(spec["fs_hz"])

    cap, cap_note = pick_cap(profile, spec["cdac"]["cap"])
    sw = profile["switches"][spec["sampling"]["backend"]]
    cmp_ = profile["comparator"][spec["comparator"]["backend"]]

    notes = []
    assumed = []
    if cap_note:
        notes.append(cap_note)

    # ---- constraints on the unit capacitor --------------------------------
    sigma_target = float(spec.get("dnl_target_lsb", 0.5)) / (2.0 ** ((n - 1) / 2.0))
    area_mismatch = ((cap["ac_pct_um"] / 100.0) / sigma_target) ** 2
    cu_mismatch_ff = area_mismatch * cap["density_ff_um2"]

    c_tot_ktc_f = 12.0 * K_B * temperature * (2 ** (2 * n)) / (vfs ** 2)
    cu_ktc_ff = c_tot_ktc_f / n_codes * 1e15

    cu_layout_ff = cap["min_geom_area_um2"] * cap["density_ff_um2"]

    cu_par_ff = profile["c_parasitic_est_ff"] * headroom / n_codes

    constraints = {
        "ktc_ff": cu_ktc_ff,
        "mismatch_ff": cu_mismatch_ff,
        "layout_ff": cu_layout_ff,
        "parasitic_ff": cu_par_ff,
    }
    cu_auto_ff = max(constraints.values())
    binding = max(constraints, key=constraints.get)

    cu_spec = spec["cdac"].get("cu_ff", "auto")
    if cu_spec == "auto":
        cu_ff = cu_auto_ff
        cu_source = "auto"
    else:
        cu_ff = float(cu_spec)
        cu_source = "override"
        if cu_ff < cu_auto_ff:
            notes.append(
                f"Cu {cu_ff:.3f} fF is below the auto floor {cu_auto_ff:.3f} fF "
                f"(binding: {binding})"
            )

    cu_f = cu_ff * 1e-15
    c_tot_f = n_codes * cu_f
    c_tot_ff = c_tot_f * 1e15

    # ---- matching and noise ----------------------------------------------
    area_cu_um2 = cu_ff / cap["density_ff_um2"]
    sigma_cu_rel = (cap["ac_pct_um"] / 100.0) / math.sqrt(area_cu_um2)
    dnl_msb_sigma_lsb = (2.0 ** ((n - 1) / 2.0)) * sigma_cu_rel

    snr_ktc_db = 10.0 * math.log10(c_tot_f * vfs ** 2 / (16.0 * K_B * temperature))
    snr_ktc_bits = (snr_ktc_db - 1.76) / 6.02

    comp_budget_uv = vfs / (2 ** (n + 1)) * 1e6
    comp_sigma_uv = float(cmp_["sigma_uv"])
    comp_margin = comp_budget_uv / comp_sigma_uv

    # ---- timing -----------------------------------------------------------
    ron = float(sw["ron_ohm"])
    comp_delay_ns = float(cmp_["delay_ns"])
    ln_settle = math.log(2.0 ** (n + 1))
    t_settle_ns = ln_settle * ron * c_tot_f * 1e9
    t_bit_ns = t_settle_ns + comp_delay_ns
    t_conv_ns = (n + 1) * t_bit_ns
    fs_max = 1e9 / t_conv_ns

    # what would make the target reachable
    t_bit_budget_ns = 1e9 / (fs_target * (n + 1))
    t_settle_budget_ns = t_bit_budget_ns - comp_delay_ns
    ron_required = (
        t_settle_budget_ns * 1e-9 / (ln_settle * c_tot_f) if t_settle_budget_ns > 0 else None
    )
    cu_max_ff = (
        t_settle_budget_ns * 1e-9 / (ln_settle * ron) / n_codes * 1e15
        if t_settle_budget_ns > 0 else 0.0
    )

    area_cap_um2 = 2.0 * c_tot_ff / cap["density_ff_um2"]

    # ---- provenance -------------------------------------------------------
    for label, obj in (("cap", cap), ("switch", sw), ("comparator", cmp_)):
        # anything short of a fully measured entry stays flagged: a measured
        # density with an assumed Pelgrom coefficient is still an assumption
        if obj.get("source") != "measured":
            assumed.append(label)
        if "source" not in obj:
            assumed.append(label + "(unmarked)")

    # ---- status -----------------------------------------------------------
    if comp_margin < 1.0:
        status = "FAIL_COMPARATOR"
        notes.append(
            f"comparator noise {comp_sigma_uv:.0f} uV exceeds the {comp_budget_uv:.0f} uV "
            f"budget; need a quieter front end or fewer bits"
        )
    elif fs_target > fs_max:
        status = "FAIL_SETTLING"
        notes.append(
            f"target {fs_target/1e3:.0f} kS/s needs Ron <= {ron_required:.1f} ohm "
            f"or Cu <= {cu_max_ff:.3f} fF (array now {c_tot_ff:.1f} fF at {ron:.0f} ohm)"
        )
    elif assumed:
        status = "PASS_FEASIBLE_ASSUMPTION"
    else:
        status = "PASS_FEASIBLE"

    return {
        "pdk": profile["id"],
        "family": profile["family"],
        "nbits": n,
        "vfs": vfs,
        "vdd": profile["vdd"],
        "fs_target": fs_target,
        "cap": cap["name"],
        "cap_kind": cap["kind"],
        "cu_ff": cu_ff,
        "cu_source": cu_source,
        "cu_auto_ff": cu_auto_ff,
        "binding_constraint": binding,
        "constraints_ff": constraints,
        "c_tot_ff": c_tot_ff,
        "area_cap_um2": area_cap_um2,
        "sigma_cu_rel": sigma_cu_rel,
        "dnl_msb_sigma_lsb": dnl_msb_sigma_lsb,
        "snr_ktc_db": snr_ktc_db,
        "snr_ktc_bits": snr_ktc_bits,
        "comparator_budget_uv": comp_budget_uv,
        "comparator_sigma_uv": comp_sigma_uv,
        "comparator_margin": comp_margin,
        "ron_ohm": ron,
        "t_settle_bit_ns": t_settle_ns,
        "t_conv_ns": t_conv_ns,
        "fs_max": fs_max,
        "ron_required_ohm": ron_required,
        "cu_max_ff": cu_max_ff,
        "assumed_inputs": assumed,
        "status": status,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# ramp (single-slope) backend
# --------------------------------------------------------------------------
#
# One hold capacitor, one ramp, one comparator, one counter:
#
#   hold cap   C_hold >= 12 kT 2^(2N) / V_FS^2        (no oversampling)
#   DNL        ramp step error + comparator noise, both already in LSB
#   timing     fs = f_clk / (2^N + overhead)
#              f_clk <= 1 / (ln(2^(N+1)) Ron C_hold + t_step + t_cmp)
#
# There is no cap *array*, so Pelgrom mismatch on C_hold is a gain error
# (reported as sigma_gain_rel), not a DNL term.  The ramp's own step-to-step
# linearity is what sets DNL, which is why it is a profile input.

def evaluate_ramp(spec, profile, headroom=DEFAULT_HEADROOM, temperature=T_DEFAULT):
    n = int(spec["resolution"])
    vfs = float(spec["input"]["vfs"])
    fs_target = float(spec["fs_hz"])
    ramp = profile.get("ramp")
    if not ramp:
        raise KeyError(f"ramp backend not characterized in {profile['id']}")

    cap, cap_note = pick_cap(profile, spec["cdac"]["cap"])
    sw = profile["switches"][spec["sampling"]["backend"]]
    cmp_ = profile["comparator"][spec["comparator"]["backend"]]
    notes, assumed = [], []
    if cap_note:
        notes.append(cap_note)

    # ---- hold capacitor --------------------------------------------------
    c_ktc_f = 12.0 * K_B * temperature * (2.0 ** (2 * n)) / (vfs ** 2)
    c_par_f = profile["c_parasitic_est_ff"] * headroom * 1e-15
    c_layout_f = cap["min_geom_area_um2"] * cap["density_ff_um2"] * 1e-15
    constraints = {"ktc_ff": c_ktc_f * 1e15,
                   "layout_ff": c_layout_f * 1e15,
                   "parasitic_ff": c_par_f * 1e15}
    c_hold_f = max(constraints.values()) * 1e-15
    c_hold_ff = c_hold_f * 1e15
    binding = max(constraints, key=constraints.get)
    area_hold_um2 = c_hold_ff / cap["density_ff_um2"]
    sigma_hold_rel = (cap["ac_pct_um"] / 100.0) / math.sqrt(max(area_hold_um2, 1e-9))

    # ---- error budget ----------------------------------------------------
    lsb_v = vfs / (1 << n)
    comp_budget_uv = lsb_v / 2.0 * 1e6
    comp_sigma_uv = float(cmp_["sigma_uv"])
    comp_margin = comp_budget_uv / comp_sigma_uv
    dnl_lin = float(ramp.get("linearity_lsb_dnl", 0.1))
    dnl_cmp = comp_sigma_uv * 1e-6 / lsb_v
    dnl_est = dnl_lin + dnl_cmp

    # ---- timing ----------------------------------------------------------
    ron = float(sw["ron_ohm"])
    delay_ns = float(cmp_["delay_ns"])
    t_samp_ns = math.log(2.0 ** (n + 1)) * ron * c_hold_f * 1e9
    t_step_ns = float(ramp.get("step_settle_ns", 0.0))
    f_clk_cap = 1e9 / max(t_samp_ns + t_step_ns + delay_ns, 1e-9)
    ceiling_mhz = min(float(profile.get("digital_ceiling_mhz") or 1e9),
                      float(ramp.get("clk_max_mhz") or 1e9))
    f_clk = min(f_clk_cap, ceiling_mhz * 1e6)
    codes = (1 << n) + int(ramp.get("overhead_clocks", 2))
    fs_max = f_clk / codes
    f_clk_required = fs_target * codes

    assumed = _provenance((("cap", cap), ("switch", sw), ("comparator", cmp_),
                           ("ramp", ramp)))
    if comp_margin < 1.0:
        status = "FAIL_COMPARATOR"
        notes.append(f"comparator noise {comp_sigma_uv:.0f} uV exceeds the "
                     f"{comp_budget_uv:.0f} uV half-LSB budget; a ramp ADC "
                     f"cannot average it away, the counter latches the first "
                     f"crossing")
    elif fs_target > fs_max:
        status = "FAIL_SETTLING"
        notes.append(f"target {fs_target/1e3:.0f} kS/s needs f_clk "
                     f">= {f_clk_required/1e6:.1f} MHz for {codes} cycles at "
                     f"C_hold {c_hold_ff:.1f} fF / Ron {ron:.0f} ohm; "
                     f"available {f_clk/1e6:.1f} MHz")
    elif assumed:
        status = "PASS_FEASIBLE_ASSUMPTION"
    else:
        status = "PASS_FEASIBLE"

    return {
        "arch": "ramp",
        "pdk": profile["id"],
        "family": profile["family"],
        "nbits": n,
        "vfs": vfs,
        "vdd": profile["vdd"],
        "fs_target": fs_target,
        "cap": cap["name"],
        "cap_kind": cap["kind"],
        "cap_role": "hold",
        "cu_ff": c_hold_ff,
        "c_tot_ff": c_hold_ff,
        "area_cap_um2": area_hold_um2,
        "binding_constraint": binding,
        "constraints_ff": constraints,
        "sigma_cu_rel": sigma_hold_rel,
        "dnl_msb_sigma_lsb": dnl_est,
        "dnl_terms_lsb": {"ramp": dnl_lin, "comparator": dnl_cmp},
        "comparator_budget_uv": comp_budget_uv,
        "comparator_sigma_uv": comp_sigma_uv,
        "comparator_margin": comp_margin,
        "ron_ohm": ron,
        "t_settle_ns": t_samp_ns + t_step_ns,
        "t_conv_ns": 1e9 / fs_max,
        "f_clk_hz": f_clk,
        "f_clk_required_hz": f_clk_required,
        "fs_max": fs_max,
        "assumed_inputs": assumed,
        "status": status,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# sdm (delta-sigma) backend
# --------------------------------------------------------------------------
#
# L-th order, B-bit quantizer, oversampled by OSR; the output is a bitstream
# and an ideal decimator is assumed (no decimator exists in this repo yet).
#
#   ideal SQNR  6.02 B + 1.76 - 10 log10(pi^(2L)/(2L+1))
#               + 10 (2L+1) log10(OSR) - loading_db
#   in-band kT/C  C_s >= 12 kT 2^(2N) / (V_FS^2 OSR)   (OSR buys back noise)
#   clock      f_mod = OSR fs <= min(digital ceiling, integrator ceiling)
#   settling   t_int = ln(2^(B+L+1)) / (2 pi GBW) <= 0.5 / f_mod
#
# loading_db is the 1-bit stability/loading penalty (the loop only tolerates
# about -3 dBFS); it is a profile input so the closed-form number can be
# pinned to the simulated one instead of being fudged here.

def _cic_magnitude(f_hz, f_mod_hz, r1, order):
    """|H(f)| of an R1-decimating CIC with `order` integrator/comb pairs."""
    x = math.pi * f_hz / f_mod_hz
    den = r1 * math.sin(x)
    ratio = 1.0 if abs(den) < 1e-15 else math.sin(r1 * x) / den
    return abs(ratio) ** order


def _decimator_report(spec, sdm, osr, sqnr):
    """CIC + halfband sizing and alias/droop for the chosen decimator.

    The CIC runs at the modulator clock and decimates by R1; the alias bands
    it must reject sit at k*f_mod/R1 +- f_out/2.  The halfband cascade then
    finishes the decimation.  The alias number is the worst of those bands, so
    it is directly comparable with the required stopband (SQNR + 10 dB).
    """
    dec = (spec.get("sdm") or {}).get("decimator") or {}
    cic_order = int(dec.get("cic_order", 4))
    r1 = int(dec.get("cic_r1", 4))
    guard = float(dec.get("guard_band", 0.9))
    r1 = max(1, min(r1, osr))
    f_mod = osr * float(spec["fs_hz"])
    f_out = float(spec["fs_hz"])
    worst = 0.0
    if r1 > 1:
        for k in range(1, r1):
            for edge in (-f_out / 2.0, f_out / 2.0):
                worst = max(worst, _cic_magnitude(k * f_mod / r1 + edge,
                                                  f_mod, r1, cic_order))
    alias_db = -20.0 * math.log10(max(worst, 1e-15))
    droop_db = 20.0 * math.log10(max(_cic_magnitude(
        guard * f_out / 2.0, f_mod, r1, cic_order), 1e-15))
    halfbands = max(0, int(round(math.log2(max(osr / r1, 1)))))
    # Kaiser tap estimate for the halfband cascade (10 % guard band).
    f_pass = guard * f_out / 2.0
    fs_in = f_mod / r1
    taps, mac_rate = 0, 0.0
    for _ in range(halfbands):
        f_alias = fs_in / 2.0 - f_out / 2.0 + f_pass
        df = max(f_alias - f_pass, 1e-9)
        dw = 2.0 * math.pi * df / fs_in
        n_tap = max(4, int(math.ceil((sqnr + 10.0 - 8.0) / (2.285 * dw))))
        taps += n_tap
        mac_rate += n_tap * fs_in / 2.0
        fs_in /= 2.0
    return {
        "cic_order": cic_order,
        "cic_r1": r1,
        "halfbands": halfbands,
        "guard_band": guard,
        "alias_db": alias_db,
        "droop_db": droop_db,
        "taps": taps,
        "mac_rate_msps": mac_rate / 1e6,
        "required_db": sqnr + 10.0,
        "source": dec.get("source", "assumption"),
    }


def evaluate_sdm(spec, profile, headroom=DEFAULT_HEADROOM, temperature=T_DEFAULT):
    n = int(spec["resolution"])
    vfs = float(spec["input"]["vfs"])
    fs_target = float(spec["fs_hz"])
    spec_sdm = spec.get("sdm") or {}
    prof_sdm = profile.get("sdm")
    if not prof_sdm:
        raise KeyError(f"sdm backend not characterized in {profile['id']}")
    # The schema documents the spec block as "the profile's sdm block is the
    # default", so an override must win.  Without this merge sdm.order and
    # sdm.quantizer_bits were accepted by the schema and then ignored, while
    # the behavioural model honoured them.
    sdm = dict(prof_sdm)
    sdm.update(spec_sdm)

    order = int(sdm.get("order", 2))
    qbits = int(sdm.get("quantizer_bits", 1))
    osr = int(spec.get("logic", {}).get("osr") or sdm.get("default_osr", 64))
    osr_max = int(sdm.get("osr_max", max(osr, 1)))
    if qbits != 1 and "loading_db" not in spec_sdm:
        # the profile's loading_db is the measured 1-bit gap; it does not
        # transfer to a multibit quantizer
        sdm["loading_db"] = 0.0
    loading_db = float(sdm.get("loading_db", 3.9 if qbits == 1 else 0.0))
    beta = float(sdm.get("feedback_beta", 0.5))
    opamp = _opamp(profile)
    cap, cap_note = pick_cap(profile, spec["cdac"]["cap"])
    sw = profile["switches"][spec["sampling"]["backend"]]
    cmp_ = profile["comparator"][spec["comparator"]["backend"]]
    notes, assumed = [], []
    if cap_note:
        notes.append(cap_note)

    def sqnr_db(osr_):
        return (6.02 * qbits + 1.76
                - 10.0 * math.log10(math.pi ** (2 * order) / (2 * order + 1))
                + 10.0 * (2 * order + 1) * math.log10(osr_)
                - loading_db)

    sqnr = sqnr_db(osr)
    enob_ideal = (sqnr - 1.76) / 6.02

    # OSR needed for the requested resolution, +6 dB design margin
    target_db = 6.02 * n + 1.76 + 6.0
    osr_required = 10 ** ((target_db + loading_db
                           - 6.02 * qbits - 1.76
                           + 10.0 * math.log10(math.pi ** (2 * order) / (2 * order + 1)))
                          / (10.0 * (2 * order + 1)))

    # ---- sampling capacitor ---------------------------------------------
    # The cap must keep the loop's own floor, not the requested N: size to
    # min(N, loop ENOB) so a 12 b claim cannot sit on a 4 fF cap.  The 24 kT
    # form (16 kT * 10^(1.76/10)) makes enob_thermal land on the sizing point
    # instead of half a bit below it; thermal_margin_db adds the usual 10 dB.
    n_eff = min(float(n), enob_ideal)
    thermal_margin_db = float(sdm.get("thermal_margin_db", 10.0))
    n_size = n_eff + thermal_margin_db / 6.02
    c_ktc_f = (24.0 * K_B * temperature * (2.0 ** (2 * n_size))
               / (vfs ** 2 * osr))
    c_par_f = profile["c_parasitic_est_ff"] * headroom * 1e-15 / osr
    c_layout_f = cap["min_geom_area_um2"] * cap["density_ff_um2"] * 1e-15
    constraints = {"ktc_ff": c_ktc_f * 1e15,
                   "layout_ff": c_layout_f * 1e15,
                   "parasitic_ff": c_par_f * 1e15}
    c_in_f = max(constraints.values()) * 1e-15
    c_in_ff = c_in_f * 1e15
    cap_binding = max(constraints, key=constraints.get)
    area_in_um2 = c_in_ff / cap["density_ff_um2"]
    snr_ktc_db = 10.0 * math.log10(
        c_in_f * vfs ** 2 * osr / (16.0 * K_B * temperature))
    enob_thermal = (snr_ktc_db - 1.76) / 6.02

    # ---- clock and integrator settling ----------------------------------
    f_mod = osr * fs_target
    ceiling_hz = min(float(profile.get("digital_ceiling_mhz") or 1e9),
                     float(sdm.get("clk_max_mhz") or 1e9)) * 1e6
    gbw_hz = float(opamp.get("gbw_mhz") or 0.0) * 1e6
    # the schema documents sdm.integrator_gain_db as an override; the loaded
    # opamp block is the default (behavioural already used the spec value)
    a0_db = float(spec_sdm.get("integrator_gain_db",
                               opamp.get("gain_db") or 0.0))
    t_int_s = (math.log(2.0 ** (qbits + order + 1))
               / (2 * math.pi * gbw_hz * beta)) if gbw_hz else None
    t_int_budget_s = 0.5 / f_mod
    f_settle_max = (1.0 / (2.0 * t_int_s)) if t_int_s else None
    fs_ceiling = min(ceiling_hz, f_settle_max) if f_settle_max else ceiling_hz
    fs_max = fs_ceiling / osr

    # ---- decimator -------------------------------------------------------
    dec = _decimator_report(spec, sdm, osr, sqnr)

    # ---- dnl: 1-bit is monotonic; a multibit DAC is not ------------------
    sigma_cu_rel = (cap["ac_pct_um"] / 100.0) / math.sqrt(max(area_in_um2, 1e-9))
    if qbits > 1:
        dnl_sigma = math.sqrt(2.0) * sigma_cu_rel * (2.0 ** (n - 1))
    else:
        dnl_sigma = 0.0

    # ---- binding constraint: the tightest real margin --------------------
    margins = {
        "osr": min(osr / max(osr_required, 1e-9), osr_max / max(osr, 1)),
        "clock_ceiling": (ceiling_hz / osr) / fs_target,
        "integrator_settling": (f_settle_max / osr) / fs_target
                               if f_settle_max else 1e9,
        "decimator": dec["alias_db"] / max(dec["required_db"], 1e-9),
        "cap_matching": enob_thermal / max(n_size, 1e-9),
    }
    binding = min(margins, key=margins.get)

    assumed = _provenance((("cap", cap), ("switch", sw), ("comparator", cmp_),
                           ("sdm", sdm), ("opamp", opamp)))
    notes.append(f"OSR {osr}: ideal in-band SQNR {sqnr:.1f} dB "
                 f"({enob_ideal:.2f} bit) for order {order}, {qbits}-bit "
                 f"quantizer; OSR {osr_required:.0f} needed for {n} bit "
                 f"+6 dB.")
    notes.append(f"sampling cap sized for n_eff {n_eff:.2f} bit (loop floor); "
                 f"thermal ENOB {enob_thermal:.2f} bit at {c_in_ff:.1f} fF")
    notes.append(f"decimator CIC{dec['cic_order']}/R1={dec['cic_r1']}: alias "
                 f"rejection {dec['alias_db']:.1f} dB vs {dec['required_db']:.1f} dB "
                 f"required, droop {dec['droop_db']:.2f} dB, "
                 f"{dec['taps']} halfband taps")
    if qbits == 1:
        notes.append("1-bit DAC: DNL is 0 by construction; the reported DNL "
                     "belongs to the decimated samples, not to element matching")
    else:
        notes.append(f"{qbits}-bit DAC: unshaped element mismatch gives "
                     f"DNL {dnl_sigma:.2f} LSB (1 sigma)")
    if osr < osr_required:
        status = "FAIL_OSR"
        notes.append(f"OSR {osr} is below the {osr_required:.0f} the order-"
                     f"{order} loop needs for {n} bit")
    elif osr > osr_max:
        status = "FAIL_OSR"
        notes.append(f"OSR {osr} exceeds the characterized ceiling {osr_max}")
    elif f_mod > ceiling_hz:
        status = "FAIL_SETTLING"
        notes.append(f"modulator clock {f_mod/1e6:.1f} MHz exceeds the "
                     f"{ceiling_hz/1e6:.1f} MHz ceiling at OSR {osr}")
    elif t_int_s is not None and t_int_s > t_int_budget_s:
        gbw_min_mhz = (math.log(2.0 ** (qbits + order + 1))
                       / (2 * math.pi * t_int_budget_s * beta)) / 1e6
        status = "FAIL_SETTLING"
        notes.append(f"integrator needs GBW >= {gbw_min_mhz:.1f} MHz at "
                     f"beta {beta:.2f} (t_int {t_int_s*1e9:.2f} ns vs "
                     f"{t_int_budget_s*1e9:.2f} ns budget)")
    elif dec["alias_db"] < dec["required_db"]:
        status = "FAIL_DECIMATOR"
        notes.append(f"CIC{dec['cic_order']}/R1={dec['cic_r1']} alias "
                     f"rejection {dec['alias_db']:.1f} dB is below the "
                     f"{dec['required_db']:.1f} dB requirement")
    elif assumed:
        status = "PASS_FEASIBLE_ASSUMPTION"
    else:
        status = "PASS_FEASIBLE"

    return {
        "arch": "sdm",
        "pdk": profile["id"],
        "family": profile["family"],
        "nbits": n,
        "vfs": vfs,
        "vdd": profile["vdd"],
        "fs_target": fs_target,
        "cap": cap["name"],
        "cap_kind": cap["kind"],
        "cap_role": "sampling",
        "cu_ff": c_in_ff,
        "c_tot_ff": c_in_ff,
        "area_cap_um2": area_in_um2,
        "binding_constraint": binding,
        "constraints_ff": constraints,
        "c_ktc_ff": c_ktc_f * 1e15,
        "c_layout_ff": c_layout_f * 1e15,
        "sigma_cu_rel": sigma_cu_rel,
        "dnl_msb_sigma_lsb": dnl_sigma,
        "order": order,
        "quantizer_bits": qbits,
        "feedback_beta": beta,
        "osr": osr,
        "osr_required": osr_required,
        "osr_max": osr_max,
        "n_eff": n_eff,
        "sqnr_ideal_db": sqnr,
        "enob_ideal": enob_ideal,
        "snr_ktc_db": snr_ktc_db,
        "enob_thermal": enob_thermal,
        "opamp_gain_db": a0_db,
        "opamp_gbw_mhz": gbw_hz / 1e6,
        "f_mod_hz": f_mod,
        "t_int_ns": t_int_s * 1e9 if t_int_s is not None else None,
        "gbw_min_mhz": (math.log(2.0 ** (qbits + order + 1))
                        / (2 * math.pi * t_int_budget_s * beta)) / 1e6,
        "fs_max": fs_max,
        "decimator": dec,
        "assumed_inputs": assumed,
        "status": status,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# vco (ring-oscillator / counter) backend
# --------------------------------------------------------------------------
#
# Vin -> frequency -> cycle count over a fixed window:
#
#   window     t_w = 2^N / (f_max - f_min)      (full scale spans 2^N counts)
#   rate       fs = 1 / t_w = (f_max - f_min) / 2^N
#   jitter     SNR_j = -20 log10(2 pi f_in t_j),  f_in = fs/2
#   tuning     y(x) = x + 4 k x (1-x)   ->  DNL = 4k (k = curvature)
#
# There is no sampling capacitor and no comparator: the quantizer is the
# counter, so kT/C and comparator noise do not appear at all.  The limits are
# tuning span, aperture jitter and tuning-curve curvature.

def evaluate_vco(spec, profile, headroom=DEFAULT_HEADROOM, temperature=T_DEFAULT):
    n = int(spec["resolution"])
    vfs = float(spec["input"]["vfs"])
    fs_target = float(spec["fs_hz"])
    vco = profile.get("vco")
    if not vco:
        raise KeyError(f"vco backend not characterized in {profile['id']}")

    f_min = float(vco["f_min_mhz"]) * 1e6
    f_max = float(vco["f_max_mhz"]) * 1e6
    span = f_max - f_min
    if span <= 0:
        raise KeyError(f"vco tuning range in {profile['id']} is empty")

    notes, assumed = [], []
    t_window = (1 << n) / span
    fs_max = 1.0 / t_window
    kvco_mhz_v = span / vfs / 1e6

    snr_q = 6.02 * n + 1.76
    tj_fs = vco.get("period_jitter_fs_rms")
    if tj_fs:
        f_in = fs_target / 2.0
        snr_j = -20.0 * math.log10(2 * math.pi * f_in * float(tj_fs) * 1e-15)
    else:
        snr_j = None
        notes.append("aperture/period jitter is not measured for this PDK, so "
                     "the dynamic ENOB is unverified; the count quantization "
                     "alone would give "
                     f"{(snr_q-1.76)/6.02:.2f} bit")
    snr = snr_q if snr_j is None else min(snr_q, snr_j)
    enob_est = (snr - 1.76) / 6.02

    curvature = float(vco.get("curvature_pct", 2.0)) / 100.0
    dnl_est = 4.0 * curvature          # code-width ratio, x = 0 or 1 vs 0.5
    inl_est = curvature * (1 << n)     # max |y-x| = k of FS = k*2^N LSB

    assumed = _provenance((("vco", vco),))
    if fs_target > fs_max:
        status = "FAIL_TUNING"
        notes.append(f"target {fs_target/1e3:.0f} kS/s needs a {span/1e6:.0f} MHz "
                     f"tuning span for {n} bit (have {span/1e6:.1f} MHz: "
                     f"fs_max {fs_max/1e3:.0f} kS/s); use fewer bits or a "
                     f"faster VCO")
    elif snr_j is not None and (snr_j - 1.76) / 6.02 < n - 0.5:
        status = "FAIL_JITTER"
        notes.append(f"jitter-limited ENOB {(snr_j-1.76)/6.02:.2f} bit "
                     f"({snr_j:.1f} dB) is more than 0.5 bit below {n}")
    elif assumed:
        status = "PASS_FEASIBLE_ASSUMPTION"
    else:
        status = "PASS_FEASIBLE"

    return {
        "arch": "vco",
        "pdk": profile["id"],
        "family": profile["family"],
        "nbits": n,
        "vfs": vfs,
        "vdd": profile["vdd"],
        "fs_target": fs_target,
        "cap": "none",
        "cap_kind": "none",
        "cap_role": "none",
        "cu_ff": 0.0,
        "c_tot_ff": 0.0,
        "area_cap_um2": 0.0,
        "binding_constraint": "tuning_span",
        "f_min_hz": f_min,
        "f_max_hz": f_max,
        "span_hz": span,
        "kvco_mhz_v": kvco_mhz_v,
        "t_window_ns": t_window * 1e9,
        "snr_quant_db": snr_q,
        "snr_jitter_db": snr_j,
        "snr_db": snr,
        "enob_ideal": enob_est,
        "dnl_msb_sigma_lsb": dnl_est,
        "inl_est_lsb": inl_est,
        "curvature_pct": curvature * 100.0,
        "fs_max": fs_max,
        "assumed_inputs": assumed,
        "status": status,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# dual-slope integrating backend
# --------------------------------------------------------------------------
#
# Two phases, one integrator, one comparator, one counter:
#
#   run-up    integrate Vin/R for exactly 2^N clocks      T1 = 2^N / f_clk
#   run-down  de-integrate Vref/R until the comparator trips
#   codes     fs = f_clk / (2^(N+1) + overhead)
#
# R, C and the clock cancel in the ratio, so the error budget is A0, dielectric
# absorption, comparator offset/delay and input bias -- not Pelgrom matching.
# The integrator is sized by R*C = Vin,max*T1/Vint,max, NOT by kT/C; the kT/C
# term is only a floor.

def evaluate_dual_slope(spec, profile, headroom=DEFAULT_HEADROOM,
                        temperature=T_DEFAULT):
    n = int(spec["resolution"])
    vfs = float(spec["input"]["vfs"])
    fs_target = float(spec["fs_hz"])
    ds_prof = profile.get("dual_slope")
    if not ds_prof:
        raise KeyError(f"dual_slope backend not characterized in {profile['id']}")
    ramp = profile.get("ramp") or {}

    cap, cap_note = pick_cap(profile, spec["cdac"]["cap"])
    sw = profile["switches"][spec["sampling"]["backend"]]
    cmp_ = profile["comparator"][spec["comparator"]["backend"]]
    ds = spec.get("dual_slope") or {}
    notes, assumed = [], []
    if cap_note:
        notes.append(cap_note)

    variant = str(ds.get("variant", "one_resistor")).lower()
    auto_zero = bool(ds.get("auto_zero", True))
    v_int_max = min(float(ds.get("v_int_max", 1.0)), vfs)
    overhead = int(ds.get("overhead_clocks", 4))
    t_delay_s = float(ds_prof.get("comparator_delay_ns",
                                  cmp_.get("delay_ns", 0.0))) * 1e-9

    # ---- speed -----------------------------------------------------------
    ceilings = [float(profile.get("digital_ceiling_mhz") or 1e9),
                float(ramp.get("clk_max_mhz") or 1e9)]
    if ds_prof.get("clk_max_mhz"):
        ceilings.append(float(ds_prof["clk_max_mhz"]))
    f_clk = min(ceilings) * 1e6
    codes = (1 << (n + 1)) + overhead
    fs_max = f_clk / codes
    t1_s = (1 << n) / f_clk

    # ---- integrator: the term the old ramp clamp was missing -------------
    rc_s = vfs * t1_s / v_int_max
    lsb_v = vfs / (1 << n)
    c_ktc_f = 12.0 * K_B * temperature * (2.0 ** (2 * n)) / (vfs ** 2)
    c_layout_f = cap["min_geom_area_um2"] * cap["density_ff_um2"] * 1e-15
    c_floor_f = max(c_ktc_f, c_layout_f)
    c_spec_f = float(ds.get("c_integ_pf", 100.0)) * 1e-12
    c_integ_f = max(c_floor_f, c_spec_f)
    r_max_ohm = float(ds_prof.get("r_max_ohm", 2.5e6))
    if rc_s / c_integ_f > r_max_ohm:
        c_integ_f = rc_s / r_max_ohm
    r_ohm = rc_s / c_integ_f
    c_integ_ff = c_integ_f * 1e15
    area_integ_um2 = c_integ_ff / cap["density_ff_um2"]
    max_area_um2 = float(ds_prof.get("max_cap_area_um2", 2.0))

    # ---- error budget ----------------------------------------------------
    a0_req_db = 20.0 * math.log10(2.0 ** (n + 1))
    a0_db = float(ds_prof.get("opamp_gain_db", 0.0))
    da_req_pct = 50.0 / (1 << n)
    da_pct = float(ds_prof.get("dielectric_absorption_pct", 0.0))
    comp_offset_uv = 0.0 if auto_zero else float(
        cmp_.get("offset_uv") or cmp_.get("sigma_uv") or 0.0)
    ib_na = float(ds_prof.get("ib_na", 0.0))
    offset_uv = (float(ds_prof.get("opamp_offset_uv", 0.0))
                 + ib_na * 1e-9 * r_ohm * 1e6 + comp_offset_uv)
    offset_lsb = offset_uv / (lsb_v * 1e6)
    delay_lsb = t_delay_s * f_clk
    dnl_bound_lsb = (float(ds_prof.get("delay_variation_ns", 0.0)) * 1e-9 * f_clk)
    gain_error_pct = (0.0 if variant == "one_resistor"
                      else float(ds_prof.get("resistor_match_pct", 0.0)))
    sndr_db = 6.02 * n + 1.76

    assumed = _provenance((("cap", cap), ("switch", sw), ("comparator", cmp_),
                           ("dual_slope", ds_prof)))
    margins = {
        "clock": fs_max / fs_target,
        "opamp_gain": 10.0 ** ((a0_db - a0_req_db) / 20.0),
        "dielectric_absorption": (da_req_pct / da_pct) if da_pct > 0 else 1e9,
        "offset": 0.5 / offset_lsb if offset_lsb > 0 else 1e9,
        "rc_product": max_area_um2 / area_integ_um2,
    }
    binding = min(margins, key=margins.get)

    if fs_target > fs_max:
        status = "FAIL_RATE"
        binding = "clock"
        notes.append(f"target {fs_target/1e3:.0f} kS/s needs f_clk >= "
                     f"{fs_target*codes/1e6:.1f} MHz for {codes} clocks; "
                     f"available {f_clk/1e6:.1f} MHz ({fs_max/1e3:.0f} kS/s)")
    elif a0_db < a0_req_db:
        status = "FAIL_OPAMP_GAIN"
        binding = "opamp_gain"
        notes.append(f"integrator needs A0 >= {a0_req_db:.1f} dB for 0.5 LSB "
                     f"(have {a0_db:.1f} dB)")
    elif da_pct > da_req_pct:
        status = "FAIL_DA"
        binding = "dielectric_absorption"
        notes.append(f"dielectric absorption {da_pct:.4f}% exceeds the "
                     f"{da_req_pct:.4f}% half-LSB budget at {n} bit")
    elif offset_uv > 0.5 * lsb_v * 1e6:
        status = "FAIL_OFFSET"
        binding = "offset"
        notes.append(f"residual offset {offset_uv:.0f} uV is {offset_lsb:.2f} "
                     f"LSB; auto-zero is {'on' if auto_zero else 'off'}")
    elif area_integ_um2 > max_area_um2:
        status = "FAIL_INTEGRATOR"
        binding = "rc_product"
        notes.append(f"R*C {rc_s*1e6:.2f} us needs {c_integ_ff:.0f} fF "
                     f"({area_integ_um2:.2f} mm^2) at R <= {r_max_ohm/1e3:.0f} kohm")
    else:
        status = ("PASS_FEASIBLE_ASSUMPTION" if assumed else "PASS_FEASIBLE")

    notes.append(f"one comparator, one counter: delay {delay_lsb:.2f} code; "
                 f"DNL is a bound ({dnl_bound_lsb:.3f} LSB), not a histogram")
    if variant == "one_resistor":
        notes.append("one-resistor variant: R and C cancel in both phases, so "
                     "absolute value is not a gain-error term")
    else:
        notes.append(f"two-resistor variant: R2/R1 mismatch {gain_error_pct:.3f}% "
                     f"is a gain term")
    if da_pct == 0.0:
        notes.append("dielectric absorption is 0 because no profile has "
                     "measured it; this is a missing input, not a good part")

    return {
        "arch": "dual_slope",
        "pdk": profile["id"],
        "family": profile["family"],
        "nbits": n,
        "vfs": vfs,
        "vdd": profile["vdd"],
        "fs_target": fs_target,
        "cap": cap["name"],
        "cap_kind": cap["kind"],
        "cap_role": "integrator",
        "cu_ff": c_integ_ff,
        "c_tot_ff": c_integ_ff,
        "area_cap_um2": area_integ_um2,
        "binding_constraint": binding,
        "constraints_ff": {"ktc_ff": c_ktc_f * 1e15,
                           "layout_ff": c_layout_f * 1e15},
        "sigma_cu_rel": 0.0,
        "dnl_msb_sigma_lsb": dnl_bound_lsb,
        "dnl_bound_lsb": dnl_bound_lsb,
        "inl_lsb": 0.0,
        "variant": variant,
        "auto_zero": auto_zero,
        "f_clk_hz": f_clk,
        "codes": codes,
        "t1_ns": t1_s * 1e9,
        "fs_max": fs_max,
        "rc_us": rc_s * 1e6,
        "r_ohm": r_ohm,
        "c_integ_pf": c_integ_f * 1e12,
        "a0_req_db": a0_req_db,
        "a0_db": a0_db,
        "da_req_pct": da_req_pct,
        "da_pct": da_pct,
        "offset_uv": offset_uv,
        "offset_lsb": offset_lsb,
        "delay_lsb": delay_lsb,
        "gain_error_pct": gain_error_pct,
        "sndr_db": sndr_db,
        "assumed_inputs": assumed,
        "status": status,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# flash backend
# --------------------------------------------------------------------------
#
# One comparator per tap, resistor ladder, popcount encoder:
#
#   budget     sigma_os <= dnl_target*Vfs / (2^N * 3*sqrt(2))
#   area       scale comparators by (sigma_os/budget)^2, cap the scale
#   DNL        sqrt(2)*sigma_os / LSB (adjacent offsets add in quadrature)
#   timing     t_ladder = ln(2^(N+1))*R_tap*C_in, R_tap = 2^(N-2)*Ru
#
# The bind is the comparator offset, not the timing: at MS/s flash is
# 100-1000x faster than needed.

def evaluate_flash(spec, profile, headroom=DEFAULT_HEADROOM,
                   temperature=T_DEFAULT):
    n = int(spec["resolution"])
    vfs = float(spec["input"]["vfs"])
    fs_target = float(spec["fs_hz"])
    fl = profile.get("flash")
    if not fl:
        raise KeyError(f"flash backend not characterized in {profile['id']}")
    resistors = profile.get("resistors") or []
    if not resistors:
        raise KeyError(f"flash resistor ladder not characterized in {profile['id']}")
    fs_spec = spec.get("flash") or {}
    ru_name = fs_spec.get("ru")
    res = next((r for r in resistors if r["name"] == ru_name),
               resistors[0] if ru_name is None else None)
    if res is None:
        raise KeyError(f"flash resistor '{ru_name}' not in {profile['id']}")

    cmp_ = profile["comparator"][spec["comparator"]["backend"]]
    notes, assumed = [], []
    lsb_v = vfs / (1 << n)
    sigma_os_uv = float(cmp_["sigma_uv"])
    dnl_target = float(spec.get("dnl_target_lsb", 0.5))
    budget_uv = dnl_target * vfs / ((1 << n) * 3.0 * math.sqrt(2.0)) * 1e6
    area_scale = max(1.0, (sigma_os_uv / budget_uv) ** 2)
    max_scale = float(fl.get("max_area_scale", 8.0))
    input_cap_ff = float(cmp_.get("input_cap_ff", 3.5)) * area_scale

    ru_ohm = float(fs_spec.get("ru_ohm", res.get("ru_ohm", 1000.0)))
    r_tap_ohm = (2 ** (n - 2)) * ru_ohm
    t_ladder_ns = (math.log(2.0 ** (n + 1)) * r_tap_ohm
                   * input_cap_ff * 1e-15 * 1e9)
    t_cmp_ns = float(cmp_["delay_ns"]) * math.sqrt(area_scale)
    t_enc_ns = float(fl.get("encoder_delay_ns", 0.2))
    t_total_ns = t_ladder_ns + t_cmp_ns + t_enc_ns
    fs_max = 1e9 / t_total_ns
    dnl_sigma = math.sqrt(2.0) * sigma_os_uv * 1e-6 / lsb_v
    n_cmp = (1 << n) - 1
    area_ladder_um2 = (1 << n) * float(fl.get("ladder_unit_area_um2", 11.4))

    assumed = _provenance((("comparator", cmp_), ("flash", fl),
                           ("resistor", res)))
    if not fl.get("sigma_is_random", False):
        assumed.append("comparator_sigma(random_offset_unmeasured)")
    margins = {
        "comparator_offset": max_scale / area_scale,
        "ladder_settling": fs_max / fs_target,
        "comparator_delay": (1e9 / max(t_cmp_ns, 1e-9)) / fs_target,
        "encoder_delay": (1e9 / max(t_enc_ns, 1e-9)) / fs_target,
    }
    binding = min(margins, key=margins.get)

    if area_scale > max_scale:
        status = "FAIL_OFFSET"
        binding = "comparator_offset"
        notes.append(f"comparator sigma {sigma_os_uv:.0f} uV needs "
                     f"{area_scale:.1f}x input area (cap {max_scale:.1f}x) to "
                     f"meet the {budget_uv:.0f} uV half-LSB budget")
    elif fs_target > fs_max:
        status = "FAIL_SETTLING"
        binding = max(("ladder_settling", t_ladder_ns),
                      ("comparator_delay", t_cmp_ns),
                      ("encoder_delay", t_enc_ns), key=lambda x: x[1])[0]
        notes.append(f"target {fs_target/1e3:.0f} kS/s vs {fs_max/1e3:.0f} kS/s "
                     f"(ladder {t_ladder_ns:.2f} + comparator {t_cmp_ns:.2f} "
                     f"+ encoder {t_enc_ns:.2f} ns)")
    else:
        status = "PASS_FEASIBLE_ASSUMPTION" if assumed else "PASS_FEASIBLE"

    notes.append(f"{n_cmp} comparators, {1 << n} ladder units at "
                 f"{ru_ohm:.0f} ohm ({r_tap_ohm/1e3:.1f} kohm Thevenin at "
                 f"mid-string); DNL {dnl_sigma:.2f} LSB (3 sigma budget "
                 f"{dnl_target:.2f})")
    if fs_max > 100.0 * fs_target:
        notes.append("flash is >100x faster than the target here; at MS/s a "
                     "SAR/flash choice is set by area, not rate")

    return {
        "arch": "flash",
        "pdk": profile["id"],
        "family": profile["family"],
        "nbits": n,
        "vfs": vfs,
        "vdd": profile["vdd"],
        "fs_target": fs_target,
        "cap": "none",
        "cap_kind": "none",
        "cap_role": "comparator_input",
        "cu_ff": input_cap_ff,
        "c_tot_ff": n_cmp * input_cap_ff,
        "area_cap_um2": area_ladder_um2,
        "binding_constraint": binding,
        "sigma_cu_rel": 0.0,
        "dnl_msb_sigma_lsb": dnl_sigma,
        "n_comparators": n_cmp,
        "area_scale": area_scale,
        "max_area_scale": max_scale,
        "input_cap_ff": input_cap_ff,
        "sigma_os_uv": sigma_os_uv,
        "offset_budget_uv": budget_uv,
        "ru_ohm": ru_ohm,
        "r_tap_ohm": r_tap_ohm,
        "t_ladder_ns": t_ladder_ns,
        "t_cmp_ns": t_cmp_ns,
        "t_enc_ns": t_enc_ns,
        "fs_max": fs_max,
        "assumed_inputs": assumed,
        "status": status,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# pipeline backend
# --------------------------------------------------------------------------
#
# S stages of G=2^floor(bps), beta = 1/G, digital redundancy:
#
#   A0      >= 2^N / beta          (6.02 N + 6.02 log2 G dB)
#   matching sigma_Cu/Cu <= 1/(3 sqrt(2) 2^N), or the calibrated residual
#   OTA     t_set = (floor(bps)+1) ln2 / (2 pi GBW beta)
#   switch  t_sample = ln(2^(N+1)) Ron 2 G Cu
#   fs_max  = 1 / max(S (t_set+t_cmp), t_sample)
#
# Comparator offset is absorbed by redundancy; the bind is OTA gain, cap
# matching (or calibration), then the sampling switch at high N.

def evaluate_pipeline(spec, profile, headroom=DEFAULT_HEADROOM,
                      temperature=T_DEFAULT):
    n = int(spec["resolution"])
    vfs = float(spec["input"]["vfs"])
    fs_target = float(spec["fs_hz"])
    pl = spec.get("pipeline") or {}
    opamp = _opamp(profile)
    cmp_ = profile["comparator"][spec["comparator"]["backend"]]
    sw = profile["switches"][spec["sampling"]["backend"]]
    cap, cap_note = pick_cap(profile, spec["cdac"]["cap"])
    notes, assumed = [], []
    if cap_note:
        notes.append(cap_note)

    bps = float(pl.get("bits_per_stage", 1.5))
    stage_bits = int(math.floor(bps))
    G = 1 << stage_bits
    beta = 1.0 / G
    stages_spec = pl.get("stages", "auto")
    if stages_spec == "auto":
        stages = (n - 1) if stage_bits == 1 else int(math.ceil(n / 2.0))
    else:
        stages = int(stages_spec)

    a0_req = (2.0 ** n) / beta
    a0_req_db = 20.0 * math.log10(a0_req)
    a0_db = float(opamp.get("gain_db", 0.0))
    gbw_hz = float(opamp.get("gbw_mhz", 0.0)) * 1e6
    tau_s = 1.0 / (2.0 * math.pi * gbw_hz * beta) if gbw_hz else float("inf")
    t_set_s = (stage_bits + 1) * math.log(2.0) * tau_s
    t_cmp_s = float(cmp_["delay_ns"]) * 1e-9
    fs_ota = 1.0 / (stages * (t_set_s + t_cmp_s)) if math.isfinite(t_set_s) else 0.0

    # ---- cap matching and calibration ------------------------------------
    cal = spec.get("calibration") or {}
    cal_residual = None
    if cal.get("gain"):
        cal_residual = float(pl.get("cal_residual_lsb", 0.5))
        sigma_cu_req = cal_residual / (3.0 * (2.0 ** (n - 1)))
    else:
        sigma_cu_req = 1.0 / (3.0 * math.sqrt(2.0) * (2.0 ** n))
    area_mismatch_um2 = ((cap["ac_pct_um"] / 100.0) / sigma_cu_req) ** 2
    cu_mismatch_ff = area_mismatch_um2 * cap["density_ff_um2"]
    c_ktc_total_f = 12.0 * K_B * temperature * (2.0 ** (2 * n)) / (vfs ** 2)
    cu_ktc_ff = c_ktc_total_f / G * 1e15
    cu_layout_ff = cap["min_geom_area_um2"] * cap["density_ff_um2"]
    constraints = {"mismatch_ff": cu_mismatch_ff,
                   "ktc_ff": cu_ktc_ff,
                   "layout_ff": cu_layout_ff}
    cu_auto_ff = max(constraints.values())
    cap_binding = max(constraints, key=constraints.get)
    cu_spec = pl.get("cu_ff", "auto")
    if cu_spec == "auto":
        cu_ff = cu_auto_ff
        cu_source = "auto"
    else:
        cu_ff = float(cu_spec)
        cu_source = "override"
        if cu_ff < cu_auto_ff:
            notes.append(f"Cu {cu_ff:.3f} fF is below the auto floor "
                         f"{cu_auto_ff:.3f} fF (binding: {cap_binding})")

    c_stage_ff = G * cu_ff
    c_diff_ff = 2.0 * c_stage_ff
    area_cu_um2 = cu_ff / cap["density_ff_um2"]
    sigma_cu_actual = (cap["ac_pct_um"] / 100.0) / math.sqrt(max(area_cu_um2, 1e-9))
    dnl_sigma = math.sqrt(2.0) * sigma_cu_actual * (2.0 ** (n - 1))

    ron = float(sw["ron_ohm"])
    t_sample_ns = (math.log(2.0 ** (n + 1)) * ron * c_diff_ff * 1e-15 * 1e9)
    fs_switch = 1e9 / max(t_sample_ns, 1e-9)
    fs_max = min(fs_ota, fs_switch)

    assumed = _provenance((("cap", cap), ("switch", sw), ("comparator", cmp_),
                           ("opamp", opamp)))
    margins = {
        "ota_gain": 10.0 ** ((a0_db - a0_req_db) / 20.0),
        "ota_gbw": fs_ota / fs_target,
        "switch_settling": fs_switch / fs_target,
        "cap_matching": sigma_cu_req / max(sigma_cu_actual, 1e-12),
    }
    binding = min(margins, key=margins.get)

    if a0_db < a0_req_db:
        status = "FAIL_OPAMP_GAIN"
        binding = "ota_gain"
        notes.append(f"OTA needs {a0_req_db:.1f} dB for 0.5 LSB at beta "
                     f"{beta:.2f} (have {a0_db:.1f} dB)")
    elif fs_ota < fs_target:
        status = "FAIL_SETTLING"
        binding = "ota_gbw"
        notes.append(f"OTA settling gives {fs_ota/1e3:.0f} kS/s at GBW "
                     f"{gbw_hz/1e6:.0f} MHz, beta {beta:.2f}, {stages} stages")
    elif fs_switch < fs_target:
        status = "FAIL_SETTLING"
        binding = "switch_settling"
        notes.append(f"sampling switch {t_sample_ns:.1f} ns limits to "
                     f"{fs_switch/1e3:.0f} kS/s (Ron {ron:.0f} ohm, "
                     f"C_diff {c_diff_ff:.1f} fF); use a bootstrap")
    else:
        status = "PASS_FEASIBLE_ASSUMPTION" if assumed else "PASS_FEASIBLE"

    notes.append(f"{stages} stages of {bps:.1f} b, G={G}, beta={beta:.2f}; "
                 f"cap unit {cu_ff:.3f} fF (binding {cap_binding}), "
                 f"sigma_Cu/Cu {sigma_cu_actual*100:.4f}% vs required "
                 f"{sigma_cu_req*100:.4f}%")
    if cal_residual is not None:
        notes.append(f"gain calibration assumed, residual {cal_residual:.2f} LSB")
    else:
        notes.append("no gain calibration: sigma_Cu/Cu gate is the uncalibrated "
                     "1/(3 sqrt(2) 2^N)")
    if ron > 1e4:
        notes.append(f"Ron {ron:.0f} ohm is the measured transmission gate; a "
                     f"pipeline on this PDK needs a bootstrapped switch")

    return {
        "arch": "pipeline",
        "pdk": profile["id"],
        "family": profile["family"],
        "nbits": n,
        "vfs": vfs,
        "vdd": profile["vdd"],
        "fs_target": fs_target,
        "cap": cap["name"],
        "cap_kind": cap["kind"],
        "cap_role": "mdac_unit",
        "cu_ff": cu_ff,
        "cu_source": cu_source,
        "cu_auto_ff": cu_auto_ff,
        "c_tot_ff": c_stage_ff,
        "area_cap_um2": c_stage_ff / cap["density_ff_um2"],
        "binding_constraint": binding,
        "constraints_ff": constraints,
        "sigma_cu_rel": sigma_cu_actual,
        "dnl_msb_sigma_lsb": dnl_sigma,
        "stages": stages,
        "bits_per_stage": bps,
        "stage_gain": G,
        "beta": beta,
        "a0_req_db": a0_req_db,
        "opamp_gain_db": a0_db,
        "opamp_gbw_mhz": gbw_hz / 1e6,
        "sigma_cu_req_pct": sigma_cu_req * 100.0,
        "cal_residual_lsb": cal_residual,
        "t_set_ns": t_set_s * 1e9 if math.isfinite(t_set_s) else None,
        "t_sample_ns": t_sample_ns,
        "fs_ota": fs_ota,
        "fs_switch": fs_switch,
        "fs_max": fs_max,
        "assumed_inputs": assumed,
        "status": status,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# time-interleaved wrapper
# --------------------------------------------------------------------------
#
# TI is N copies of a sub-ADC plus a sampling clock:
#
#   fs_max = N * fs_max(sub)
#   offset spur 20log10(delta/(2A)), gain 20log10(eps/2),
#   skew 20log10(2 pi fin sigma_t), fin = fs/2
#
# The wrapper calls the existing evaluator for the sub-ADC at fs/N; it does not
# duplicate any sub-ADC algebra.

def evaluate_ti(spec, profile, headroom=DEFAULT_HEADROOM, temperature=T_DEFAULT):
    import copy

    n = int(spec["resolution"])
    vfs = float(spec["input"]["vfs"])
    fs_target = float(spec["fs_hz"])
    ti = spec.get("ti")
    if not ti:
        raise KeyError(f"ti block missing in {profile['id']} spec")
    ti_prof = profile.get("ti") or {}
    if not ti_prof:
        raise KeyError(f"ti backend not characterized in {profile['id']}")

    n_channels = int(ti.get("channels", 2))
    sub_arch = str(ti.get("sub_arch", "sar")).lower()
    sub_spec = copy.deepcopy(spec)
    sub_spec["arch"] = sub_arch
    sub_spec["fs_hz"] = fs_target / n_channels
    sub = evaluate(sub_spec, profile, headroom, temperature)
    sub_fs_max = float(sub["fs_max"])
    fs_max = n_channels * sub_fs_max

    cal = spec.get("calibration") or {}
    delta_uv = float(ti.get("offset_mismatch_uv",
                            ti_prof.get("offset_mismatch_uv", 0.0)))
    if cal.get("offset"):
        delta_uv = float(ti.get("offset_residual_uv",
                                ti_prof.get("offset_residual_uv", delta_uv * 0.1)))
    gain_pct = float(ti.get("gain_mismatch_pct",
                            ti_prof.get("gain_mismatch_pct", 0.0)))
    if cal.get("gain"):
        gain_pct = float(ti.get("gain_residual_pct",
                                ti_prof.get("gain_residual_pct", gain_pct * 0.1)))
    eps = gain_pct / 100.0
    skew_fs = float(ti.get("skew_fs_rms", ti_prof.get("skew_fs_rms", 0.0)))
    amp = vfs / 2.0
    fin_hz = fs_target / 2.0

    def spur(x):
        return -300.0 if x <= 0 else 20.0 * math.log10(x)

    spur_off = spur(delta_uv * 1e-6 / (2.0 * amp))
    spur_gain = spur(eps / 2.0)
    spur_skew = spur(2.0 * math.pi * fin_hz * skew_fs * 1e-15)
    worst = min(spur_off, spur_gain, spur_skew)
    sfdr_db = -worst
    sub_enob = float(sub.get("enob_ideal", sub.get("snr_ktc_bits", n)))
    enob_eff = min(sub_enob, (sfdr_db - 1.76) / 6.02)
    spur_target = 6.02 * n

    multiphase = bool(ti_prof.get("multiphase_clock", False))
    notes = []
    assumed = _provenance((("ti", ti_prof), ("opamp", _opamp(profile))))
    margins = {
        "sub_adc_rate": 20.0 * math.log10(fs_max / fs_target),
        "clock": 1e9 if (n_channels <= 2 or multiphase) else -1e9,
        "offset_mismatch": -spur_off - spur_target,
        "gain_mismatch": -spur_gain - spur_target,
        "skew": -spur_skew - spur_target,
    }
    binding = min(margins, key=margins.get)
    if fs_max < fs_target:
        status = "FAIL_RATE"
        binding = "sub_adc_rate"
        notes.append(f"{n_channels} channels x {sub_fs_max/1e3:.0f} kS/s = "
                     f"{fs_max/1e3:.0f} kS/s, below the {fs_target/1e3:.0f} kS/s "
                     f"target")
    elif n_channels > 2 and not multiphase:
        status = "FAIL_CLOCK"
        binding = "clock"
        notes.append(f"{n_channels} channels need a multiphase clock; the "
                     f"profile has none (adpll_gen has no PI/DTC)")
    elif sfdr_db < spur_target:
        status = "FAIL_OFFSET_MISMATCH"
        binding = "offset_mismatch"
        if worst == spur_gain:
            status = "FAIL_GAIN_MISMATCH"
            binding = "gain_mismatch"
        elif worst == spur_skew:
            status = "FAIL_SKEW"
            binding = "skew"
        notes.append(f"worst spur {sfdr_db:.1f} dB is below the {spur_target:.1f} "
                     f"dB ({n} bit) requirement; offset/gain/skew = "
                     f"{spur_off:.1f}/{spur_gain:.1f}/{spur_skew:.1f} dBc")
    else:
        status = "PASS_FEASIBLE_ASSUMPTION" if assumed else "PASS_FEASIBLE"

    notes.append(f"{n_channels}x {sub_arch}; sub-ADC at {fs_target/n_channels/1e3:.0f}"
                 f" kS/s, spurs at k*{fs_target/n_channels/1e3:.0f} kS/s")
    notes.append(f"skew at fin {fin_hz/1e3:.1f} kHz: {spur_skew:.1f} dBc for "
                 f"{skew_fs:.1f} fs rms; skew is a high-rate term, not a "
                 f"1 MS/s term")

    return {
        "arch": "ti",
        "pdk": profile["id"],
        "family": profile["family"],
        "nbits": n,
        "vfs": vfs,
        "vdd": profile["vdd"],
        "fs_target": fs_target,
        "cap": sub.get("cap", "none"),
        "cap_kind": sub.get("cap_kind", "none"),
        "cap_role": sub.get("cap_role", "none"),
        "cu_ff": float(sub.get("cu_ff", 0.0)),
        "c_tot_ff": n_channels * float(sub.get("c_tot_ff", 0.0)),
        "c_in_total_ff": n_channels * float(sub.get("c_tot_ff", 0.0)),
        "area_cap_um2": n_channels * float(sub.get("area_cap_um2", 0.0)),
        "binding_constraint": binding,
        "sigma_cu_rel": float(sub.get("sigma_cu_rel", 0.0)),
        "dnl_msb_sigma_lsb": float(sub.get("dnl_msb_sigma_lsb", 0.0)),
        "n_channels": n_channels,
        "sub_arch": sub_arch,
        "sub_status": sub["status"],
        "sub_fs_max": sub_fs_max,
        "fs_max": fs_max,
        "spur_offset_db": spur_off,
        "spur_gain_db": spur_gain,
        "spur_skew_db": spur_skew,
        "sfdr_db": sfdr_db,
        "spur_target_db": spur_target,
        "enob_eff": enob_eff,
        "offset_mismatch_uv": delta_uv,
        "gain_mismatch_pct": gain_pct,
        "skew_fs_rms": skew_fs,
        "multiphase_clock": multiphase,
        "assumed_inputs": assumed,
        "status": status,
        "notes": notes,
    }


def _provenance(pairs):
    """Anything short of a fully measured entry stays flagged."""
    assumed = []
    for label, obj in pairs:
        if obj.get("source") != "measured":
            assumed.append(label)
        if "source" not in obj:
            assumed.append(label + "(unmarked)")
    return assumed


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def human(report):
    arch = report.get("arch", "sar")
    render = {"sar": human_sar, "ramp": human_ramp,
              "sdm": human_sdm, "vco": human_vco,
              "dual_slope": human_dual_slope, "flash": human_flash,
              "pipeline": human_pipeline, "ti": human_ti}.get(arch)
    if render is None:
        raise KeyError(f"no report renderer for architecture '{arch}'")
    return render(report)


def human_sar(report):
    c = report["constraints_ff"]
    lines = [
        f"[{report['status']}] {report['pdk']}  {report['nbits']}-bit  "
        f"VFS={report['vfs']:.2f} V  target={report['fs_target']/1e3:.0f} kS/s",
        f"  cap            {report['cap']} ({report['cap_kind']}), "
        f"Cu={report['cu_ff']:.4f} fF [{report['cu_source']}], "
        f"Ctot={report['c_tot_ff']:.1f} fF",
        f"  binding        {report['binding_constraint']}  "
        f"(ktc {c['ktc_ff']:.4f} / mismatch {c['mismatch_ff']:.4f} / "
        f"layout {c['layout_ff']:.3f} / parasitic {c['parasitic_ff']:.3f} fF)",
        f"  matching       sigma_Cu/Cu={report['sigma_cu_rel']*100:.2f}%  "
        f"MSB DNL={report['dnl_msb_sigma_lsb']:.2f} LSB (1 sigma)",
        f"  noise          kT/C SNR={report['snr_ktc_db']:.1f} dB "
        f"({report['snr_ktc_bits']:.1f} bit); comparator budget "
        f"{report['comparator_budget_uv']:.0f} uV vs {report['comparator_sigma_uv']:.0f} uV "
        f"(x{report['comparator_margin']:.1f})",
        f"  timing         Ron={report['ron_ohm']:.0f} ohm, "
        f"t_bit={report['t_settle_bit_ns']:.1f} ns, "
        f"t_conv={report['t_conv_ns']:.1f} ns, fs_max={report['fs_max']/1e3:.0f} kS/s",
        f"  area           cap array ~{report['area_cap_um2']:.0f} um^2",
    ]
    if report["assumed_inputs"]:
        lines.append(f"  unverified     {', '.join(report['assumed_inputs'])}")
    for note in report["notes"]:
        lines.append(f"  note           {note}")
    return "\n".join(lines)


def human_ramp(report):
    c = report["constraints_ff"]
    lines = [
        f"[{report['status']}] {report['pdk']}  ramp {report['nbits']}-bit  "
        f"VFS={report['vfs']:.2f} V  target={report['fs_target']/1e3:.0f} kS/s",
        f"  hold cap       {report['cap']} ({report['cap_kind']}), "
        f"C_hold={report['cu_ff']:.1f} fF, area ~{report['area_cap_um2']:.0f} um^2 "
        f"(binding: {report['binding_constraint']}; "
        f"ktc {c['ktc_ff']:.1f} / layout {c['layout_ff']:.1f} / "
        f"parasitic {c['parasitic_ff']:.1f} fF)",
        f"  dnl            {report['dnl_msb_sigma_lsb']:.2f} LSB "
        f"(ramp {report['dnl_terms_lsb']['ramp']:.2f} + comparator "
        f"{report['dnl_terms_lsb']['comparator']:.2f}); gain sigma "
        f"{report['sigma_cu_rel']*100:.2f}%",
        f"  comparator     {report['comparator_sigma_uv']:.0f} uV vs "
        f"{report['comparator_budget_uv']:.0f} uV half-LSB (x"
        f"{report['comparator_margin']:.1f})",
        f"  timing         f_clk={report['f_clk_hz']/1e6:.1f} MHz "
        f"(need {report['f_clk_required_hz']/1e6:.1f}), "
        f"t_conv={report['t_conv_ns']:.0f} ns, fs_max="
        f"{report['fs_max']/1e3:.0f} kS/s",
    ]
    if report["assumed_inputs"]:
        lines.append(f"  unverified     {', '.join(report['assumed_inputs'])}")
    for note in report["notes"]:
        lines.append(f"  note           {note}")
    return "\n".join(lines)


def human_sdm(report):
    c = report["constraints_ff"]
    lines = [
        f"[{report['status']}] {report['pdk']}  sdm {report['nbits']}-bit  "
        f"VFS={report['vfs']:.2f} V  fs_out={report['fs_target']/1e3:.0f} kS/s  "
        f"order {report['order']}, {report['quantizer_bits']}-bit, "
        f"OSR {report['osr']}",
        f"  sampling cap   {report['cap']} ({report['cap_kind']}), "
        f"C_s={report['cu_ff']:.1f} fF, area ~{report['area_cap_um2']:.0f} um^2 "
        f"(binding: {report['binding_constraint']}; "
        f"ktc {c['ktc_ff']:.4f} / layout {c['layout_ff']:.1f} / "
        f"parasitic {c['parasitic_ff']:.3f} fF)",
        f"  loop           ideal SQNR {report['sqnr_ideal_db']:.1f} dB "
        f"({report['enob_ideal']:.2f} bit); OSR {report['osr_required']:.0f} "
        f"needed for {report['nbits']} bit +6 dB (ceiling "
        f"{report['osr_max']}); thermal ENOB {report['enob_thermal']:.2f} bit "
        f"at n_eff {report['n_eff']:.2f}",
        f"  clock          f_mod={report['f_mod_hz']/1e6:.2f} MHz, fs_max="
        f"{report['fs_max']/1e3:.0f} kS/s; integrator t_int="
        f"{report['t_int_ns']:.2f} ns at beta {report['feedback_beta']:.2f} "
        f"(GBW >= {report['gbw_min_mhz']:.1f} MHz)",
        f"  decimator      CIC{report['decimator']['cic_order']}/R1="
        f"{report['decimator']['cic_r1']} + {report['decimator']['halfbands']} "
        f"halfbands, alias {report['decimator']['alias_db']:.1f} dB vs "
        f"{report['decimator']['required_db']:.1f} required, droop "
        f"{report['decimator']['droop_db']:.2f} dB, "
        f"{report['decimator']['taps']} taps",
    ]
    if report["assumed_inputs"]:
        lines.append(f"  unverified     {', '.join(report['assumed_inputs'])}")
    for note in report["notes"]:
        lines.append(f"  note           {note}")
    return "\n".join(lines)


def human_vco(report):
    lines = [
        f"[{report['status']}] {report['pdk']}  vco {report['nbits']}-bit  "
        f"VFS={report['vfs']:.2f} V  target={report['fs_target']/1e3:.0f} kS/s",
        f"  tuning         {report['f_min_hz']/1e6:.1f}.."
        f"{report['f_max_hz']/1e6:.1f} MHz (span "
        f"{report['span_hz']/1e6:.1f} MHz, KVCO "
        f"{report['kvco_mhz_v']:.0f} MHz/V), window "
        f"{report['t_window_ns']:.1f} ns",
        f"  resolution     {report['t_window_ns']*1e-9*report['span_hz']:.0f} "
        f"counts at full scale for {1 << report['nbits']} codes; fs_max="
        f"{report['fs_max']/1e3:.0f} kS/s",
        f"  errors         count SNR {report['snr_quant_db']:.1f} dB, jitter SNR "
        + (f"{report['snr_jitter_db']:.1f} dB" if report.get("snr_jitter_db")
           else "unmeasured")
        + f"; ENOB<={report['enob_ideal']:.2f} bit",
        f"  nonlinearity   curvature {report['curvature_pct']:.1f}% -> "
        f"DNL~{report['dnl_msb_sigma_lsb']:.3f} LSB, "
        f"INL~{report['inl_est_lsb']:.3f} LSB",
    ]
    if report["assumed_inputs"]:
        lines.append(f"  unverified     {', '.join(report['assumed_inputs'])}")
    for note in report["notes"]:
        lines.append(f"  note           {note}")
    return "\n".join(lines)


def human_dual_slope(report):
    c = report["constraints_ff"]
    lines = [
        f"[{report['status']}] {report['pdk']}  dual-slope {report['nbits']}-bit  "
        f"VFS={report['vfs']:.2f} V  target={report['fs_target']/1e3:.0f} kS/s",
        f"  rate           f_clk={report['f_clk_hz']/1e6:.1f} MHz / "
        f"{report['codes']} clocks = {report['fs_max']/1e3:.0f} kS/s "
        f"(T1 {report['t1_ns']/1e3:.2f} us)",
        f"  integrator     C={report['c_integ_pf']:.1f} pF, R={report['r_ohm']/1e3:.1f} "
        f"kohm, R*C={report['rc_us']:.2f} us, area ~{report['area_cap_um2']:.0f} "
        f"um^2 (binding {report['binding_constraint']}; ktc {c['ktc_ff']:.1f} / "
        f"layout {c['layout_ff']:.1f} fF)",
        f"  errors         A0 {report['a0_db']:.1f} dB vs {report['a0_req_db']:.1f} "
        f"required; DA {report['da_pct']:.4f}% vs {report['da_req_pct']:.4f}%; "
        f"offset {report['offset_lsb']:.2f} LSB; delay "
        f"{report['delay_lsb']:.2f} code",
        f"  linearity      DNL bound {report['dnl_bound_lsb']:.3f} LSB "
        f"(monotonic), gain error {report['gain_error_pct']:.3f}%, "
        f"SNDR<={report['sndr_db']:.1f} dB",
    ]
    if report["assumed_inputs"]:
        lines.append(f"  unverified     {', '.join(report['assumed_inputs'])}")
    for note in report["notes"]:
        lines.append(f"  note           {note}")
    return "\n".join(lines)


def human_flash(report):
    lines = [
        f"[{report['status']}] {report['pdk']}  flash {report['nbits']}-bit  "
        f"VFS={report['vfs']:.2f} V  target={report['fs_target']/1e3:.0f} kS/s",
        f"  ladder         {report['n_comparators']} comparators, "
        f"{1 << report['nbits']} units at {report['ru_ohm']:.0f} ohm "
        f"(R_tap {report['r_tap_ohm']/1e3:.1f} kohm), area ~"
        f"{report['area_cap_um2']:.0f} um^2",
        f"  offset         sigma {report['sigma_os_uv']:.0f} uV vs "
        f"{report['offset_budget_uv']:.0f} uV budget -> area x"
        f"{report['area_scale']:.2f} (cap x{report['max_area_scale']:.1f}), "
        f"DNL {report['dnl_msb_sigma_lsb']:.2f} LSB",
        f"  timing         ladder {report['t_ladder_ns']:.2f} + comparator "
        f"{report['t_cmp_ns']:.2f} + encoder {report['t_enc_ns']:.2f} ns -> "
        f"fs_max {report['fs_max']/1e3:.0f} kS/s",
    ]
    if report["assumed_inputs"]:
        lines.append(f"  unverified     {', '.join(report['assumed_inputs'])}")
    for note in report["notes"]:
        lines.append(f"  note           {note}")
    return "\n".join(lines)


def human_pipeline(report):
    c = report["constraints_ff"]
    lines = [
        f"[{report['status']}] {report['pdk']}  pipeline {report['nbits']}-bit  "
        f"VFS={report['vfs']:.2f} V  target={report['fs_target']/1e3:.0f} kS/s",
        f"  stages         {report['stages']} x {report['bits_per_stage']:.1f} b, "
        f"G={report['stage_gain']}, beta={report['beta']:.2f}",
        f"  mdac cap       unit {report['cu_ff']:.3f} fF [{report['cu_source']}], "
        f"stage {report['c_tot_ff']:.1f} fF, area ~"
        f"{report['area_cap_um2']:.0f} um^2 (binding {report['binding_constraint']}; "
        f"mismatch {c['mismatch_ff']:.3f} / ktc {c['ktc_ff']:.3f} / layout "
        f"{c['layout_ff']:.3f} fF)",
        f"  matching       sigma_Cu/Cu {report['sigma_cu_rel']*100:.4f}% vs "
        f"{report['sigma_cu_req_pct']:.4f}% required, DNL "
        f"{report['dnl_msb_sigma_lsb']:.2f} LSB",
        f"  ota            A0 {report['opamp_gain_db']:.1f} dB vs "
        f"{report['a0_req_db']:.1f} required; GBW {report['opamp_gbw_mhz']:.0f} "
        f"MHz, t_set {report['t_set_ns']:.2f} ns -> {report['fs_ota']/1e3:.0f} kS/s",
        f"  switch         t_sample {report['t_sample_ns']:.1f} ns -> "
        f"{report['fs_switch']/1e3:.0f} kS/s; fs_max "
        f"{report['fs_max']/1e3:.0f} kS/s",
    ]
    if report["assumed_inputs"]:
        lines.append(f"  unverified     {', '.join(report['assumed_inputs'])}")
    for note in report["notes"]:
        lines.append(f"  note           {note}")
    return "\n".join(lines)


def human_ti(report):
    lines = [
        f"[{report['status']}] {report['pdk']}  ti {report['nbits']}-bit  "
        f"VFS={report['vfs']:.2f} V  target={report['fs_target']/1e3:.0f} kS/s",
        f"  channels       {report['n_channels']} x {report['sub_arch']} at "
        f"{report['fs_target']/report['n_channels']/1e3:.0f} kS/s; sub "
        f"fs_max {report['sub_fs_max']/1e3:.0f} kS/s -> total "
        f"{report['fs_max']/1e3:.0f} kS/s",
        f"  spurs          offset {report['spur_offset_db']:.1f}, gain "
        f"{report['spur_gain_db']:.1f}, skew {report['spur_skew_db']:.1f} dBc; "
        f"SFDR {report['sfdr_db']:.1f} dB (need {report['spur_target_db']:.1f})",
        f"  mismatch       offset {report['offset_mismatch_uv']:.1f} uV, gain "
        f"{report['gain_mismatch_pct']:.3f}%, skew {report['skew_fs_rms']:.1f} "
        f"fs rms; ENOB_eff {report['enob_eff']:.2f} bit",
        f"  input          C_tot {report['c_tot_ff']:.1f} fF for "
        f"{report['n_channels']} channels (binding {report['binding_constraint']})",
    ]
    if report["assumed_inputs"]:
        lines.append(f"  unverified     {', '.join(report['assumed_inputs'])}")
    for note in report["notes"]:
        lines.append(f"  note           {note}")
    return "\n".join(lines)


def matrix(spec, profiles):
    rows = []
    for profile in profiles:
        try:
            rows.append(evaluate(spec, profile))
        except KeyError as exc:
            rows.append({"pdk": profile["id"], "status": "FAIL_UNCHARACTERIZED",
                         "error": str(exc), "fs_target": spec["fs_hz"]})
    return rows


def matrix_table(rows):
    head = (f"{'pdk':24s} {'arch':10s} {'status':26s} {'cap fF':>8s} {'Ctot fF':>9s} "
            f"{'fs_max':>9s} {'ENOB_est':>9s} {'area um2':>9s}")
    out = [head, "-" * len(head)]
    for r in rows:
        if "error" in r:
            out.append(f"{r['pdk']:24s} {r.get('arch', 'sar'):10s} {r['status']:26s} {r['error']}")
            continue
        if "snr_ktc_bits" in r:
            enob = f"{r['snr_ktc_bits']:.2f}"
        elif r.get("enob_thermal") is not None:
            enob = f"{r['enob_thermal']:.2f}"
        elif r.get("enob_ideal") is not None:
            enob = f"{r['enob_ideal']:.2f}"
        elif r.get("enob_eff") is not None:
            enob = f"{r['enob_eff']:.2f}"
        else:
            enob = "-"
        out.append(
            f"{r['pdk']:24s} {r.get('arch', 'sar'):10s} {r['status']:26s} "
            f"{r['cu_ff']:8.3f} {r['c_tot_ff']:9.1f} "
            f"{r['fs_max']/1e3:7.0f}k {enob:>9s} {r['area_cap_um2']:9.0f}"
        )
    return "\n".join(out)


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv):
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", help="generator spec JSON")
    ap.add_argument("--pdk", help="PDK profile id or path")
    ap.add_argument("--pdk-dir", default=os.path.join(os.path.dirname(__file__), "pdk"))
    ap.add_argument("--matrix", action="store_true", help="run every profile on --spec")
    ap.add_argument("--headroom", type=float, default=DEFAULT_HEADROOM)
    ap.add_argument("--temperature", type=float, default=T_DEFAULT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.spec:
        ap.error("--spec is required")

    spec = load(args.spec)

    def profiles():
        names = sorted(f[:-5] for f in os.listdir(expand(args.pdk_dir)) if f.endswith(".json"))
        return [load(os.path.join(args.pdk_dir, n + ".json")) for n in names]

    if args.matrix:
        rows = matrix(spec, profiles())
        if args.json:
            print(dump(rows))
        else:
            print(matrix_table(rows))
        return 0 if any(r["status"].startswith("PASS") for r in rows) else 1

    if not args.pdk:
        ap.error("--pdk is required unless --matrix is used")
    if os.path.sep in args.pdk:
        profile = load(args.pdk)
    else:
        profile = load(os.path.join(args.pdk_dir, args.pdk + ".json"))

    report = evaluate(spec, profile, headroom=args.headroom,
                      temperature=args.temperature)
    print(dump(report) if args.json else human(report))
    return 0 if report["status"].startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

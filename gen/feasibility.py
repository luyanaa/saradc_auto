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


ARCHITECTURES = ("sar", "ramp", "sdm", "vco")


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
    raise KeyError(f"architecture '{arch}' is not implemented; "
                   f"known: {', '.join(ARCHITECTURES)}")


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

def evaluate_sdm(spec, profile, headroom=DEFAULT_HEADROOM, temperature=T_DEFAULT):
    n = int(spec["resolution"])
    vfs = float(spec["input"]["vfs"])
    fs_target = float(spec["fs_hz"])
    sdm = profile.get("sdm")
    if not sdm:
        raise KeyError(f"sdm backend not characterized in {profile['id']}")

    order = int(sdm.get("order", 2))
    qbits = int(sdm.get("quantizer_bits", 1))
    osr = int(spec.get("logic", {}).get("osr") or sdm.get("default_osr", 64))
    osr_max = int(sdm.get("osr_max", max(osr, 1)))
    loading_db = float(sdm.get("loading_db", 3.9 if qbits == 1 else 0.0))
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
    c_ktc_f = 12.0 * K_B * temperature * (2.0 ** (2 * n)) / (vfs ** 2 * osr)
    c_par_f = profile["c_parasitic_est_ff"] * headroom * 1e-15 / osr
    c_layout_f = cap["min_geom_area_um2"] * cap["density_ff_um2"] * 1e-15
    constraints = {"ktc_ff": c_ktc_f * 1e15,
                   "layout_ff": c_layout_f * 1e15,
                   "parasitic_ff": c_par_f * 1e15}
    c_in_f = max(constraints.values()) * 1e-15
    c_in_ff = c_in_f * 1e15
    binding = max(constraints, key=constraints.get)
    area_in_um2 = c_in_ff / cap["density_ff_um2"]

    # ---- clock and integrator settling ----------------------------------
    f_mod = osr * fs_target
    ceiling_hz = min(float(profile.get("digital_ceiling_mhz") or 1e9),
                     float(sdm.get("clk_max_mhz") or 1e9)) * 1e6
    gbw_mhz = sdm.get("integrator_gbw_mhz")
    if gbw_mhz:
        t_int_ns = math.log(2.0 ** (qbits + order + 1)) / (2 * math.pi * gbw_mhz * 1e6) * 1e9
        t_int_budget_ns = 0.5 / f_mod * 1e9
        gbw_min_mhz = (math.log(2.0 ** (qbits + order + 1))
                       / (2 * math.pi * 0.5 / f_mod)) / 1e6
    else:
        t_int_ns = t_int_budget_ns = gbw_min_mhz = None

    assumed = _provenance((("cap", cap), ("switch", sw), ("comparator", cmp_),
                           ("sdm", sdm)))
    notes.append(f"OSR {osr}: ideal in-band SQNR {sqnr:.1f} dB "
                 f"({enob_ideal:.2f} bit) for order {order}, {qbits}-bit "
                 f"quantizer; OSR {osr_required:.0f} needed for {n} bit "
                 f"+6 dB.")
    notes.append("output is a bitstream: an ideal decimator is assumed and no "
                 "decimator exists in this repo yet, so DNL/INL apply to the "
                 "reconstructed samples only")
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
    elif t_int_ns is not None and t_int_ns > t_int_budget_ns:
        status = "FAIL_SETTLING"
        notes.append(f"integrator needs GBW >= {gbw_min_mhz:.1f} MHz "
                     f"(t_int {t_int_ns:.2f} ns vs {t_int_budget_ns:.2f} ns "
                     f"budget)")
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
        "sigma_cu_rel": (cap["ac_pct_um"] / 100.0) / math.sqrt(max(area_in_um2, 1e-9)),
        "dnl_msb_sigma_lsb": 0.0,
        "order": order,
        "quantizer_bits": qbits,
        "osr": osr,
        "osr_required": osr_required,
        "osr_max": osr_max,
        "sqnr_ideal_db": sqnr,
        "enob_ideal": enob_ideal,
        "f_mod_hz": f_mod,
        "t_int_ns": t_int_ns,
        "gbw_min_mhz": gbw_min_mhz,
        "fs_max": fs_target if status.startswith("PASS") else ceiling_hz / osr,
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
              "sdm": human_sdm, "vco": human_vco}.get(arch)
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
        f"ktc {c['ktc_ff']:.1f} / layout {c['layout_ff']:.1f} / "
        f"parasitic {c['parasitic_ff']:.1f} fF)",
        f"  loop           ideal SQNR {report['sqnr_ideal_db']:.1f} dB "
        f"({report['enob_ideal']:.2f} bit); OSR {report['osr_required']:.0f} "
        f"needed for {report['nbits']} bit +6 dB (ceiling "
        f"{report['osr_max']})",
        f"  clock          f_mod={report['f_mod_hz']/1e6:.2f} MHz"
        + (f", integrator t_int={report['t_int_ns']:.2f} ns "
           f"(GBW >= {report['gbw_min_mhz']:.1f} MHz)"
           if report.get("t_int_ns") else ", integrator GBW not characterized"),
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
    head = (f"{'pdk':24s} {'arch':5s} {'status':26s} {'cap fF':>8s} {'Ctot fF':>9s} "
            f"{'fs_max':>9s} {'ENOB_est':>9s} {'area um2':>9s}")
    out = [head, "-" * len(head)]
    for r in rows:
        if "error" in r:
            out.append(f"{r['pdk']:24s} {r.get('arch', 'sar'):5s} {r['status']:26s} {r['error']}")
            continue
        if "snr_ktc_bits" in r:
            enob = f"{r['snr_ktc_bits']:.2f}"
        elif r.get("enob_ideal") is not None:
            enob = f"{r['enob_ideal']:.2f}"
        else:
            enob = "-"
        out.append(
            f"{r['pdk']:24s} {r.get('arch', 'sar'):5s} {r['status']:26s} "
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

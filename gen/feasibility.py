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


def evaluate(spec, profile, headroom=DEFAULT_HEADROOM, temperature=T_DEFAULT):
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
# reporting
# --------------------------------------------------------------------------

def human(report):
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
    head = (f"{'pdk':24s} {'status':26s} {'Cu fF':>8s} {'Ctot fF':>9s} "
            f"{'fs_max':>9s} {'ENOB_kTC':>9s} {'area um2':>9s}")
    out = [head, "-" * len(head)]
    for r in rows:
        if "error" in r:
            out.append(f"{r['pdk']:24s} {r['status']:26s} {r['error']}")
            continue
        out.append(
            f"{r['pdk']:24s} {r['status']:26s} {r['cu_ff']:8.3f} {r['c_tot_ff']:9.1f} "
            f"{r['fs_max']/1e3:7.0f}k {r['snr_ktc_bits']:9.2f} {r['area_cap_um2']:9.0f}"
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

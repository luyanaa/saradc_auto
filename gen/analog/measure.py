#!/usr/bin/env python3
"""Measure a generated MOSCAP leaf with the real PDK deck.

Two measurements:

1. ``density``  sweep the bias across the capacitor and report C(V).  A
   MOSCAP is not a linear capacitor: near flat band the channel depletes and
   C collapses.  The usable density is the value away from flat band, and the
   report keeps the whole curve so nobody quotes a single number as if the
   device were linear.

2. ``mismatch`` Monte Carlo over the PDK's own oxide-thickness mismatch.  The
   sky130 models scale ``toxm`` with ``agauss(0,1,1) * slope / sqrt(l*w*mult)``,
   so C inherits a Pelgrom-shaped sigma.  Fitting sigma_C/C against
   1/sqrt(area) gives A_C in %*um, which is the number the feasibility gate
   wants.

Nothing here is written back automatically.  ``--write-profile`` prints the
patch and the caller decides.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import deck  # noqa: E402
import macro_gen  # noqa: E402

FREQ = 1.0e6
ROW = re.compile(r"^\s*\d+\s+([-\d.eE+]+)\s+([-\d.eE+]+),\s*([-\d.eE+]+)\s*$")


def ngspice(netlist, workdir):
    path = os.path.join(workdir, "meas.sp")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(netlist)
    proc = subprocess.run(["ngspice", "-b", path], cwd=workdir,
                          capture_output=True, text=True, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def parse_ac(log):
    """Return (re, im) of the first printed AC current row."""
    for line in log.splitlines():
        m = ROW.match(line)
        if m:
            return float(m.group(2)), float(m.group(3))
    return None


def cap_from_im(im):
    return abs(im) / (2.0 * math.pi * FREQ) * 1e15          # fF


def cap_netlist(deck_path, params, vcm, vdiff, leaf="", extra=""):
    wn = params["wn"]
    wp = params["wp"]
    l = params["l"]
    n = params.get("n_parallel", 1)
    return "* adc generator measurement netlist\n" + leaf + f"""
.include {deck_path}
Vg vsh 0 dc {vcm + vdiff:.6f} ac 1
Vb fl 0 dc {vcm - vdiff:.6f}
Vnw vnw 0 dc {vcm * 2:.6f}
Vpw vpw 0 dc 0
Xc vsh fl vnw vpw SARADC_CAP_UNIT
.ac lin 1 {FREQ:g} {FREQ:g}
.print ac i(vg)
.end
"""


def area_um2(params):
    return params.get("n_parallel", 1) * (params["wn"] + params["wp"]) * params["l"]


def sweep(deck_path, params, vcm, points, leaf=""):
    rows = []
    with tempfile.TemporaryDirectory(prefix="adcgen_meas_") as tmp:
        for v in points:
            rc, log = ngspice(cap_netlist(deck_path, params, vcm, v, leaf), tmp)
            if rc != 0:
                raise SystemExit(f"ngspice failed at vdiff={v}: "
                                 + log[-800:])
            re_, im = parse_ac(log)
            rows.append({"vdiff_v": v, "c_ff": cap_from_im(im),
                         "g_s": re_})
    return rows


def fit_pelgrom(points):
    """points: [(area_um2, sigma_rel)] -> A_C in %*um by least squares."""
    xs = [1.0 / math.sqrt(a) for a, _ in points]
    ys = [s for _, s in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return slope * 100.0                                # %*um


def mismatch(deck_path, params, vcm, runs, seed0=1, leaf=""):
    """Monte Carlo over the PDK oxide mismatch."""
    values = []
    with tempfile.TemporaryDirectory(prefix="adcgen_mc_") as tmp:
        for r in range(runs):
            net = cap_netlist(deck_path, params, vcm, 0.6, leaf)
            net = net.replace(".ac lin", f""".control
set rndseed={seed0 + r}
run
.endc
.ac lin""")
            net = net.replace(".include ", ".param mc_mm_switch=1\n.include ", 1)
            rc, log = ngspice(net, tmp)
            if rc != 0:
                return {"runs": 0, "error": log[-400:]}
            got = parse_ac(log)
            if not got:
                return {"runs": 0, "error": "no AC row printed"}
            values.append(cap_from_im(got[1]))
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    return {"runs": n, "mean_ff": mean, "sigma_ff": math.sqrt(var),
            "sigma_rel": math.sqrt(var) / mean if mean else None,
            "min_ff": min(values), "max_ff": max(values)}


def measure(profile, deck_path, geometry=None, vcm=None, mc_runs=0):
    analog = profile.get("analog") or {}
    size = analog.get("sizing_um") or {}
    params = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
              "l": size.get("l", 0.15), "n_parallel": 1}
    if geometry:
        params.update(geometry)
    vcm = vcm if vcm is not None else profile["vdd"] / 2.0

    deck_path = os.path.abspath(os.path.expanduser(deck_path))
    dev = macro_gen.device_binding(profile)
    leaf, _meta = macro_gen.topo_cap_unit(dev, params)

    points = [round(-0.6 + 0.1 * i, 3) for i in range(13)]
    curve = sweep(deck_path, params, vcm, points, leaf)

    usable = [r["c_ff"] for r in curve if abs(r["vdiff_v"]) >= 0.3]
    typ = sum(usable) / len(usable) if usable else curve[len(curve) // 2]["c_ff"]
    area = area_um2(params)

    result = {
        "tech": profile["id"],
        "deck": deck_path,
        "geometry_um": {"wn": params["wn"], "wp": params["wp"], "l": params["l"],
                        "n_parallel": params["n_parallel"]},
        "area_um2": area,
        "vcm_v": vcm,
        "cuv": curve,
        "c_typ_ff": typ,
        "density_ff_um2": typ / area,
        "flatband_min_ff": min(r["c_ff"] for r in curve),
    }

    if mc_runs:
        mc = mismatch(deck_path, params, vcm, mc_runs, leaf=leaf)
        result["mismatch"] = mc
        if mc.get("sigma_rel"):
            result["ac_pct_um_estimate"] = fit_pelgrom([(area, mc["sigma_rel"])])
    return result


def human(r):
    lines = [
        f"{r['tech']} unit cap  Wn={r['geometry_um']['wn']:g} "
        f"Wp={r['geometry_um']['wp']:g} L={r['geometry_um']['l']:g} "
        f"(area {r['area_um2']:.3f} um^2)",
        f"  C typ {r['c_typ_ff']:.3f} fF over |Vdiff|>=0.3 V, "
        f"flat-band min {r['flatband_min_ff']:.3f} fF",
        f"  density {r['density_ff_um2']:.3f} fF/um^2",
        "  Vdiff    C(fF)",
    ]
    for row in r["cuv"]:
        lines.append(f"  {row['vdiff_v']:+5.2f}  {row['c_ff']:8.3f}   "
                     + "#" * int(row["c_ff"] * 2))
    if "mismatch" in r:
        mc = r["mismatch"]
        if mc.get("sigma_rel"):
            lines.append(f"  MC {mc['runs']} runs: mean {mc['mean_ff']:.3f} fF, "
                         f"sigma {mc['sigma_ff']:.4f} fF "
                         f"({mc['sigma_rel']*100:.3f} %), "
                         f"A_C ~ {r.get('ac_pct_um_estimate', float('nan')):.4f} %*um")
        else:
            lines.append(f"  MC failed: {mc.get('error', 'unknown')}")
    return "\n".join(lines)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdk", default="sky130A_hd")
    ap.add_argument("--deck", required=True)
    ap.add_argument("--wn", type=float)
    ap.add_argument("--wp", type=float)
    ap.add_argument("--l", type=float)
    ap.add_argument("--n-parallel", type=int, default=1)
    ap.add_argument("--mc-runs", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    profile = macro_gen.load_profile(args.pdk)
    geometry = {k: v for k, v in
                (("wn", args.wn), ("wp", args.wp), ("l", args.l),
                 ("n_parallel", args.n_parallel)) if v is not None}
    result = measure(profile, args.deck, geometry, mc_runs=args.mc_runs)

    text = json.dumps(result, indent=2) if args.json else human(result)
    if args.out:
        with open(os.path.expanduser(args.out), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

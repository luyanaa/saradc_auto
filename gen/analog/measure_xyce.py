#!/usr/bin/env python3
"""Measure the ics55 analog leaves with the flattened Xyce deck.

This is the Xyce counterpart of measure.py (MOSCAP) and the deterministic
half of char.py (switch, comparator).  The ics55 PDK only ships HSpice
models, so the leaves are measured against the flattened deck produced by
``deck.py --ics55-xyce``.

Measured here:

  moscap       C(Vdiff) at 1 MHz and the usable/flat-band density.  A MOSCAP
               is not a linear capacitor: near flat band the channel depletes
               and C collapses, so the whole curve is kept and the density is
               reported as the flat-band minimum (conservative for sizing).
  switch_tg    Ron(Vin) over the input range with 10 mV across the closed
               gate, and the off-state capacitance at the DAC-side node.
               Both are measured over a width study, because widening the
               switch trades Ron against the cap it dumps on the bottom plate.
  comparator   systematic input offset by bisection on the decision, and the
               clk-to-output regeneration delay (50 % crossing).  Inside the
               same netlist the StrongArm is 9 bare MOS devices.

Not measured here, on purpose:

  * Pelgrom / random mismatch.  The vendor statistical hooks are HSpice
    ``agauss`` expressions inside the wrapper subckt.  The flattened deck
    bakes every mismatch delta to zero, so there is no scatter to sample.
  * input-referred noise.  Xyce 7.10 ``.noise`` returns a zero spectrum even
    for a 1 kohm resistor (see gen/analog/PDK_FLOW.md), so there is no
    comparator noise number either.

Nothing is written back to the profile automatically; ``--json`` prints the
raw result and ``--out`` writes it to a file for the profile author to copy.
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

# ics55 55 nm 1.2 V hvt device, W=1 um L=60 nm -- the size the analog leaves
# are drawn at.  The binned hvt models are geometry-generic inside
# W 0.90..9.09 um / L 54..72 nm; the svt/lvt cards would be locked to this
# geometry.  These S/D numbers are the vendor wrapper defaults at that size.
DEFAULT_DEVICES = {
    "nmos": "nm1p2_hvt_lp",
    "pmos": "pm1p2_hvt_lp",
}
SA = 4.056e-7

FREQ_HZ = 1.0e6

NUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def clean(log, keep=800):
    lines = [ln for ln in log.splitlines()
             if not re.search(r"^\s*$|Warning:.*BSIM4|pbswgd|conductance",
                              ln)]
    return "\n".join(lines)[-keep:]


def default_xyce():
    env = os.environ.get("XYCE")
    if env:
        return env
    cand = ("/home/yanlu/spack/opt/spack/linux-ivybridge/xyce-7.10.0-"
            "lfrc7zxg3xzdkh67lqrvbig5oyxrklvf/bin/Xyce")
    return cand if os.path.exists(cand) else "Xyce"


# --------------------------------------------------------------------------
# runner and .prn parser
# --------------------------------------------------------------------------

def run(netlist, workdir, xyce=None, timeout=900):
    """Run one netlist; return (rc, log, prn_path, header, rows)."""
    path = os.path.join(workdir, "probe.cir")
    for ext in (".prn", ".FD.prn", ".NOISE.prn", "_noise.dat"):
        stale = path + ext
        if os.path.exists(stale):
            os.remove(stale)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(netlist)
    proc = subprocess.run([xyce or default_xyce(), "probe.cir"], cwd=workdir,
                          capture_output=True, text=True, timeout=timeout)
    log = proc.stdout + proc.stderr
    for cand in (path + ".FD.prn", path + ".prn"):
        if os.path.exists(cand):
            header, rows = read_prn(cand)
            return proc.returncode, log, cand, header, rows
    return proc.returncode, log, None, [], []


def read_prn(path):
    header, rows = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("Index"):
                header = s.split()
                continue
            if s.startswith("End of"):
                break
            nums = [float(x) for x in NUM.findall(s)]
            if len(nums) >= 2:
                rows.append(nums)
    return header, rows


def col(header, want, fallback):
    """1-based index of a named column (Xyce keeps .print order otherwise)."""
    try:
        return header.index(want)
    except ValueError:
        return fallback


def mos(name, d, g, s, b, model, w_um, l_um, nf=1):
    """One bare BSIM4 instance with explicit S/D geometry.

    ``sa``/``sb`` are the vendor wrapper defaults (0.4056 um).  The junction
    area/perimeter are the physical values for a W x sa diffusion; leaving
    them at the wrapper default of 0 makes Xyce reset the S/D conductance and
    drops the junction capacitance, which changes the comparator delay.
    """
    w_m = w_um * 1e-6
    area = w_m * SA
    perim = 2.0 * (w_m + SA)
    return (f"{name} {d} {g} {s} {b} {model} "
            f"w={w_um:g}e-6 l={l_um:g}e-6 nf={nf} "
            f"sa={SA:g} sb={SA:g} sd=0 "
            f"as={area:g} ad={area:g} ps={perim:g} pd={perim:g} "
            f"nrd=0 nrs=0 sca=0 scb=0 scc=0")


def profile_devices(profile):
    dev = (profile.get("devices") or {}).copy()
    for key, default in DEFAULT_DEVICES.items():
        dev.setdefault(key, default)
    return dev


# --------------------------------------------------------------------------
# moscap
# --------------------------------------------------------------------------

def cap_netlist(deck, params, vcm, vdiff, dev, vdd):
    wn = params["wn"]
    wp = params["wp"]
    l = params["l"]
    n = int(params.get("n_parallel", 1))
    lines = ["* ics55 moscap C(Vdiff) probe", f".include {deck}",
             f"Vg vsh 0 dc {vcm + vdiff:.6f} ac 1",
             f"Vb fl 0 dc {vcm - vdiff:.6f}",
             f"Vnw vnw 0 dc {vdd:g}",
             "Vpw vpw 0 dc 0"]
    for k in range(n):
        lines.append(mos(f"MN{k}", "fl", "vsh", "fl", "vpw",
                         dev["nmos"], wn, l))
    for k in range(n):
        lines.append(mos(f"MP{k}", "fl", "vsh", "fl", "vnw",
                         dev["pmos"], wp, l))
    lines += [f".ac lin 1 {FREQ_HZ:g} {FREQ_HZ:g}",
              ".print ac i(Vg)", ".end"]
    return "\n".join(lines) + "\n"


def cap_sweep(deck, params, vcm, points, dev, vdd, xyce=None):
    out = []
    with tempfile.TemporaryDirectory(prefix="ics55_cap_") as tmp:
        for v in points:
            rc, log, prn, header, rows = run(
                cap_netlist(deck, params, vcm, v, dev, vdd), tmp, xyce)
            if rc != 0 or not rows:
                raise SystemExit(f"Xyce cap probe failed at vdiff={v}: "
                                 + clean(log))
            im = rows[0][col(header, "Im(I(VG))", 3)]
            out.append({"vdiff_v": v,
                        "c_ff": abs(im) / (2.0 * math.pi * FREQ_HZ) * 1e15})
    return out


def measure_moscap(profile, deck, geometry=None, points=13, vcm=None,
                   xyce=None):
    dev = profile_devices(profile)
    size = (profile.get("analog") or {}).get("sizing_um") or {}
    params = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 1.0),
              "l": size.get("l", 0.06), "n_parallel": 1}
    if geometry:
        params.update(geometry)
    vdd = profile["vdd"]
    vcm = vdd / 2.0 if vcm is None else vcm
    span = vdd / 2.0
    sweep = [round(-span + 2.0 * span * i / (points - 1), 3)
             for i in range(points)]
    curve = cap_sweep(deck, params, vcm, sweep, dev, vdd, xyce)
    area = params.get("n_parallel", 1) * (params["wn"] + params["wp"]) * params["l"]
    cmin = min(r["c_ff"] for r in curve)
    cmax = max(r["c_ff"] for r in curve)
    usable = [r["c_ff"] for r in curve if abs(r["vdiff_v"]) >= vdd / 4.0]
    typ = sum(usable) / len(usable)
    return {
        "geometry_um": {k: params[k] for k in ("wn", "wp", "l", "n_parallel")},
        "area_um2": area,
        "vcm_v": vcm,
        "freq_hz": FREQ_HZ,
        "c_ff_vs_vdiff": {f"{r['vdiff_v']:+.1f}": round(r["c_ff"], 4)
                          for r in curve},
        "curve": curve,
        "c_typ_ff": typ,
        "c_flatband_min_ff": cmin,
        "c_max_ff": cmax,
        "density_ff_um2": cmin / area,
        "density_typ_ff_um2": cmax / area,
        "method": ("1 MHz small-signal gate current, C = |Im(I(Vg))|/(2*pi*f), "
                   "both devices with S/D/bulk on FL and the gate on VSH"),
        "note": ("density_ff_um2 is the flat-band minimum (depleted channel, "
                 "conservative for area); density_typ_ff_um2 is the peak. "
                 "ac_pct_um is NOT measured: the vendor mismatch hooks are "
                 "HSpice agauss expressions and the flat deck zeroes them."),
    }


# --------------------------------------------------------------------------
# transmission gate
# --------------------------------------------------------------------------

def ron_netlist(deck, params, dev, vdd, points):
    lo, hi = 0.15 * vdd, 0.85 * vdd
    step = (hi - lo) / (points - 1)
    lines = ["* ics55 TG Ron vs Vin, 10 mV across the closed gate",
             f".include {deck}",
             f"Vdd vdd 0 dc {vdd:g}",
             "Vss vss 0 dc 0",
             f"Vs s 0 dc {vdd:g}",
             "Vsb sb 0 dc 0",
             f"V1 z1 0 dc {lo:g}",
             "E2 z2 0 VALUE={V(z1)-0.01}",
             mos("MN", "z2", "s", "z1", "vss", dev["nmos"],
                 params["wn"], params["l"]),
             mos("MP", "z2", "sb", "z1", "vdd", dev["pmos"],
                 params["wp"], params["l"]),
             f".dc V1 {lo:g} {hi:g} {step:g}",
             ".print dc v(z1) i(V1)",
             ".end"]
    return "\n".join(lines) + "\n"


def measure_ron(deck, params, dev, vdd, points=11, xyce=None):
    lo, hi = 0.15 * vdd, 0.85 * vdd
    step = (hi - lo) / (points - 1)
    with tempfile.TemporaryDirectory(prefix="ics55_ron_") as tmp:
        rc, log, prn, header, rows = run(
            ron_netlist(deck, params, dev, vdd, points), tmp, xyce)
    if rc != 0 or not rows:
        raise SystemExit("Xyce Ron probe failed: " + clean(log))
    ci, cv = col(header, "I(V1)", 2), col(header, "V(Z1)", 1)
    curve = []
    for k, row in enumerate(rows):
        vin = row[cv] if cv < len(row) else lo + k * step
        cur = abs(row[ci])
        if cur < 1e-12:
            continue
        curve.append({"vin_v": round(vin, 4), "ron_ohm": 0.01 / cur})
    if not curve:
        raise SystemExit("TG never conducted")
    return curve


def coff_netlist(deck, params, dev, vdd):
    lines = ["* ics55 TG off-state capacitance at the DAC-side node",
             f".include {deck}",
             "Vs s 0 dc 0",
             f"Vsb sb 0 dc {vdd:g}",
             "Vdd vdd 0 dc " + f"{vdd:g}",
             "Vss vss 0 dc 0",
             f"V1 z1 0 dc {vdd / 2:g}",
             f"V2 z2 0 dc {vdd / 2:g} ac 1",
             mos("MN", "z2", "s", "z1", "vss", dev["nmos"],
                 params["wn"], params["l"]),
             mos("MP", "z2", "sb", "z1", "vdd", dev["pmos"],
                 params["wp"], params["l"]),
             f".ac lin 1 {FREQ_HZ:g} {FREQ_HZ:g}",
             ".print ac i(V2)",
             ".end"]
    return "\n".join(lines) + "\n"


def measure_coff(deck, params, dev, vdd, xyce=None):
    with tempfile.TemporaryDirectory(prefix="ics55_coff_") as tmp:
        rc, log, prn, header, rows = run(
            coff_netlist(deck, params, dev, vdd), tmp, xyce)
    if rc != 0 or not rows:
        raise SystemExit("Xyce off-cap probe failed: " + clean(log))
    im = rows[0][col(header, "Im(I(V2))", 3)]
    return abs(im) / (2.0 * math.pi * FREQ_HZ) * 1e15


def measure_switch(profile, deck, geometry=None, points=11, widths=(1, 2, 4, 8),
                   xyce=None):
    dev = profile_devices(profile)
    size = (profile.get("analog") or {}).get("sizing_um") or {}
    base = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 1.0),
            "l": size.get("l", 0.06)}
    if geometry:
        base.update(geometry)
    vdd = profile["vdd"]
    curve = measure_ron(deck, base, dev, vdd, points, xyce)
    coff = measure_coff(deck, base, dev, vdd, xyce)
    rons = [c["ron_ohm"] for c in curve]
    study = []
    for mult in widths:
        p = {"wn": base["wn"] * mult, "wp": base["wp"] * mult,
             "l": base["l"]}
        c = measure_ron(deck, p, dev, vdd, points, xyce)
        study.append({"mult": mult, "wn": p["wn"], "wp": p["wp"],
                      "ron_max_ohm": max(x["ron_ohm"] for x in c),
                      "ron_typ_ohm": sum(x["ron_ohm"] for x in c) / len(c),
                      "c_off_ff": measure_coff(deck, p, dev, vdd, xyce)})
    return {
        "geometry_um": base,
        "vdd": vdd,
        "ron_ohm": max(rons),
        "ron_typ_ohm": sum(rons) / len(rons),
        "ron_min_ohm": min(rons),
        "c_off_ff": coff,
        "curve": curve,
        "width_study": study,
        "method": (f"10 mV across the closed TG at Vg=rail, {points} input "
                   "points; off-cap from the AC current into the DAC-side "
                   "node with the gate off"),
        "note": ("ron_ohm is the worst point over the input range, at "
                 "mid-rail where both devices have minimum overdrive. hvt at "
                 "1.2 V is weak there: widening to the 9 um bin edge is the "
                 "cheap lever, a lower-VT device or a bootstrap is the real "
                 "one. The width study records the Ron/off-cap trade."),
    }


# --------------------------------------------------------------------------
# comparator (StrongArm)
# --------------------------------------------------------------------------

def cmp_body(params, dev):
    wn, wp, l = params["wn"], params["wp"], params["l"]
    lt = params.get("l_tail", l)
    return "\n".join([
        mos("MTAIL", "tail", "clk", "vss", "vss", dev["nmos"], wn, lt),
        mos("MINP", "outn", "inp", "tail", "vss", dev["nmos"], 2 * wn, l),
        mos("MINN", "outp", "inn", "tail", "vss", dev["nmos"], 2 * wn, l),
        mos("MLA1", "outn", "outp", "vss", "vss", dev["nmos"], wn, l),
        mos("MLA2", "outp", "outn", "vss", "vss", dev["nmos"], wn, l),
        mos("MPB1", "outn", "outp", "vdd", "vdd", dev["pmos"], wp, l),
        mos("MPB2", "outp", "outn", "vdd", "vdd", dev["pmos"], wp, l),
        mos("MPRE1", "outp", "clk", "vdd", "vdd", dev["pmos"], wp, l),
        mos("MPRE2", "outn", "clk", "vdd", "vdd", dev["pmos"], wp, l),
    ])


def cmp_netlist(deck, params, dev, vdd, vdiff, tstop_ns=3.0,
                clk_ns=1.0):
    body = cmp_body(params, dev)
    return "\n".join([
        "* ics55 StrongArm comparator probe", f".include {deck}",
        f"Vdd vdd 0 dc {vdd:g}", "Vss vss 0 dc 0",
        f"Vip inp 0 dc {vdd / 2 + vdiff / 2:.6f}",
        f"Vin inn 0 dc {vdd / 2 - vdiff / 2:.6f}",
        f"Vclk clk 0 pulse(0 {vdd:g} {clk_ns:g}e-9 1e-10 1e-10 4e-8 8e-8)",
        body,
        f".tran 1e-12 {tstop_ns:g}e-9",
        ".print tran v(clk) v(outp) v(outn)",
        ".end",
    ]) + "\n"


def _crossing(rows, ci_t, ci_y, level, t0=0.0):
    """First time after t0 that y crosses level (linear interpolation)."""
    prev = None
    for row in rows:
        t, y = row[ci_t], row[ci_y]
        if t < t0:
            prev = (t, y)
            continue
        if prev and (prev[1] - level) * (y - level) <= 0 and prev[1] != y:
            f = (level - prev[1]) / (y - prev[1])
            return prev[0] + f * (t - prev[0])
        prev = (t, y)
    return None


def _diff_crossing(rows, ci_t, ci_a, ci_b, level, t0=0.0):
    """First time after t0 that |a-b| reaches level."""
    prev = None
    for row in rows:
        t, d = row[ci_t], abs(row[ci_a] - row[ci_b])
        if t < t0:
            prev = (t, d)
            continue
        if prev and prev[1] < level <= d and prev[1] != d:
            f = (level - prev[1]) / (d - prev[1])
            return prev[0] + f * (t - prev[0])
        prev = (t, d)
    return None


def comparator_offset(profile, deck, params=None, vdd=None, span_mv=40.0,
                      iterations=9, xyce=None):
    dev = profile_devices(profile)
    size = (profile.get("analog") or {}).get("sizing_um") or {}
    p = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
         "l": size.get("l", 0.06)}
    if params:
        p.update(params)
    vdd = profile["vdd"] if vdd is None else vdd

    def decide(vdiff):
        with tempfile.TemporaryDirectory(prefix="ics55_cmp_") as tmp:
            rc, log, prn, header, rows = run(
                cmp_netlist(deck, p, dev, vdd, vdiff), tmp, xyce)
        if rc != 0 or not rows:
            raise SystemExit("comparator probe failed: " + clean(log))
        cp, cn = col(header, "V(OUTP)", None), col(header, "V(OUTN)", None)
        if cp is None or cn is None:
            raise SystemExit("comparator probe printed no outputs")
        return rows[-1][cp] - rows[-1][cn], rows, header

    lo, hi = -span_mv / 1000.0, span_mv / 1000.0
    lo_d = decide(lo)[0]
    hi_d = decide(hi)[0]
    if not (lo_d < 0 < hi_d):
        raise SystemExit(f"decision does not bracket zero (lo={lo_d:.3f}, "
                         f"hi={hi_d:.3f}); offset may exceed +-{span_mv:g} mV")
    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        if decide(mid)[0] < 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0 * 1e6, p, vdd


def comparator_delay(profile, deck, params=None, vdd=None, vdiff=0.01,
                     xyce=None):
    dev = profile_devices(profile)
    size = (profile.get("analog") or {}).get("sizing_um") or {}
    p = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
         "l": size.get("l", 0.06)}
    if params:
        p.update(params)
    vdd = profile["vdd"] if vdd is None else vdd
    with tempfile.TemporaryDirectory(prefix="ics55_dly_") as tmp:
        rc, log, prn, header, rows = run(
            cmp_netlist(deck, p, dev, vdd, vdiff), tmp, xyce)
    if rc != 0 or not rows:
        raise SystemExit("comparator delay probe failed: " + clean(log))
    ct = col(header, "TIME", 1)
    cc = col(header, "V(CLK)", 2)
    cp = col(header, "V(OUTP)", 3)
    cn = col(header, "V(OUTN)", 4)
    level = vdd / 2.0
    t_clk = _crossing(rows, ct, cc, level)
    t_out = _crossing(rows, ct, cn, level, t0=(t_clk or 0.0))
    if t_out is None:
        t_out = _crossing(rows, ct, cp, level, t0=(t_clk or 0.0))
    t_diff = _diff_crossing(rows, ct, cp, cn, level, t0=(t_clk or 0.0))
    if t_clk is None or t_diff is None:
        raise SystemExit("could not find a 50 % crossing")
    return {"delay_ns": (t_diff - t_clk) * 1e9,
            "delay_out50_ns": (t_out - t_clk) * 1e9 if t_out else None,
            "t_clk_ns": t_clk * 1e9,
            "t_diff50_ns": t_diff * 1e9,
            "vdiff_v": vdiff}


def measure_comparator(profile, deck, geometry=None, span_mv=40.0,
                       iterations=9, xyce=None):
    dev = profile_devices(profile)
    size = (profile.get("analog") or {}).get("sizing_um") or {}
    p = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
         "l": size.get("l", 0.06)}
    if geometry:
        p.update(geometry)
    offset_uv, p, vdd = comparator_offset(profile, deck, p, profile["vdd"],
                                          span_mv, iterations, xyce)
    delay = comparator_delay(profile, deck, p, vdd, xyce=xyce)
    return {
        "topology": "cmp_strongarm",
        "geometry_um": p,
        "vdd": vdd,
        "offset_uv": offset_uv,
        "delay_ns": delay["delay_ns"],
        "delay_probe": delay,
        "method": (f"systematic offset by bisection over +-{span_mv:g} mV "
                   f"({iterations} iterations); delay = clk 50 % to the "
                   "|outp-outn| = VDD/2 split at a 10 mV differential "
                   "(delay_probe.delay_out50_ns is the single-ended diagnostic)"),
        "note": ("offset is systematic only -- every device is nominal, so "
                 "this is the topology/sizing offset, not a distribution. "
                 "sigma_uv still needs the vendor agauss mismatch hooks; "
                 "Xyce 7.10 .noise returns zeros, so no noise number."),
    }


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def human(result):
    lines = [f"ics55 analog characterization (Xyce): {result['deck']}"]
    cap = result.get("moscap")
    if cap:
        lines.append(f"  moscap  Wn={cap['geometry_um']['wn']:g} "
                     f"Wp={cap['geometry_um']['wp']:g} "
                     f"L={cap['geometry_um']['l']:g} "
                     f"(area {cap['area_um2']:.3f} um^2)")
        lines.append(f"    flat-band {cap['c_flatband_min_ff']:.3f} fF, "
                     f"peak {cap['c_max_ff']:.3f} fF, "
                     f"density {cap['density_ff_um2']:.2f} .. "
                     f"{cap['density_typ_ff_um2']:.2f} fF/um^2")
        for r in cap["curve"]:
            lines.append(f"    Vdiff {r['vdiff_v']:+5.2f}  {r['c_ff']:7.3f} fF")
    sw = result.get("switch")
    if sw:
        lines.append(f"  switch  Wn={sw['geometry_um']['wn']:g} "
                     f"Wp={sw['geometry_um']['wp']:g} "
                     f"L={sw['geometry_um']['l']:g}")
        lines.append(f"    Ron typ {sw['ron_typ_ohm']:.0f} ohm, "
                     f"max {sw['ron_ohm']:.0f} ohm, "
                     f"Coff {sw['c_off_ff']:.3f} fF")
        for r in sw["curve"]:
            lines.append(f"    Vin {r['vin_v']:.3f}  Ron {r['ron_ohm']:9.1f} ohm")
        for w in sw["width_study"]:
            lines.append(f"    x{w['mult']:<2d} ({w['wn']:g}/{w['wp']:g} um)  "
                         f"Ron max {w['ron_max_ohm']:8.0f} ohm  "
                         f"Coff {w['c_off_ff']:.3f} fF")
    cm = result.get("comparator")
    if cm:
        lines.append(f"  comparator {cm['geometry_um']}")
        lines.append(f"    systematic offset {cm['offset_uv']:+.2f} uV, "
                     f"delay {cm['delay_ns']:.3f} ns")
    return "\n".join(lines)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdk", default="ics55_h7ch")
    ap.add_argument("--deck", required=True)
    ap.add_argument("--xyce")
    ap.add_argument("--wn", type=float)
    ap.add_argument("--wp", type=float)
    ap.add_argument("--l", type=float)
    ap.add_argument("--cmp-wn", type=float)
    ap.add_argument("--cmp-wp", type=float)
    ap.add_argument("--cmp-l", type=float)
    ap.add_argument("--cap-points", type=int, default=13)
    ap.add_argument("--ron-points", type=int, default=11)
    ap.add_argument("--span-mv", type=float, default=40.0)
    ap.add_argument("--iterations", type=int, default=9)
    ap.add_argument("--skip", nargs="*", default=[],
                    choices=["moscap", "switch", "comparator"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    sys.path.insert(0, HERE)
    import macro_gen  # noqa: E402
    profile = macro_gen.load_profile(args.pdk)
    deck = os.path.abspath(os.path.expanduser(args.deck))
    geometry = {k: v for k, v in
                (("wn", args.wn), ("wp", args.wp), ("l", args.l))
                if v is not None}
    cmp_geometry = {k: v for k, v in
                    (("wn", args.cmp_wn), ("wp", args.cmp_wp),
                     ("l", args.cmp_l)) if v is not None}

    result = {"tech": profile["id"], "deck": deck,
              "simulator": "Xyce 7.10.0", "geometry_um": geometry}
    if "moscap" not in args.skip:
        result["moscap"] = measure_moscap(
            profile, deck, geometry, args.cap_points, xyce=args.xyce)
    if "switch" not in args.skip:
        result["switch"] = measure_switch(
            profile, deck, geometry, args.ron_points, xyce=args.xyce)
    if "comparator" not in args.skip:
        result["comparator"] = measure_comparator(
            profile, deck, cmp_geometry, args.span_mv, args.iterations,
            xyce=args.xyce)

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

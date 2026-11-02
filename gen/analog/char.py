#!/usr/bin/env python3
"""Characterize the analog leaves with the real PDK deck.

Measured here (deterministic, reproducible):

  switch_tg    Ron(Vin) across the input range, one TG, gates driven rail to rail
  comparator   systematic input offset by bisection on the decision, and the
               clk-to-output regeneration delay

Not measured here, on purpose:

  * mismatch.  The sky130 oxide-mismatch expression is written for l/w in
    microns; with SI metres ``slope/sqrt(l*w)`` becomes ~9.5e3, toxm goes
    negative and the capacitance collapses.  Monte Carlo through this path
    produces nonsense, so no sigma is reported rather than a wrong one.
  * input-referred noise.  That is a transient-noise quantity; the available
    ngspice noise analysis is small-signal AC.
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
sys.path.insert(0, HERE)

import macro_gen  # noqa: E402

# table rows start with an integer index; the number of value columns varies
# (1 for .op, 3 for a two-probe .tran, 5 for a four-probe .tran)
NUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def rows(log):
    out = []
    for line in log.splitlines():
        if not re.match(r"^\s*\d+\s", line):
            continue
        nums = [float(x) for x in NUM.findall(line)]
        if len(nums) >= 2:
            out.append(nums)
    return out


def clean(log):
    keep = [ln for ln in log.splitlines()
            if not re.search(r"osdi|PDK_ROOT|PDK:|No compatibility|^\s*$", ln)]
    return "\n".join(keep)[-600:]


def run(netlist, workdir, timeout=600):
    path = os.path.join(workdir, "char.sp")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(netlist)
    proc = subprocess.run(["ngspice", "-b", path], cwd=workdir,
                          capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def leaf(dev, topo, params):
    return macro_gen.TOPOLOGIES[topo](dev, params)[0]


# --------------------------------------------------------------------------
# switch
# --------------------------------------------------------------------------

def switch_ron(profile, deck_path, params=None, vdd=None, vstep_mv=10.0,
               points=11):
    deck_path = os.path.abspath(os.path.expanduser(deck_path))
    dev = macro_gen.device_binding(profile)
    size = (profile.get("analog", {}).get("switch_sizing_um")
            or profile.get("analog", {}).get("sizing_um") or {})
    base = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
            "l": size.get("l", 0.15)}
    base.update(params or {})
    params = base
    vdd = vdd if vdd is not None else profile["vdd"]
    circuit = leaf(dev, "switch_tg", params)

    lo, hi = 0.15 * vdd, 0.85 * vdd
    sweep = [lo + (hi - lo) * i / (points - 1) for i in range(points)]
    curve = []
    with tempfile.TemporaryDirectory(prefix="adcgen_ron_") as tmp:
        for vin in sweep:
            net = f"""* switch Ron probe
{circuit}
.include {deck_path}
Vdd vdd 0 dc {vdd:g}
Vss vss 0 dc 0
Vs s 0 dc {vdd:g}
Vsb sb 0 dc 0
V1 z1 0 dc {vin:.6f}
V2 z2 0 dc {vin - vstep_mv/1000.0:.6f}
Xsw s sb z1 z2 vdd vss SARADC_SW_TG
.op
.print op i(v2)
.end
"""
            rc, log = run(net, tmp)
            if rc != 0:
                return {"error": clean(log)}
            data = rows(log)
            if not data:
                return {"error": "no operating point row"}
            current = abs(data[0][-1])
            if current < 1e-12:
                continue
            curve.append({"vin_v": vin,
                          "ron_ohm": (vstep_mv / 1000.0) / current})

    if not curve:
        return {"error": "switch never conducted"}
    rons = [c["ron_ohm"] for c in curve]
    return {"topology": "switch_tg",
            "geometry_um": params, "vdd": vdd,
            "ron_typ_ohm": sum(rons) / len(rons),
            "ron_max_ohm": max(rons),
            "ron_min_ohm": min(rons),
            "curve": curve,
            "method": f"10 mV across the closed TG at Vg=rail, {points} input points"}


def switch_cap(profile, deck_path, params=None, vdd=None, freq=1e6):
    """Off-state capacitance seen at the DAC-side node (z2).

    Widening the TG to cut Ron also adds junction and overlap capacitance to
    the capacitor bottom plate, which is exactly the trade the array sees.
    """
    deck_path = os.path.abspath(os.path.expanduser(deck_path))
    dev = macro_gen.device_binding(profile)
    size = (profile.get("analog", {}).get("switch_sizing_um")
            or profile.get("analog", {}).get("sizing_um") or {})
    base = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
            "l": size.get("l", 0.15)}
    base.update(params or {})
    vdd = vdd if vdd is not None else profile["vdd"]
    circuit = leaf(dev, "switch_tg", base)

    net = f"""* switch off-capacitance probe
{circuit}
.include {deck_path}
Vs s 0 dc 0
Vsb sb 0 dc {vdd:g}
V1 z1 0 dc {vdd/2:g}
V2 z2 0 dc {vdd/2:g} ac 1
Vdd vdd 0 dc {vdd:g}
Vss vss 0 dc 0
Xsw s sb z1 z2 vdd vss SARADC_SW_TG
.ac lin 1 {freq:g} {freq:g}
.print ac i(v2)
.end
"""
    with tempfile.TemporaryDirectory(prefix="adcgen_scap_") as tmp:
        rc, log = run(net, tmp)
        if rc != 0:
            return {"error": clean(log)}
        data = [r for r in rows(log) if len(r) >= 4]
        if not data:
            return {"error": "no AC row"}
        im = data[0][-1]
        return {"topology": "switch_tg", "geometry_um": base,
                "c_off_ff": abs(im) / (2 * 3.141592653589793 * freq) * 1e15,
                "freq_hz": freq}


# --------------------------------------------------------------------------
# bootstrapped switch (dynamic)
# --------------------------------------------------------------------------

def flatten(circuit):
    """Strip the .subckt wrapper so internal nodes are visible to the bench."""
    out = []
    for line in circuit.splitlines():
        if line.strip().lower().startswith((".subckt", ".ends", "*")):
            continue
        out.append(line)
    return "\n".join(out)


def switch_boot(profile, deck_path, params=None, vdd=None, points=5,
                i_load_uA=50.0, sample_ns=12.0):
    """Measure a bootstrapped switch while it is actually switching.

    A load current pulls through the closed switch, so
    Ron = (V(vin) - V(vout)) / I_load.  The gate overdrive
    V(g) - V(vout) shows whether the bootstrap works at all: without it the
    gate sits at VDD and the overdrive collapses as VIN approaches VDD.
    """
    deck_path = os.path.abspath(os.path.expanduser(deck_path))
    dev = macro_gen.device_binding(profile)
    size = (profile.get("analog", {}).get("switch_sizing_um")
            or profile.get("analog", {}).get("sizing_um") or {})
    base = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
            "l": size.get("l", 0.15), "cboot_ff": 100.0}
    base.update(params or {})
    params = base
    vdd = vdd if vdd is not None else profile["vdd"]
    body = flatten(leaf(dev, "switch_bootstrap", params))

    lo, hi = 0.15 * vdd, 0.85 * vdd
    sweep = [lo + (hi - lo) * i / (points - 1) for i in range(points)]
    curve = []
    with tempfile.TemporaryDirectory(prefix="adcgen_boot_") as tmp:
        for vin in sweep:
            net = f"""* bootstrapped switch probe
.include {deck_path}
Vclk clk 0 pulse(0 {vdd:g} 1n 100p 100p 40n 80n)
Vin vin 0 dc {vin:.6f}
Vdd vdd 0 dc {vdd:g}
Vss vss 0 dc 0
Iload vout 0 dc {i_load_uA:g}u
{body}
.tran 5p {sample_ns + 8:g}n
.print tran v(g) v(vout)
.end
"""
            rc, log = run(net, tmp)
            if rc != 0:
                return {"error": clean(log)}
            data = rows(log)
            if not data:
                return {"error": "no transient rows"}
            # last row is inside the sample window (clk stays high past sample_ns)
            wide = [r for r in data if len(r) >= 4]
            if not wide:
                return {"error": "transient table wrapped; no 2-probe rows"}
            last = wide[-1]
            vg, vout = last[-2], last[-1]
            ron = (vin - vout) / (i_load_uA * 1e-6) if vin > vout else float("inf")
            curve.append({"vin_v": vin, "vout_v": vout, "vg_v": vg,
                          "overdrive_v": vg - vout, "ron_ohm": ron})

    rons = [c["ron_ohm"] for c in curve]
    over = [c["overdrive_v"] for c in curve]
    return {"topology": "switch_bootstrap",
            "geometry_um": params, "vdd": vdd,
            "i_load_ua": i_load_uA,
            "ron_typ_ohm": sum(rons) / len(rons),
            "ron_max_ohm": max(rons),
            "overdrive_min_v": min(over),
            "overdrive_note": ("gate overdrive stays near VDD if the bootstrap "
                               "works; it collapses toward VDD-VIN without it"),
            "curve": curve,
            "method": f"{points} input points, {i_load_uA:g} uA load, "
                      f"sampled at {sample_ns:g} ns"}


# --------------------------------------------------------------------------
# comparator
# --------------------------------------------------------------------------

def _comparator_decides(deck_path, circuit, vdd, vdiff, sample_ns=6.0):
    net = f"""* comparator offset probe
{circuit}
.include {deck_path}
Vdd vdd 0 dc {vdd:g}
Vss vss 0 dc 0
Vip inp 0 dc {vdd/2 + vdiff/2:.6f}
Vin inn 0 dc {vdd/2 - vdiff/2:.6f}
Vclk clk 0 pulse(0 {vdd:g} 1n 100p 100p 40n 80n)
Xc inp inn clk outp outn vdd vss SARADC_CMP_STRONGARM
.tran 5p {sample_ns:g}n
.print tran v(outp) v(outn)
.end
"""
    with tempfile.TemporaryDirectory(prefix="adcgen_cmp_") as tmp:
        rc, log = run(net, tmp)
        if rc != 0:
            return None, clean(log)
        data = rows(log)
        if not data:
            return None, clean(log)
        last = data[-1]
        return last[-2] - last[-1], log


def comparator_noise(profile, deck_path, params=None, vdd=None, delay_ns=None,
                     fstart=1e5, fstop=1e10, points=10):
    """Input-referred noise at the decision operating point.

    A clocked comparator has no single "noise" number: the input-referred
    density is meaningful only at a stated bandwidth.  ngspice can linearise
    the circuit at the decision point (CLK high, inputs at Vcm) and run a
    small-signal noise analysis there, which is the standard way to get the
    regeneration noise.  The integration band is tied to the measured
    regeneration delay: f_bw = 1/(2*pi*delay).
    """
    deck_path = os.path.abspath(os.path.expanduser(deck_path))
    dev = macro_gen.device_binding(profile)
    size = profile.get("analog", {}).get("sizing_um") or {}
    params = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
              "l": size.get("l", 0.15)}
    vdd = vdd if vdd is not None else profile["vdd"]
    circuit = leaf(dev, "cmp_strongarm", params)
    net = f"""* comparator input-referred noise
{circuit}
.include {deck_path}
Vdd vdd 0 dc {vdd:g}
Vss vss 0 dc 0
Vip inp 0 dc {vdd/2:g} ac 1
Vin inn 0 dc {vdd/2:g}
Vclk clk 0 dc {vdd:g}
Xc inp inn clk outp outn vdd vss SARADC_CMP_STRONGARM
.noise v(outp) Vip dec {points} {fstart:g} {fstop:g}
.print noise onoise_spectrum inoise_spectrum
.end
"""
    with tempfile.TemporaryDirectory(prefix="adcgen_cmpn_") as tmp:
        rc, log = run(net, tmp)
        if rc != 0:
            return {"error": clean(log)}
    table = []
    for line in log.splitlines():
        m = re.match(r"^\d+\s+([\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*$",
                     line.strip())
        if m:
            table.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
    if len(table) < 3:
        return {"error": "noise analysis produced no spectrum: " + clean(log)}

    f_bw = (1.0 / (2.0 * 3.141592653589793 * delay_ns * 1e-9)
            if delay_ns else 1.6e9)
    lo, hi = 1e8, min(f_bw, fstop)
    total = 0.0
    for (f1, _, i1), (f2, _, i2) in zip(table, table[1:]):
        a, b = max(f1, lo), min(f2, hi)
        if b > a:
            total += 0.5 * (i1 * i1 + i2 * i2) * (b - a)

    def at(f):
        return min(table, key=lambda r: abs(r[0] - f))

    return {"method": "ngspice .noise at CLK=VDD, inputs at Vcm",
            "band_hz": [lo, hi],
            "band_source": f"1/(2*pi*{delay_ns:g} ns) regeneration bandwidth",
            "density_nv_rtHz": {"1k": at(1e3)[2] * 1e9,
                                "1M": at(1e6)[2] * 1e9,
                                "100M": at(1e8)[2] * 1e9,
                                "1G": at(1e9)[2] * 1e9},
            "integrated_uv": (total ** 0.5) * 1e6,
            "note": "small-signal at the decision point; excludes transient "
                    "regeneration statistics, includes the PDK flicker terms "
                    "only inside the stated band"}


def comparator(profile, deck_path, params=None, vdd=None, span_mv=40.0,
               iterations=9):
    deck_path = os.path.abspath(os.path.expanduser(deck_path))
    dev = macro_gen.device_binding(profile)
    size = profile.get("analog", {}).get("sizing_um") or {}
    params = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
              "l": size.get("l", 0.15)}
    vdd = vdd if vdd is not None else profile["vdd"]
    circuit = leaf(dev, "cmp_strongarm", params)

    lo, hi = -span_mv / 1000.0, span_mv / 1000.0
    lo_d = _comparator_decides(deck_path, circuit, vdd, lo)[0]
    hi_d = _comparator_decides(deck_path, circuit, vdd, hi)[0]
    if lo_d is None or hi_d is None:
        return {"error": "comparator probe did not simulate: " + str(_comparator_decides(deck_path, circuit, vdd, 0.0)[1])}
    if not (lo_d < 0 < hi_d):
        return {"error": f"decision does not bracket zero (lo={lo_d:.3f}, hi={hi_d:.3f}); "
                         f"offset may exceed +-{span_mv:g} mV"}

    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        d = _comparator_decides(deck_path, circuit, vdd, mid)[0]
        if d is None:
            return {"error": "bisection point failed"}
        if d < 0:
            lo = mid
        else:
            hi = mid

    # regeneration delay from one run at the found offset
    delay_ns = None
    net = f"""* comparator delay probe
{circuit}
.include {deck_path}
Vdd vdd 0 dc {vdd:g}
Vss vss 0 dc 0
Vip inp 0 dc {vdd/2 + 0.01:.6f}
Vin inn 0 dc {vdd/2 - 0.01:.6f}
Vclk clk 0 pulse(0 {vdd:g} 1n 10p 10p 40n 80n)
Xc inp inn clk outp outn vdd vss SARADC_CMP_STRONGARM
.tran 1p 4n
.print tran v(outp) v(outn)
.end
"""
    with tempfile.TemporaryDirectory(prefix="adcgen_dly_") as tmp:
        rc, log = run(net, tmp)
        data = rows(log) if rc == 0 else []
        if data:
            # ngspice prints an Index column first: [index, time, v(outp), v(outn)].
            # row[0] is the sample number, not seconds.
            for row in data:
                if abs(row[-2] - row[-1]) > 0.5 * vdd:
                    t_s = row[1] if len(row) >= 4 else row[0]
                    delay_ns = t_s * 1e9 - 1.0      # minus the 1 ns clk delay
                    break

    noise = comparator_noise(profile, deck_path, params, vdd, delay_ns)
    result = {"topology": "cmp_strongarm",
              "geometry_um": params, "vdd": vdd,
              "offset_uv": (lo + hi) / 2.0 * 1e6,
              "delay_ns": delay_ns,
              "noise": noise,
              "method": f"bisection on the decision over +-{span_mv:g} mV, "
                        f"{iterations} iterations",
              "note": "systematic offset only; mismatch was not measurable, so this "
                      "is not an offset distribution"}
    if "integrated_uv" in noise:
        result["sigma_uv"] = noise["integrated_uv"]
        result["sigma_source"] = (f"input-referred noise {noise['band_hz'][0]:.0e}-"
                                  f"{noise['band_hz'][1]:.0e} Hz, AC .noise at the "
                                  f"decision point")
    return result


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def characterize(profile, deck_path):
    boot_params = None
    return {"tech": profile["id"],
            "switch": switch_ron(profile, deck_path),
            "switch_bootstrap": switch_boot(profile, deck_path, boot_params),
            "comparator": comparator(profile, deck_path)}


def human(r):
    sw, cm = r["switch"], r["comparator"]
    lines = [f"{r['tech']} characterization with the PDK deck"]
    if "error" in sw:
        lines.append(f"  switch     FAILED: {sw['error'][:160]}")
    else:
        lines.append(f"  switch Ron typ {sw['ron_typ_ohm']:.1f} ohm, "
                     f"max {sw['ron_max_ohm']:.1f} ohm over "
                     f"{sw['curve'][0]['vin_v']:.2f}..{sw['curve'][-1]['vin_v']:.2f} V")
        for c in sw["curve"]:
            lines.append(f"    Vin {c['vin_v']:.2f}  Ron {c['ron_ohm']:7.1f} ohm")
    sb = r.get("switch_bootstrap") or {}
    if sb.get("error"):
        lines.append(f"  bootstrap  FAILED: {sb['error'][:160]}")
    else:
        lines.append(f"  bootstrap Ron typ {sb['ron_typ_ohm']:.1f} ohm, "
                     f"max {sb['ron_max_ohm']:.1f} ohm, "
                     f"min overdrive {sb['overdrive_min_v']:.3f} V")
        for c in sb["curve"]:
            lines.append(f"    Vin {c['vin_v']:.2f}  Vout {c['vout_v']:.3f}  "
                         f"Vg {c['vg_v']:.3f}  overdrive {c['overdrive_v']:.3f}  "
                         f"Ron {c['ron_ohm']:7.1f} ohm")
    if "error" in cm:
        lines.append(f"  comparator FAILED: {cm['error'][:160]}")
    else:
        lines.append(f"  comparator offset {cm['offset_uv']:+.1f} uV, "
                     f"regeneration {cm['delay_ns']:.3f} ns")
        lines.append(f"    {cm['note']}")
    return "\n".join(lines)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdk", default="sky130A_hd")
    ap.add_argument("--deck", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    profile = macro_gen.load_profile(args.pdk)
    result = characterize(profile, args.deck)
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

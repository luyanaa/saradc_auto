#!/usr/bin/env python3
"""What the switch parasitic actually does to the CDAC weights.

Two measured numbers feed this:

  * the unit capacitor C(V) curve (measured on the real deck),
  * the transmission-gate off-capacitance at the DAC node.  The schematic
    bench gives 4.63 fF for the 4x gate; the layout PEX of the same gate
    (layout_check.py --device tg) gives 7.708 fF, of which 3.075 fF is the
    cell's own routing.  The default here is the PEX number, because that is
    what a bit node actually sees.

The analytic table is still a bound, not a simulation of the array: the
schematic AC check does not reproduce it (see README).

The point is the asymmetry nobody budgets for: every bit node carries the same
off-capacitance, but bit i only carries 2^i unit capacitors.  A fixed parasitic
is therefore negligible on the MSB and dominant on the LSB.

Analytic model, per bit i with C_i = 2^i * Cu and off-cap Cp:

    the node divider attenuates the switching by  C_i / (C_i + Cp)
    so the charge delivered to the top plate is  C_i^2 / (C_i + Cp) * dV
    instead of                                   C_i * dV

`--simulate` checks the extremes (bit 0 and bit n-1) with a transient charge
integration on the real deck; the analytic table covers every bit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import macro_gen  # noqa: E402

NUM = __import__("re").compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _rows(log):
    out = []
    for line in log.splitlines():
        if not line.strip():
            continue
        if "=" in line and line.split("=")[0].strip().replace("_", "").isalnum():
            key, _, val = line.partition("=")
            toks = NUM.findall(val)
            if toks:
                out.append((key.strip(), float(toks[0])))
    return out


def measure(profile, deck_path, nbits=8, cu_ff=None, bits=None):
    deck_path = os.path.abspath(os.path.expanduser(deck_path))
    analog = profile.get("analog") or {}
    size = analog.get("sizing_um") or {}
    params = {"wn": size.get("wn", 1.0), "wp": size.get("wp", 2.0),
              "l": size.get("l", 0.15)}
    dev = macro_gen.device_binding(profile)
    cap_leaf = macro_gen.TOPOLOGIES["cap_unit"](dev, params)[0]
    sw_leaf = macro_gen.TOPOLOGIES["switch_tg"](dev, params)[0]

    cu = cu_ff if cu_ff is not None else 2.673
    bits = bits if bits is not None else [0, nbits - 1]
    results = []
    with tempfile.TemporaryDirectory(prefix="adcgen_w_") as tmp:
        for bit in bits:
            count = 1 << bit
            caps = "\n".join(
                f"XC{k} vtop ni VNW VPW SARADC_CAP_UNIT" for k in range(count))
            # AC transfer: drive refh with 1 V, hold the top plate stiff, and
            # read the current into it. C_eff = Im(I) / (2*pi*f).
            net = f"""* bit {bit} weight probe (AC)
{cap_leaf}
{sw_leaf}
.include {deck_path}
Vrefh refh 0 dc 1.0 ac 1
Vrefl refl 0 dc 0.4
Vtop vtop 0 dc 0.6
Vnw vnw 0 dc 1.8
Vpw vpw 0 dc 0
Vdd vdd 0 dc 1.8
Vss vss 0 dc 0
{caps}
XSWON s_hi s_lo refh ni vdd vss SARADC_SW_TG
XSWOFF s_lo s_hi refl ni vdd vss SARADC_SW_TG
Vs_hi s_hi 0 dc 1.8
Vs_lo s_lo 0 dc 0
.ac lin 1 1e6 1e6
.print ac i(vtop)
.end
"""
            path = os.path.join(tmp, f"w{bit}.sp")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(net)
            proc = subprocess.run(["ngspice", "-b", path], cwd=tmp,
                                  capture_output=True, text=True, timeout=900)
            log = proc.stdout + proc.stderr
            import math
            ac = None
            for line in log.splitlines():
                m = __import__("re").match(r"^\s*0\s+([-\d.eE+]+)\s+([-\d.eE+]+),\s*([-\d.eE+]+)\s*$", line)
                if m:
                    ac = (float(m.group(2)), float(m.group(3)))
                    break
            c_eff_ff = (abs(ac[1]) / (2 * math.pi * 1e6) * 1e15) if ac else None
            q = ac
            c_i_ff = count * cu
            results.append({"bit": bit, "caps": count,
                            "c_i_ff": c_i_ff, "c_eff_ff": c_eff_ff,
                            "ratio": (c_eff_ff / c_i_ff) if c_eff_ff else None,
                            "q_c": q})
    return results


def analytic(nbits, cu_ff, cp_ff):
    rows = []
    for bit in range(nbits):
        c_i = (1 << bit) * cu_ff
        att = c_i / (c_i + cp_ff)
        rows.append({"bit": bit, "c_i_ff": c_i,
                     "attenuation": att,
                     "weight_error_pct": (att - 1.0) * 100.0})
    return rows


def report(profile, nbits=8, cu_ff=2.673, cp_ff=7.708):
    rows = analytic(nbits, cu_ff, cp_ff)
    worst = min(rows, key=lambda r: r["attenuation"])
    # DNL at the worst bit if every lower bit were ideal: the weight error of
    # bit k appears as a step of |error| LSB at code 2^k
    dnl = max(abs(r["weight_error_pct"]) / 100.0 * (1 << r["bit"]) / (1 << nbits) * (1 << nbits)
              for r in rows)
    return {"pdk": profile["id"], "nbits": nbits, "cu_ff": cu_ff,
            "cp_ff": cp_ff, "rows": rows, "worst_bit": worst["bit"],
            "worst_attenuation": worst["attenuation"], "max_dnl_lsb": dnl}


def human(r, sim=None):
    lines = [f"{r['pdk']} CDAC with Cu={r['cu_ff']:.3f} fF and "
             f"Cp={r['cp_ff']:.2f} fF per bit node",
             "",
             "  bit   C_i (fF)   attenuation   weight error",
             "  ---   --------   -----------   ------------"]
    for row in r["rows"]:
        lines.append(f"  {row['bit']:3d}   {row['c_i_ff']:8.2f}   "
                     f"{row['attenuation']:11.3f}   {row['weight_error_pct']:+11.1f} %")
    lines += ["",
              f"  worst: bit {r['worst_bit']} keeps only "
              f"{r['worst_attenuation']*100:.1f}% of its intended weight",
              f"  implied DNL up to {r['max_dnl_lsb']:.2f} LSB",
              "",
              "  This is an UPPER BOUND under the stated Cp, not a measurement.",
              "  Cp comes from the TG layout PEX, but the bound still assumes it",
              "  sits at every bit node at every bias; the schematic AC check does",
              "  not reproduce that, so it must not size the array."]
    if sim:
        lines += ["", "  simulated check (transient charge integration):"]
        for s in sim:
            if s["c_eff_ff"] is None:
                lines.append(f"    bit {s['bit']}: simulation produced no charge")
                continue
            lines.append(f"    bit {s['bit']}: {s['caps']:3d} caps, "
                         f"C_eff {s['c_eff_ff']:7.3f} fF vs ideal {s['c_i_ff']:7.3f} fF "
                         f"-> ratio {s['ratio']:.3f}   (bench status: unconfirmed)")
    return "\n".join(lines)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdk", default="sky130A_hd")
    ap.add_argument("--deck")
    ap.add_argument("--nbits", type=int, default=8)
    ap.add_argument("--cu-ff", type=float, default=2.673)
    ap.add_argument("--cp-ff", type=float, default=7.708)
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    profile = macro_gen.load_profile(args.pdk)
    rep = report(profile, args.nbits, args.cu_ff, args.cp_ff)
    sim = measure(profile, args.deck, args.nbits, args.cu_ff) if args.simulate else None
    out = {"report": rep, "simulated": sim}
    text = json.dumps(out, indent=2) if args.json else human(rep, sim)
    if args.out:
        with open(os.path.expanduser(args.out), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

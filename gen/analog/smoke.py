#!/usr/bin/env python3
"""Connectivity smoke for generated analog leaves.

This is deliberately NOT a PDK-model simulation.  It uses explicit level-1
models so that port order, polarity and device wiring can be checked in a few
seconds on any machine.  Numbers from it say nothing about the real process.

The PDK-model path is separate and is the only one allowed to report a
capacitance, an Ron or a comparator offset.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.dirname(HERE)

MODELS = """* level-1 connectivity models (NOT the PDK deck)
.model nch nmos level=1 vto=0.45 kp=120u gamma=0.4 phi=0.7 lambda=0.02 tox=4e-9
.model pch pmos level=1 vto=-0.45 kp=40u gamma=0.4 phi=0.7 lambda=0.02 tox=4e-9
"""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def bind_devices(circuit, nmos, pmos):
    """Rewrite generated X-instances into level-1 M-devices.

    The generated netlist calls PDK subcircuits (``X... <model> w= l= nf=``).
    The smoke deck has no PDK subcircuits, so each call becomes a primitive
    ``M`` device on a level-1 model.  Finger counts are dropped: level-1 has
    no equivalent, and this deck is not used for any numeric claim.
    """
    models = {nmos: "nch", pmos: "pch"}
    out = []
    for line in circuit.splitlines():
        tok = line.split()
        if tok and tok[0].startswith("X") and len(tok) >= 6 and tok[5] in models:
            name, d, g, s, b, model = tok[:6]
            keep = [t for t in tok[6:] if t.startswith(("w=", "l=", "m="))]
            out.append(" ".join([f"M{name[1:]}", d, g, s, b, models[model]] + keep))
        else:
            out.append(line)
    return "\n".join(out)


def parse_table(text):
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and all(_is_num(p) for p in parts):
            rows.append([float(p) for p in parts])
    return rows


def _is_num(token):
    try:
        float(token)
        return True
    except ValueError:
        return False


def run_ngspice(netlist, workdir):
    path = os.path.join(workdir, "smoke.sp")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(netlist)
    proc = subprocess.run(["ngspice", "-b", path], cwd=workdir,
                          capture_output=True, text=True, timeout=180)
    return proc.returncode, proc.stdout + proc.stderr


def smoke_cap(manifests, workdir, vdd, nmos, pmos):
    circuit = read(os.path.join(manifests["cap_unit"], "circuit.spice"))
    net = MODELS + bind_devices(circuit, nmos, pmos) + f"""
Vsh vsh 0 dc {vdd/2}
Vfl fl 0 dc {vdd/2}
Vnw vnw 0 dc {vdd}
Vpw vpw 0 dc 0
Xc vsh fl vnw vpw SARADC_CAP_UNIT
.op
.print op @vsh[i]
.end
"""
    rc, log = run_ngspice(net, workdir)
    ok = rc == 0 and "error" not in log.lower().split("circuit:")[-1][:400]
    return {"topology": "cap_unit", "pass": bool(ok),
            "check": "DC operating point converges, no floating node",
            "models": "level1-smoke"}


def smoke_tg(manifests, workdir, vdd, nmos, pmos):
    circuit = read(os.path.join(manifests["switch_tg"], "circuit.spice"))
    net = MODELS + bind_devices(circuit, nmos, pmos) + f"""
Vdd vdd 0 dc {vdd}
Vin z1 0 dc {vdd*0.5}
Vs s 0 dc 0
Vsb sb 0 dc {vdd}
Rl z2 0 1meg
Xsw s sb z1 z2 vdd 0 SARADC_SW_TG
.dc Vs 0 {vdd} {vdd/10}
.print dc v(z2)
.end
"""
    rc, log = run_ngspice(net, workdir)
    rows = parse_table(log)
    # first row S=0 (off, z2 pulled to 0 by Rl), last row S=VDD (on, z2 ~ VIN)
    ok = False
    detail = rows[:2]
    if len(rows) >= 2:
        off = rows[0][-1]
        on = rows[-1][-1]
        ok = abs(off) < 0.1 * vdd and abs(on - vdd * 0.5) < 0.15 * vdd
        detail = {"off_v": off, "on_v": on, "vin": vdd * 0.5}
    return {"topology": "switch_tg", "pass": bool(ok),
            "check": "off when S=0, passes VIN when S=VDD", "detail": detail,
            "models": "level1-smoke"}


def smoke_cmp(manifests, workdir, vdd, nmos, pmos):
    circuit = read(os.path.join(manifests["cmp_strongarm"], "circuit.spice"))
    net = MODELS + bind_devices(circuit, nmos, pmos) + f"""
Vdd vdd 0 dc {vdd}
Vip inp 0 dc {vdd*0.55}
Vin inn 0 dc {vdd*0.45}
Vclk clk 0 pulse(0 {vdd} 1n 100p 100p 40n 80n)
Xc inp inn clk outp outn vdd 0 SARADC_CMP_STRONGARM
.tran 20p 8n
.print tran v(outp) v(outn)
.end
"""
    rc, log = run_ngspice(net, workdir)
    rows = parse_table(log)
    ok = False
    detail = None
    if rows:
        last = rows[-1]
        outp, outn = last[-2], last[-1]
        ok = outp > outn + 0.1 * vdd
        detail = {"outp_v": outp, "outn_v": outn}
    return {"topology": "cmp_strongarm", "pass": bool(ok),
            "check": "INP>INN gives OUTP>OUTN after CLK rises", "detail": detail,
            "models": "level1-smoke"}


SMOKES = {"cap_unit": smoke_cap, "switch_tg": smoke_tg, "cmp_strongarm": smoke_cmp}


def run(manifests, vdd=1.8, only=None, devices=None):
    devices = devices or {"nmos": "sky130_fd_pr__nfet_01v8",
                          "pmos": "sky130_fd_pr__pfet_01v8"}
    results = []
    for name, fn in SMOKES.items():
        if only and name not in only:
            continue
        if name not in manifests:
            continue
        with tempfile.TemporaryDirectory(prefix="adcgen_smoke_") as tmp:
            try:
                results.append(fn(manifests, tmp, vdd, devices['nmos'], devices['pmos']))
            except Exception as exc:                      # noqa: BLE001
                results.append({"topology": name, "pass": False,
                                "check": "harness error", "error": str(exc)})
    return results


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="output of macro_gen.py --out")
    ap.add_argument("--pdk", default="sky130A_hd")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sys.path.insert(0, HERE)
    from macro_gen import load_profile

    profile = load_profile(args.pdk)
    manifests = {name: os.path.join(args.dir, name)
                 for name in SMOKES
                 if os.path.isfile(os.path.join(args.dir, name, "circuit.spice"))}
    results = run(manifests, profile["vdd"], devices=profile["devices"])
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['topology']:16s} {r['check']}")
            if r.get("detail"):
                print(f"      {r['detail']}")
            if r.get("error"):
                print(f"      error: {r['error']}")
    return 0 if results and all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""DRC / LVS / PEX closure check for every analog layout model, every PDK.

The generator has eight architecture *models*.  Only the SAR family has
layouts; the other seven are behavioural-only (see GAPS.md L3).  This driver
therefore audits the physical models that exist:

    leaves   cap_unit, switch_tg
    array    4-bit binary slice (array4b)   ihp / gf180 / ics55 only

sky130 currently closes only cap_unit + switch_tg through layout_check.py;
it has no array4b model, so the "all" run covers 3 array4b slices, not 4.

and runs the full proof chain on each, regenerating the GDS from its layout
script first so the result is reproducible and not a stale artifact:

    layout   klayout -b -r layout_<pdk>_<leaf>.py
    drc      the PDK DRC deck (hard vs context classification)
    lvs      netgen / sg13g2.lvs / ics55 run_lvs.py against a reference
    pex      Magic extract (cthresh 0 / rthresh 0)

Exit is non-zero if any *hard* proof fails.  Arrays currently have DRC + PEX
but no matched reference schematic, so their LVS is reported `n/a` rather
than claimed; that is the open item this check is meant to surface.

    python3 gen/analog/closure.py --pdk all --out gen/outputs/closure
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import klayout_bin  # noqa: E402
from pdk_flow import parse_lyrdb, magic_pex  # noqa: E402

SKY = None


def kl() -> str:
    return klayout_bin.find()


def run(cmd, cwd=None, env=None, timeout=1800):
    return subprocess.run(cmd, cwd=cwd, env=env, text=True,
                          capture_output=True, timeout=timeout)


def roots():
    import glob

    def one(pat):
        hits = glob.glob(os.path.expanduser(pat))
        if not hits:
            raise FileNotFoundError(pat)
        return Path(hits[0])

    return {
        "ihp": one("~/.ciel/ciel/ihp-sg13/versions/*/ihp-sg13g2"),
        "gf180": one("~/.ciel/ciel/gf180mcu/versions/*/gf180mcuD"),
        "ics55": Path(os.environ.get("ICS55_ROOT", "/grid/icsprout55-pdk")),
        "sky130": one("~/.ciel/ciel/sky130/versions/*/sky130A"),
    }


# Every layout script and the GDS / top cell it produces.  `env` entries are
# passed to the klayout run; OUT is filled with the per-cell output path.
MODELS = {
    "ihp": [
        dict(design="cap_unit", script="layout_ihp_cap.py",
             gds="cap_unit.gds", top="cap_unit3"),
        dict(design="switch_tg", script="layout_ihp_tg.py",
             gds="switch_tg.gds", top="SARADC_SW_TG_IHP"),
        dict(design="array4b", script="layout_ihp_array.py",
             gds="array4b.gds", top="SARADC_ARRAY_IHP_4B"),
    ],
    "gf180": [
        dict(design="cap_unit", script="layout_gf180_cap.py",
             gds="cap_unit.gds", top="gf_cap_unit"),
        dict(design="switch_tg", script="layout_gf180_tg.py",
             gds="switch_tg.gds", top="gf_switch_tg"),
        dict(design="array4b", script="layout_gf180_array.py",
             gds="array4b.gds", top="SARADC_ARRAY_GF_4B"),
    ],
    "ics55": [
        dict(design="cap_unit", script="layout_ics55_cap.py",
             gds="cap_unit.gds", top="cap_unit"),
        dict(design="switch_tg", script="layout_ics55_tg.py",
             gds="switch_tg.gds", top="switch_tg"),
        dict(design="array4b", script="layout_ics55_array.py",
             gds="array4b.gds", top="SARADC_ARRAY_ICS55_4B"),
    ],
}

CONTEXT = {"ihp": set(), "gf180": {"DF.13_MV", "DF.14_MV"},
           "ics55": {"M1DUM_W_1", "metal.m1.area"}}


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------

def build(pdk, spec, root, out) -> Path:
    gds = out / spec["gds"]
    env = dict(os.environ, OUT=str(gds))
    if pdk == "ihp":
        env["IHP_ROOT"] = str(root)
    elif pdk == "ics55":
        env["ICS55_ROOT"] = str(root)
    proc = run([kl(), "-b", "-r", str(HERE / spec["script"])], env=env)
    if proc.returncode != 0 or not gds.is_file():
        raise RuntimeError(f"{pdk}/{spec['design']} layout failed\n"
                           f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
    return gds


def drc(pdk, gds, top, root, out) -> dict:
    report = out / f"drc_{gds.stem}.lyrdb"
    report.unlink(missing_ok=True)
    if pdk == "ihp":
        deck = root / "libs.tech/klayout/tech/drc/ihp-sg13g2.drc"
        rd = ["-rd", f"topcell={top}"]
    elif pdk == "gf180":
        deck = root / "libs.tech/klayout/tech/drc/gf180mcu.drc"
        rd = ["-rd", f"topcell={top}", "-rd", "variant=gf180mcuD",
              "-rd", "decks=all,-density"]
    else:  # ics55
        deck = root / "libs.tech/drc/ics55_drc.drc"
        rd = ["-rd", f"top={top}", "-rd", "no_density=true"]
    proc = run([kl(), "-b", "-r", str(deck), "-rd", f"input={gds}",
                "-rd", f"report={report}", *rd])
    items = parse_lyrdb(report) if report.is_file() else []
    ctx = CONTEXT[pdk]
    hard = [i for i in items if i["rule"] not in ctx]
    return {"ok": not hard, "hard": hard, "context": len(items) - len(hard),
            "rc": proc.returncode}


def pex(pdk, gds, top, root, out) -> dict:
    if pdk == "ihp":
        rc = root / "libs.tech/magic/ihp-sg13g2.magicrc"
        base = root
    elif pdk == "gf180":
        rc = root / "libs.tech/magic/gf180mcuD.magicrc"
        base = root.parent
    else:
        rc = HERE / "ics55_magic_fix.magicrc"
        base = root
        if not rc.is_file():
            rc = root / "libs.tech/magic/ics55.magicrc"
    return magic_pex(gds, top, base, rc, out)


# ---- reference schematics for the leaves --------------------------------

# Reference schematics.  The device *form* matters: KLayout's sg13g2 and
# ics55 decks compare M devices, while Magic+netgen (gf180, ihp cap) want the
# X-subcircuit form.  netgen/KLayout ignore instance names, so M$1 is fine.
SCHEM = {
    # ihp cap has no 8/25 port labels -> the KLayout deck extracts a port-less
    # subckt.  It is matched through Magic+netgen instead (see lvs()).
    ("ihp", "cap_unit"): """\
.SUBCKT cap_unit3 FL VSH VNW VPW
XMN FL VSH FL VPW sg13_lv_nmos w=0.15u l=0.13u
XMP FL VSH FL VNW sg13_lv_pmos w=0.15u l=0.13u
.ENDS
""",
    ("ihp", "switch_tg"): """\
.SUBCKT SARADC_SW_TG_IHP S SB VDD VSS Z1 Z2
M$1 Z1 S Z2 VSS sg13_lv_nmos L=0.13u W=0.15u
M$2 Z1 SB Z2 VDD sg13_lv_pmos L=0.13u W=0.15u
.ENDS
""",
    ("gf180", "cap_unit"): """\
.subckt gf_cap_unit VSH FL VNW VPW
X0 FL VSH FL VPW nfet_03v3 w=1.6u l=0.6u
X1 FL VSH FL VNW pfet_03v3 w=1.6u l=0.6u
.ends
""",
    ("gf180", "switch_tg"): """\
.subckt gf_switch_tg Z1 S SB Z2 VSS VDD
X0 Z1 S Z2 VSS nfet_03v3 w=1.6u l=0.6u
X1 Z1 SB Z2 VDD pfet_03v3 w=1.6u l=0.6u
.ends
""",
    ("ics55", "cap_unit"): """\
.SUBCKT cap_unit FL VSH
M$1 FL VSH FL VSS NM1P2_SVT_LP L=0.06U W=1U
M$2 FL VSH FL VDD PM1P2_SVT_LP L=0.06U W=1U
.ENDS
""",
    ("ics55", "switch_tg"): """\
.SUBCKT switch_tg S SB VDD VSS Z1 Z2
M$1 Z2 S Z1 VSS NM1P2_SVT_LP L=0.06U W=1U
M$2 Z2 SB Z1 VDD PM1P2_SVT_LP L=0.06U W=1U
.ENDS
""",
}


def gf180_array_ref() -> str:
    """Independent 4-bit CDAC reference for the GF180 slice.

    The gf180 deck compares M devices (COMBINE off), so each bit is written
    as 2^i unit cells (a pmos + nmos MOSCAP pair, S=D=B_i, gate=VSH) plus one
    TG pair between B_i and REF_i.  The extractor keeps the cap pmos nwells
    per bit and each TG's pmos nwell as a floating net, and the nmos bodies on
    the global SUB; the reference mirrors that partitioning.
    """
    lines = [".SUBCKT SARADC_ARRAY_GF_4B VSH B0 REF0 B1 REF1 B2 REF2 "
             "B3 REF3 SUB"]
    n = 0
    for i in range(4):
        b, r = f"B{i}", f"REF{i}"
        for _ in range(2 ** i):
            n += 1
            lines.append(f"M${n} {b} VSH {b} NW{i} pfet_03v3 "
                         "W=1.6U L=0.6U")
            n += 1
            lines.append(f"M${n} {b} VSH {b} SUB nfet_03v3 "
                         "W=1.6U L=0.6U")
        n += 1
        lines.append(f"M${n} {b} SB{i} {r} NWT{i} pfet_03v3 W=1.6U L=0.6U")
        n += 1
        lines.append(f"M${n} {b} S{i} {r} SUB nfet_03v3 W=1.6U L=0.6U")
    lines.append(".ENDS")
    return "\n".join(lines) + "\n"


def ihp_array_ref() -> str:
    """Independent 4-bit CDAC reference for IHP, matching the deck's combine.

    bit i = 2^i unit caps (W = 2^i x 0.15u) between VSH and B_i, plus one TG
    between B_i (Z2) and REF_i (Z1).  The SG13G2 extractor combines the
    parallel cap units and leaves each TG's PMOS nwell bulk as its own floating
    net; the reference mirrors both so it stays a statement of design intent.
    """
    lines = [".SUBCKT SARADC_ARRAY_IHP_4B B0 B1 B2 B3 REF0 REF1 REF2 REF3 "
             "VDD VSH"]
    n = 0
    for i in range(4):
        w, b, r = 2 ** i * 0.15, f"B{i}", f"REF{i}"
        n += 1
        lines.append(f"M${n} {b} VSH {b} SUB sg13_lv_nmos L=0.13u W={w:g}u")
        n += 1
        lines.append(f"M${n} {b} VSH {b} VDD sg13_lv_pmos L=0.13u W={w:g}u")
        n += 1
        lines.append(f"M${n} {b} S{i} {r} SUB sg13_lv_nmos L=0.13u W=0.15u")
        n += 1
        lines.append(f"M${n} {b} SB{i} {r} VDD{i} sg13_lv_pmos L=0.13u W=0.15u")
    lines.append(".ENDS")
    return "\n".join(lines) + "\n"


def ics55_array_ref() -> str:
    """Independent 4-bit CDAC reference: bit i is 2^i unit caps + one TG.

    The ics55 LVS extractor combines the parallel unit capacitors of a bit into
    one device with W = 2^i x 1u, so the reference is written the same way (as
    a binary-weighted CDAC is drawn).  Verified to match the laid-out array.
    """
    lines = [".SUBCKT SARADC_ARRAY_ICS55_4B B0 B1 B2 B3 REF0 REF1 REF2 REF3 VSH"]
    n = 0
    for i in range(4):
        w, b, r = 2 ** i, f"B{i}", f"REF{i}"
        n += 1
        lines.append(f"M${n} {b} S{i} {r} VSS NM1P2_SVT_LP L=0.06U W=1U")
        n += 1
        lines.append(f"M${n} {b} SB{i} {r} VDD PM1P2_SVT_LP L=0.06U W=1U")
        n += 1
        lines.append(f"M${n} {b} VSH {b} VSS NM1P2_SVT_LP L=0.06U W={w}U")
        n += 1
        lines.append(f"M${n} {b} VSH {b} VDD PM1P2_SVT_LP L=0.06U W={w}U")
    lines.append(".ENDS")
    return "\n".join(lines) + "\n"


# Why the other two array slices cannot close LVS from a reference alone.
ARRAY_REASON = {
    "ihp": "closed: top pins on 8/25 + 10/25 and a combined-unit reference "
           "(see ihp_array_ref).",
    "gf180": "closed: label-stripped LVS copy + KLayout gf180mcu deck and a "
             "38-device reference (see gf180_array_ref).",
}


def lvs(pdk, design, gds, top, root, out, spec=None) -> dict:
    sch = SCHEM.get((pdk, design))
    if design == "array4b" and pdk == "ihp":
        sch = ihp_array_ref()
    if design == "array4b" and pdk == "gf180":
        sch = gf180_array_ref()
    if design == "array4b" and pdk == "ics55":
        sch = ics55_array_ref()
    if sch is None:
        return {"ok": None, "note": ARRAY_REASON.get(pdk, "no reference")}
    sch_path = out / f"{design}.cir"
    sch_path.write_text(sch)

    if pdk == "ihp" and design == "cap_unit":
        # KLayout sg13g2 LVS takes ports only from 8/25; the cap labels live
        # on 8/2, so it extracts a port-less subckt.  Magic reads them.
        lvs_net = magic_pex(gds, top, root,
                            root / "libs.tech/magic/ihp-sg13g2.magicrc", out,
                            lvs=True)
        proc = run(["netgen", "-batch", "lvs",
                    f"{lvs_net['netlist']} {top}", f"{sch_path} {top}",
                    str(root / "libs.tech/netgen/ihp-sg13g2_setup.tcl")],
                   cwd=out)
        log = proc.stdout + proc.stderr
        ok = "Circuits match uniquely." in log
    elif pdk == "ihp":
        report = out / f"lvs_{design}.lvsdb"
        proc = run([kl(), "-b", "-r",
                    str(root / "libs.tech/klayout/tech/lvs/sg13g2.lvs"),
                    "-rd", f"input={gds}", "-rd", f"topcell={top}",
                    "-rd", f"report={report}", "-rd", f"schematic={sch_path}",
                    "-rd", "disable_tap_extraction=true"])
        log = proc.stdout + proc.stderr
        ok = "Netlists match" in log
    elif pdk == "gf180":
        if design == "array4b":
            # The Magic PEX copy must keep the leaf labels (its flat
            # extractor uses them to merge leaf nets), but the gf180mcu LVS
            # deck promotes every hierarchical 34/10 & 36/10 text to a top
            # pin.  Regenerate the identical geometry with the leaf labels
            # stripped for LVS only.
            lvs_gds = out / f"{design}_lvs.gds"
            env = dict(os.environ, OUT=str(lvs_gds),
                       GF180_ARRAY_LVS_STRIP="1")
            b = run([kl(), "-b", "-r", str(HERE / spec["script"])], env=env)
            if b.returncode != 0 or not lvs_gds.is_file():
                raise RuntimeError(f"gf180/{design} LVS layout failed\n"
                                   f"{b.stdout[-1500:]}{b.stderr[-1500:]}")
            gds = lvs_gds
            report = out / f"lvs_{design}.lvsdb"
            proc = run([kl(), "-b", "-r",
                        str(root / "libs.tech/klayout/tech/lvs/gf180mcu.lvs"),
                        "-rd", f"input={gds}", "-rd", f"topcell={top}",
                        "-rd", f"schematic={sch_path}",
                        "-rd", f"report={report}",
                        "-rd", f"target_netlist={out / (design + '_extracted.cir')}",
                        "-rd", "variant=gf180mcuD"])
            log = proc.stdout + proc.stderr
            ok = "Netlists match" in log
        else:
            lvs_net = magic_pex(gds, top, root.parent,
                                root / "libs.tech/magic/gf180mcuD.magicrc", out,
                                lvs=True)
            proc = run(["netgen", "-batch", "lvs",
                        f"{lvs_net['netlist']} {top}", f"{sch_path} {top}",
                        str(root / "libs.tech/netgen/gf180mcuD_setup.tcl")],
                       cwd=out)
            log = proc.stdout + proc.stderr
            ok = "Circuits match uniquely." in log
    else:  # ics55: run_lvs.py takes an explicit schematic netlist
        report = out / f"lvs_{design}.lvsdb"
        proc = run([sys.executable,
                    str(root / "libs.tech/lvs/run_lvs.py"),
                    "--layout", str(gds), "--top", top,
                    "--netlist", str(sch_path), "--klayout", kl(),
                    "--report", str(report),
                    "--target-netlist", str(out / f"{design}_extracted.cir")],
                   cwd=out)
        log = proc.stdout + proc.stderr
        ok = "ICS55_LVS_COMPARE=PASS" in log

    (out / f"lvs_{design}.log").write_text(log)
    return {"ok": ok, "note": "matched" if ok else "mismatch", "rc": proc.returncode}


# --------------------------------------------------------------------------

def audit(pdk, root, out):
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for spec in MODELS[pdk]:
        # One directory per design: pex.spice / lvs.spice / *.ext collide
        # otherwise (they are all named after the artifact, not the design).
        work = out / spec["design"]
        work.mkdir(parents=True, exist_ok=True)
        row = {"pdk": pdk, **spec}
        try:
            gds = build(pdk, spec, root, work)
            row["layout"] = True
            d = drc(pdk, gds, spec["top"], root, work)
            row["drc"] = d
            p = pex(pdk, gds, spec["top"], root, work)
            row["pex"] = p
            l = lvs(pdk, spec["design"], gds, spec["top"], root, work,
                    spec=spec)
            row["lvs"] = l
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
        print(f"{pdk:6s} {spec['design']:10s} "
              f"layout={'ok' if row.get('layout') else 'FAIL'} "
              f"drc={'PASS' if row.get('drc', {}).get('ok') else 'FAIL'}"
              f"({len(row.get('drc', {}).get('hard', []))} hard/"
              f"{row.get('drc', {}).get('context', 0)} ctx) "
              f"lvs={_lvs_txt(row.get('lvs'))} "
              f"pex={_pex_txt(row.get('pex'))}")
        if row.get("error"):
            print(f"       ERROR {row['error']}")
        for item in row.get("drc", {}).get("hard", [])[:3]:
            print(f"       HARD {item['rule']}")
    return rows


def sky130_lane(root, out):
    """sky130 leaves go through layout_check.py (Magic+netgen proof chain)."""
    import types
    import layout_check as lc

    out.mkdir(parents=True, exist_ok=True)
    deck = ROOT / "gen/outputs/deck/sky130_tt.spice"
    # NOTE: sky130's layout scripts import numpy, which the 0.30.12 build does
    # not ship; build_gds therefore uses the system `klayout` (0.30.0), while
    # drc/lvs/pex below use klayout_bin (0.30.12) exactly as layout_check does.
    args = types.SimpleNamespace(pdk="sky130A_hd", out=str(out),
                                 pdk_root=str(root.parent), deck=str(deck))
    rows = []
    for dev in ("cap", "tg"):
        spec = lc.DEVICES[dev]
        rep = lc.check(dev, args)
        steps = rep["steps"]
        lvs_step = steps["lvs"]
        pex_step = steps["pex"]
        row = {
            "pdk": "sky130",
            "design": "cap_unit" if dev == "cap" else "switch_tg",
            "script": spec["script"], "gds": spec["gds"],
            "top": spec["cell"], "layout": True,
            "drc": {"ok": steps["drc"]["ok"],
                    "hard": steps["drc"]["items"], "context": 0},
            "lvs": {"ok": lvs_step["match"],
                    "note": "matched" if lvs_step["match"] else "mismatch"},
            "pex": {"ok": bool(pex_step.get("netlist")),
                    "devices": lvs_step.get("devices_extracted"),
                    "caps": pex_step.get("caps")},
        }
        rows.append(row)
        print(f"sky130 {row['design']:10s} layout=ok "
              f"drc={'PASS' if row['drc']['ok'] else 'FAIL'}"
              f"({len(row['drc']['hard'])} hard/0 ctx) "
              f"lvs={_lvs_txt(row['lvs'])} pex={_pex_txt(row['pex'])}")
    return rows


def _lvs_txt(l):
    if not l:
        return "FAIL"
    if l.get("ok") is None:
        return "n/a"
    return "PASS" if l["ok"] else "FAIL"


def _pex_txt(p):
    if not p:
        return "FAIL"
    return f"{'ok' if p['ok'] else 'FAIL'} {p.get('devices')}dev/{p.get('caps')}cap"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdk", choices=["ihp", "gf180", "ics55", "sky130", "all"],
                    default="all")
    ap.add_argument("--out", default=str(ROOT / "gen/outputs/closure"))
    args = ap.parse_args()

    r = roots()
    wanted = (["sky130", "ihp", "gf180", "ics55"] if args.pdk == "all"
              else [args.pdk])
    all_rows = []
    for pdk in wanted:
        out = Path(args.out).resolve() / pdk
        if pdk == "sky130":
            all_rows.extend(sky130_lane(r["sky130"], out))
        else:
            all_rows.extend(audit(pdk, r[pdk], out))

    (Path(args.out) / "closure.json").write_text(
        json.dumps(all_rows, indent=2) + "\n")

    # A leaf that has an LVS reference must prove `ok is True`: a None
    # (no-reference or unrun) result cannot silently pass just because DRC
    # and PEX happened to succeed.
    hard = [r_ for r_ in all_rows
            if r_.get("error") or not r_.get("drc", {}).get("ok", False)
            or (r_["design"] in {"cap_unit", "switch_tg"}
                and r_.get("lvs", {}).get("ok") is not True)
            or (r_["design"] == "array4b"
                and r_.get("lvs", {}).get("ok") is False)
            or not r_.get("pex", {}).get("ok", False)]
    if hard:
        print("\nFAILED: " + ", ".join(f"{r_['pdk']}/{r_['design']}"
                                       for r_ in hard), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

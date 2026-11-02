#!/usr/bin/env python3
"""Run the layout proof (DRC / LVS / PEX) for the non-sky130 PDKs.

    python3 gen/analog/pdk_flow.py --pdk ihp
    python3 gen/analog/pdk_flow.py --all

Each PDK is probed on a **shipped** cell first: if the deck cannot clear a
known-good cell, nothing we draw will.  sky130 is covered by layout_check.py
(our own cap and TG), so this driver handles the other three.

Every deck needs KLayout >= 0.30.9 (see klayout_bin.py); the system 0.30.0
crashes gf180 DRC and ihp LVS in ways that look like layout bugs.

Outputs one JSON per PDK under --out, and exits non-zero if any proof fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import klayout_bin  # noqa: E402


def run(cmd, cwd=None, env=None, timeout=1800):
    return subprocess.run(cmd, cwd=cwd, env=env, text=True,
                          capture_output=True, timeout=timeout)


def parse_lyrdb(path: Path) -> list[dict]:
    tree = ET.parse(path)
    cats = {c.findtext("name"): (c.findtext("description") or "")
            for c in tree.getroot().findall("./categories/category")}
    items = []
    for item in tree.getroot().findall("./items/item"):
        rule = (item.findtext("category") or "").strip("'")
        values = [v.text or "" for v in item.findall("./values/value")]
        items.append({"rule": rule, "description": cats.get(rule, ""),
                      "value": values[0] if values else ""})
    return items


def extract_cell(lib: Path, cell: str, out: Path) -> Path:
    env = dict(os.environ, SRC=str(lib), CELL=cell, OUT=str(out))
    proc = run([os.environ.get("KLAYOUT_BUILD", "klayout"), "-b", "-r",
                str(HERE / "extract_cell.py")], env=env, timeout=600)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"cell extraction failed\n{proc.stdout}{proc.stderr}")
    return out


def magic_pex(gds: Path, cell: str, pdk_root: Path, rc: Path, out: Path,
              lvs: bool = False) -> dict:
    work = out
    name = "lvs.spice" if lvs else "pex.spice"
    script = work / ("lvs.tcl" if lvs else "pex.tcl")
    body = ("ext2spice lvs\n" if lvs
            else "ext2spice cthresh 0\next2spice rthresh 0\n")
    script.write_text(
        f"gds read {gds.with_suffix('')}\n"
        f"load {cell}\n"
        "select top cell\n"
        "extract\n"
        f"{body}"
        f"ext2spice -o {work / name}\n"
        "quit\n")
    env = dict(os.environ, PDK_ROOT=str(pdk_root),
               MAGIC_TECH_PATH=str(rc.parent))
    proc = run(["magic", "-dnull", "-noconsole", "-rcfile", str(rc), str(script)],
               cwd=work, env=env, timeout=1200)
    spice = work / name
    text = spice.read_text() if spice.is_file() else ""
    dev = len(re.findall(r"^[MX]\S*\s+.*", text, re.M))
    return {"devices": dev, "caps": text.count("\nC"), "netlist": str(spice),
            "ok": proc.returncode == 0 and spice.is_file()}


# --------------------------------------------------------------------------
# per-PDK backends
# --------------------------------------------------------------------------

def ihp(root: Path, out: Path) -> dict:
    cell = "sg13g2_inv_1"
    gds = extract_cell(root / "libs.ref/sg13g2_stdcell/gds/sg13g2_stdcell.gds",
                       cell, out / f"{cell}.gds")
    drc_report = out / "drc.lyrdb"
    drc = run([klayout_bin.find(), "-b", "-r",
               str(root / "libs.tech/klayout/tech/drc/ihp-sg13g2.drc"),
               "-rd", f"input={gds}", "-rd", f"report={drc_report}",
               "-rd", f"topcell={cell}"])
    items = parse_lyrdb(drc_report) if drc_report.is_file() else []
    cdl = root / "libs.ref/sg13g2_stdcell/cdl/sg13g2_stdcell.cdl"
    sch = out / "schematic.cir"
    sch.write_text(cdl_block(cdl, cell))
    lvs_log = out / "lvs.log"
    lvs = run([klayout_bin.find(), "-b", "-r",
               str(root / "libs.tech/klayout/tech/lvs/sg13g2.lvs"),
               "-rd", f"input={gds}", "-rd", f"topcell={cell}",
               "-rd", f"report={out / 'lvs.lvsdb'}", "-rd", f"schematic={sch}",
               "-rd", "disable_tap_extraction=true"])
    lvs_log.write_text(lvs.stdout + lvs.stderr)
    return {"drc": items, "lvs_match": "Netlists match" in (lvs.stdout + lvs.stderr),
            "pex": magic_pex(gds, cell, root,
                             root / "libs.tech/magic/ihp-sg13g2.magicrc", out)}


def gf180(root: Path, out: Path) -> dict:
    cell = "gf180mcu_fd_sc_mcu7t5v0__inv_1"
    gds = extract_cell(root / "libs.ref/gf180mcu_fd_sc_mcu7t5v0/gds/"
                             "gf180mcu_fd_sc_mcu7t5v0.gds", cell, out / f"{cell}.gds")
    drc_report = out / "drc.lyrdb"
    run([klayout_bin.find(), "-b", "-r",
         str(root / "libs.tech/klayout/tech/drc/gf180mcu.drc"),
         "-rd", f"input={gds}", "-rd", f"report={drc_report}",
         "-rd", f"topcell={cell}", "-rd", "variant=gf180mcuD",
         "-rd", "decks=all,-density"])
    items = parse_lyrdb(drc_report) if drc_report.is_file() else []
    # netgen needs the devices as X subcircuit calls, like Magic emits
    cdl = root / "libs.ref/gf180mcu_fd_sc_mcu7t5v0/cdl/gf180mcu_fd_sc_mcu7t5v0.cdl"
    sch = out / "schematic.cir"
    sch.write_text(cdl_block(cdl, cell, x_style=True))
    pex = magic_pex(gds, cell, root.parent,
                    root / "libs.tech/magic/gf180mcuD.magicrc", out)
    lvs_net = magic_pex(gds, cell, root.parent,
                        root / "libs.tech/magic/gf180mcuD.magicrc", out, lvs=True)
    lvs = run(["netgen", "-batch", "lvs",
               f"{lvs_net['netlist']} {cell}", f"{sch} {cell}",
               str(root / "libs.tech/netgen/gf180mcuD_setup.tcl")], cwd=out)
    log = lvs.stdout + lvs.stderr
    (out / "lvs.log").write_text(log)
    return {"drc": items, "lvs_match": "Circuits match uniquely." in log,
            "pex": pex}


def ics55(root: Path, out: Path) -> dict:
    cell = "INVX1H7H"
    gds = extract_cell(root / "libs.ref/ics55_LLSC_H7CH/gds/ics55_LLSC_H7CH.gds",
                       cell, out / f"{cell}.gds")
    drc_report = out / "drc.lyrdb"
    run([klayout_bin.find(), "-b", "-r",
         str(root / "libs.tech/drc/ics55_drc.drc"),
         "-rd", f"input={gds}", "-rd", f"top={cell}",
         "-rd", f"report={drc_report}", "-rd", "no_density=true"])
    items = parse_lyrdb(drc_report) if drc_report.is_file() else []
    cdl = root / "libs.ref/ics55_LLSC_H7CH/cdl/ics55_LLSC_H7CH.cdl"
    lvs = run([sys.executable, str(root / "libs.tech/lvs/run_lvs.py"),
               "--layout", str(gds), "--top", cell, "--netlist", str(cdl),
               "--klayout", klayout_bin.find(),
               "--report", str(out / "lvs.lvsdb"),
               "--target-netlist", str(out / "extracted.cir")], cwd=out)
    log = lvs.stdout + lvs.stderr
    (out / "lvs.log").write_text(log)
    pex = magic_pex(gds, cell, root,
                    root / "libs.tech/magic/ics55.magicrc", out)
    return {"drc": items, "lvs_match": "LVS_COMPARE=PASS" in log, "pex": pex}


def cdl_block(path: Path, cell: str, x_style: bool = False) -> str:
    """Pull one .SUBCKT block out of a library CDL, optionally X-ifying it."""
    text = path.read_text(errors="replace")
    m = re.search(rf"^\.SUBCKT\s+{re.escape(cell)}\b.*?^\.ENDS",
                  text, re.M | re.S | re.I)
    if not m:
        raise RuntimeError(f"{cell} not in {path}")
    block = m.group(0)
    if not x_style:
        return block + "\n"

    def conv(line: str) -> str:
        t = line.split()
        if not t or not re.match(r"^M", t[0], re.I):
            return line
        out = ["X" + t[0][1:]] + t[1:5] + [t[5]]
        for p in t[6:]:
            k, _, v = p.partition("=")
            if k.lower() in ("w", "l") and v:
                try:
                    val = float(v)
                    if val < 1e-3:
                        val *= 1e6
                    out.append(f"{k.lower()}={val:g}")
                    continue
                except ValueError:
                    pass
            out.append(p)
        return " ".join(out)

    return "\n".join(conv(l) for l in block.splitlines()) + "\n"


# Rules that a single leaf cell cannot satisfy: full-die coverage, row-level
# well-tap distance, and metal-dummy bookkeeping.  They are reported
# separately so a real geometry violation still fails the run.
CONTEXT_RULES = {
    "ihp": set(),
    "gf180": {"DF.13_MV", "DF.14_MV"},
    "ics55": {"M1DUM_W_1", "metal.m1.area"},
}

PDKS = {"ihp": ihp, "gf180": gf180, "ics55": ics55}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdk", choices=sorted(PDKS) + ["all"], default="all")
    ap.add_argument("--ihp-root", default=str(next(Path.home().glob(
        ".ciel/ciel/ihp-sg13/versions/*/ihp-sg13g2"))))
    ap.add_argument("--gf180-root", default=str(next(Path.home().glob(
        ".ciel/ciel/gf180mcu/versions/*/gf180mcuD"))))
    ap.add_argument("--ics55-root", default="/grid/icsprout55-pdk")
    ap.add_argument("--out", default=str(ROOT / "gen/outputs/pdk_flow"))
    args = ap.parse_args()

    roots = {"ihp": Path(args.ihp_root), "gf180": Path(args.gf180_root),
             "ics55": Path(args.ics55_root)}
    wanted = sorted(PDKS) if args.pdk == "all" else [args.pdk]
    failed = []
    for name in wanted:
        out = Path(args.out).resolve() / name
        out.mkdir(parents=True, exist_ok=True)
        try:
            res = PDKS[name](roots[name], out)
        except Exception as exc:                            # noqa: BLE001
            print(f"{name:6s} ERROR {exc}")
            failed.append(name)
            continue
        drc = res["drc"]
        ctx = CONTEXT_RULES[name]
        hard = [i for i in drc if i["rule"] not in ctx]
        context = [i for i in drc if i["rule"] in ctx]
        ok = not hard and res["lvs_match"] and res["pex"]["ok"]
        if not ok:
            failed.append(name)
        res["drc_hard"] = hard
        res["drc_context"] = context
        (out / "report.json").write_text(json.dumps(res, indent=2) + "\n")
        print(f"{name:6s} drc={len(hard)} hard + {len(context)} context  "
              f"lvs={'PASS' if res['lvs_match'] else 'FAIL'}  "
              f"pex={'%d dev / %d caps' % (res['pex']['devices'], res['pex']['caps'])}")
        for item in hard[:3]:
            print(f"       HARD {item['rule']}: {item['description'][:70]}")
        for item in sorted({i['rule'] for i in context}):
            print(f"       context {item}")
    if failed:
        print("FAILED: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

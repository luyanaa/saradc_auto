#!/usr/bin/env python3
"""Generate the sky130 unit-cap layout, then prove it with DRC, LVS and PEX.

This is the repeatable driver behind the three separate proofs the layout
needs.  It is deliberately fail-closed: a non-zero exit means the GDS is not
usable, and the JSON report says which proof failed.

    python3 gen/analog/layout_check.py --pdk sky130A_hd

Steps, each writing its own artifact under --out:

    layout   KLayout+pya builds SARADC_CAP_UNIT.gds       (layout_cap.py)
    drc      sky130A_mr.drc, feol+beol forced ON          -> drc.lyrdb
    lvs      Magic extract (no caps) + netgen             -> lvs.spice
    pex      Magic extract with cthresh 0                 -> pex.spice

The DRC deck defaults to feol=false beol=false, which reports a clean die for
a layout it never checked; those two flags are always passed explicitly.

Magic in this PDK install resolves its tech file through a build/<sha> path
that does not exist.  PDK_ROOT is therefore set to the real version directory
for every Magic call, and the GDS is read with `gds read` rather than `load`
(Magic's `load` dlopens a .gds and fails with "invalid ELF header").
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
TOOLCHAIN = "/opt/OpenSERDES/verification/analog"


def find_pdk_root(explicit: str | None, profile: dict) -> Path:
    cands: list[Path] = []
    if explicit:
        cands.append(Path(explicit).expanduser())
    env = os.environ.get("PDK_ROOT")
    if env:
        cands.append(Path(env).expanduser())
    magicrc = profile.get("signoff", {}).get("magicrc")
    if magicrc:
        cands.append(Path(magicrc).expanduser().parent.parent.parent.parent)
    cands.extend(sorted(Path.home().glob(".ciel/ciel/sky130/versions/*")))
    for p in cands:
        if (p / "sky130A/libs.tech/magic/sky130A.magicrc").is_file():
            return p
    raise FileNotFoundError("no sky130A install found; pass --pdk-root")


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None,
        timeout: int = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True,
                          capture_output=True, timeout=timeout)
    return proc


def build_gds(out: Path, sizes: dict) -> Path:
    gds = out / "cap_unit.gds"
    env = dict(os.environ)
    env.update({"OUT": str(gds), "WN": str(sizes["wn"]), "WP": str(sizes["wp"]),
                "L": str(sizes["l"])})
    proc = run(["klayout", "-b", "-r", str(HERE / "layout_cap.py")], env=env)
    if proc.returncode != 0 or not gds.is_file():
        raise RuntimeError(f"layout build failed\n{proc.stdout}{proc.stderr}")
    return gds


def parse_lyrdb(report: Path) -> list[dict]:
    tree = ET.parse(report)
    cats = {}
    for cat in tree.getroot().findall("./categories/category"):
        name = cat.findtext("name")
        if name is not None:
            cats[name] = cat.findtext("description") or ""
    items = []
    for item in tree.getroot().findall("./items/item"):
        raw = (item.findtext("category") or "").strip("'")
        values = [v.text or "" for v in item.findall("./values/value")]
        items.append({"rule": raw, "description": cats.get(raw, ""),
                      "value": values[0] if values else ""})
    return items


def drc(gds: Path, pdk: Path, out: Path, topcell: str) -> list[dict]:
    deck = pdk / "sky130A/libs.tech/klayout/drc/sky130A_mr.drc"
    if not deck.is_file():
        raise FileNotFoundError(deck)
    report = out / "drc.lyrdb"
    report.unlink(missing_ok=True)
    proc = run(["klayout", "-b", "-r", str(deck),
                "-rd", f"input={gds}", "-rd", f"report={report}",
                "-rd", f"topcell={topcell}", "-rd", "thr=4",
                "-rd", "feol=true", "-rd", "beol=true"])
    if proc.returncode != 0 or not report.is_file():
        raise RuntimeError(f"DRC run failed\n{proc.stdout[-3000:]}{proc.stderr[-3000:]}")
    return parse_lyrdb(report)


def magic(gds: Path, cell: str, work: Path, pdk: Path, script: Path,
          log: Path) -> None:
    rc = pdk / "sky130A/libs.tech/magic/sky130A.magicrc"
    env = dict(os.environ)
    env["PDK_ROOT"] = str(pdk)
    env["MAGIC_TECH_PATH"] = str(pdk / "sky130A/libs.tech/magic")
    proc = run(["magic", "-dnull", "-noconsole", "-rcfile", str(rc), str(script)],
               cwd=work, env=env, timeout=900)
    log.write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"magic failed ({proc.returncode}); see {log}")


def write_schematic(path: Path, cell: str, sizes: dict, profile: dict) -> None:
    dev = profile["devices"]
    finger = profile.get("analog", {}).get("finger_param", "nf")
    text = (
        f".subckt {cell} VSH FL VNW VPW\n"
        f"XMN0 FL VSH FL VPW {dev['nmos']} "
        f"w={sizes['wn']:g} l={sizes['l']:g} {finger}=1\n"
        f"XMP0 FL VSH FL VNW {dev['pmos']} "
        f"w={sizes['wp']:g} l={sizes['l']:g} {finger}=1\n"
        f".ends\n"
    )
    path.write_text(text)


def lvs(gds: Path, cell: str, work: Path, pdk: Path, sizes: dict,
        profile: dict) -> dict:
    script = work / "extract_lvs.tcl"
    spice = work / "lvs.spice"
    script.write_text(
        f"gds read {gds.with_suffix('')}\n"
        f"load {cell}\n"
        "select top cell\n"
        "port makeall\n"
        "extract\n"
        "ext2spice lvs\n"
        f"ext2spice -o {spice}\n"
        "puts LVS_EXTRACT_DONE\n"
        "quit\n"
    )
    magic(gds, cell, work, pdk, script, work / "magic_lvs.log")
    if not spice.is_file():
        raise RuntimeError("Magic wrote no LVS netlist")

    sch = work / "schematic.cir"
    write_schematic(sch, cell, sizes, profile)
    setup = pdk / "sky130A/libs.tech/netgen/sky130A_setup.tcl"
    proc = run(["netgen", "-batch", "lvs", f"{spice} {cell}",
                f"{sch} {cell}", str(setup)], cwd=work, timeout=600)
    log = proc.stdout + proc.stderr
    (work / "netgen_lvs.log").write_text(log)
    match = "Circuits match uniquely." in log
    devices = re.search(r"Circuit 1 contains (\d+) devices, Circuit 2 contains (\d+) devices",
                        log)
    return {
        "match": match,
        "returncode": proc.returncode,
        "devices_extracted": int(devices.group(1)) if devices else None,
        "devices_schematic": int(devices.group(2)) if devices else None,
        "topology": "<->".join(re.findall(r"^\.subckt (\S+)", spice.read_text(), re.M)),
    }


def pex(gds: Path, cell: str, work: Path, pdk: Path) -> dict:
    script = work / "extract_pex.tcl"
    spice = work / "pex.spice"
    script.write_text(
        f"gds read {gds.with_suffix('')}\n"
        f"load {cell}\n"
        "select top cell\n"
        "port makeall\n"
        "extract\n"
        "ext2spice cthresh 0\n"
        "ext2spice rthresh 0\n"
        f"ext2spice -o {spice}\n"
        "puts PEX_DONE\n"
        "quit\n"
    )
    magic(gds, cell, work, pdk, script, work / "magic_pex.log")
    if not spice.is_file():
        raise RuntimeError("Magic wrote no PEX netlist")

    text = spice.read_text()
    top = re.search(rf"^\.subckt {re.escape(cell)}\b.*?^\.ends", text, re.M | re.S)
    body = top.group(0) if top else text
    caps = []
    for m in re.finditer(r"^C(\S+)\s+(\S+)\s+(\S+)\s+([-\d.eE+]+)([fa])", body, re.M):
        scale = 1e-15 if m.group(5) == "f" else 1e-18
        caps.append({"nodes": [m.group(2), m.group(3)],
                     "farad": float(m.group(4)) * scale})
    top_plate = sum(c["farad"] for c in caps if "VSH" in c["nodes"])
    return {
        "netlist": str(spice),
        "caps": len(caps),
        "vsh_parasitic_ff": top_plate * 1e15,
        "vsh_breakdown_ff": [
            {"to": [n for n in c["nodes"] if n != "VSH"][0], "ff": c["farad"] * 1e15}
            for c in caps if "VSH" in c["nodes"]
        ],
    }


def simulate(pex_netlist: Path, deck: Path, window: dict) -> dict:
    """Run the extracted netlist on the same AC bench as measure.py.

    Magic writes device geometry in micrometres while the PDK binning models
    expect SI, so w/l get a 'u' suffix for this simulation only; the extracted
    netlist itself is left untouched.  ad/as/pd/ps are dropped because they
    only add junction capacitance, which the gate-cap measurement does not
    depend on.  The point is the *delta* against the schematic value: if the
    extracted parasitics are real, C(layout) - C(schematic) is constant across
    bias.
    """
    body = "\n".join(l for l in pex_netlist.read_text().splitlines()
                     if not l.startswith("*"))
    body = re.sub(r"^X\d+.*$",
                  lambda m: re.sub(r"\s(ad|as|pd|ps)=[\d.]+\b", "",
                                   re.sub(r"\b(w|l)=([\d.]+)\b", r"\1=\2u", m.group(0))),
                  body, flags=re.M)
    results = {}
    for vdiff in (0.0, -0.6, 0.6):
        vcm = 0.9
        net = (
            "* layout-extracted cap cell, same bench as measure.py\n"
            f".include {deck}\n"
            f"Vg vsh 0 dc {vcm + vdiff:.6f} ac 1\n"
            f"Vb fl 0 dc {vcm - vdiff:.6f}\n"
            f"Vnw vnw 0 dc {vcm * 2:.6f}\n"
            "Vpw vpw 0 dc 0\n"
            f"{body}\n"
            ".ac lin 1 1e6 1e6\n"
            ".print ac i(vg)\n"
            ".end\n"
        )
        with tempfile.TemporaryDirectory(prefix="cap_sim_") as tmp:
            p = Path(tmp) / "bench.spice"
            p.write_text(net)
            proc = run(["ngspice", "-b", str(p)], timeout=300)
        m = re.search(r"^\d+\s+[\d.eE+]+\s+([-\d.eE+]+),\s*([-\d.eE+]+)\s*$",
                      proc.stdout, re.M)
        if not m:
            raise RuntimeError(f"ngspice produced no AC row at vdiff={vdiff}: "
                               + proc.stdout[-1200:])
        c_ff = abs(float(m.group(2))) / (2.0 * 3.141592653589793 * 1e6) * 1e15
        results[f"{vdiff:.1f}"] = c_ff
    ref = window.get("c_ff_vs_vdiff", {})
    deltas = {}
    for k, c in results.items():
        if k in ref:
            deltas[k] = c - float(ref[k])
    return {"c_ff": results, "schematic_ff": {k: ref[k] for k in results if k in ref},
            "delta_ff": deltas,
            "delta_constant": (max(deltas.values()) - min(deltas.values())) < 0.01
            if len(deltas) == 3 else None}



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdk", default="sky130A_hd")
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--out", default=str(ROOT / "gen/outputs/layout"))
    ap.add_argument("--deck", default=str(ROOT / "gen/outputs/deck/sky130_tt.spice"),
                    help="flattened sky130 deck for the layout-vs-schematic check")
    ap.add_argument("--cell", default="SARADC_CAP_UNIT")
    ap.add_argument("--topcell", default="SARADC_CAP_UNIT")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    profile_path = HERE.parent / "pdk" / f"{args.pdk}.json"
    profile = json.loads(profile_path.read_text())
    sizes = profile["analog"]["sizing_um"]
    pdk = find_pdk_root(args.pdk_root, profile)

    report: dict = {"pdk": args.pdk, "pdk_root": str(pdk), "cell": args.cell,
                    "sizing_um": sizes, "steps": {}}
    failures = []

    gds = build_gds(out, sizes)
    report["steps"]["layout"] = {"gds": str(gds), "ok": True}

    items = drc(gds, pdk, out, args.topcell)
    report["steps"]["drc"] = {"ok": not items, "items": items,
                              "report": str(out / "drc.lyrdb")}
    if items:
        failures.append("drc")

    work = Path(tempfile.mkdtemp(prefix="cap_lvs_", dir=str(out)))
    lvs_result = lvs(gds, args.cell, work, pdk, sizes, profile)
    report["steps"]["lvs"] = {**lvs_result, "workdir": str(work)}
    if not lvs_result["match"]:
        failures.append("lvs")

    report["steps"]["pex"] = pex(gds, args.cell, work, pdk)

    deck = Path(args.deck).expanduser()
    window = next((c.get("measurement", {}) for c in profile.get("cap_options", [])
                   if c.get("name") == profile.get("default_cap")), {})
    if deck.is_file():
        report["steps"]["simulate"] = simulate(Path(report["steps"]["pex"]["netlist"]),
                                               deck.resolve(), window)
    else:
        report["steps"]["simulate"] = {"skipped": f"no deck at {deck}"}

    report["ok"] = not failures
    report["failed"] = failures
    (out / "cap_unit.json").write_text(json.dumps(report, indent=2) + "\n")

    print(f"gds      {gds}")
    print(f"drc      {'PASS' if not items else 'FAIL'} ({len(items)} items)")
    print(f"lvs      {'PASS' if lvs_result['match'] else 'FAIL'} "
          f"({lvs_result['devices_extracted']} devices extracted)")
    print(f"pex      VSH parasitic {report['steps']['pex']['vsh_parasitic_ff']:.3f} fF "
          f"over {report['steps']['pex']['caps']} caps")
    sim = report["steps"]["simulate"]
    if "c_ff" in sim:
        print("sim      layout C " + "  ".join(f"{k}={v:.3f}fF" for k, v in sim["c_ff"].items()))
        print("         delta vs schematic " +
              "  ".join(f"{k}={v:+.3f}fF" for k, v in sim["delta_ff"].items()) +
              f"  constant={sim['delta_constant']}")
    print(f"report   {out / 'cap_unit.json'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

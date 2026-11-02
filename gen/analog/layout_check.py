#!/usr/bin/env python3
"""Generate sky130 analog leaf layouts, then prove them with DRC, LVS and PEX.

This is the repeatable driver behind the separate proofs a layout needs.  It
is deliberately fail-closed: a non-zero exit means at least one GDS is not
usable, and the JSON report says which proof failed.

    python3 gen/analog/layout_check.py --device all

Devices:

    cap  SARADC_CAP_UNIT  unit capacitor          (layout_cap.py)
    tg   SARADC_SW_TG     selected 4x transmission gate (layout_tg.py)

Steps per device, each writing its own artifact under --out:

    layout   KLayout+pya builds the GDS
    drc      sky130A_mr.drc with feol+beol forced ON       -> drc_<dev>.lyrdb
    lvs      Magic extract (no caps) + netgen              -> lvs.spice
    pex      Magic extract with cthresh 0 / rthresh 0      -> pex.spice
    sim      ngspice on the extracted netlist

The DRC deck defaults to feol=false beol=false, which reports a clean die for
a layout it never checked; those two flags are always passed explicitly.

Magic in this PDK install resolves its tech file through a build/<sha> path
that does not exist.  PDK_ROOT and MAGIC_TECH_PATH are therefore set to the
real version directory for every call, and the GDS is read with `gds read`
rather than `load` (Magic's `load` dlopens a .gds and fails with "invalid ELF
header").

The cap simulation compares C(V) against measure.py and expects the extracted
parasitics to be a *constant* offset.  The switch simulation reproduces the
off-state bench from char.switch_cap and reports the measured Coff, replacing
the 4.63 fF schematic assumption with geometry.
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

PI = 3.141592653589793
FREQ = 1.0e6


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
    return subprocess.run(cmd, cwd=cwd, env=env, text=True,
                          capture_output=True, timeout=timeout)


# --------------------------------------------------------------------------
# device table
# --------------------------------------------------------------------------

def cap_schematic(cell: str, sizes: dict, profile: dict) -> str:
    dev = profile["devices"]
    finger = profile.get("analog", {}).get("finger_param", "nf")
    return (
        f".subckt {cell} VSH FL VNW VPW\n"
        f"XMN0 FL VSH FL VPW {dev['nmos']} "
        f"w={sizes['wn']:g} l={sizes['l']:g} {finger}=1\n"
        f"XMP0 FL VSH FL VNW {dev['pmos']} "
        f"w={sizes['wp']:g} l={sizes['l']:g} {finger}=1\n"
        f".ends\n"
    )


def tg_schematic(cell: str, sizes: dict, profile: dict) -> str:
    dev = profile["devices"]
    finger = profile.get("analog", {}).get("finger_param", "nf")
    # Port order must match what Magic emits: with two symmetric devices,
    # netgen's permutation search cannot disambiguate six pins, so a different
    # order fails as "top level cell failed pin matching".
    return (
        f".subckt {cell} Z1 Z2 S SB VSS VDD\n"
        f"XMN0 Z2 S Z1 VSS {dev['nmos']} "
        f"w={sizes['wn']:g} l={sizes['l']:g} {finger}=1\n"
        f"XMP0 Z2 SB Z1 VDD {dev['pmos']} "
        f"w={sizes['wp']:g} l={sizes['l']:g} {finger}=1\n"
        f".ends\n"
    )


def sim_cap(netlist: Path, deck: Path, profile: dict) -> dict:
    """C(V) of the extracted cap cell, against measure.py's schematic curve.

    Magic writes device geometry in micrometres while the PDK binning models
    expect SI, so w/l get a 'u' suffix for this simulation only; the extracted
    netlist itself is left untouched.  ad/as/pd/ps are dropped because they
    only add junction capacitance, which the gate-cap measurement does not
    depend on.  The point is the *delta* against the schematic value: if the
    extracted parasitics are real, C(layout) - C(schematic) is constant.
    """
    body = bench_body(netlist)
    window = next((c.get("measurement", {}) for c in profile.get("cap_options", [])
                   if c.get("name") == profile.get("default_cap")), {})
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
            f".ac lin 1 {FREQ:g} {FREQ:g}\n"
            ".print ac i(vg)\n"
            ".end\n"
        )
        c_ff = ac_cap_ff(net, "i(vg)")
        results[f"{vdiff:.1f}"] = c_ff
    ref = window.get("c_ff_vs_vdiff", {})
    deltas = {k: results[k] - float(ref[k]) for k in results if k in ref}
    return {"c_ff": results,
            "schematic_ff": {k: float(ref[k]) for k in results if k in ref},
            "delta_ff": deltas,
            "delta_constant": (max(deltas.values()) - min(deltas.values())) < 0.01
            if len(deltas) == 3 else None}


def sim_tg(netlist: Path, deck: Path, profile: dict) -> dict:
    """Off-state capacitance at Z2, the bench char.switch_cap uses."""
    body = bench_body(netlist)
    vdd = profile["vdd"]
    net = (
        "* layout-extracted TG, same bench as char.switch_cap\n"
        f".include {deck}\n"
        "Vs s 0 dc 0\n"
        f"Vsb sb 0 dc {vdd:g}\n"
        f"V1 z1 0 dc {vdd / 2:g}\n"
        f"V2 z2 0 dc {vdd / 2:g} ac 1\n"
        f"Vdd vdd 0 dc {vdd:g}\n"
        "Vss vss 0 dc 0\n"
        f"{body}\n"
        f".ac lin 1 {FREQ:g} {FREQ:g}\n"
        ".print ac i(v2)\n"
        ".end\n"
    )
    measured = ac_cap_ff(net, "i(v2)")
    ref = profile.get("switches", {}).get("transmission_gate", {}).get("c_off_ff")
    return {"c_off_ff": measured, "schematic_ff": ref,
            "delta_ff": (measured - ref) if ref is not None else None}


def bench_body(netlist: Path) -> str:
    body = "\n".join(l for l in netlist.read_text().splitlines()
                     if not l.startswith("*"))
    return re.sub(
        r"^X\d+.*$",
        lambda m: re.sub(r"\s(ad|as|pd|ps)=[\d.]+\b", "",
                         re.sub(r"\b(w|l)=([\d.]+)\b", r"\1=\2u", m.group(0))),
        body, flags=re.M)


def ac_cap_ff(net: str, probe: str) -> float:
    with tempfile.TemporaryDirectory(prefix="adcgen_sim_") as tmp:
        p = Path(tmp) / "bench.spice"
        p.write_text(net)
        proc = run(["ngspice", "-b", str(p)], timeout=300)
    m = re.search(r"^\d+\s+[\d.eE+]+\s+([-\d.eE+]+),\s*([-\d.eE+]+)\s*$",
                  proc.stdout, re.M)
    if not m:
        raise RuntimeError(f"ngspice produced no AC row for {probe}: "
                           + proc.stdout[-1200:])
    return abs(float(m.group(2))) / (2.0 * PI * FREQ) * 1e15


DEVICES = {
    "cap": {"script": "layout_cap.py", "gds": "cap_unit.gds",
            "cell": "SARADC_CAP_UNIT", "sizing": "sizing_um",
            "ports": ["VSH", "FL", "VNW", "VPW"], "probe": "VSH",
            "schematic": cap_schematic, "simulate": sim_cap},
    "tg": {"script": "layout_tg.py", "gds": "switch_tg.gds",
           "cell": "SARADC_SW_TG", "sizing": "switch_sizing_um",
           "ports": ["Z1", "Z2", "S", "SB", "VSS", "VDD"], "probe": "Z2",
           "schematic": tg_schematic, "simulate": sim_tg},
}


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------

def build_gds(out: Path, spec: dict, sizes: dict) -> Path:
    gds = out / spec["gds"]
    env = dict(os.environ)
    env.update({"OUT": str(gds), "WN": str(sizes["wn"]), "WP": str(sizes["wp"]),
                "L": str(sizes["l"])})
    proc = run(["klayout", "-b", "-r", str(HERE / spec["script"])], env=env)
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


def drc(gds: Path, pdk: Path, out: Path, topcell: str, tag: str) -> list[dict]:
    deck = pdk / "sky130A/libs.tech/klayout/drc/sky130A_mr.drc"
    if not deck.is_file():
        raise FileNotFoundError(deck)
    report = out / f"drc_{tag}.lyrdb"
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
               cwd=work, env=env, timeout=1200)
    log.write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"magic failed ({proc.returncode}); see {log}")


def lvs(gds: Path, spec: dict, work: Path, pdk: Path, sizes: dict,
        profile: dict) -> dict:
    cell = spec["cell"]
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
    sch.write_text(spec["schematic"](cell, sizes, profile))
    setup = pdk / "sky130A/libs.tech/netgen/sky130A_setup.tcl"
    proc = run(["netgen", "-batch", "lvs", f"{spice} {cell}",
                f"{sch} {cell}", str(setup)], cwd=work, timeout=600)
    log = proc.stdout + proc.stderr
    (work / "netgen_lvs.log").write_text(log)
    devices = re.search(r"Circuit 1 contains (\d+) devices, Circuit 2 contains (\d+) devices",
                        log)
    return {
        "match": "Circuits match uniquely." in log,
        "returncode": proc.returncode,
        "devices_extracted": int(devices.group(1)) if devices else None,
        "devices_schematic": int(devices.group(2)) if devices else None,
        "pins": [p for p in re.findall(r"^\.subckt " + re.escape(cell) + r" (.*)$",
                                       spice.read_text(), re.M)][:1],
    }


def pex(gds: Path, spec: dict, work: Path, pdk: Path) -> dict:
    cell = spec["cell"]
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
    probe = spec["probe"]
    to_probe = sum(c["farad"] for c in caps if probe in c["nodes"])
    return {
        "netlist": str(spice),
        "caps": len(caps),
        "probe": probe,
        "probe_parasitic_ff": to_probe * 1e15,
        "probe_breakdown_ff": sorted(
            ({"to": [n for n in c["nodes"] if n != probe][0], "ff": c["farad"] * 1e15}
             for c in caps if probe in c["nodes"]),
            key=lambda d: -d["ff"]),
    }


def check(device: str, args) -> dict:
    spec = DEVICES[device]
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    profile = json.loads((HERE.parent / "pdk" / f"{args.pdk}.json").read_text())
    sizes = (profile["analog"].get(spec["sizing"])
             or profile["analog"]["sizing_um"])
    pdk = find_pdk_root(args.pdk_root, profile)

    report: dict = {"pdk": args.pdk, "pdk_root": str(pdk), "device": device,
                    "cell": spec["cell"], "sizing_um": sizes, "steps": {}}
    failures = []

    gds = build_gds(out, spec, sizes)
    report["steps"]["layout"] = {"gds": str(gds), "ok": True}

    items = drc(gds, pdk, out, spec["cell"], device)
    report["steps"]["drc"] = {"ok": not items, "items": items,
                              "report": str(out / f"drc_{device}.lyrdb")}
    if items:
        failures.append("drc")

    work = Path(tempfile.mkdtemp(prefix=f"{device}_lvs_", dir=str(out)))
    lvs_result = lvs(gds, spec, work, pdk, sizes, profile)
    report["steps"]["lvs"] = {**lvs_result, "workdir": str(work)}
    if not lvs_result["match"]:
        failures.append("lvs")

    report["steps"]["pex"] = pex(gds, spec, work, pdk)

    deck = Path(args.deck).expanduser()
    if deck.is_file():
        report["steps"]["simulate"] = spec["simulate"](
            Path(report["steps"]["pex"]["netlist"]), deck.resolve(), profile)
    else:
        report["steps"]["simulate"] = {"skipped": f"no deck at {deck}"}

    report["ok"] = not failures
    report["failed"] = failures
    (out / f"{device}.json").write_text(json.dumps(report, indent=2) + "\n")

    print(f"[{device}] gds  {gds.name}  {spec['cell']}")
    print(f"[{device}] drc  {'PASS' if not items else 'FAIL'} ({len(items)} items)")
    print(f"[{device}] lvs  {'PASS' if lvs_result['match'] else 'FAIL'} "
          f"({lvs_result['devices_extracted']} devices)")
    pex_info = report["steps"]["pex"]
    print(f"[{device}] pex  {pex_info['probe']} parasitic "
          f"{pex_info['probe_parasitic_ff']:.3f} fF over {pex_info['caps']} caps")
    sim = report["steps"]["simulate"]
    if "c_off_ff" in sim:
        print(f"[{device}] sim  Coff {sim['c_off_ff']:.3f} fF  "
              f"(schematic {sim['schematic_ff']} fF, delta {sim['delta_ff']:+.3f})")
    elif "c_ff" in sim:
        print(f"[{device}] sim  layout C " +
              "  ".join(f"{k}={v:.3f}fF" for k, v in sim["c_ff"].items()))
        print(f"[{device}]      delta vs schematic " +
              "  ".join(f"{k}={v:+.3f}fF" for k, v in sim["delta_ff"].items()) +
              f"  constant={sim['delta_constant']}")
    print(f"[{device}] report {out / (device + '.json')}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdk", default="sky130A_hd")
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--out", default=str(ROOT / "gen/outputs/layout"))
    ap.add_argument("--deck", default=str(ROOT / "gen/outputs/deck/sky130_tt.spice"),
                    help="flattened sky130 deck for the layout-vs-schematic checks")
    ap.add_argument("--device", choices=sorted(DEVICES) + ["all"], default="all")
    args = ap.parse_args()

    wanted = sorted(DEVICES) if args.device == "all" else [args.device]
    failed = []
    for device in wanted:
        report = check(device, args)
        failed.extend(f"{device}:{f}" for f in report["failed"])
    if failed:
        print("FAILED: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

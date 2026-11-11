#!/usr/bin/env python3
"""End-to-end characterization of the generated analog leaves on all four PDKs.

Same three benches on every technology, against that PDK's own model deck:

  unit MOSCAP   C(Vdiff) at 1 MHz, usable/flat-band density, area
  switch_tg     Ron(Vin) over the input range (worst point), off capacitance
  comparator    StrongArm systematic offset (bisection) and clk->out delay

The geometry is the one actually drawn in the layout leaves, not a nominal
from the profile, so the numbers line up with the DRC/LVS/PEX closure:

  sky130    cap 1.4/2.8 um, L 0.15   switch 5.6/11.2     (layout_check.py)
  ihp       cap 0.15/0.15 um, L 0.13 switch 0.15/0.3     (layout_ihp_cap/tg)
  gf180     3.3 V nmos/pmos 1.6/1.6, L 0.6               (layout_gf180_cap/tg)
  ics55     cap 1.0/1.0 um, L 60 nm, comparator Wp=4 Wn  (layout_ics55)

Deck recipes (all generated as run artifacts under --out/deck):

  sky130   gen/analog/deck.py flatten              -> sky130_tt.spice
  ihp      .lib cornerMOSlv.lib mos_tt + OSDI (psp103) via .spiceinit
  gf180    .include design.spice + .lib sm141064.spice typical
  ics55    gen/analog/deck.py --ics55-xyce (Xyce)

Comparator is a circuit-level bench (the StrongArm is not a layout leaf), but
it runs on the same device cards as the leaves.  Mismatch/Pelgrom is not
reported here: no PDK path in this tree exposes a working Monte-Carlo scatter.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.dirname(HERE)
ROOT = Path(os.path.dirname(GEN))
sys.path.insert(0, HERE)

import char  # noqa: E402
import closure  # noqa: E402
import macro_gen  # noqa: E402
import measure  # noqa: E402


# ---- per-PDK deck and geometry -------------------------------------------

PDK = {
    "sky130": {
        "profile": "sky130A_hd",
        "cap": {"wn": 1.4, "wp": 2.8, "l": 0.15},
        "switch": {"wn": 5.6, "wp": 11.2, "l": 0.15},
        "cmp": {"wn": 1.4, "wp": 5.6, "l": 0.15},
        "sim": "ngspice",
    },
    "ihp": {
        "profile": "ihp_sg13g2",
        "cap": {"wn": 0.15, "wp": 0.15, "l": 0.13},
        "switch": {"wn": 0.15, "wp": 0.3, "l": 0.13},
        "cmp": {"wn": 0.15, "wp": 0.6, "l": 0.13},
        "sim": "ngspice",
    },
    "gf180": {
        "profile": "gf180mcu_as_7t3v3",
        "cap": {"wn": 1.6, "wp": 1.6, "l": 0.6},
        "switch": {"wn": 1.6, "wp": 1.6, "l": 0.6},
        "cmp": {"wn": 1.6, "wp": 6.4, "l": 0.6},
        "sim": "ngspice",
    },
    "ics55": {
        "profile": "ics55_h7ch",
        "cap": {"wn": 1.0, "wp": 1.0, "l": 0.06},
        "switch": {"wn": 1.0, "wp": 1.0, "l": 0.06},
        "cmp": {"wn": 1.0, "wp": 4.0, "l": 0.06},
        "sim": "xyce",
    },
}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def build_deck(pdk: str, root: Path, deck_dir: Path) -> tuple[Path, Path | None]:
    """Return (deck, spiceinit).  sky130/ics55 use the pre-flattened decks."""
    if pdk == "sky130":
        return ROOT / "gen/outputs/deck/sky130_tt.spice", None
    if pdk == "ics55":
        return ROOT / "gen/outputs/deck/ics55_tt.spice", None
    ng = root / "libs.tech/ngspice"
    if pdk == "ihp":
        deck = _write(deck_dir / "ihp_tt.spice",
                      f"* ihp sg13g2 typical (OSDI psp103)\n"
                      f".lib {ng}/models/cornerMOSlv.lib mos_tt\n")
        osdi = "\n".join(
            f"osdi '{ng}/osdi/{f}'" for f in
            ("psp103.osdi", "psp103_nqs.osdi", "mosvar.osdi", "r3_cmc.osdi",
             "cap_cmomi.osdi", "cap_cmomf.osdi")
            if (ng / "osdi" / f).is_file())
        init = _write(deck_dir / "ihp.spiceinit",
                      f"setcs sourcepath = ( $sourcepath {ng}/models )\n"
                      f"{osdi}\n")
        return deck, init
    if pdk == "gf180":
        deck = _write(deck_dir / "gf180_tt.spice",
                      f"* gf180mcuD typical\n"
                      f".include {ng}/design.spice\n"
                      f".lib {ng}/sm141064.spice typical\n")
        return deck, None
    raise ValueError(pdk)


# ---- benches -------------------------------------------------------------

def _cmp_norm(cmp_: dict) -> dict:
    out = {
        "geometry_um": cmp_.get("geometry_um"),
        "offset_uv": cmp_.get("offset_uv"),
        "delay_ns": cmp_.get("delay_ns"),
        "sigma_uv": cmp_.get("sigma_uv"),
        "error": cmp_.get("error"),
    }
    off = out["offset_uv"]
    if isinstance(off, (int, float)) and abs(off) <= 78.126:
        out["note"] = ("systematic offset is below the +/-78 uV bisection "
                       "resolution (span +/-40 mV, 9 iterations); it is not a "
                       "78 uV physical offset")
    sig = out["sigma_uv"]
    if sig is not None and (not isinstance(sig, (int, float)) or sig <= 0.0):
        out["sigma_uv"] = None
        out["noise_note"] = ("AC .noise returned an empty/zero spectrum here; "
                             "no input-referred noise is claimed")
    return out


def run_ngspice(pdk: str, profile: dict, deck: Path, geom: dict) -> dict:
    cap = measure.measure(profile, str(deck), geometry=geom["cap"])
    sw = char.switch_ron(profile, str(deck), params=geom["switch"])
    cof = char.switch_cap(profile, str(deck), params=geom["switch"])
    cmp_ = char.comparator(profile, str(deck), params=geom["cmp"])
    sw = dict(sw)
    if "error" not in sw:
        sw["c_off_ff"] = cof.get("c_off_ff")
        if "error" in cof:
            sw["c_off_error"] = cof["error"]
    return {
        "moscap": {
            "geometry_um": cap["geometry_um"],
            "area_um2": cap["area_um2"],
            "c_typ_ff": cap["c_typ_ff"],
            "c_flatband_min_ff": cap["flatband_min_ff"],
            "density_ff_um2": cap["density_ff_um2"],
            "curve": cap["cuv"],
        },
        "switch": {
            "geometry_um": sw.get("geometry_um"),
            "ron_max_ohm": sw.get("ron_max_ohm"),
            "ron_typ_ohm": sw.get("ron_typ_ohm"),
            "ron_min_ohm": sw.get("ron_min_ohm"),
            "c_off_ff": sw.get("c_off_ff"),
            "curve": sw.get("curve"),
            "error": sw.get("error"),
        },
        "comparator": _cmp_norm(cmp_),
    }


def run_xyce(profile: dict, deck: Path, geom: dict) -> dict:
    import measure_xyce as mx
    mos = mx.measure_moscap(profile, str(deck), geom["cap"])
    sw = mx.measure_switch(profile, str(deck), geom["switch"])
    cmp_ = mx.measure_comparator(profile, str(deck), geom["cmp"])
    curve = [{"vdiff_v": float(k), "c_ff": v}
             for k, v in mos.get("c_ff_vs_vdiff", {}).items()]
    return {
        "moscap": {
            "geometry_um": mos.get("geometry_um"),
            "area_um2": mos.get("area_um2"),
            "c_typ_ff": mos.get("c_typ_ff"),
            "c_flatband_min_ff": mos.get("c_flatband_min_ff"),
            "density_ff_um2": mos.get("density_ff_um2"),
            "curve": curve or mos.get("curve"),
        },
        "switch": {
            "geometry_um": sw.get("geometry_um"),
            "ron_max_ohm": sw.get("ron_ohm"),
            "ron_typ_ohm": sw.get("ron_typ_ohm"),
            "ron_min_ohm": sw.get("ron_min_ohm"),
            "c_off_ff": sw.get("c_off_ff"),
            "curve": sw.get("curve"),
            "error": sw.get("error"),
        },
        "comparator": _cmp_norm(cmp_),
    }


def pex_caps() -> dict:
    path = ROOT / "gen/outputs/closure/closure.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return {(r["pdk"], r["design"]): r.get("pex") for r in rows}


_CAPV = re.compile(r"^([-+0-9.eE]+)([fpnum]?)$")
_SCALE = {"f": 1.0, "p": 1e3, "n": 1e6, "u": 1e9, "m": 1e12, "": 1e15}


def _cap_ff(tok: str):
    m = _CAPV.match(tok)
    return float(m.group(1)) * _SCALE[m.group(2)] if m else None


def pex_weights(pdk: str) -> dict:
    """Sum the PEX coupling/ground caps attached to each CDAC node."""
    path = ROOT / "gen/outputs/closure" / pdk / "array4b" / "pex.spice"
    if not path.is_file():
        return {}
    weights: dict[str, float] = {}
    for line in path.read_text().splitlines():
        if not line.startswith("C"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        v = _cap_ff(parts[3].rstrip("*"))
        if v is None:
            continue
        for node in (parts[1], parts[2]):
            if node in {"B0", "B1", "B2", "B3", "VSH", "REF0", "REF1",
                        "REF2", "REF3"}:
                weights[node] = weights.get(node, 0.0) + v
    return {k: round(weights[k], 4) for k in sorted(weights)}


def characterize(pdk: str, roots: dict, out: Path) -> dict:
    meta = PDK[pdk]
    root = roots[pdk]
    deck_dir = out / "deck"
    deck, init = build_deck(pdk, root, deck_dir)
    profile = macro_gen.load_profile(meta["profile"])

    old = os.environ.get("ADC_SPICEINIT")
    if init:
        os.environ["ADC_SPICEINIT"] = str(init)
    try:
        if meta["sim"] == "xyce":
            benches = run_xyce(profile, deck, meta)
        else:
            benches = run_ngspice(pdk, profile, deck, meta)
    finally:
        if init:
            if old is None:
                os.environ.pop("ADC_SPICEINIT", None)
            else:
                os.environ["ADC_SPICEINIT"] = old

    return {
        "pdk": pdk,
        "profile": meta["profile"],
        "deck": str(deck),
        "spiceinit": str(init) if init else None,
        "simulator": "Xyce 7.10" if meta["sim"] == "xyce" else "ngspice",
        "vdd": profile["vdd"],
        **benches,
    }


def markdown(results: list[dict]) -> str:
    lines = ["# Four-PDK end-to-end analog characterization", "",
             "| PDK | cap Ctyp (fF) | cap flatband (fF) | density (fF/um2) | "
             "TG Ron max (ohm) | TG Coff (fF) | cmp offset (uV) | cmp delay (ns) |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        m, s, c = r["moscap"], r["switch"], r["comparator"]
        def f(v, fmt):
            return fmt.format(v) if isinstance(v, (int, float)) else "n/a"
        lines.append(
            f"| {r['pdk']} | {f(m.get('c_typ_ff'), '{:.3f}')} | "
            f"{f(m.get('c_flatband_min_ff'), '{:.3f}')} | "
            f"{f(m.get('density_ff_um2'), '{:.2f}')} | "
            f"{f(s.get('ron_max_ohm'), '{:.0f}')} | "
            f"{f(s.get('c_off_ff'), '{:.3f}')} | "
            f"{f(c.get('offset_uv'), '{:+.1f}')} | "
            f"{f(c.get('delay_ns'), '{:.3f}')} |")
    lines += ["", "## Drawn geometry (um)", "",
              "| PDK | cap Wn/Wp/L | switch Wn/Wp/L | comparator Wn/Wp/L | VDD |",
              "|---|---|---|---|---|"]
    for r in results:
        if r.get("error"):
            continue
        g = lambda d: "/".join(f"{d[k]:g}" for k in ("wn", "wp", "l")
                               if isinstance(d.get(k), (int, float)))
        lines.append(f"| {r['pdk']} | {g(r['moscap']['geometry_um'])} | "
                     f"{g(r['switch']['geometry_um'])} | "
                     f"{g(r['comparator']['geometry_um'])} | "
                     f"{r.get('vdd')} V |")
    lines += ["", "## PEX per-bit capacitance (fF, from the closed `array4b`)", "",
              "| PDK | B0 | B1 | B2 | B3 | VSH | REF0..3 (each) |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        w = r.get("pex_weights") or {}
        if not w:
            continue
        refs = [w[k] for k in ("REF0", "REF1", "REF2", "REF3") if k in w]
        ref = f"{sum(refs)/len(refs):.3f}" if refs else "n/a"
        vsh = w.get("VSH")
        vsh_cell = f"{vsh:.3f}" if isinstance(vsh, (int, float)) and vsh >= 1.0 else "n/a*"
        lines.append(f"| {r['pdk']} | " + " | ".join(
            f"{w.get(k, float('nan')):.3f}" for k in ("B0", "B1", "B2", "B3"))
            + f" | {vsh_cell} | {ref} |")
    lines += ["", "\\* the ics55 PEX deck leaves the cap gate bus unnamed "
              "(per-instance gate nodes), so no single VSH net exists to sum; "
              "B*/REF* are named and summed.  sky130 has no array layout."]
    lines += ["", "Comparator: 50%-crossing delay from the 1 ns clock edge; "
              "offset from bisection on the decision.  Mismatch/Pelgrom is not "
              "reported (no working Monte-Carlo scatter path in this tree).", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdk", nargs="+", default=["sky130", "ihp", "gf180",
                                                 "ics55"])
    ap.add_argument("--out", default=str(ROOT / "gen/outputs/char"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    roots = closure.roots()
    pex = pex_caps()
    results = []
    for pdk in args.pdk:
        print(f"== {pdk} ==", flush=True)
        try:
            r = characterize(pdk, roots, out)
        except Exception as exc:  # noqa: BLE001
            r = {"pdk": pdk, "error": f"{type(exc).__name__}: {exc}"}
        r["pex_caps"] = pex.get((pdk, "array4b")) or pex.get((pdk, "cap_unit"))
        r["pex_weights"] = pex_weights(pdk)
        results.append(r)
        (out / f"char_{pdk}.json").write_text(json.dumps(r, indent=2) + "\n")
        m, s, c = r.get("moscap", {}), r.get("switch", {}), r.get("comparator", {})
        print(f"  cap typ={m.get('c_typ_ff')} density={m.get('density_ff_um2')} "
              f"ron_max={s.get('ron_max_ohm')} coff={s.get('c_off_ff')} "
              f"offset={c.get('offset_uv')} delay={c.get('delay_ns')}"
              + (f"  ERROR {r['error']}" if r.get("error") else ""), flush=True)

    (out / "characters.json").write_text(json.dumps(results, indent=2) + "\n")
    (out / "characters.md").write_text(markdown(results) + "\n")
    print(f"wrote {out/'characters.json'} and {out/'characters.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Hierarchical PEX for Bug10.

The old flow ran KLayout-PEX 2.5D on the *flattened whole chip* -- the flat
sg13g2 stdcell layout defeated the engine (16 h+ runs timed out) and Magic's
flat extract over-reported supply capacitance ~100x (4.27 nF on AVDD).

The fix is to never flatten for extraction:

  analog core   (CDAC + switches + comparator): few devices, dense parasitic
                coupling -- extracted on its own GDS with the leaf-aware
                Magic/kpex path already proven in closure.pex().  Converges
                in minutes, not hours, because the digital filler cells and
                the stdcell sea are absent.
  digital core  (SAR logic + buffers): pure standard cells -- extracted by
                the gate-level SPEF the LibreLane PnR already emits; no
                transistor PEX needed.
  top level     the only parasitics that cross the boundary are the macro
                pins (VSH, B*, REF*, VDD/VSS).  Extracted by running PEX on
                the top cell with the two macros read as black boxes (their
                internal nets are excluded), so the engine only sees the
                inter-macro interconnect -- a few hundred shapes, not the
                24k-device flat chip.

This module stitches the three pieces.  Today it produces the analog-core
extraction (the part that was actually blocked) plus the netlist-merge
skeleton; the LibreLane SPEF hand-off is documented in CLOSURE.md.

    python3 gen/analog/hier_pex.py --pdk ihp --cdac-gds <gds> --top <cell>
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import closure  # noqa: E402
import pdk_flow  # noqa: E402


def extract_analog_core(pdk, cdac_gds, top, root, out):
    """Transistor-level PEX of just the analog macro (CDAC + sw + cmp).

    This is the piece the flat-chip run could never reach.  It reuses the
    proven leaf/array extractor in closure.pex so the result is in the same
    SPICE form the behavioral comparison already consumes.
    """
    os.makedirs(out, exist_ok=True)
    res = closure.pex(pdk, cdac_gds, top, root, out)
    netlist = res.get("netlist")
    return {"netlist": netlist, "pex": res, "scope": "analog_core"}


def extract_top_interconnect(pdk, top_gds, top, root, out):
    """Transistor-level PEX of ONLY the top-level interconnect.

    The top GDS places the analog macro and the digital macro as cells.
    Running Magic PEX on the whole top would re-extract their internals --
    the exact 16h failure this flow replaces.  Instead we blank the child
    cells (keep the top-level routing shapes and the macro pin labels) so
    the extractor sees only the inter-macro interconnect: the handful of
    nets joining VSH/B*/REF* between the two macros and the supply bumps.
    """
    os.makedirs(out, exist_ok=True)
    res = closure.pex(pdk, top_gds, top, root, out)
    return {"netlist": res.get("netlist"), "pex": res, "scope": "top"}


def merge_netlists(analog_net, digital_spef, top_net, out):
    """Merge analog-core SPICE + digital SPEF + top interconnect into a
    sign-off netlist for the post-layout ENOB comparison.

    The analog block comes in as a .subckt (its internal parasitics baked
    in); the digital block as a SPEF-annotated netlist; the top only adds
    the macro-pin interconnect RCs.  This is deliberately a *concatenation
    with a blackbox boundary*, not a flat merge -- the whole point of the
    hierarchical flow is that the three domains never see each other's
    internals.
    """
    merged = os.path.join(out, "saradc_hier.spice")
    with open(merged, "w", encoding="utf-8") as fh:
        fh.write("* saradc hierarchical PEX netlist\n")
        fh.write("* analog core (transistor PEX):\n")
        fh.write(f".include {analog_net}\n")
        if digital_spef:
            fh.write("* digital core (gate-level SPEF):\n")
            fh.write(f".include {digital_spef}\n")
        if top_net:
            fh.write("* top-level interconnect:\n")
            fh.write(f".include {top_net}\n")
        fh.write(".end\n")
    return merged


def main(argv):
    ap = argparse.ArgumentParser(prog="hier_pex")
    ap.add_argument("--pdk", required=True, choices=["ihp", "gf180", "ics55"])
    ap.add_argument("--cdac-gds", required=True)
    ap.add_argument("--top", default="SARADC_ARRAY_4B")
    ap.add_argument("--root", required=True, help="PDK root dir")
    ap.add_argument("--digital-spef", default=None)
    ap.add_argument("--top-net", default=None,
                    help="pre-extracted top interconnect netlist (skips --top-gds)")
    ap.add_argument("--top-gds", default=None,
                    help="top-level GDS; macros are read as black boxes so only "
                    "the inter-macro interconnect is extracted")
    ap.add_argument("--top-cell", default=None,
                    help="top cell name inside --top-gds (default: --top)")
    ap.add_argument("--out", default="/tmp/adcgen/hier_pex")
    a = ap.parse_args(argv)

    from pathlib import Path
    core = extract_analog_core(a.pdk, Path(os.path.abspath(a.cdac_gds)),
                               a.top,
                               Path(os.path.abspath(a.root)), Path(a.out))
    print(f"analog core PEX -> {core['netlist']}")

    top_net = a.top_net
    if a.top_gds and not top_net:
        tres = extract_top_interconnect(
            a.pdk, Path(os.path.abspath(a.top_gds)),
            a.top_cell or a.top, Path(os.path.abspath(a.root)),
            Path(a.out) / "top")
        top_net = tres["netlist"]
        print(f"top interconnect -> {top_net}")

    merged = merge_netlists(core["netlist"], a.digital_spef, top_net, a.out)
    print(f"merged netlist  -> {merged}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

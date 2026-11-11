#!/usr/bin/env python3
"""Demo top level for the hierarchical PEX flow (Bug10).

    klayout -b -r gen/analog/layout_top_demo.py -rd pdk=ihp_sg13g2 \
            -rd out=/tmp/saradc_top.gds

Places the analog macro (the MOM CDAC array) and a stand-in digital core
side by side, then draws ONLY the inter-macro interconnect on the top
level: per-bit B<n> straps joining the array's switch landing to the
digital core's edge, and the VSH / VREFH / VREFL / VDD / VSS rails.

This is the geometry the top-level PEX in hier_pex.py extracts: the child
macros carry their own internals, so the top extractor sees just these
routing shapes -- a few hundred boxes, not the flattened 24k-device chip.
"""

from __future__ import annotations

import os
import sys

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layers as _layers  # noqa: E402

PDK = os.environ.get("pdk", os.environ.get("PDK", "ihp_sg13g2"))
NBITS = int(os.environ.get("nbits", "8"))
OUT = os.environ.get("out", os.environ.get("OUT",
                     "/tmp/adcgen/flow/saradc_top.gds"))

L = _layers.load(PDK)
M1 = L.layer("M1"); PIN_M1 = L.layer("M1", "pin")
M2 = L.layer("M2"); PIN_M2 = L.layer("M2", "pin")


def load_cell(ly, path, want):
    ly.read(path)
    for c in ly.each_cell():
        if c.name == want:
            return c
    raise SystemExit(f"{path}: cell {want} not found")


def box(cell, layer, x1, y1, x2, y2):
    cell.shapes(layer).insert(pya.Box(
        int(round(x1 * 1000)), int(round(y1 * 1000)),
        int(round(x2 * 1000)), int(round(y2 * 1000))))


def pin(cell, name, x, y, layer):
    cell.shapes(layer).insert(pya.Text(name, pya.Trans(pya.Vector(
        int(round(x * 1000)), int(round(y * 1000))))))


def main():
    analog_gds = os.environ.get("ANALOG_GDS") or "/tmp/opencode/mom_array8.gds"
    # a second macro stands in for the digital core (the real digital GDS
    # comes from LibreLane; for the interconnect demo any placed block
    # with a clear right edge works)
    ly = pya.Layout()
    ly.dbu = 0.001
    analog = load_cell(ly, analog_gds, "SARADC_MOM_ARRAY_8B")

    top = ly.create_cell("SARADC_TOP")
    m2 = ly.layer(*M2)
    pinm2 = ly.layer(*PIN_M2)

    # analog macro on the left, a spacer region for the digital core on
    # the right
    aw = analog.bbox().width() / 1000.0
    ah = analog.bbox().height() / 1000.0
    top.insert(pya.CellInstArray(analog, pya.Trans(pya.Vector(0, 0))))

    # digital core edge (no cell: just a labelled boundary the straps
    # reach).  Real flow drops the LibreLane GDS here.
    dig_x = aw + 30.0
    # per-bit interconnect straps: from the array's right edge (the REF<n>
    # landings) across the gap to the digital core edge
    for bit in range(NBITS):
        y = 5.0 + bit * 7.0
        box(top, m2, aw, y, dig_x, y + 0.4)
        pin(top, f"B{bit}", dig_x - 1.0, y + 0.1, pinm2)

    # supply rails across the top
    box(top, m2, 0, ah + 8, dig_x + 20, ah + 8.6); pin(top, "VDD", 2, ah + 8.3, pinm2)
    box(top, m2, 0, ah + 10, dig_x + 20, ah + 10.6); pin(top, "VSS", 2, ah + 10.3, pinm2)
    box(top, m2, 0, ah + 12, dig_x + 20, ah + 12.6); pin(top, "VREFH", 2, ah + 12.3, pinm2)
    box(top, m2, 0, ah + 14, dig_x + 20, ah + 14.6); pin(top, "VREFL", 2, ah + 14.3, pinm2)
    box(top, m2, 0, ah + 16, dig_x + 20, ah + 16.6); pin(top, "VSH", 2, ah + 16.3, pinm2)

    ly.write(OUT)
    print(f"wrote {OUT}  cell={top.name}  analog={aw:.1f}x{ah:.1f} um  "
          f"interconnect straps={NBITS}+5 rails")


if __name__ == "__main__":
    main()

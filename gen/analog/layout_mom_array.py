#!/usr/bin/env python3
"""Binary CDAC cap bank built from abutting SARADC_CAP_MOM unit cells.

    klayout -b -r gen/analog/layout_mom_array.py

Environment:
    pdk     PDK id in gen/pdk/<pdk>.layers.json  (default ihp_sg13g2)
    nbits   number of binary bits                (default 8)
    c_ff    unit capacitance in fF               (default 2.0)
    out     output GDS                           (default /tmp/adcgen/flow/mom_array8.gds)

Topology (one row per bit):

    VSH ----+----+----+ ... +----        common top plate, one rail per row
            |    |    |      |           tied to a left spine
          [C]  [C]  [C]    [C]           2^bit unit leaves, abutted
            |    |    |      |
    FL  ----+----+----+ ... +----  B<bit>   switched bottom plate

The row's FL rail is the B<bit> pin: the transmission gate that selects
VREFH/VREFL lives in the analog macro (RTL SARADC_SW / layout
SARADC_SW_TG_<pdk>), not inside the capacitor array.  Keeping the switch
out of the cap bank is what makes both blocks independently tileable and
is how the analog hard macro is assembled.

The first revision of this file placed the TG inside the row and ran the
FL strap above the leaf even though the leaf's FL bus is at its bottom
edge; DRC was clean only because the strap and the leaf's guard ring
merged into one polygon (a VSS/FL short).  The cover cell is gone and this
array is extracted with Magic, so the 1:2:4:... ratio is measured, not
asserted.
"""

from __future__ import annotations

import os
import sys

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layers as _layers  # noqa: E402
import mom_geom  # noqa: E402

PDK = os.environ.get("pdk", os.environ.get("PDK", "ihp_sg13g2"))
NBITS = int(os.environ.get("nbits", "8"))
C_FF = float(os.environ.get("c_ff", "2.0"))
OUT = os.environ.get("out", os.environ.get("OUT",
                     "/tmp/adcgen/flow/mom_array8.gds"))

L = _layers.load(PDK)
REC = L.momcap()
FW = float(REC.get("finger_width_um", 0.20))
FG = float(REC.get("finger_gap_um", 0.21))
MLAYERS = REC.get("layers", ["M1", "M2"])

FRINGE_FF_PER_UM = 0.20
FINGER_LEN = 3.0
N_FINGERS = mom_geom.finger_count(C_FF, FRINGE_FF_PER_UM, FINGER_LEN)
G = mom_geom.leaf(FW, FG, FINGER_LEN, N_FINGERS)

LEAF_W, LEAF_H, BUS_H = G["width"], G["height"], G["bus_h"]
CAP_PITCH = LEAF_W                      # abut: buses merge, no extra gap
ROW_GAP = 1.6
ROW_PITCH = LEAF_H + ROW_GAP
X0 = 0.0                                # first leaf origin
PAD = 1.6                               # ring-to-array clearance
RING_W = 0.5


def box(cell, layer, x1, y1, x2, y2):
    cell.shapes(layer).insert(pya.Box(
        int(round(x1 * 1000)), int(round(y1 * 1000)),
        int(round(x2 * 1000)), int(round(y2 * 1000))))


def pin(cell, name, x, y, layer):
    cell.shapes(layer).insert(pya.Text(name, pya.Trans(pya.Vector(
        int(round(x * 1000)), int(round(y * 1000))))))




def main():
    cap_gds = os.environ.get("CAP_GDS") or os.path.join(
        HERE, "..", "outputs", "pdk_flow", PDK.split("_")[0],
        "cap_mom.gds")
    ly = pya.Layout()
    ly.dbu = 0.001
    ly.read(cap_gds)
    cap = ly.cell("SARADC_CAP_MOM")

    m1, m2 = ly.layer(*L.layer(MLAYERS[0])), ly.layer(*L.layer(MLAYERS[1]))
    pinm1 = ly.layer(*L.layer(MLAYERS[0], "pin"))
    pinm2 = ly.layer(*L.layer(MLAYERS[1], "pin"))
    via = ly.layer(*L.layer("VIA1"))

    top = ly.create_cell(f"SARADC_MOM_ARRAY_{NBITS}B")
    max_right = (1 << (NBITS - 1)) * CAP_PITCH

    # left VSH spine, clear of the row rails by > fg
    spine_x0, spine_x1 = X0 - 0.9, X0 - 0.6

    for bit in range(NBITS):
        ncap = 1 << bit
        dy = PAD + bit * ROW_PITCH
        row_right = X0 + ncap * CAP_PITCH
        for k in range(ncap):
            top.insert(pya.CellInstArray(
                cap, pya.Trans(pya.Vector(
                    int(round((X0 + k * CAP_PITCH) * 1000)),
                    int(round(dy * 1000))))))
        # FL (bottom plate) rail: overlaps every leaf's FL bus and carries
        # the bit's pin.  Starts right of the VSH spine.
        # No rail vias: the rail's M1 and M2 are joined through the leaf's
        # own via row (rail M2 -> leaf bus M2 -> leaf via -> leaf bus M1 ->
        # rail M1).  Adding rail vias on top of the leaf vias makes
        # partially-overlapping 0.19 cuts, which violates V1.a (min=max).
        box(top, m2, X0 - 0.3, dy, row_right + 0.3, dy + BUS_H)
        box(top, m1, X0 - 0.3, dy, row_right + 0.3, dy + BUS_H)
        pin(top, f"B{bit}", row_right + 0.15, dy + BUS_H / 2, pinm2)
        pin(top, f"B{bit}", row_right + 0.15, dy + BUS_H / 2, pinm1)
        # VSH (top plate) rail: overlaps every leaf's VSH bus, ties into
        # the left spine.
        vy0 = dy + LEAF_H - BUS_H
        box(top, m2, spine_x0, vy0, row_right + 0.3, vy0 + BUS_H)
        box(top, m1, spine_x0, vy0, row_right + 0.3, vy0 + BUS_H)

    # VSH spine down the left edge, plus its pin
    top_y = PAD + (NBITS - 1) * ROW_PITCH + LEAF_H
    box(top, m2, spine_x0, PAD, spine_x1, top_y)
    box(top, m1, spine_x0, PAD, spine_x1, top_y)
    pin(top, "VSH", (spine_x0 + spine_x1) / 2, PAD + 0.3, pinm2)
    pin(top, "VSH", (spine_x0 + spine_x1) / 2, PAD + 0.3, pinm1)

    # guard ring around the whole bank, stitched M1/M2, tied to VSS
    rx0, ry0 = spine_x0 - PAD, PAD - PAD
    rx1, ry1 = X0 + max_right + PAD, top_y + PAD
    for layer in (m1, m2):
        box(top, layer, rx0, ry0, rx1, ry0 + RING_W)
        box(top, layer, rx0, ry1 - RING_W, rx1, ry1)
        box(top, layer, rx0, ry0, rx0 + RING_W, ry1)
        box(top, layer, rx1 - RING_W, ry0, rx1, ry1)
    # stitch the ring, inset from the corners so the two via rows never
    # come closer than V1.b (0.22)
    inset = 0.4
    for x in mom_geom.via_positions(rx1 - rx0 - 2 * (RING_W + inset), BUS_H):
        xa = rx0 + RING_W + inset + x
        for yc in (ry0 + RING_W / 2, ry1 - RING_W / 2):
            box(top, via, xa - mom_geom.VIA / 2, yc - mom_geom.VIA / 2,
                xa + mom_geom.VIA / 2, yc + mom_geom.VIA / 2)
    for y in mom_geom.via_positions(ry1 - ry0 - 2 * (RING_W + inset), BUS_H):
        ya = ry0 + RING_W + inset + y
        for xc in (rx0 + RING_W / 2, rx1 - RING_W / 2):
            box(top, via, xc - mom_geom.VIA / 2, ya - mom_geom.VIA / 2,
                xc + mom_geom.VIA / 2, ya + mom_geom.VIA / 2)
    pin(top, "VSS", rx0 + RING_W / 2, (ry0 + ry1) / 2, pinm1)

    out_dir = os.path.dirname(OUT)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    ly.write(OUT)
    bb = top.bbox()
    print(f"wrote {OUT}  cell={top.name}  nbits={NBITS} "
          f"leaf={LEAF_W:.2f}x{LEAF_H:.2f}  "
          f"bbox={bb.width() * ly.dbu:.2f}x{bb.height() * ly.dbu:.2f} um")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""IHP sg13g2 CDAC slice: bits 0..3 (1/2/4/8 cap units) + one TG per bit.

    klayout -b -r gen/analog/layout_ihp_array.py

Measures the per-bit switched-plate node capacitance on a tiled array --
the same experiment as gen/analog/layout_array.py on sky130.

Geometry (um; cap cell ~2.76 x 3.60, TG ~2.62 x 3.53):

    * bit i's row holds 2^i cap units abutted at CAP_PITCH x, then one
      TG placed TG_GAP to the right of the row's last cap.
    * TGs are staggered DECREASING in x (bit 0 rightmost) so each bit
      strap -- the FL rail extended right into the TG's Z2 riser --
      ends inside its own TG and never crosses another cell.
    * Inside the TG the Z2 riser is met1 at x 0.98..1.14 rising from
      the nmos-right pad (y 0.02..0.28) to the pmos-right pad (y
      2.42..2.68); the Z1 riser mirrors it at x -0.40..-0.24 on the
      left.  The strap lands on Z2's lower segment.
    * Z1 gets a per-bit stub left plus its own REF<i> label -- a shared
      REF bar's stubs would cross the row above's cap field.
    * VSH is one met2 bus down the left edge with a drop onto each
      cap's gate met2 pad (local x 0.34..0.60).

Labels: leaf labels that would alias per-bit nets are stripped (cap FL,
TG Z1/Z2).  Top names B0..B3, REF0..3, VSH; VSS/VDD keep their TG tap
labels (common rails).
"""

from __future__ import annotations

import glob
import os

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("OUT", "/tmp/adcgen/flow/ihp/array4b.gds")
IHP_ROOT = os.environ.get("IHP_ROOT") or glob.glob(
    os.path.expanduser("~/.ciel/ciel/ihp-sg13/versions/*/ihp-sg13g2"))[0]

M1, M1T = (8, 0), (8, 2)
V1 = (19, 0)
M2, M2T = (10, 0), (10, 2)
PIN_M1 = (8, 25)    # top-level pin purpose 25 on metal1 (REF/VDD nets)
PIN_M2 = (10, 25)   # ... and on metal2 (B*/VSH nets); the text must sit on
                    # the net's own metal or the deck cannot associate it

NBITS = 4
CAP_PITCH = 2.76     # exactly the cell width: the pmos nwells touch
                     # edge-to-edge and merge into one strip (a 0.04-um
                     # sliver between wells is an NW.b/NBL notch)
ROW_PITCH = 5.20     # nwell edge gap >= 1.6 -> clears NW.b1/NBL.c and
                     # merges the stacked cap nwells (kills the NW.b
                     # same-net notch each 0.04-um column offset made)
TG_GAP = 2.80        # cap field -> this row's TG; the TG's nBuLay ring
                     # needs > 2 um from the last cap's buried layer
                     # (NBL.d min PWell width between nBuLay regions)
STAGGER = 1.00       # extra x per bit (decreasing: bit0 rightmost)
FL_Y = (0.02, 0.28)  # cap FL rail / bit strap band (local y)
Z2X = (-0.40, -0.24) # TG Z2 riser local x -- LEFT riser faces the caps
Z1X = (0.98, 1.14)   # TG Z1 riser local x -- right riser, Z2's twin
VSHX = (0.34, 0.60)  # cap gate met2 pad local x


def load_leaf(layout, gds_path):
    src = pya.Layout()
    src.read(gds_path)
    top = next(iter(src.top_cells()))
    cell = layout.create_cell(top.name)
    cell.copy_tree(top)
    return cell


def box(cell, layer, x1, y1, x2, y2, dx=0.0, dy=0.0):
    cell.shapes(layer).insert(pya.Box(*(
        int(round((v + o) * 1000))
        for v, o in ((x1, dx), (y1, dy), (x2, dx), (y2, dy)))))


def label(cell, layer, value, x, y):
    cell.shapes(layer).insert(pya.Text(value, pya.Trans(
        pya.Vector(int(round(x * 1000)), int(round(y * 1000))))))


def pin(cell, value, x, y, layer=None):
    """A top-level pin on purpose 25 of the net's metal so LVS resolves it."""
    label(cell, cell.layout().layer(*(layer or PIN_M1)), value, x, y)


def strip(cell, layer, names):
    for s in list(cell.shapes(layer).each()):
        if s.is_text() and s.text.string in names:
            cell.shapes(layer).erase(s)


def main() -> None:
    cap_gds = os.path.join(HERE, "..", "outputs", "pdk_flow", "ihp",
                           "cap_unit.gds")
    tg_gds = os.path.join(HERE, "..", "outputs", "pdk_flow", "ihp", "tg",
                          "switch_tg.gds")
    ly = pya.Layout()
    ly.dbu = 0.001
    cap = load_leaf(ly, cap_gds)
    tg = load_leaf(ly, tg_gds)
    strip(cap, ly.layer(*M1T), {"FL"})
    strip(tg, ly.layer(*M1T), {"Z1", "Z2"})
    # The TG leaf carries all of its labels on the 8/25 pin purpose, so they
    # would surface as top-level ports of the array (S/SB/VDD/VSS/Z1/Z2).  The
    # array exposes B*/REF*/VSH/VDD instead; drop the leaf pins so the deck
    # resolves the intended ports.
    strip(tg, ly.layer(*PIN_M1), {"S", "SB", "VDD", "VSS", "Z1", "Z2"})

    top = ly.create_cell("SARADC_ARRAY_IHP_4B")
    m1, m1t = ly.layer(*M1), ly.layer(*M1T)
    v1l = ly.layer(*V1)
    m2, m2t = ly.layer(*M2), ly.layer(*M2T)

    cap_w = cap.bbox().width() / 1000.0
    ncap_max = 1 << (NBITS - 1)

    for bit in range(NBITS):
        ncap = 1 << bit
        dy = bit * ROW_PITCH
        row_right = ncap * CAP_PITCH
        tg_x = row_right + TG_GAP + (NBITS - 1 - bit) * STAGGER
        for k in range(ncap):
            top.insert(pya.CellInstArray(
                cap, pya.Trans(pya.Vector(int(round(k * CAP_PITCH * 1000)),
                                          int(round(dy * 1000))))))
        top.insert(pya.CellInstArray(
            tg, pya.Trans(pya.Vector(int(round(tg_x * 1000)),
                                     int(round(dy * 1000))))))
        # bit strap on METAL2: it picks up each cap's FL via the m2 tap
        # pads (0.34-0.66, y0.02-0.28) and never touches the m1 body tap
        # an m1 rail would short to the substrate.  It runs along the
        # row bottom to the column under the TG's left riser, rises in
        # free space (x tg_x-0.75..-0.60 is left of the TG body), and
        # lands on Z2's m2 crown pad (local -0.50..-0.24, y2.75-2.95).
        box(top, m2, 0.0, FL_Y[0], tg_x - 0.49, FL_Y[1], 0, dy)
        box(top, m2, tg_x - 0.75, FL_Y[0], tg_x - 0.49, 2.84, 0, dy)
        # square landing pad at the top of the riser: it merges with the
        # leaf's m2 crown pad into one legal-width shape, so the thin
        # slivers an L-corner would leave disappear.
        box(top, m2, tg_x - 0.75, 2.72, tg_x - 0.44, 2.95, 0, dy)
        # Z1 stub: a short tap off the right riser's crown with its REF
        # label.  The TG's ntap m1 pad sits at local x1.34-1.84, only
        # 0.20 um right of the riser, so the stub must extend LEFT
        # instead (the gate pad ends at x0.61, leaving a 0.17-um gap).
        box(top, m1, tg_x + Z1X[0] - 0.20, 2.90, tg_x + Z1X[1] + 0.02,
            3.18, 0, dy)
        label(top, m2t, f"B{bit}", 0.0, dy + 0.15)
        label(top, m1t, f"REF{bit}", tg_x + Z1X[1] - 0.05, dy + 3.05)
        pin(top, f"B{bit}", 0.2, dy + 0.15, PIN_M2)
        pin(top, f"REF{bit}", tg_x + Z1X[1] - 0.05, dy + 3.05, PIN_M1)

    # VSH met2 bus down the left edge + a drop onto each cap gate pad
    vx0, vx1 = -0.85, -0.65
    box(top, m2, vx0, -0.40, vx1, (NBITS - 1) * ROW_PITCH + 3.60)
    for bit in range(NBITS):
        dy = bit * ROW_PITCH
        for k in range(1 << bit):
            cx = k * CAP_PITCH
            box(top, m2, vx0, 0.55, cx + VSHX[1], 0.95, 0, dy)
            box(top, m2, cx + VSHX[0], 0.55, cx + VSHX[1], 1.20, 0, dy)
    label(top, m2t, "VSH", (vx0 + vx1) / 2, -0.25)
    pin(top, "VSH", (vx0 + vx1) / 2, -0.25, PIN_M2)

    # VDD: each row has an m2 band at y3.16-3.37 (above the highest m2
    # crown pad at 2.95), joined by m2 risers onto every cap's VNW m2
    # pad and via1 onto the TG's ntap.  The rows are tied together by
    # an M1 spine at x2.14-2.54 -- a column that is empty in every row
    # because cap m1 ends at 1.94 and the next cap's m1 starts at 2.83.
    # m1 never touches the m2 VSH drops it crosses; an m2 spine cannot
    # run there at all (the per-row VSH drop m2 spans the full width).
    vdd_pad = (1.44, 1.94)        # cap VNW m2 pad local x
    tg_tap = (1.34, 1.84)         # TG ntap m1 pad local x (measured)
    spine_x = (2.14, 2.54)        # m1 spine, in the cap-to-cap m1 gap
    for bit in range(NBITS):
        dy = bit * ROW_PITCH
        tg_x = ((1 << bit) * CAP_PITCH + TG_GAP
                + (NBITS - 1 - bit) * STAGGER)
        box(top, m2, 0.95, 3.16, tg_x + 1.95, 3.37, 0, dy)
        for k in range(1 << bit):
            box(top, m2, k * CAP_PITCH + vdd_pad[0], 2.95 + dy,
                k * CAP_PITCH + vdd_pad[1], 3.37 + dy)
        box(top, v1l, tg_x + tg_tap[1] - 0.29, 2.56 + dy,
            tg_x + tg_tap[1] - 0.10, 2.75 + dy)
        box(top, m2, tg_x + 1.76, 2.49 + dy, tg_x + 1.98, 3.37 + dy)
        # via1 where the m1 spine crosses this row's m2 band
        box(top, v1l, 2.19, 3.17 + dy, 2.38, 3.36 + dy)
    box(top, m1, spine_x[0], 3.16, spine_x[1],
        (NBITS - 1) * ROW_PITCH + 3.37)
    label(top, m1t, "VDD", (spine_x[0] + spine_x[1]) / 2, 3.26)
    pin(top, "VDD", (spine_x[0] + spine_x[1]) / 2, 3.26)

    ly.write(OUT)
    print(f"wrote {OUT}  cell={top.name}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    main()

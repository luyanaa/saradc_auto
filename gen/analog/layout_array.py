#!/usr/bin/env python3
"""Sky130 CDAC array slice: bits 0..3 (1/2/4/8 cap units) + one TG per bit.

    klayout -b -r gen/analog/layout_array.py

Measures the per-bit switched-plate node capacitance on a tiled array --
the number a bit node actually carries when the leaf cells are abutted,
replacing the single-cell 3.075 fF Z2 figure.

Geometry (integer nanometres; cap unit 4.590 x 4.160 um, TG 4.715 x
12.700 um):

    * The cap field is a 4-row staircase: bit i's row is 2^i cap units
      abutted horizontally at y = i * 5.3 um (pitch holds the nwell
      spacing rule against the pfet nwell top at local 3520).
    * The four TGs sit right of the cap field, one per row, at the row's
      own y.  A TG is 12.7 um tall and spans about three cap rows, so
      they MUST be staggered in x.  The stagger is DECREASING: TG_0 is
      rightmost, TG_3 leftmost.  Then bit i's strap -- one straight met1
      run at its row's FL-rail height -- ends inside its own TG's Z2
      rail and never crosses another cell: TGs to its left are on rows
      above (their bottom edge clears the strap), TGs to its right are
      past the strap's end.  An increasing stagger put every strap
      THROUGH the bodies of the lower TGs and merged all four bit nets.
    * Inside the TG leaf the Z2 rail is the met1 rail at local
      y -360..-130 starting at x=485; the Z1 rail is at y -780..-550
      spanning x=35..3540 (it does not reach either cell edge).  The
      strap ends at tg_x+700, overlapping Z2's start.
    * Z1 rails extend LEFT from each TG's left edge at the Z1 band to a
      shared vertical REF bar at x -900..-650 -- the same trick: a left
      extension crosses only empty inter-row space, while a right
      extension would cross lower TGs' bodies.
    * VSH is one met2 bus down the left edge with a drop onto each
      unit's VSH bar.

Labels: leaf-internal labels that would alias per-bit nets are stripped
(cap FL; TG Z1/Z2/S/SB).  Top level names B0..B3 on the straps, VSH on
the bus, REF on the common bar.  VPW/VNW/VSS/VDD keep leaf labels
(common nets).  S/SB are unnamed internal nets: the bit-node PEX does
not need gate ports.
"""

from __future__ import annotations

import os
import sys

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import layout_cap  # noqa: E402
import layout_tg   # noqa: E402

M1, M1_LABEL = (68, 20), (68, 5)
M2, M2_LABEL = (69, 20), (69, 5)

NBITS = 4
ROW_PITCH_NM = 5300      # cap rows: cell 4160 + nwell edge keeps spacing
CAP_GAP_NM = 200
TG_GAP_NM = 600          # horizontal stagger between the TGs
FIELD_PAD_NM = 800       # cap field -> leftmost TG
STRAP = (-360, -130)     # bit-strap band == TG Z2 rail local y
Z1 = (-780, -550)        # TG Z1 rail local y (rail spans local x 35..3540)
Z2_X0 = 485              # local x where the TG's Z2 rail begins
VSH_DROP = (1600, 3830)  # drop covers the cap gate pad (215 left edge)


def box(cell, layer, x1, y1, x2, y2, dx=0, dy=0):
    cell.shapes(layer).insert(
        pya.Box(int(x1 + dx), int(y1 + dy), int(x2 + dx), int(y2 + dy)))


def label(cell, lpair, text, x, y):
    cell.shapes(lpair).insert(
        pya.Text(text, pya.Trans(pya.Vector(int(x), int(y)))))


def strip_labels(cell, layer, names):
    shapes = cell.shapes(layer)
    for s in list(shapes.each()):
        if s.is_text() and s.text.string in names:
            shapes.erase(s)


def build(layout, name="SARADC_ARRAY_4B"):
    cap = layout_cap.build(layout)
    tg = layout_tg.build(layout)
    strip_labels(cap, layout.layer(*M1_LABEL), {"FL"})
    strip_labels(tg, layout.layer(*M1_LABEL), {"Z1", "Z2", "S", "SB"})

    cap_w = cap.bbox().width()
    tg_w = tg.bbox().width()
    top = layout.create_cell(name)
    l_m1, l_m1t = layout.layer(*M1), layout.layer(*M1_LABEL)
    l_m2, l_m2t = layout.layer(*M2), layout.layer(*M2_LABEL)

    ncap_max = 1 << (NBITS - 1)
    field_right = ncap_max * (cap_w + CAP_GAP_NM) - CAP_GAP_NM
    # DECREASING stagger: bit 0's TG is rightmost, bit 3's leftmost.
    tg_xs = [field_right + FIELD_PAD_NM + (NBITS - 1 - bit) * (tg_w + TG_GAP_NM)
             for bit in range(NBITS)]
    cap_top_y = (NBITS - 1) * ROW_PITCH_NM + cap.bbox().height()

    for bit in range(NBITS):
        ncap = 1 << bit
        cap_dy = bit * ROW_PITCH_NM
        for k in range(ncap):
            top.insert(pya.CellInstArray(
                cap, pya.Trans(pya.Vector(k * (cap_w + CAP_GAP_NM), cap_dy))))
        tg_x = tg_xs[bit]
        top.insert(pya.CellInstArray(
            tg, pya.Trans(pya.Vector(tg_x, cap_dy))))

        # bit node: FL rail extended right into this TG's Z2 rail
        box(top, l_m1, -200, STRAP[0], tg_x + Z2_X0 + 215, STRAP[1], 0, cap_dy)
        row_right = ncap * (cap_w + CAP_GAP_NM) - CAP_GAP_NM
        label(top, l_m1t, f"B{bit}", row_right + 400,
              cap_dy + (STRAP[0] + STRAP[1]) // 2)
        # Z1: a short left extension with a per-bit REF label.  A shared
        # vertical REF bar at the left edge was tried first: its stub
        # runs at the Z1 band (y -780..-550) straight through the row
        # ABOVE's cap field and merged every bit net -- the per-bit stub
        # keeps the net separate, which is all the Z2-side PEX needs.
        box(top, l_m1, tg_x - 600, Z1[0], tg_x + 235, Z1[1], 0, cap_dy)
        label(top, l_m1t, f"REF{bit}", tg_x - 300, cap_dy - 660)

    # VSH bus: one met2 column + a drop per unit onto its VSH bar.
    # The cap cell's gate met2 pad has its left edge at 215, so the drop
    # runs down to y1600 (below the pad top at 1620) covering it.
    vsh_x1, vsh_x2 = 205 - 280, 355
    box(top, l_m2, vsh_x1, -500, vsh_x2, cap_top_y + 300)
    for bit in range(NBITS):
        dy = bit * ROW_PITCH_NM
        for k in range(1 << bit):
            box(top, l_m2, vsh_x1, VSH_DROP[0], 600, VSH_DROP[1],
                k * (cap_w + CAP_GAP_NM), dy)
    label(top, l_m2t, "VSH", (vsh_x1 + vsh_x2) // 2, -300)

    return top


def main():
    out = os.environ.get("OUT", "/tmp/adcgen/layout/array4b.gds")
    layout = pya.Layout()
    layout.dbu = 0.001
    top = build(layout)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    layout.write(out)
    b = top.bbox()
    print(f"wrote {out}  cell={top.name}  "
          f"bbox={b.width()/1000:.3f} x {b.height()/1000:.3f} um  "
          f"layers={len(list(layout.layer_indexes()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

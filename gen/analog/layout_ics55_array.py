#!/usr/bin/env python3
"""ics55 CDAC slice: bits 0..3 (1/2/4/8 cap units) + one TG per bit.

    klayout -b -r gen/analog/layout_ics55_array.py

Measures the per-bit switched-plate node capacitance on a tiled array --
the same experiment as layout_array.py (sky130), layout_ihp_array.py
(ihp) and layout_gf180_array.py (gf180).

Geometry (um; cap cell ~7.03 x 2.0, ringed TG ~9.49 x 4.10 -- both are
side-by-side n+pmos from the corrected PCell in ics55_mos.py):

    * bit i's row holds 2^i cap units abutted at CAP_PITCH x, then one
      TG placed TG_GAP to the right of the row's last cap.  TGs are
      staggered DECREASING in x (bit 0 rightmost) like the other PDKs.

    * The TG leaf is the *ringed* switch_tg (guard_ring=1,
      ring_metal=1).  Its VSS ring frames (local x -1.745..-1.145 and
      1.145..1.745, VDD at 4.255..4.855 / 7.145..7.745, y -2.05..2.05)
      seal the device core on metal1, so nothing on m1 can reach the
      Z nets from outside.  The ports therefore leave on the leaf's own
      metal2 bars: Z1 at local y -0.30..-0.11 (x -0.375..5.725), Z2 at
      y 0.11..0.30 (x -0.115..5.985).

    * Route per bit (m1 bit strap -> via1 -> m2 hop -> leaf Z2 bar):
        - m1 bit strap y -0.86..-0.68 runs from x=0 (feed point, VSH bus
          lives further left) to the via pad at tg_x-3.39.  The strap
          clears the caps' lowest m1 (S/D straps end at y -0.55) by
          0.13 um.
        - via1 0.09 square centred at (tg_x-3.525, -0.765), with
          0.09/0.05 m1 and 0.05 m2 enclosure.
        - m2 riser x tg_x-3.62..-3.43, y -0.86..0.30 climbs past the
          Z1 bar; an m2 toe at y 0.11..0.30 runs right and merges the
          leaf's Z2 bar.
      REF_i is a bare m2 bar at y -0.30..-0.11 (tg_x-3.0..tg_x+0.3)
      landing on the leaf's Z1 bar.  The riser stops 0.43 um short of
      the REF bar, and the toe/REF bands are 0.22 um apart in y.

    * One m1 stub per cap at x+2.00..2.30 (the empty corridor between
      the nmos right strap at 0.02 and the pmos left strap at 5.59)
      rises from the strap into the FL bar (y -0.45..-0.30).

    * VSH: m1 bus down the left edge (x -0.85..-0.65, left of every
      cap m1 which starts at -0.41) + one m1 drop per cap to the gate
      bar (y 0.72..0.88).

Labels: leaf labels kept (Magic equivs leaf nets up to the top nets
that touch them).  Top names B0..B3, REF0..3, VSH; VSS/VDD ride the
guard-ring labels inside the TG leaf.
"""

from __future__ import annotations

import os

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("OUT", "/tmp/adcgen/flow/ics55/array4b.gds")

import sys
sys.path.insert(0, HERE)
import layers as _layers
L = _layers.load("ics55_h7ch")
M1, V1, M2 = L.layer("M1"), L.layer("VIA1"), L.layer("M2")
L1, L2 = L.layer("M1", "pin"), L.layer("M2", "pin")

NBITS = 4
CAP_PITCH = 7.15    # cap bbox 7.03 wide
ROW_PITCH = L.rule("row_pitch")   # ringed TG is 4.10 tall
TG_GAP = 1.50       # cap field -> this row's TG; leaves a free lane
                    # before the leaf's VSS ring frame for the via pad
STAGGER = L.rule("stagger")       # extra x per bit (decreasing: bit0 rightmost)

STRAP_Y = (-0.86, -0.68)   # bit strap band, below all S/D m1 (-0.55)
STUB_X = (2.00, 2.30)      # per-cap riser column, free corridor
VIA = 0.09                 # fixed via1 size
VSH_Y = (0.72, 0.88)       # cap gate m1 bar local y
VX = (-0.85, -0.65)        # VSH bus column, left of every cap m1

# TG-relative x of the m1 via pad / m2 riser (see module docstring).
PAD_X = (-3.66, -3.39)
RISE_X = (-3.62, -3.43)
TOE_Y = (0.11, 0.30)       # = leaf Z2 bar
REF_Y = (-0.30, -0.11)     # = leaf Z1 bar


def load_leaf(layout, gds_path, rename=None):
    src = pya.Layout()
    src.read(gds_path)
    top = next(iter(src.top_cells()))
    cell = layout.create_cell(rename or top.name)
    cell.copy_tree(top)
    return cell


def snap(v):
    return int(round(v * 1000))


def box(cell, layer, x1, y1, x2, y2, dx=0.0, dy=0.0):
    cell.shapes(layer).insert(pya.Box(
        snap(x1 + dx), snap(y1 + dy), snap(x2 + dx), snap(y2 + dy)))


def label(cell, layer, value, x, y):
    cell.shapes(layer).insert(pya.Text(value, pya.Trans(
        pya.Vector(snap(x), snap(y)))))


def main() -> None:
    cap_gds = os.path.join(HERE, "..", "outputs", "pdk_flow", "ics55",
                           "cap_unit.gds")
    tg_gds = os.path.join(HERE, "..", "outputs", "pdk_flow", "ics55", "tg",
                          "switch_tg.gds")
    ly = pya.Layout()
    ly.dbu = 0.001
    cap = load_leaf(ly, cap_gds, "cap_unit")
    tg = load_leaf(ly, tg_gds, "switch_tg")

    top = ly.create_cell("SARADC_ARRAY_ICS55_4B")
    m1, m1t = ly.layer(*M1), ly.layer(*L1)
    v1l = ly.layer(*V1)
    m2, m2t = ly.layer(*M2), ly.layer(*L2)

    via_cx = (PAD_X[0] + PAD_X[1]) / 2           # -3.525 rel. to tg_x
    via_cy = (STRAP_Y[0] + STRAP_Y[1]) / 2       # -0.77

    for bit in range(NBITS):
        ncap = 1 << bit
        dy = bit * ROW_PITCH
        row_right = ncap * CAP_PITCH
        tg_x = row_right + TG_GAP + (NBITS - 1 - bit) * STAGGER
        for k in range(ncap):
            top.insert(pya.CellInstArray(
                cap, pya.Trans(pya.Vector(snap(k * CAP_PITCH), snap(dy)))))
        top.insert(pya.CellInstArray(
            tg, pya.Trans(pya.Vector(snap(tg_x), snap(dy)))))

        # m1 bit strap: feed point x=0 -> via pad just outside the VSS ring
        box(top, m1, 0.0, STRAP_Y[0], tg_x + PAD_X[1], STRAP_Y[1], 0, dy)
        # per-cap stub up into the FL bar (net = switched plate)
        for k in range(ncap):
            box(top, m1, k * CAP_PITCH + STUB_X[0], STRAP_Y[0],
                k * CAP_PITCH + STUB_X[1], -0.30, 0, dy)

        # via1 -> m2 riser -> m2 toe onto the leaf Z2 bar
        box(top, v1l, tg_x + via_cx - VIA / 2, dy + via_cy - VIA / 2,
            tg_x + via_cx + VIA / 2, dy + via_cy + VIA / 2)
        box(top, m2, tg_x + RISE_X[0], STRAP_Y[0],
            tg_x + RISE_X[1], TOE_Y[1], 0, dy)
        box(top, m2, tg_x + RISE_X[0], TOE_Y[0],
            tg_x + 0.30, TOE_Y[1], 0, dy)

        label(top, m1t, f"B{bit}", tg_x - 3.50, dy - 0.76)

        # REF: m2 bar straight onto the leaf's Z1 bar
        box(top, m2, tg_x - 3.0, REF_Y[0], tg_x + 0.30, REF_Y[1], 0, dy)
        label(top, m2t, f"REF{bit}", tg_x - 2.0, dy - 0.20)

    # VSH: m1 bus down the left edge + a drop per cap onto its gate bar
    vx0, vx1 = VX
    top_y = (NBITS - 1) * ROW_PITCH + 1.0
    box(top, m1, vx0, STRAP_Y[0], vx1, top_y)
    for bit in range(NBITS):
        dy = bit * ROW_PITCH
        for k in range(1 << bit):
            cx = k * CAP_PITCH
            box(top, m1, vx0, VSH_Y[0], cx + 0.30, VSH_Y[1], 0, dy)
    label(top, m1t, "VSH", (vx0 + vx1) / 2, -0.2)

    ly.write(OUT)
    print(f"wrote {OUT}  cell={top.name}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""gf180mcu CDAC slice: bits 0..3 (1/2/4/8 cap units) + one TG per bit.

    klayout -b -r gen/analog/layout_gf180_array.py

Measures the per-bit switched-plate node capacitance on a tiled array --
the same experiment as gen/analog/layout_array.py (sky130) and
layout_ihp_array.py (ihp).

Geometry (um; cap cell ~3.62 x 7.75, TG ~4.28 x 7.75 -- the two leaves
share the nmos-below-pmos stack and the same device recipe):

    * bit i's row holds 2^i cap units abutted at CAP_PITCH x, then one
      TG placed TG_GAP to the right of the row's last cap.
    * TGs are staggered DECREASING in x (bit 0 rightmost) so each bit
      strap -- an m1 run at the nmos FL-rail height extended right into
      the TG's Z2 riser -- ends inside its own TG and never crosses
      another cell.
    * Inside the TG leaf the Z2 net is the m2 riser at local x
      3.28..3.58 spanning both rows; the Z1 net is the m1 riser at
      local x -0.60..-0.35.  The strap lands on the Z2 riser's lower
      segment (m1 jog at the nmos-right pad height).
    * Z1 gets a per-bit stub left plus its own REF<i> label.
    * VSH is one m2 bus down the left edge with a drop onto each cap's
      gate m2 bar (local x 0.60..1.00 spanning y1.75..6.55).

Labels: leaf labels that would alias per-bit nets are stripped (cap FL;
TG Z1/Z2/S/SB).  Top names B0..B3, REF0..3, VSH; VPW/VNW/VSS/VDD keep
their leaf well labels (common nets).
"""

from __future__ import annotations

import os

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("OUT", "/tmp/adcgen/flow/gf180/array4b.gds")

import sys
sys.path.insert(0, HERE)
import layers as _layers
L = _layers.load("gf180mcu_fd_7t5v0")
M1, M1T = L.layer("M1"), L.layer("M1", "pin")
VIA1, M2, M2T = L.layer("VIA1"), L.layer("M2"), L.layer("M2", "pin")

NBITS = 4
CAP_PITCH = 5.50    # cap nwell spans x -0.80..4.70; abutting at 5.50
                    # merges the wells (a <0.6 um gap fires NW.2a_LV)
ROW_PITCH = 8.80    # cell 7.90 + ~0.9 row gap (nwells touch vertically)
TG_GAP = L.rule("tg_gap")   # >1.4 um nwell-to-nwell (NW.2b_LV)
STAGGER = L.rule("stagger") # extra x per bit (decreasing: bit0 rightmost)
STRAP_Y = (-0.30, 0.0)   # strap band under every pad (S/D pads and
                         # taps sit at y>=0.55; the cap FL rail itself
                         # is y0.30..0.60, reached by per-cap stubs)
Z2_X = (3.28, 3.58)      # TG Z2 m2 riser local x
Z2_LO = (0.52, 0.82)     # nmos-right pad local y (Z2 riser bottom tap)
Z1_X = (-0.60, -0.35)    # TG Z1 m1 riser local x
VSH_X = (0.60, 1.00)     # cap gate m2 bar local x


def load_leaf(layout, gds_path, rename=None):
    src = pya.Layout()
    src.read(gds_path)
    top = next(iter(src.top_cells()))
    cell = layout.create_cell(rename or top.name)
    cell.copy_tree(top)
    return cell


def box(cell, layer, x1, y1, x2, y2, dx=0.0, dy=0.0):
    snap = lambda v: int(round(v * 1000 / 5.0)) * 5
    cell.shapes(layer).insert(pya.Box(
        snap(x1 + dx), snap(y1 + dy), snap(x2 + dx), snap(y2 + dy)))


def label(cell, layer, value, x, y):
    snap = lambda v: int(round(v * 1000 / 5.0)) * 5
    cell.shapes(layer).insert(pya.Text(value, pya.Trans(
        pya.Vector(snap(x), snap(y)))))


def strip(cell, layer, names):
    for s in list(cell.shapes(layer).each()):
        if s.is_text() and s.text.string in names:
            cell.shapes(layer).erase(s)


def main() -> None:
    cap_gds = os.path.join(HERE, "..", "outputs", "pdk_flow", "gf180",
                           "cap_unit.gds")
    tg_gds = os.path.join(HERE, "..", "outputs", "pdk_flow", "gf180", "tg",
                          "switch_tg.gds")
    ly = pya.Layout()
    ly.dbu = 0.001
    cap = load_leaf(ly, cap_gds, "gf_cap_unit")
    tg = load_leaf(ly, tg_gds, "gf_switch_tg")
    # Keep the leaf net labels: Magic's extractor equivs each leaf
    # instance's FL/Z1/Z2 up to the top net that touches it, which is
    # exactly how B_i picks up the caps' FL pads and the TG's Z2.
    # Stripping them leaves the leaf nets nameless and the port merge
    # silent (every device terminal then maps onto one global net).
    #
    # The KLayout LVS deck (gf180mcu.lvs) reads every 34/10 and 36/10 text
    # in the hierarchy and `make_top_level_pins` promotes each named net,
    # so the leaf labels also surface as top ports (S/SB/Z2, and FL/Z1
    # aliased onto B_i/REF_i).  For that deck only we emit a second,
    # label-stripped copy of the identical geometry: connectivity is
    # geometric, so the extracted netlist keeps the intended B*/REF*/VSH
    # ports and the leaf nets simply become unnamed internal nodes.
    if os.environ.get("GF180_ARRAY_LVS_STRIP") == "1":
        leaf_names = {"FL", "VSH", "Z1", "Z2", "S", "SB"}
        for lay in (M1T, M2T):
            strip(cap, ly.layer(*lay), leaf_names)
            strip(tg, ly.layer(*lay), leaf_names)

    top = ly.create_cell("SARADC_ARRAY_GF_4B")
    m1, m1t = ly.layer(*M1), ly.layer(*M1T)
    v1l = ly.layer(*VIA1)
    m2, m2t = ly.layer(*M2), ly.layer(*M2T)

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

        # bit strap along the row's bottom edge on m1, and one m1 stub
        # per cap up onto its nmos-right S/D pad / FL join bar.  A strap
        # at the FL-rail height (y0.30..0.60) crosses every cap's
        # body-tap pad (x2.45..2.95, y0.55..1.05, net VPW) and merges
        # FL with the substrate -- the low strap avoids that band
        # entirely (0.55 um clearance to every pad) and the stubs make
        # the per-cap connection where no tap exists.
        box(top, m1, 0.0, STRAP_Y[0], tg_x - 0.30, STRAP_Y[1], 0, dy)
        for k in range(ncap):
            box(top, m1, k * CAP_PITCH + 1.40, STRAP_Y[0],
                k * CAP_PITCH + 1.86, 0.80, 0, dy)
        # hop to m2 for the TG entry: the TG's Z1 pad and VSS tap pad
        # occupy the m1 band, m2 rides over them into the m2 Z2 riser.
        # The riser's bottom stub sits at local y 0.53-1.07, so the hop
        # must climb from the strap band (y<=0.0) up to it -- a purely
        # horizontal bar leaves the bit bus open to the TG Z2 side.
        box(top, m1, tg_x - 0.66, STRAP_Y[0] - 0.04, tg_x - 0.28,
            STRAP_Y[1] + 0.04, 0, dy)
        box(top, v1l, tg_x - 0.58, STRAP_Y[0] + 0.02,
            tg_x - 0.32, STRAP_Y[1] - 0.02, 0, dy)
        box(top, m2, tg_x - 0.70, STRAP_Y[0] - 0.10,
            tg_x + Z2_X[1], STRAP_Y[1] + 0.10, 0, dy)
        box(top, m2, tg_x + Z2_X[0], STRAP_Y[0] - 0.10,
            tg_x + Z2_X[1], Z2_LO[1] + 0.10, 0, dy)
        # label ON the strap metal: a label in empty space becomes its
        # own one-point net instead of naming the strap.
        label(top, m1t, f"B{bit}", row_right + 0.3,
              (STRAP_Y[0] + STRAP_Y[1]) / 2 + dy)

        # Z1 stub left off the m1 riser + its REF label
        box(top, m1, tg_x + Z1_X[0] - 0.60, 2.0, tg_x + Z1_X[1], 2.3, 0, dy)
        label(top, m1t, f"REF{bit}", tg_x + Z1_X[0] - 0.3, 2.15 + dy)

    # VSH: one m2 bus down the left edge + a drop onto each cap's gate
    # m2 bar (x 0.60..1.00, y 1.75..6.55).  The drop is a short m2
    # finger at mid-height; the bus stays left of the field.
    vx0, vx1 = -1.05, -0.75
    top_y = (NBITS - 1) * ROW_PITCH + 7.90
    box(top, m2, vx0, -0.5, vx1, top_y + 0.4)
    for bit in range(NBITS):
        dy = bit * ROW_PITCH
        for k in range(1 << bit):
            cx = k * CAP_PITCH
            box(top, m2, vx0, 3.9, cx + VSH_X[1], 4.3, 0, dy)
    label(top, m2t, "VSH", (vx0 + vx1) / 2, -0.1)

    ly.write(OUT)
    print(f"wrote {OUT}  cell={top.name}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    main()

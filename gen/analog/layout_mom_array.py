#!/usr/bin/env python3
"""8-bit binary CDAC array built from SARADC_CAP_MOM unit cells (Bug3).

    klayout -b -r gen/analog/layout_mom_array.py -rd pdk=ihp_sg13g2 \
            -rd nbits=8 -rd c_ff=2.0 -rd out=/tmp/mom_array8.gds

Rows: bit i gets 2^i unit MOM caps abutted at the leaf pitch, plus one
transmission gate (SARADC_SW_TG) that switches the bit's bottom plates
between VREFH and VREFL.  Unlike the MOSCAP array:

  * the unit cap is pure metal -- no nwell, so rows can sit at a pitch set
    by the momcap recipe's finger pitch, not the MOSCAP cell bbox;
  * both plates are already bused inside the leaf (VSH top comb, FL bottom
    comb), so the array only routes the per-bit FL strap to its TG and one
    VSH rail down the side -- the leaf does the rest;
  * the bottom-plate parasitic inside SARADC_CAP_MOM already accounts for
    the fringe cap's substrate coupling, so no extra shield routing is
    needed at array level.

The leaf must be generated first (layout_mom_cap.py) and its GDS path is
read from env ``CAP_GDS`` (defaults to the momcap output name).

Pinout: VSH (common top plate), B0..B7 (per-bit switched bottom plates to
the TG array), VREFH, VREFL, VDD, VSS.  The digital logic drives each TG's
S/SB to pick the reference the bottom plate sees.
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
C_FF = float(os.environ.get("c_ff", "2.0"))
OUT = os.environ.get("out", os.environ.get("OUT",
                     "/tmp/adcgen/flow/mom_array8.gds"))

L = _layers.load(PDK)
REC = L.momcap()
# leaf geometry, matching layout_mom_cap.py
FW = float(REC.get("finger_width_um", 0.20))
FG = float(REC.get("finger_gap_um", 0.21))
FINGER_LEN = 3.0
MARGIN = 0.5
SHIELD_W = 0.5
# the leaf's footprint: width x height as drawn by emit_cell
fringe_ff_per_um = 0.20
need_um = C_FF / fringe_ff_per_um
n_pairs = max(2, int(round(need_um / (2 * FINGER_LEN))))
LEAF_W = n_pairs * (FW + FG) + FG
LEAF_H = FINGER_LEN + 2 * MARGIN + FG + FW + SHIELD_W + MARGIN
CAP_PITCH = LEAF_W + 0.30          # + shield-to-shield clearance
ROW_PITCH = LEAF_H + 2.50          # room for the per-bit FL strap + TG
TG_GAP = 1.50

M1 = L.layer("M1"); PIN_M1 = L.layer("M1", "pin")
M2 = L.layer("M2"); PIN_M2 = L.layer("M2", "pin")
VIA1 = L.layer("VIA1")


def load_leaf(ly, path, want):
    ly.read(path)
    for c in ly.each_cell():
        if c.name == want:
            return c
    raise SystemExit(f"{path}: cell {want} not found")


def box(cell, layer, x1, y1, x2, y2, dx=0.0, dy=0.0):
    cell.shapes(layer).insert(pya.Box(
        int(round((x1 + dx) * 1000)), int(round((y1 + dy) * 1000)),
        int(round((x2 + dx) * 1000)), int(round((y2 + dy) * 1000))))


def pin(cell, name, x, y, layer):
    cell.shapes(layer).insert(pya.Text(name, pya.Trans(pya.Vector(
        int(round(x * 1000)), int(round(y * 1000))))))


def main():
    cap_gds = os.environ.get("CAP_GDS") or os.path.join(
        HERE, "..", "outputs", "pdk_flow", PDK.split("_")[0],
        "cap_mom.gds")
    tg_gds = os.environ.get("TG_GDS") or os.path.join(
        HERE, "..", "outputs", "pdk_flow", PDK.split("_")[0], "tg",
        "switch_tg.gds")
    ly = pya.Layout()
    ly.dbu = 0.001
    cap = load_leaf(ly, cap_gds, "SARADC_CAP_MOM")
    # each bit row gets one transmission gate that switches the bottom
    # plate between VREFH and VREFL; the leaf is required now (assembly
    # no longer defers it)
    tg = load_leaf(ly, tg_gds, "SARADC_SW_TG_IHP")

    top = ly.create_cell(f"SARADC_MOM_ARRAY_{NBITS}B")
    m1, m2 = ly.layer(*M1), ly.layer(*M2)
    pinm1, pinm2 = ly.layer(*PIN_M1), ly.layer(*PIN_M2)

    # VSH rail down the left edge on m2
    vsh_x = (-0.85, -0.60)

    # TG leaf geometry (from layout_ihp_tg.py): the Z2 crown pad faces the
    # cap field at local x -0.50..-0.17, y 2.74..2.95; the Z1 riser is at
    # local x ~0.98..1.14 on m1 (right side); VDD/VSS taps near x1.6.
    TG_Z2 = (-0.50, -0.17, 2.74, 2.95)   # bottom-plate side (faces caps)
    TG_Z1 = (0.98, 1.14, 0.0, 2.95)      # reference side (m1 riser)

    for bit in range(NBITS):
        ncap = 1 << bit
        dy = bit * ROW_PITCH
        row_right = ncap * CAP_PITCH
        for k in range(ncap):
            top.insert(pya.CellInstArray(
                cap, pya.Trans(pya.Vector(
                    int(round(k * CAP_PITCH * 1000)),
                    int(round(dy * 1000))))))
        # place this row's TG just right of the cap field, vertically
        # centred so its Z2 crown (local y ~2.8) meets the row's FL strap
        tg_x = row_right + TG_GAP
        tg_dy = dy + LEAF_H - 2.95        # align Z2 crown with the strap
        top.insert(pya.CellInstArray(
            tg, pya.Trans(pya.Vector(
                int(round(tg_x * 1000)), int(round(tg_dy * 1000))))))
        # per-bit FL strap (bottom plates) on m2, run under the row and
        # land on the TG's Z2 crown pad (bottom-plate terminal)
        strap_y = dy + LEAF_H + 0.3
        box(top, m2, 0.0, strap_y, tg_x + TG_Z2[0], strap_y + FG + 0.2)
        # riser up to the Z2 crown (which is at tg local y 2.74..2.95)
        box(top, m2, tg_x + TG_Z2[0], tg_dy + TG_Z2[2],
            tg_x + TG_Z2[1], strap_y + FG + 0.2)
        pin(top, f"B{bit}", tg_x + TG_Z2[0] - 0.1, strap_y + 0.1, pinm2)
        # the TG's Z1 riser is the reference terminal: label it per bit.
        # The reference driver (VREFH/VREFL selection) lands here.
        pin(top, f"REF{bit}", tg_x + TG_Z1[0] + 0.05, tg_dy + 1.30, pinm1)

    # common VSH bus.  Each leaf's top comb bus is already VSH inside the
    # cell at the row's top edge.  A single continuous m2 rail laid over the
    # whole row's top-bus band merges with every leaf's bus into one wide
    # shape -- much cleaner than per-leaf drops, which left <0.21 um notches
    # against the neighbouring shield/bus metal.  One left-edge spine ties
    # all the row rails together.
    # leaf top bus sits at local y MARGIN+FG+FINGER_LEN .. +FW (the VSH comb
    # bus), i.e. ~3.71..3.91 within the cell -- well below the cell top.
    # Cover that band continuously so it merges with every leaf's bus.
    vsh_bus_y0 = MARGIN + FG + FINGER_LEN         # ~3.71
    vsh_bus_y1 = vsh_bus_y0 + FW                   # ~3.91
    for bit in range(NBITS):
        dy = bit * ROW_PITCH
        ncap = 1 << bit
        row_right = ncap * CAP_PITCH
        box(top, m2, -0.2, dy + vsh_bus_y0, row_right + 0.2, dy + vsh_bus_y1)
    box(top, m2, vsh_x[0], -0.4, vsh_x[1], (NBITS - 1) * ROW_PITCH + LEAF_H + 0.5)
    # link the spine into each row rail at the row's left end
    for bit in range(NBITS):
        dy = bit * ROW_PITCH
        box(top, m2, vsh_x[0], dy + vsh_bus_y0, LEAF_W * 0.3, dy + vsh_bus_y1)
    pin(top, "VSH", (vsh_x[0] + vsh_x[1]) / 2, -0.2, pinm2)

    ly.write(OUT)
    print(f"wrote {OUT}  cell={top.name}  nbits={NBITS} "
          f"leaf={LEAF_W:.2f}x{LEAF_H:.2f}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    main()

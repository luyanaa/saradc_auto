#!/usr/bin/env python3
"""IHP sg13g2 cap_unit: nmos + pmos with gates on VSH, S/D on FL.

    klayout -b -r gen/analog/layout_ihp_cap.py

Both devices are the PDK's fixed three-terminal cells with a gate strap
(poly neck, landing pad, one 0.19 um via1 for IHP's min=max rule) and a tap
(ptap1 -> VPW, ntap1 -> VNW) inside an nwell extension.  FL is met1, VSH is
met2; metal1 pin labels (GDS 8/2) and a met2 pin label (10/2) name the nets.

DRC 0 on the IHP deck; netgen reports "Circuits match uniquely" against
    XMN FL VSH FL VPW sg13_lv_nmos w=0.15u l=0.13u
    XMP FL VSH FL VNW sg13_lv_pmos w=0.15u l=0.13u
PEX: 2 devices, 6 caps, VSH-FL 132 aF (one unit device; the array scales by
n_parallel).

Environment: OUT (default /tmp/adcgen/flow/ihp/cap_unit.gds)
"""
import os, glob
import pya

OUT = os.environ.get("OUT", "/tmp/adcgen/flow/ihp/cap_unit.gds")
IHP_ROOT = glob.glob(os.path.expanduser("~/.ciel/ciel/ihp-sg13/versions/*/ihp-sg13g2"))[0]
SRC = os.path.join(IHP_ROOT, "libs.ref/sg13g2_pr/gds/sg13g2_pr.gds")

ly = pya.Layout()
ly.read(SRC)
nmos, pmos = ly.cell("nmos"), ly.cell("pmos")
ptap, ntap = ly.cell("ptap1"), ly.cell("ntap1")
top = ly.create_cell("cap_unit3")
top.insert(pya.CellInstArray(nmos, pya.Trans()))
top.insert(pya.CellInstArray(pmos, pya.Trans(pya.Vector(0, 2200))))
top.insert(pya.CellInstArray(ptap, pya.Trans(pya.Vector(1300, 0))))
top.insert(pya.CellInstArray(ntap, pya.Trans(pya.Vector(1300, 2200))))

POLY, MCON, M1, M2, V1, NW = (ly.layer(5, 0), ly.layer(6, 0), ly.layer(8, 0),
                              ly.layer(10, 0), ly.layer(19, 0), ly.layer(31, 0))
L1, L2 = ly.layer(8, 2), ly.layer(10, 2)


def box(layer, x1, y1, x2, y2):
    top.shapes(layer).insert(pya.Box(*(int(v * 1000) for v in (x1, y1, x2, y2))))


def strap(dy):
    box(POLY, 0.37, 0.40 + dy, 0.50, 0.48 + dy)
    box(POLY, 0.31, 0.47 + dy, 0.61, 0.77 + dy)
    box(MCON, 0.38, 0.54 + dy, 0.54, 0.70 + dy)
    box(M1, 0.31, 0.47 + dy, 0.61, 0.77 + dy)
    box(V1, 0.37, 0.53 + dy, 0.56, 0.72 + dy)


strap(0)      # nmos gate
strap(2.2)    # pmos gate

# VSH: met2 bus over both gate pads
box(M2, 0.34, 0.50, 0.60, 2.94)

# FL: the two source/drain pairs on met1, tied on the right.  The rail
# ends inside the cell (x0.07-1.16) -- a top-level metal2 strap picks it
# up through a via1 instead, because a metal1 rail long enough to touch
# the cell edge would sweep the body tap and short FL to the substrate.
box(M1, 0.07, 0.02, 1.16, 0.28)
box(M1, 0.07, 2.22, 1.16, 2.48)
box(M1, 1.00, 0.02, 1.16, 2.48)
# FL metal2 tap: via1 on the rail + an m2 pad the array strap (also m2)
# overlaps.  m2 does not couple to the tap's m1/active, so the strap can
# cross the ptap column where an m1 rail could not.
# IHP via1 is fixed min=max 0.19 um; the gate straps already use it.
box(V1, 0.40, 0.05, 0.59, 0.24)
box(M2, 0.34, 0.02, 0.66, 0.28)
# VNW metal2 tap on the ntap pad: same trick as FL -- a top-level m2
# VDD bus crosses the pmos FL rail and Z stubs without touching m1.
# The via sits INSIDE the tap's m1 window (top 2.89) so m1 encloses it
# (V1.c), and the m2 pad drops low enough (2.60) to cover the via.
box(V1, 1.55, 2.66, 1.74, 2.85)
box(M2, 1.44, 2.60, 1.94, 3.25)

# nwell extension so the ntap sits in the pmos well
box(NW, 0.99, 1.89, 2.45, 3.29)

for text, layer, x, y in (("FL", L1, 0.50, 0.17), ("VSH", L2, 0.46, 1.50),
                          ("VPW", L1, 1.60, 0.40), ("VNW", L1, 1.60, 2.60)):
    top.shapes(layer).insert(pya.Text(text, pya.Trans(
        pya.Vector(int(x * 1000), int(y * 1000)))))

ly.write(OUT)
print("wrote", OUT, "bbox", top.bbox().to_s())

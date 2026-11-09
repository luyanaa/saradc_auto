#!/usr/bin/env python3
"""IHP sg13g2 switch_tg: nmos + pmos transmission gate, DRC/LVS/PEX leaf.

    klayout -b -r gen/analog/layout_ihp_tg.py

Port contract (matches the sky130 SARADC_SW_TG):  Z1 Z2 S SB VSS VDD.

Both devices are the PDK's fixed 3-terminal cells with the same gate
strap + tap additions the proven nmos/pmos leaves use
(layout_ihp_dev.py).  The transmission gate is symmetric, so the S/D
pairing that avoids a mirror is the one layout_tg.py used on sky130:
with the pmos to the right, Z2 ties the nmos RIGHT diffusion to the
pmos LEFT diffusion, Z1 ties the nmos LEFT to the pmos RIGHT -- each
net's strap drops clear of the other's.

Device m1 pads (cell-local, um): diffusions at x 0.07-0.23 and
0.64-0.80 (y 0.02-0.28), gate pad after strapping at x 0.31-0.61,
y 0.47-0.77.  Taps sit 1.2 um to the right of each device.
"""

from __future__ import annotations

import glob
import os

import pya

IHP_ROOT = os.environ.get("IHP_ROOT") or glob.glob(
    os.path.expanduser("~/.ciel/ciel/ihp-sg13/versions/*/ihp-sg13g2"))[0]
SRC = os.path.join(IHP_ROOT, "libs.ref/sg13g2_pr/gds/sg13g2_pr.gds")
OUT = os.environ.get("OUT", "/tmp/adcgen/flow/ihp/switch_tg.gds")

POLY, MCON, M1 = (5, 0), (6, 0), (8, 0)
V1, M2 = (19, 0), (10, 0)
NW = (31, 0)
LBL = (8, 25)        # 8/25 is the pin-text purpose the LVS deck reads
                     # (8/2 is drawing text and is NOT treated as a port).
NM = 1000


def box(cell, layer, x1, y1, x2, y2):
    # round, not truncate: 0.28+2.4 is 2.679999... in float and int()
    # would floor it to 2679 nm -- offgrid and a 1 nm gap off the pad.
    cell.shapes(layer).insert(
        pya.Box(*(int(round(v * NM)) for v in (x1, y1, x2, y2))))


def strap(cell, ly, dy):
    """Gate strap on top of a device placed at y offset dy (um).

    Same geometry as layout_ihp_dev.py: poly neck up from the gate, a
    landing pad, one 0.16 contact, a met1 pad.  Returns the met1 pad
    bounds so a label can be placed inside it.
    """
    p, c, m = ly.layer(*POLY), ly.layer(*MCON), ly.layer(*M1)
    box(cell, p, 0.37, 0.40 + dy, 0.50, 0.46 + dy)
    box(cell, p, 0.31, 0.45 + dy, 0.61, 0.75 + dy)
    box(cell, c, 0.38, 0.52 + dy, 0.54, 0.68 + dy)
    box(cell, m, 0.34, 0.48 + dy, 0.58, 0.72 + dy)
    return (0.34, 0.48 + dy, 0.58, 0.72 + dy)


def label(cell, ly, text, x, y):
    cell.shapes(ly.layer(*LBL)).insert(
        pya.Text(text, pya.Trans(pya.Vector(int(x * NM), int(y * NM)))))


def build() -> None:
    ly = pya.Layout()
    ly.read(SRC)
    nmos, pmos = ly.cell("nmos"), ly.cell("pmos")
    ptap, ntap = ly.cell("ptap1"), ly.cell("ntap1")
    top = ly.create_cell("SARADC_SW_TG_IHP")
    m1 = ly.layer(*M1)

    # nmos on the lower row, pmos above (its nwell keeps the two devices
    # apart); taps 1.2 um right of each device, like the proven leaves.
    # ntap1/pmos carry their own nwell -- no hand-drawn extension needed
    # (a hand box went offgrid and tripped NW.b1/NBL.d).
    top.insert(pya.CellInstArray(nmos, pya.Trans()))
    top.insert(pya.CellInstArray(ptap, pya.Trans(pya.Vector(1200, 0))))
    p_dy = 2.4
    top.insert(pya.CellInstArray(pmos, pya.Trans(pya.Vector(0, int(p_dy * NM)))))
    top.insert(pya.CellInstArray(ntap, pya.Trans(pya.Vector(1200, int(p_dy * NM)))))

    g_n = strap(top, ly, 0.0)
    g_p = strap(top, ly, p_dy)

    # Diffusion pads, cell-local (dy added for the pmos row):
    #   left  x 0.07-0.23, right x 0.64-0.80, both y 0.02-0.28
    def pads(dy):
        return ((0.07, 0.02 + dy, 0.23, 0.28 + dy),
                (0.64, 0.02 + dy, 0.80, 0.28 + dy))

    nl, nr = pads(0.0)          # nmos left / right diffusions
    pl, pr = pads(p_dy)         # pmos left / right
    # The column between the gate pad (x 0.34-0.58) and the right
    # diffusion pad (x 0.64) is only 0.06 um wide -- a strap dropped
    # straight up from the pad fails M1.b.  Route AROUND the gate
    # instead: Z1 extends the nmos-left pad left, rises in free space,
    # and reaches the pmos-left pad from the left; Z2 does the same on
    # the right.  Both rails stay vertical, no crossing.
    # Z1: nmos-left -> left riser -> pmos-left.  It pokes to y2.95 just
    # like Z2 so either riser can be probed at the 2.75-2.95 crown band.
    box(top, m1, -0.40, nl[1], nl[2], nl[3])           # pad extension
    box(top, m1, -0.40, nl[1], -0.24, 2.95)            # left riser
    box(top, m1, -0.40, pl[1], pl[2], pl[3])           # onto pmos pad
    # Z2 metal2 crown tap: an array bit strap runs on metal2 (metal1
    # would short onto the caps' body taps), so Z2 gets a via1+m2 pad
    # above the riser head.  It stays left of the cell's own x range so
    # the m2 strap reaching in does not brush the ptap m1 pad at x1.34+.
    v1, m2 = ly.layer(*V1), ly.layer(*M2)
    box(top, v1, -0.37, 2.75, -0.18, 2.94)
    box(top, m2, -0.50, 2.74, -0.17, 2.95)
    # Z2: nmos-right -> right riser -> pmos-right.  The riser must keep
    # 0.16 um off the ptap m1 pad (abs x >= 1.34), so it ends at x1.14.
    # It pokes 0.27 um ABOVE the pmos pad (to y2.95, past Z1's 2.68 top)
    # so an array-level strap can tap Z2's crown without brushing Z1.
    box(top, m1, nr[0], nr[1], 1.14, nr[3])            # pad extension
    box(top, m1, 0.98, nr[1], 1.14, 2.95)              # right riser
    box(top, m1, pr[0], pr[1], 1.14, pr[3])            # onto pmos pad

    # Z2 sits on the LEFT riser (facing the cap field), Z1 on the right:
    # a bit strap arriving from the left lands on Z2's foot and never
    # reaches Z1, so the array needs no jog to keep the nets apart.
    label(top, ly, "Z2", -0.32, 1.30)
    label(top, ly, "Z1", 1.06, 1.30)
    label(top, ly, "S", (g_n[0] + g_n[2]) / 2, (g_n[1] + g_n[3]) / 2)
    label(top, ly, "SB", (g_p[0] + g_p[2]) / 2, (g_p[1] + g_p[3]) / 2)
    label(top, ly, "VSS", 1.60, 0.40)
    label(top, ly, "VDD", 1.60, p_dy + 0.40)

    ly.write(OUT)
    print(f"wrote {OUT}  cell={top.name}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    build()

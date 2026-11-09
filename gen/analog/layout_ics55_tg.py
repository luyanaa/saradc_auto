#!/usr/bin/env python3
"""ics55 switch_tg: nmos + pmos transmission gate, DRC/LVS/PEX leaf.

    klayout -b -r gen/analog/layout_ics55_tg.py

Port contract Z1 Z2 S SB VSS VDD.  Both devices come from the corrected
local PCell (ics55_mos.py, guard_ring=0) exactly as layout_ics55_cap.py
uses it.  The ics55 LVS deck ties device bulks to the global VDD/VSS
rails, so a TG needs no explicit body route -- the rails are the bulk
nets.

With the nmos and pmos side by side on one row the S/D pairing that
needs no mirror is: Z1 ties nmos-left + pmos-left, Z2 ties nmos-right +
pmos-right.  Each PCell finger already lands a metal1 strap on its
left and right S/D contacts, so a Z net is a short metal2 run between
the two pads at the same x offset -- via1 (91/1) up, metal2 (82/1)
across, via1 down.  The gate pads get metal2 bars for S and SB.
"""

from __future__ import annotations

import os
import sys
import types

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("OUT", "/tmp/adcgen/flow/ics55/switch_tg.gds")

kl = types.ModuleType("klayout")
kl.db = pya
sys.modules.setdefault("klayout", kl)
sys.modules["klayout.db"] = pya
sys.path.insert(0, HERE)
import ics55_mos  # noqa: F401,E402

M1, V1, M2 = (81, 1), (91, 1), (82, 1)
L1, L2 = (81, 6), (82, 6)
# Measured PCell pads for a single finger w=1.0 (device centred on
# origin; nmos at dx=0, pmos at dx=P_DX):
#   left  S/D m1 strap x -0.410..-0.240, right strap x -0.150..0.020,
#   both y -0.55..0.55; gate poly pad lands an m1 at x -0.195..-0.015,
#   y 0.68..0.82.  via1 is a fixed 0.09 um square needing ~0.04 um of
#   m1/m2 enclosure.
LX = (-0.410, -0.240)
RX = (-0.150, 0.020)
SD_Y = (-0.55, 0.55)
GPAD = (-0.195, 0.68, -0.015, 0.82)   # gate m1 landing
VIA = 0.09                            # fixed via1 size
VIA_EN = 0.05                         # m1/m2 overhang around a via


def build() -> None:
    ly = pya.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("switch_tg")

    def dev(kind: str, dx: float):
        # guard_ring=1 gives the bulk tap its own metal ring, which is
        # where the deck expects the VDD/VSS labels -- a bare S/D strap
        # would short the rail onto a Z net instead.
        c = ly.create_cell("ics55_mos_analog", "ICsprout55_local",
                           {"dev_type": kind, "w": 1.0, "l": 0.06, "fingers": 1,
                            "guard_ring": 1, "ring_metal": 1})
        if c is None:
            raise SystemExit("PCell not found")
        top.insert(pya.CellInstArray(c.cell_index(),
                                     pya.Trans(pya.Vector(int(dx * 1000), 0))))

    P_DX = 6.0
    dev("n", 0.0)
    dev("p", P_DX)
    top.flatten(True)

    def snap(v):
        return int(round(v * 1000))

    def box(layer, x1, y1, x2, y2):
        top.shapes(ly.layer(*layer)).insert(
            pya.Box(*(snap(v) for v in (x1, y1, x2, y2))))

    def text(value, layer, x, y):
        top.shapes(layer).insert(pya.Text(value, pya.Trans(
            pya.Vector(snap(x), snap(y)))))

    def via(xc, yc):
        box(V1, xc - VIA / 2, yc - VIA / 2, xc + VIA / 2, yc + VIA / 2)

    # Z1: nmos-left strap -> via -> metal2 down the left lane -> via ->
    # pmos-left strap.  The straps sit at the same x offset, so this is a
    # vertical metal2 bar plus short m1 tails under each via.
    # Each Z net is one horizontal metal2 bar joining the two same-x
    # straps' vias -- a riser plus a crossbar overlaps the OTHER net's
    # bar and shorts Z1 to Z2 (the extractor printed Z1|Z2).  A plain
    # bar at the via height is enough and stays clear of its twin.
    lx = (LX[0] + LX[1]) / 2
    for dx in (0.0, P_DX):
        box(M1, dx + LX[0], -0.30, dx + LX[1], -0.11)   # tail under via
        via(dx + lx, -0.20)
    box(M2, lx - 0.05, -0.30, P_DX + lx + 0.05, -0.11)  # Z1 bar
    # Z2: nmos-right strap -> via -> metal2 -> pmos-right strap.
    rx = (RX[0] + RX[1]) / 2
    for dx in (0.0, P_DX):
        box(M1, dx + RX[0], 0.11, dx + RX[1], 0.30)
        via(dx + rx, 0.20)
    box(M2, rx - 0.05, 0.11, P_DX + rx + 0.05, 0.30)    # Z2 bar

    # S / SB: via on each gate pad + a metal2 stub big enough to clear
    # the 0.052 um^2 M2 area rule (0.28 x 0.22).
    for dx in (0.0, P_DX):
        via(dx + (GPAD[0] + GPAD[2]) / 2, (GPAD[1] + GPAD[3]) / 2)
        box(M2, dx + GPAD[0] - 0.05, GPAD[1] - 0.03,
            dx + GPAD[2] + 0.05, GPAD[3] + 0.05)

    lab1, lab2 = ly.layer(*L1), ly.layer(*L2)
    text("Z1", lab2, lx, -0.2)
    text("Z2", lab2, rx, 0.2)
    text("S", lab2, (GPAD[0] + GPAD[2]) / 2, GPAD[3] + 0.05)
    text("SB", lab2, P_DX + (GPAD[0] + GPAD[2]) / 2, GPAD[3] + 0.05)
    # bulk rails ride the guard-ring metal, well outside the Z bars.
    text("VSS", lab1, -1.20, 0.0)
    text("VDD", lab1, P_DX + 1.20, 0.0)

    ly.write(OUT)
    print(f"wrote {OUT}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    build()

#!/usr/bin/env python3
"""ics55 cap_unit: nmos + pmos, gates on VSH, S/D on FL, rings on VDD/VSS.

    klayout -b -r gen/analog/layout_ics55_cap.py

Built from the corrected local PCell (ics55_mos.py).  The guard rings are the
body taps; the ics55 LVS deck ties bulks to the global VDD/VSS rails, so the
rings carry those labels rather than VNW/VPW.  Gates and S/D are tied on
metal2 (91/1 is VIA1, 82/1 is M2) because a metal1 route would cross the
guard rings.
"""

from __future__ import annotations

import os
import sys
import types

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("OUT", "/tmp/adcgen/flow/ics55/cap_unit.gds")

kl = types.ModuleType("klayout")
kl.db = pya
sys.modules.setdefault("klayout", kl)
sys.modules["klayout.db"] = pya
sys.path.insert(0, HERE)
import ics55_mos  # noqa: F401,E402

M1, V1, M2 = (81, 1), (91, 1), (82, 1)
L1, L2 = (81, 6), (82, 6)


def build() -> None:
    ly = pya.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("cap_unit")

    def dev(kind: str, dx: float):
        c = ly.create_cell("ics55_mos_analog", "ICsprout55_local",
                           {"dev_type": kind, "w": 1.0, "l": 0.06, "fingers": 1,
                            "guard_ring": 0, "ring_metal": 0})
        if c is None:
            raise SystemExit("PCell not found")
        top.insert(pya.CellInstArray(c.cell_index(),
                                     pya.Trans(pya.Vector(int(dx * 1000), 0))))

    dev("n", 0.0)
    dev("p", 6.0)
    top.flatten(True)

    def box(layer, x1, y1, x2, y2):
        top.shapes(ly.layer(*layer)).insert(
            pya.Box(*(int(v * 1000) for v in (x1, y1, x2, y2))))

    # FL: one metal1 bar across both source/drain straps (S and D are the
    # same net in a MOSCAP), low enough to clear the gate pads
    box(M1, -0.38, -0.45, 5.98, -0.30)
    # VSH: metal1 bar just above both gate pads
    box(M1, -0.34, 0.72, 5.94, 0.88)

    lab1, lab2 = ly.layer(*L1), ly.layer(*L2)
    for text, layer, x, y in (("FL", lab1, 2.8, -0.37), ("VSH", lab1, 2.8, 0.80)):
        top.shapes(layer).insert(pya.Text(text, pya.Trans(
            pya.Vector(int(x * 1000), int(y * 1000)))))

    ly.write(OUT)
    print(f"wrote {OUT}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    build()

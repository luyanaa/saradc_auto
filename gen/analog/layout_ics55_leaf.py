#!/usr/bin/env python3
"""ics55 single-device leaf with top-cell pin labels, for LVS.

    klayout -b -r gen/analog/layout_ics55_leaf.py

Uses the corrected local PCell (ics55_mos.py), flattens it into the top cell
(the ics55 LVS deck only takes ports from direct top-cell text) and puts
S/D/G/B labels on the M1 net-label layer 81/6.
"""

from __future__ import annotations

import os
import sys
import types

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("OUT", "/tmp/adcgen/flow/ics55/leaf_nmos.gds")
DEV = os.environ.get("DEV", "n")

kl = types.ModuleType("klayout")
kl.db = pya
sys.modules.setdefault("klayout", kl)
sys.modules["klayout.db"] = pya
sys.path.insert(0, HERE)
import ics55_mos  # noqa: F401,E402

LABEL = (81, 6)


def build() -> None:
    ly = pya.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("leaf_nmos")
    c = ly.create_cell("ics55_mos_analog", "ICsprout55_local",
                       {"dev_type": DEV, "w": 1.0, "l": 0.06, "fingers": 1,
                        "guard_ring": 1, "ring_metal": 1})
    if c is None:
        raise SystemExit("PCell not found")
    top.insert(pya.CellInstArray(c.cell_index(), pya.Trans()))
    top.flatten(True)

    lab = ly.layer(*LABEL)
    for text, x, y in (("S", -0.33, 0.0), ("D", -0.06, 0.0),
                       ("G", -0.22, 0.75), ("B", 0.0, -1.75)):
        top.shapes(lab).insert(pya.Text(text, pya.Trans(
            pya.Vector(int(x * 1000), int(y * 1000)))))

    ly.write(OUT)
    print(f"wrote {OUT}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    build()

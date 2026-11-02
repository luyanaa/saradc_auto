#!/usr/bin/env python3
"""Generate ics55 MOS devices from the PDK PCell under the pya API.

    klayout -b -r gen/analog/layout_ics55_dev.py

The PDK PCell (`ics55_mos_analog.py`) imports `klayout.db`, which is the pip
module; the KLayout binary only exposes `pya`.  The two APIs are the same
object model, so a module shim (`klayout.db = pya`) is enough to run the
PCell unmodified.  No source is changed in the PDK.

Environment:
    ICS55_ROOT  PDK root (default /grid/icsprout55-pdk)
    OUT         output GDS (default /tmp/adcgen/flow/ics55/leaf.gds)
"""

from __future__ import annotations

import os
import sys
import types

import pya

ICS55_ROOT = os.environ.get("ICS55_ROOT", "/grid/icsprout55-pdk")
OUT = os.environ.get("OUT", "/tmp/adcgen/flow/ics55/leaf.gds")

# shim: klayout.db -> pya (identical object model)
kl = types.ModuleType("klayout")
kl.db = pya
sys.modules.setdefault("klayout", kl)
sys.modules["klayout.db"] = pya

PC = os.path.join(ICS55_ROOT, "libs.tech/klayout/pcells")
sys.path.insert(0, PC)
import ics55_mos_analog  # noqa: F401,E402  (registers the ICsprout55 library)


def build() -> None:
    ly = pya.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("ics55_leaf")

    def add(dev_type: str, x: float, y: float, name: str):
        c = ly.create_cell("ics55_mos_analog", "ICsprout55",
                           {"dev_type": dev_type, "w": 1.0, "l": 0.06,
                            "fingers": 1, "guard_ring": 1, "ring_metal": 1})
        if c is None:
            raise SystemExit("PCell ics55_mos_analog not found in ICsprout55")
        c.name = name
        top.insert(pya.CellInstArray(c.cell_index(),
                                     pya.Trans(pya.Vector(int(x * 1000), int(y * 1000)))))
        return c

    add("n", 0.0, 0.0, "nmos_dev")
    add("p", 6.0, 0.0, "pmos_dev")

    ly.write(OUT)
    print(f"wrote {OUT}  bbox={top.bbox().to_s()}  cells={len(list(ly.each_cell()))}")


if __name__ == "__main__":
    build()

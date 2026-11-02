#!/usr/bin/env python3
"""Build an IHP sg13g2 NMOS leaf from the PDK's own device cells.

    klayout -b -r gen/analog/layout_ihp_dev.py

The PDK ships `nmos`/`pmos` as fixed three-terminal cells: met1 pads on the
two diffusions, the gate as poly only, and no body tap.  A usable leaf needs
two additions, both copied from the way `sg13g2_inv_1` is drawn:

  * gate strap: a poly neck up to a landing pad, one 0.16 contact, met1 pad;
  * body tap: `ptap1` placed close enough for the latch-up rule.

With those two the cell clears the IHP DRC deck with 0 items.  Without the
tap the same cell reports LU.b (max space from N+ activ in PWell to a
pSD-PWell tap), which is the expected baseline for a bare device.

Environment:
    IHP_ROOT  ihp-sg13g2 install (default: the ciel version in ~/.ciel)
    OUT       output GDS (default /tmp/adcgen/flow/ihp/leaf_nmos.gds)
"""

from __future__ import annotations

import glob
import os

import pya

IHP_ROOT = os.environ.get("IHP_ROOT") or glob.glob(
    os.path.expanduser("~/.ciel/ciel/ihp-sg13/versions/*/ihp-sg13g2"))[0]
OUT = os.environ.get("OUT", "/tmp/adcgen/flow/ihp/leaf_nmos.gds")

POLY = (5, 0)
MCON = (6, 0)
M1 = (8, 0)
TAP_DX_UM = 1.2


def build(src: str, out: str) -> None:
    ly = pya.Layout()
    ly.read(src)
    dev = ly.cell("nmos")
    tap = ly.cell("ptap1")
    if dev is None or tap is None:
        raise SystemExit("nmos/ptap1 not found in the pr GDS")
    top = ly.create_cell("leaf_nmos")
    top.insert(pya.CellInstArray(dev, pya.Trans()))
    top.insert(pya.CellInstArray(tap, pya.Trans(pya.Vector(int(TAP_DX_UM * 1000), 0))))

    poly, mcon, m1 = ly.layer(*POLY), ly.layer(*MCON), ly.layer(*M1)

    def box(layer, x1, y1, x2, y2):
        top.shapes(layer).insert(
            pya.Box(*(int(v * 1000) for v in (x1, y1, x2, y2))))

    # gate strap, clear of both the active region and the diffusion met1 pads
    box(poly, 0.37, 0.40, 0.50, 0.46)
    box(poly, 0.31, 0.45, 0.61, 0.75)
    box(mcon, 0.38, 0.52, 0.54, 0.68)
    box(m1, 0.34, 0.48, 0.58, 0.72)

    ly.write(out)
    print(f"wrote {out}  cell={top.name}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    build(os.path.join(IHP_ROOT, "libs.ref/sg13g2_pr/gds/sg13g2_pr.gds"), OUT)

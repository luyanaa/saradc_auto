#!/usr/bin/env python3
"""Draw the SARADC unit capacitor as real sky130 device geometry.

Runs inside KLayout:

    klayout -b -r gen/analog/layout_cap.py

The device geometry comes from the pure-pya port of the PDK draw_fet generator
that lives in /opt/OpenSERDES/verification/analog/pya_draw.  This script only
places two devices and their gate bridge; terminal strapping and the array
assembly are the next increment.

Environment:
    OUT       output GDS (default /tmp/adcgen/layout/SARADC_CAP_UNIT.gds)
    WN, WP    device widths in um
    L         channel length in um
    GAP       horizontal gap between the two devices in um

Status in this file is deliberately literal: it writes geometry, it does not
claim DRC, LVS or PEX.  Those are separate steps with separate evidence.
"""

from __future__ import annotations

import os
import sys

import pya

SERDES = "/opt/OpenSERDES/verification/analog"
if SERDES not in sys.path:
    sys.path.insert(0, SERDES)

from pya_draw import shim  # noqa: F401,E402  (registers the gdsfactory shim)
from pya_draw.draw_fet import draw_nfet, draw_pfet  # noqa: E402

M1 = (68, 20)
M1_LABEL = (68, 5)
V1 = (68, 44)
M2 = (69, 20)
M2_LABEL = (69, 5)


def box(cell, layer, x1, y1, x2, y2):
    cell.shapes(layer).insert(pya.Box(*(int(v * 1000) for v in (x1, y1, x2, y2))))


def build(layout, wn=1.4, wp=2.8, l=0.15, gap=0.8, name="SARADC_CAP_UNIT"):
    n = layout.create_cell(f"{name}$nfet")
    draw_nfet(n, l=l, w=wn, nf=1, type="sky130_fd_pr__nfet_01v8",
              bulk="bulk tie", gate_con_pos="top")
    p = layout.create_cell(f"{name}$pfet")
    draw_pfet(p, l=l, w=wp, nf=1, type="sky130_fd_pr__pfet_01v8",
              bulk="bulk tie", gate_con_pos="top")

    top = layout.create_cell(name)
    n_b = n.bbox()
    p_b = p.bbox()
    p_dx = n_b.right + int(gap * 1000) - p_b.left
    top.insert(pya.CellInstArray(n, pya.Trans(pya.Vector(0, 0))))
    top.insert(pya.CellInstArray(p, pya.Trans(pya.Vector(p_dx, 0))))

    # Gate bridge: the two gates are the top-plate net VSH.  Each device draws
    # its own gate met1 (0.366 x 0.230 um), but 230 nm is short of the 260 nm a
    # 150 nm via needs for 55 nm enclosure on both sides, so the gate met1 is
    # grown upward.  All coordinates are integer nanometres: int(0.45*1000)
    # truncates to 449 nm, which fails via.1a_a (0.15 um).
    #
    # via.4a wants 55 nm met1 enclosure, via.5a wants 85 nm on two adjacent
    # edges; the pad below gives 108 nm on all four.  The met2 landing pad
    # must then run all the way up to the joining bar -- stopping at the via
    # is what left the gate net floating in the first extraction.
    n_gate = (192, 1650, 558, 1880)          # device gate met1, nm
    p_gate = (p_dx + 192, 3050, p_dx + 558, 3280)
    bar_y = 3500
    for gx1, gy1, gx2, gy2 in (n_gate, p_gate):
        box(top, M1, gx1 / 1000, gy1 / 1000, gx2 / 1000, (gy2 + 60) / 1000)
        vx1, vy1 = gx1 + 108, gy1 + 55
        box(top, V1, vx1 / 1000, vy1 / 1000, (vx1 + 150) / 1000, (vy1 + 150) / 1000)
        box(top, M2, (vx1 - 85) / 1000, (vy1 - 85) / 1000,
            (vx1 + 150 + 85) / 1000, (bar_y + 280) / 1000)
    box(top, M2, (n_gate[0] + 98 - 85) / 1000, bar_y / 1000,
        (p_gate[0] + 98 + 150 + 85) / 1000, (bar_y + 280) / 1000)
    top.shapes(M2_LABEL).insert(
        pya.Text("VSH", pya.Trans(pya.Vector(n_gate[0] + 98 + 75, bar_y + 140))))

    # Bottom plate FL: source and drain only.  The body tap is a separate port
    # (VPW / VNW) in the schematic, so it must not be strapped to FL; shorting
    # it merges the two well ports and LVS sees a two-pin cell.
    strap(top, p_dx)
    return top


def strap(top, p_dx):
    """Tie both source/drain columns into FL.  Coordinates in nm."""
    rail_y1, rail_y2 = -320, -140
    box(top, M1, -200 / 1000, rail_y1 / 1000, (p_dx + 1765) / 1000, rail_y2 / 1000)

    def drop(x1, x2, dev_bottom):
        # the column starts at dev_bottom; touching shapes merge in Magic,
        # so this only has to reach the column edge.
        box(top, M1, x1 / 1000, rail_y1 / 1000, x2 / 1000, dev_bottom / 1000)

    for x1, x2 in ((35, 265), (485, 715)):
        drop(x1, x2, 195)
    for x1, x2 in ((35, 265), (485, 715)):
        drop(p_dx + x1, p_dx + x2, 175)

    top.shapes(M1_LABEL).insert(
        pya.Text("FL", pya.Trans(pya.Vector(200, (rail_y1 + rail_y2) // 2))))
    top.shapes(M1_LABEL).insert(pya.Text("VPW", pya.Trans(pya.Vector(1295, 700))))
    top.shapes(M1_LABEL).insert(
        pya.Text("VNW", pya.Trans(pya.Vector(p_dx + 1265, 1400))))


def main():
    out = os.environ.get("OUT", "/tmp/adcgen/layout/SARADC_CAP_UNIT.gds")
    wn = float(os.environ.get("WN", 1.4))
    wp = float(os.environ.get("WP", 2.8))
    l = float(os.environ.get("L", 0.15))
    gap = float(os.environ.get("GAP", 0.8))

    layout = pya.Layout()
    layout.dbu = 0.001
    top = build(layout, wn, wp, l, gap)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    layout.write(out)
    b = top.bbox()
    print(f"wrote {out}  cell={top.name}  "
          f"bbox={b.width()/1000:.3f} x {b.height()/1000:.3f} um  "
          f"layers={len(list(layout.layer_indexes()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

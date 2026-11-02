#!/usr/bin/env python3
"""Draw the selected 4x transmission gate as real sky130 device geometry.

    klayout -b -r gen/analog/layout_tg.py

Two devices from the same pya generator the cap cell uses:

    nfet 5.6 / 0.15   gate = S,  body tap = VSS
    pfet 11.2 / 0.15  gate = SB, body tap = VDD

Z1 / Z2 are the switch terminals.  A transmission gate is symmetric, so
"source" and "drain" are interchangeable: Z1 ties the nfet's left column to
the pfet's middle column, Z2 ties the nfet's middle column to the pfet's left
column.  That choice is what lets both straps stay on met1 -- the long rail
of one net never crosses a drop of the other.  Mirroring the pfet is the
other option; this avoids it.

Everything is integer nanometres.  int(um*1000) truncates (0.45 -> 449) and
that failed via.1a_a in the cap cell.

Environment:
    OUT   output GDS   (default /tmp/adcgen/layout/SARADC_SW_TG.gds)
    WN    nfet width um (default 5.6, the profile's switch_sizing_um)
    WP    pfet width um (default 11.2)
    L     channel length um (default 0.15)
    GAP   device-to-device gap um (default 1.0)

This file writes geometry only.  DRC, LVS and PEX are separate evidence and
live in layout_check.py.
"""

from __future__ import annotations

import json
import os
import sys

import pya

SERDES = "/opt/OpenSERDES/verification/analog"
if SERDES not in sys.path:
    sys.path.insert(0, SERDES)

from pya_draw import shim  # noqa: F401,E402
from pya_draw.draw_fet import draw_nfet, draw_pfet  # noqa: E402

M1 = (68, 20)
M1_LABEL = (68, 5)
NM = 1000  # nm per um


def box(cell, layer, x1, y1, x2, y2):
    """Place a rectangle given integer nanometres."""
    cell.shapes(layer).insert(pya.Box(int(x1), int(y1), int(x2), int(y2)))


def m1_columns(cell, layer):
    """Return the three source/drain/tap met1 columns, left to right.

    The gate pad is the highest met1 box; the rest sit on the diffusion.  The
    device generator draws exactly three, so anything else is a generator
    change this script has not been taught.
    """
    boxes = [s.box for s in cell.shapes(layer).each() if s.is_box()]
    if len(boxes) != 4:
        raise RuntimeError(f"expected 4 met1 boxes (3 columns + gate), got {len(boxes)}")
    gate = max(boxes, key=lambda b: b.top)
    cols = sorted([b for b in boxes if b is not gate], key=lambda b: b.left)
    return [(b.left, b.bottom, b.right, b.top) for b in cols], (
        gate.left, gate.bottom, gate.right, gate.top)


def build(layout, wn=5.6, wp=11.2, l=0.15, gap=1.0, name="SARADC_SW_TG"):
    n = layout.create_cell(f"{name}$nfet")
    draw_nfet(n, l=l, w=wn, nf=1, type="sky130_fd_pr__nfet_01v8",
              bulk="bulk tie", gate_con_pos="top")
    p = layout.create_cell(f"{name}$pfet")
    draw_pfet(p, l=l, w=wp, nf=1, type="sky130_fd_pr__pfet_01v8",
              bulk="bulk tie", gate_con_pos="top")

    top = layout.create_cell(name)
    n_b, p_b = n.bbox(), p.bbox()
    p_dx = n_b.right + int(gap * NM) - p_b.left
    top.insert(pya.CellInstArray(n, pya.Trans(pya.Vector(0, 0))))
    top.insert(pya.CellInstArray(p, pya.Trans(pya.Vector(p_dx, 0))))

    n_cols, n_gate = m1_columns(n, M1)
    p_cols, p_gate = m1_columns(p, M1)

    def shift(cols, dx=0):
        return [(a + dx, b, c + dx, d) for a, b, c, d in cols]

    n_cols = shift(n_cols)
    p_cols = shift(p_cols, p_dx)
    p_gate = (p_gate[0] + p_dx, p_gate[1], p_gate[2] + p_dx, p_gate[3])
    ns, nd, nt = n_cols          # left / middle / tap
    ps, pd, pt = p_cols

    # Z2 (upper rail) joins the nfet middle column to the pfet LEFT column.
    # Z1 (lower rail) joins the nfet left column to the pfet middle column.
    # This pairing matters: with the pfet to the right, its left column sits
    # inside any rail that starts at the nfet middle column, so putting Z2 on
    # the pfet middle column shorts Z1 through Z2's rail.  The drop of each
    # net now lands outside the other net's x-span.
    z2_y1, z2_y2 = -360, -130
    z1_y1, z1_y2 = -780, -550
    box(top, M1, nd[0], z2_y1, ps[2], z2_y2)
    box(top, M1, ns[0], z1_y1, pd[2], z1_y2)

    box(top, M1, nd[0], z2_y1, nd[2], nd[1])
    box(top, M1, ps[0], z2_y1, ps[2], ps[1])
    box(top, M1, ns[0], z1_y1, ns[2], ns[1])
    box(top, M1, pd[0], z1_y1, pd[2], pd[1])

    # Ports.  Magic reads a label inside a met1 shape as an m1 port, so the
    # gates and taps need no via stack; the switch has four independent nets.
    label(top, "Z1", (z1_y1 + z1_y2) // 2, ns[0] + 40)
    label(top, "Z2", (z2_y1 + z2_y2) // 2, nd[0] + 40)
    label(top, "S", (n_gate[1] + n_gate[3]) // 2, (n_gate[0] + n_gate[2]) // 2)
    label(top, "SB", (p_gate[1] + p_gate[3]) // 2, (p_gate[0] + p_gate[2]) // 2)
    label(top, "VSS", (nt[1] + nt[3]) // 2, (nt[0] + nt[2]) // 2)
    label(top, "VDD", (pt[1] + pt[3]) // 2, (pt[0] + pt[2]) // 2)
    return top


def label(cell, text, y, x):
    cell.shapes(M1_LABEL).insert(pya.Text(text, pya.Trans(pya.Vector(int(x), int(y)))))


def main():
    out = os.environ.get("OUT", "/tmp/adcgen/layout/SARADC_SW_TG.gds")
    profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pdk", "sky130A_hd.json")
    with open(profile_path) as fh:
        profile = json.load(fh)
    size = profile["analog"].get("switch_sizing_um") or profile["analog"]["sizing_um"]
    wn = float(os.environ.get("WN", size["wn"]))
    wp = float(os.environ.get("WP", size["wp"]))
    l = float(os.environ.get("L", size["l"]))
    gap = float(os.environ.get("GAP", 1.0))

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

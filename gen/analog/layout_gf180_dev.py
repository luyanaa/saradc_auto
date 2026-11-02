#!/usr/bin/env python3
"""gf180mcu primitive NMOS leaf (W=0.82, L=0.6).

    klayout -b -r gen/analog/layout_gf180_dev.py

The PDK ships no MOSFET primitive (libs.ref has only BJTs), and cropping one
out of a stdcell fails because the cell shares one active strip across both
devices. This draws the device from the layer map instead:

    active 22/0   poly 30/0   contact 33/0   metal1 34/0 (label 34/10)
    nwell 21/0    pwell 204/0  implants 31/0, 32/0

Layer numbers come from libs.tech/klayout/tech/gf180mcu.map and the stdcell
GDS layer dump.
"""

from __future__ import annotations

import os

import pya

OUT = os.environ.get("OUT", "/tmp/adcgen/flow/gf180/leaf_nmos.gds")

ACT, POLY, CT, M1, NW, PW, IMP31, IMP32 = (
    (22, 0), (30, 0), (33, 0), (34, 0), (21, 0), (204, 0), (31, 0), (32, 0))
LBL = (34, 10)

X0, Y0 = 0.0, 0.0
W, L = 0.82, 0.6
ACT_H = 1.6          # active height, W plus contact margins
GX0 = 0.5            # gate left edge
GX1 = GX0 + L


def build() -> None:
    ly = pya.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("gf_nmos")

    def box(layer, x1, y1, x2, y2):
        # gf180 wants every vertex on the 5 nm manufacturing grid
        snap = lambda v: int(round(v * 1000 / 5.0)) * 5
        top.shapes(ly.layer(*layer)).insert(
            pya.Box(*(snap(v) for v in (x1, y1, x2, y2))))

    box(ACT, X0, Y0, X0 + GX1 + 0.5, Y0 + ACT_H)
    box(POLY, X0 + GX0, Y0 - 0.5, X0 + GX1, Y0 + ACT_H + 0.5)
    box(PW, X0 - 0.4, Y0 - 0.9, X0 + GX1 + 3.4, Y0 + ACT_H + 0.9)
    box(IMP32, X0 - 0.2, Y0 - 0.73, X0 + GX1 + 0.7, Y0 + ACT_H + 0.53)

    # source / drain / gate contacts (0.22) and met1 pads
    for cx in (X0 + 0.22, X0 + GX1 + 0.28):
        box(CT, cx - 0.11, Y0 + ACT_H / 2 - 0.11, cx + 0.11, Y0 + ACT_H / 2 + 0.11)
        box(M1, cx - 0.25, Y0 + ACT_H / 2 - 0.25, cx + 0.25, Y0 + ACT_H / 2 + 0.25)
    gx = X0 + (GX0 + GX1) / 2
    gy = Y0 + ACT_H + 0.3
    box(CT, gx - 0.11, gy - 0.11, gx + 0.11, gy + 0.11)
    box(M1, gx - 0.25, gy - 0.25, gx + 0.25, gy + 0.25)

    # substrate tap: p+ active with a contact to metal1
    box(ACT, 2.2, 0.4, 3.2, 1.2)
    box(IMP31, 2.0, 0.2, 3.4, 1.4)
    box(CT, 2.59, 0.69, 2.81, 0.91)
    box(M1, 2.45, 0.55, 2.95, 1.05)
    # name the substrate node directly on the pwell text layer
    top.shapes(ly.layer(204, 10)).insert(pya.Text("B", pya.Trans(
        pya.Vector(1000, 500))))

    for text, x, y in (("S", X0 + 0.05, Y0 + ACT_H / 2),
                       ("D", X0 + GX1 + 0.45, Y0 + ACT_H / 2),
                       ("G", gx, gy + 0.25)):
        top.shapes(ly.layer(*LBL)).insert(pya.Text(text, pya.Trans(
            pya.Vector(int(x * 1000), int(y * 1000)))))

    ly.write(OUT)
    print(f"wrote {OUT}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    build()

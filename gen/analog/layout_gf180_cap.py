#!/usr/bin/env python3
"""gf180mcu cap_unit: primitive nmos + pmos, gates on VSH, S/D on FL.

    klayout -b -r gen/analog/layout_gf180_cap.py

Both devices are drawn from primitives (active 22/0, poly 30/0, contact
33/0, metal1 34/0, pwell 204/0, nwell 21/0, implants 31/0/32/0), snapped to
the 5 nm grid.  The bodies are named by well text labels (204/10 and 21/10)
rather than by routing the tap contacts; that is what made the gf180 LVS
match on the single devices.
"""

from __future__ import annotations

import os

import pya

OUT = os.environ.get("OUT", "/tmp/adcgen/flow/gf180/cap_unit.gds")
ACT, POLY, CT, M1 = (22, 0), (30, 0), (33, 0), (34, 0)
NW, PW, IMP31, IMP32 = (21, 0), (204, 0), (31, 0), (32, 0)
LBL = (34, 10)
W, L, ACT_H, GX0 = 1.6, 0.6, 1.6, 0.5


def build() -> None:
    ly = pya.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("gf_cap_unit")

    def snap(v):
        return int(round(v * 1000 / 5.0)) * 5

    def box(layer, x1, y1, x2, y2):
        top.shapes(ly.layer(*layer)).insert(
            pya.Box(*(snap(v) for v in (x1, y1, x2, y2))))

    def text(value, layer, x, y):
        top.shapes(ly.layer(*layer)).insert(pya.Text(
            value, pya.Trans(pya.Vector(snap(x), snap(y)))))

    def device(kind: str, dy: float):
        p = kind == "p"
        box(ACT, 0, dy, GX0 + L + 0.5, dy + ACT_H)
        box(POLY, GX0, dy - 0.5, GX0 + L, dy + ACT_H + 0.5)
        if p:
            box(NW, -0.8, dy - 0.9, GX0 + L + 3.6, dy + ACT_H + 0.9)
            box(IMP31, -0.2, dy - 0.73, GX0 + L + 0.7, dy + ACT_H + 0.53)
        else:
            box(PW, -0.4, dy - 0.9, GX0 + L + 3.4, dy + ACT_H + 0.9)
            box(IMP32, -0.2, dy - 0.73, GX0 + L + 0.7, dy + ACT_H + 0.53)
        for cx in (0.22, GX0 + L + 0.28):
            cy = dy + ACT_H / 2
            box(CT, cx - 0.11, cy - 0.11, cx + 0.11, cy + 0.11)
            box(M1, cx - 0.25, cy - 0.25, cx + 0.25, cy + 0.25)
        gx, gy = GX0 + L / 2, dy + ACT_H + 0.3
        box(CT, gx - 0.11, gy - 0.11, gx + 0.11, gy + 0.11)
        box(M1, gx - 0.25, gy - 0.25, gx + 0.25, gy + 0.25)
        # body tap for the latch-up rule; the body net itself is the well text
        box(ACT, 2.2, dy + 0.4, 3.2, dy + 1.2)
        box(IMP32 if p else IMP31, 2.0, dy + 0.2, 3.4, dy + 1.4)
        box(CT, 2.59, dy + 0.69, 2.81, dy + 0.91)
        box(M1, 2.45, dy + 0.55, 2.95, dy + 1.05)

    device("n", 0.0)
    device("p", 4.5)

    # VSH on metal2: a long metal1 bar runs parallel to the source/drain pads
    # and breaks the 0.23 um spacing rule, so the gate bus goes up a layer
    for gy in (1.90, 6.40):
        box((35, 0), 0.67, gy - 0.13, 0.93, gy + 0.13)
    box((36, 0), 0.60, 1.75, 1.00, 6.55)
    # FL: a metal1 bar across each device's pads, joined outside the pad
    # columns so the run never sits parallel to the gate pad
    box(M1, -0.03, 0.30, 1.86, 0.60)
    box(M1, -0.03, 4.80, 1.86, 5.10)
    box(M1, 1.63, 0.30, 1.86, 5.10)

    text("VSH", (36, 10), 0.80, 3.20)
    text("FL", LBL, 0.30, 0.40)
    text("VPW", (204, 10), 0.50, 0.20)
    text("VNW", (21, 10), 0.50, 4.70)

    ly.write(OUT)
    print(f"wrote {OUT}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    build()

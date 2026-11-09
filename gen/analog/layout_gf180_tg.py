#!/usr/bin/env python3
"""gf180mcu switch_tg: nmos + pmos transmission gate, DRC/LVS/PEX leaf.

    klayout -b -r gen/analog/layout_gf180_tg.py

Port contract Z1 Z2 S SB VSS VDD.  Both devices are drawn from
primitives (active 22/0, poly 30/0, contact 33/0, metal1 34/0, pwell
204/0, nwell 21/0, implants 31/0/32/0), 5 nm grid, same recipe as
layout_gf180_dev.py / layout_gf180_cap.py.  Bodies are named by well
text labels (204/10 pwell -> VSS, 21/10 nwell -> VDD) instead of routing
tap contacts -- that is what made the single-device LVS match.

Transmission-gate pairing is symmetric, so the no-mirror pairing is the
sky130/ihp one: Z1 ties nmos-left + pmos-left, Z2 ties nmos-right +
pmos-right.  With the pmos stacked above the nmos the two straps are
plain vertical risers outside the device bodies -- no crossing metal.
"""

from __future__ import annotations

import os

import pya

OUT = os.environ.get("OUT", "/tmp/adcgen/flow/gf180/switch_tg.gds")
ACT, POLY, CT, M1 = (22, 0), (30, 0), (33, 0), (34, 0)
NW, PW, IMP31, IMP32 = (21, 0), (204, 0), (31, 0), (32, 0)
LBL = (34, 10)
VIA1, M2 = (35, 0), (36, 0)
W, L, ACT_H, GX0 = 1.6, 0.6, 1.6, 0.5


def build() -> None:
    ly = pya.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("gf_switch_tg")

    def snap(v):
        return int(round(v * 1000 / 5.0)) * 5

    def box(layer, x1, y1, x2, y2):
        top.shapes(ly.layer(*layer)).insert(
            pya.Box(*(snap(v) for v in (x1, y1, x2, y2))))

    def text(value, layer, x, y):
        top.shapes(ly.layer(*layer)).insert(pya.Text(
            value, pya.Trans(pya.Vector(snap(x), snap(y)))))

    def device(kind: str, dy: float):
        """One primitive fet at row dy; returns its pad coordinates."""
        p = kind == "p"
        box(ACT, 0, dy, GX0 + L + 0.5, dy + ACT_H)
        box(POLY, GX0, dy - 0.5, GX0 + L, dy + ACT_H + 0.5)
        if p:
            box(NW, -0.8, dy - 0.9, GX0 + L + 3.6, dy + ACT_H + 0.9)
            box(IMP31, -0.2, dy - 0.73, GX0 + L + 0.7, dy + ACT_H + 0.53)
        else:
            box(PW, -0.4, dy - 0.9, GX0 + L + 3.4, dy + ACT_H + 0.9)
            box(IMP32, -0.2, dy - 0.73, GX0 + L + 0.7, dy + ACT_H + 0.53)
        cy = dy + ACT_H / 2
        for cx in (0.22, GX0 + L + 0.28):
            box(CT, cx - 0.11, cy - 0.11, cx + 0.11, cy + 0.11)
            box(M1, cx - 0.25, cy - 0.25, cx + 0.25, cy + 0.25)
        gx, gy = GX0 + L / 2, dy + ACT_H + 0.3
        box(CT, gx - 0.11, gy - 0.11, gx + 0.11, gy + 0.11)
        box(M1, gx - 0.25, gy - 0.25, gx + 0.25, gy + 0.25)
        # body tap for the latch-up rule; body net is the well text label
        box(ACT, 2.2, dy + 0.4, 3.2, dy + 1.2)
        box(IMP32 if p else IMP31, 2.0, dy + 0.2, 3.4, dy + 1.4)
        box(CT, 2.59, dy + 0.69, 2.81, dy + 0.91)
        box(M1, 2.45, dy + 0.55, 2.95, dy + 1.05)
        return {
            "left": (-0.03, cy - 0.25, 0.47, cy + 0.25),
            "right": (GX0 + L + 0.03, cy - 0.25, GX0 + L + 0.53, cy + 0.25),
            "gate": (gx - 0.25, gy - 0.25, gx + 0.25, gy + 0.25),
        }

    p_dy = 4.5
    n = device("n", 0.0)
    p = device("p", p_dy)

    # Z1: nmos-left -> left riser -> pmos-left on m1.  The riser column
    # (pad centre x ~0.22) is far from the gate and tap pads, so it can
    # run straight up in m1.
    lx = (n["left"][0] + n["left"][2]) / 2   # pad centre x ~ 0.22
    box(M1, -0.60, n["left"][1], n["left"][2], n["left"][3])   # jog left
    box(M1, -0.60, n["left"][1], -0.35, p["left"][3])          # riser
    box(M1, -0.60, p["left"][1], p["left"][2], p["left"][3])   # jog back
    # Z2: nmos-right -> pmos-right on metal2.  The pad column sits 0.08
    # um from the gate pad and the body tap pad (x 2.45-2.95) blocks the
    # only clear m1 lane, so the link hops to metal2 over each pad:
    # via1 on each right pad, an m2 riser between them at x 3.3-3.55 --
    # right of the tap pad (ends 2.95) plus the 0.23 um keep-out.
    rx = (n["right"][0] + n["right"][2]) / 2  # pad centre x ~ GX0+L+0.28
    # via1 is a fixed 0.26 um square; keep 0.06 um m1/m2 enclosure.
    for pad in (n["right"], p["right"]):
        cx = (pad[0] + pad[2]) / 2
        cy = (pad[1] + pad[3]) / 2
        box(VIA1, cx - 0.13, cy - 0.13, cx + 0.13, cy + 0.13)
    box(M2, n["right"][0], n["right"][1] - 0.02, 3.58, n["right"][3] + 0.02)
    box(M2, 3.28, n["right"][1] - 0.02, 3.58, p["right"][3] + 0.02)
    box(M2, p["right"][0], p["right"][1] - 0.02, 3.58, p["right"][3] + 0.02)

    text("Z1", LBL, -0.47, 2.2)
    text("Z2", (36, 10), 3.42, 2.2)
    text("S", LBL, GX0 + L / 2, 1.9)
    text("SB", LBL, GX0 + L / 2, p_dy + ACT_H + 0.3)
    text("VSS", (204, 10), 0.5, 0.2)
    text("VDD", (21, 10), 0.5, p_dy + 0.2)

    ly.write(OUT)
    print(f"wrote {OUT}  bbox={top.bbox().to_s()}")


if __name__ == "__main__":
    build()

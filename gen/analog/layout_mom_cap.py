#!/usr/bin/env python3
"""MOM (metal-oxide-metal / fringe) unit-capacitor PCell for Bug3.

    klayout -b -r gen/analog/layout_mom_cap.py -rd pdk=ihp_sg13g2 \
            -rd c_ff=2.0 -rd out=/tmp/mom.gds

Draws a lateral-flux capacitor: two interdigitated metal combs on the
layers named by the PDK's ``layers.json`` ``momcap`` recipe.  The two
terminals are VSH (top plate) and FL (switched bottom plate); a grounded
shield ring on the same metal frames the fingers and gives the plate a
deterministic substrate parasitic instead of the MOSCAP's channel.

This is the physical counterpart of ``topo_cap_mom`` in macro_gen.py:
the schematic is an ideal capacitor + a Csub parasitic, and this layout
is what makes that ideal assumption honest (fringe C has < ~10 ppm/V
voltage coefficient vs the MOSCAP's ~1.8x swing).

Finger count is solved from the target capacitance using a simple
parallel-plate-plus-fringe estimate, then reported; the exact value is
confirmed by PEX, not trusted from the drawing.
"""

from __future__ import annotations

import os
import sys

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layers as _layers  # noqa: E402

PDK = os.environ.get("pdk", os.environ.get("PDK", "ihp_sg13g2"))
C_FF = float(os.environ.get("c_ff", "2.0"))
OUT = os.environ.get("out", os.environ.get("OUT",
                     "/tmp/adcgen/flow/mom_cap.gds"))

L = _layers.load(PDK)
REC = L.momcap()
FW = float(REC.get("finger_width_um", 0.16))   # finger width
FG = float(REC.get("finger_gap_um", 0.16))     # gap between fingers
MLAYERS = REC.get("layers", ["M1", "M2"])
SHIELD_W = 0.5     # grounded shield ring width
MARGIN = 0.5       # ring-to-finger clearance


def emit_cell(ly, name, c_ff):
    """Draw one MOM unit cap.  Returns (cell, achieved_ff_estimate)."""
    cell = ly.create_cell(name)
    dbu2um = 1.0 / 1000.0  # dbu = 0.001

    # --- size the fingers ---------------------------------------------
    # lateral fringe cap per unit perimeter, ~0.2 fF/um of facing edge is a
    # conservative first-order number for thin metals; PEX refines it.
    fringe_ff_per_um = 0.20
    # square-ish plate; total facing edge length needed
    need_um = c_ff / fringe_ff_per_um
    # each finger pair contributes ~2*finger_len of facing edge
    finger_len = 3.0
    n_pairs = max(2, int(round(need_um / (2 * finger_len))))
    pitch = FW + FG
    width = n_pairs * pitch + FG            # fingers + outer gaps
    height = finger_len + 2 * MARGIN + FG

    def box(layer, x1, y1, x2, y2):
        cell.shapes(layer).insert(pya.Box(
            int(round(x1 / dbu2um)), int(round(y1 / dbu2um)),
            int(round(x2 / dbu2um)), int(round(y2 / dbu2um))))

    def label(layer, text, x, y):
        cell.shapes(layer).insert(pya.Text(text, pya.Trans(pya.Vector(
            int(round(x / dbu2um)), int(round(y / dbu2um))))))

    for mname in MLAYERS:
        m = ly.layer(*L.layer(mname))
        mt = ly.layer(*L.layer(mname, "pin"))
        # interdigitated combs: VSH fingers hang DOWN from the top bus, FL
        # fingers hang UP from the bottom bus, alternating so every finger's
        # two long edges face the opposite terminal.  Both buses run full
        # width so every finger is connected (no dangling metal).
        for i in range(n_pairs):
            x = FG + i * pitch
            if i % 2 == 0:
                # VSH finger: from the top bus down into the array
                box(m, x, MARGIN + FG, x + FW, MARGIN + FG + finger_len)
            else:
                # FL finger: from the bottom bus up into the array
                box(m, x, MARGIN, x + FW, MARGIN + finger_len)
        # comb buses: VSH across the top, FL across the bottom
        box(m, 0, MARGIN + FG + finger_len, width,
            MARGIN + FG + finger_len + FW)          # VSH bus (top)
        box(m, 0, 0, width, MARGIN)                          # FL bus (bottom)
        # grounded shield ring around the perimeter
        box(m, -SHIELD_W - MARGIN, -SHIELD_W, -MARGIN, height + SHIELD_W)
        box(m, width + MARGIN, -SHIELD_W, width + MARGIN + SHIELD_W,
            height + SHIELD_W)
        box(m, -SHIELD_W - MARGIN, height + MARGIN,
            width + MARGIN + SHIELD_W, height + MARGIN + SHIELD_W)
        box(m, -SHIELD_W - MARGIN, -SHIELD_W - MARGIN,
            width + MARGIN + SHIELD_W, -MARGIN)
        # terminal labels on the pin purpose
        label(mt, "VSH", width / 2, MARGIN + FG + finger_len + FW / 2)
        label(mt, "FL", width / 2, MARGIN / 2)
        label(mt, "VSS", -SHIELD_W / 2 - MARGIN, height / 2)

    achieved = n_pairs * 2 * finger_len * fringe_ff_per_um
    return cell, achieved


def main():
    ly = pya.Layout()
    ly.dbu = 0.001
    cell, achieved = emit_cell(ly, "SARADC_CAP_MOM", C_FF)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    ly.write(OUT)
    print(f"wrote {OUT}  cell={cell.name}  target={C_FF} fF "
          f"est={achieved:.2f} fF  fingers on {MLAYERS}")


if __name__ == "__main__":
    main()

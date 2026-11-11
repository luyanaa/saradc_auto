#!/usr/bin/env python3
"""MOM (metal-oxide-metal / fringe) unit-capacitor PCell for Bug3.

    klayout -b -r gen/analog/layout_mom_cap.py

Environment:
    pdk    PDK id in gen/pdk/<pdk>.layers.json  (default ihp_sg13g2)
    c_ff   target capacitance in fF                (default 2.0)
    out    output GDS                              (default /tmp/adcgen/flow/mom_cap.gds)

Draws a lateral-flux capacitor: two interdigitated metal combs on the
layers named by the PDK's ``layers.json`` ``momcap`` recipe.  The two
terminals are VSH (top plate) and FL (switched bottom plate).

Geometry is shared with ``layout_mom_array.py`` through ``mom_geom.py``.
The two metal layers carry the same comb and are stitched by a VIA1 row on
each bus -- without those vias the M1 comb floats and the extracted
capacitance is half of what the drawing claims (that was true of the
first revision of this leaf).

The leaf is deliberately ring-free so it can abut in an array; the guard
ring lives around the whole array.  DRC/LVS on this cell is in
``closure.py``; the reported capacitance comes from Magic PEX, not from
the analytic estimate below.
"""

from __future__ import annotations

import os
import sys

import pya

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layers as _layers  # noqa: E402
import mom_geom  # noqa: E402

PDK = os.environ.get("pdk", os.environ.get("PDK", "ihp_sg13g2"))
C_FF = float(os.environ.get("c_ff", "2.0"))
OUT = os.environ.get("out", os.environ.get("OUT",
                     "/tmp/adcgen/flow/mom_cap.gds"))

L = _layers.load(PDK)
REC = L.momcap()
FW = float(REC.get("finger_width_um", 0.16))   # finger width
FG = float(REC.get("finger_gap_um", 0.16))     # gap between fingers
MLAYERS = REC.get("layers", ["M1", "M2"])

# conservative first-order lateral fringe capacitance per um of facing edge
FRINGE_FF_PER_UM = 0.20
FINGER_LEN = 3.0


def emit_cell(ly, name, c_ff):
    """Draw one MOM unit cap.  Returns (cell, analytic_estimate_ff)."""
    cell = ly.create_cell(name)
    dbu2um = 1.0 / 1000.0

    n_fingers = mom_geom.finger_count(c_ff, FRINGE_FF_PER_UM, FINGER_LEN)
    g = mom_geom.leaf(FW, FG, FINGER_LEN, n_fingers)

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
        # interdigitated fingers, both buses full width
        for net, x0, y0, x1, y1 in g["fingers"]:
            box(m, x0, y0, x1, y1)
        box(m, 0, g["fl_bus"][0], g["width"], g["fl_bus"][1])
        box(m, 0, g["vsh_bus"][0], g["width"], g["vsh_bus"][1])
        # terminal labels on the pin purpose (KLayout LVS) and on the
        # drawing purpose (Magic's LEF writer only sees labels on the
        # metal layer itself; 8/25 alone produces an all-OBS LEF)
        label(mt, "VSH", g["width"] / 2, (g["vsh_bus"][0] + g["vsh_bus"][1]) / 2)
        label(mt, "FL", g["width"] / 2, (g["fl_bus"][0] + g["fl_bus"][1]) / 2)
        label(m, "VSH", g["width"] / 2, (g["vsh_bus"][0] + g["vsh_bus"][1]) / 2)
        label(m, "FL", g["width"] / 2, (g["fl_bus"][0] + g["fl_bus"][1]) / 2)

    # M1 <-> M2 stitching so the two combs are one node
    vl = ly.layer(*L.layer("VIA1"))
    mom_geom.stitch(box, {"VIA1": vl}, g)

    achieved = n_fingers * FINGER_LEN * FRINGE_FF_PER_UM
    return cell, achieved, g


def write_lef(path: str, name: str, g: dict, lef_layers: dict,
              m_layers: list[str]) -> None:
    """Emit the abstract for OpenROAD from the same geometry (um)."""
    w, h = g["width"], g["height"]
    m1, m2 = (lef_layers.get(m, m) for m in m_layers[:2])

    def rect(layer, x1, y1, x2, y2):
        return f"      LAYER {layer} ;\n        RECT {x1:.3f} {y1:.3f} {x2:.3f} {y2:.3f} ;\n"

    def pin(pname, y0, y1):
        body = rect(m1, 0, y0, w, y1) + rect(m2, 0, y0, w, y1)
        return (f"  PIN {pname}\n    DIRECTION INOUT ;\n    USE SIGNAL ;\n"
                f"    PORT\n{body}    END\n  END {pname}\n")

    obs = ""
    for net, x0, y0, x1, y1 in g["fingers"]:
        obs += rect(m1, x0, y0, x1, y1)
        obs += rect(m2, x0, y0, x1, y1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("VERSION 5.7 ;\nBUSBITCHARS \"[]\" ;\nDIVIDERCHAR \"/\" ;\n")
        fh.write(f"MACRO {name}\n  CLASS BLOCK ;\n  FOREIGN {name} ;\n")
        fh.write("  ORIGIN 0.000 0.000 ;\n")
        fh.write(f"  SIZE {w:.3f} BY {h:.3f} ;\n  SYMMETRY X Y ;\n")
        fh.write(pin("VSH", g["vsh_bus"][0], g["vsh_bus"][1]))
        fh.write(pin("FL", g["fl_bus"][0], g["fl_bus"][1]))
        fh.write(f"  OBS\n{obs}  END\nEND {name}\nEND LIBRARY\n")


def main():
    ly = pya.Layout()
    ly.dbu = 0.001
    cell, achieved, g = emit_cell(ly, "SARADC_CAP_MOM", C_FF)
    out_dir = os.path.dirname(OUT)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    ly.write(OUT)
    lef = os.path.splitext(OUT)[0] + ".lef"
    write_lef(lef, "SARADC_CAP_MOM", g,
              REC.get("lef_layers") or {"M1": "Metal1", "M2": "Metal2"},
              MLAYERS)
    print(f"wrote {OUT} + {lef}  cell={cell.name}  target={C_FF} fF "
          f"est={achieved:.2f} fF  {g['width']:.2f}x{g['height']:.2f} um "
          f"fingers={len(g['fingers'])} on {MLAYERS}")


if __name__ == "__main__":
    main()

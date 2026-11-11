#!/usr/bin/env python3
"""Shared geometry for the MOM unit capacitor and its array.

Before this module existed, ``layout_mom_cap.py`` and
``layout_mom_array.py`` each carried their own copy of the comb pitch,
margin and shield constants.  They drifted (the array thought the leaf was
5.41 um tall while the leaf was 6.21 um tall) and the array placed leaves
on a 1.33 um pitch even though each leaf is 3.03 um wide.  DRC stayed clean
because overlapping same-layer metal is not a spacing violation -- it is a
short, and the passive leaf was never LVS-checked.

The leaf is now a plain, tileable two-terminal block:

    y = H  +--------------------------+   VSH bus (M1/M2, via-stitched)
           |  VSH finger   FL finger  |
           |                          |
    y = 0  +--------------------------+   FL bus  (M1/M2, via-stitched)
           x = 0                    W

There is no per-leaf guard ring: the ring is drawn once around the whole
array (``layout_mom_array.py``), which is both the correct shielding
topology and what makes abutting leaves possible.  Every bus carries a
VIA1 array so the M1 and M2 combs are genuinely one node instead of two
floating combs that only share a label.
"""

from __future__ import annotations

BUS_H = 0.50          # bus height (>= 0.19 via + 2 x 0.05 enclosure)
VIA = 0.19            # V1.a is min=max 0.19 -> the cut must be exactly 0.19
VIA_SPACE = 0.24      # V1.b=0.22 with margin; leaves 0.05 keep vias apart
VIA_EDGE = 0.05       # metal enclosure at the cell edge


def finger_count(c_ff: float, fringe_ff_per_um: float, finger_len: float) -> int:
    """Even number of fingers whose facing edge reaches ``c_ff``."""
    need_um = c_ff / fringe_ff_per_um
    n = max(4, 2 * int(round(need_um / (2 * finger_len))))
    return n if n % 2 == 0 else n + 1


def leaf(fw: float, fg: float, finger_len: float, n_fingers: int,
         bus_h: float = BUS_H) -> dict:
    """Return every coordinate the leaf and the array must agree on (um)."""
    if n_fingers % 2:
        raise ValueError("n_fingers must be even (VSH/FL alternate)")
    pitch = fw + fg
    width = 2 * fg + (n_fingers - 1) * pitch + fw
    # Each finger must touch its own bus; the two sets are separated by one
    # fg tip clearance (this is what makes the comb interdigitated instead
    # of two disconnected rows of metal -- the first revision got this
    # wrong and PEX saw every finger as an isolated node).
    height = 2 * bus_h + fg + finger_len
    fingers = []
    for i in range(n_fingers):
        x = fg + i * pitch
        if i % 2 == 0:   # VSH finger hangs from the top bus
            y0, y1 = height - bus_h - finger_len, height - bus_h
            net = "VSH"
        else:            # FL finger rises from the bottom bus
            y0, y1 = bus_h, bus_h + finger_len
            net = "FL"
        fingers.append((net, x, y0, x + fw, y1))
    return dict(width=width, height=height, pitch=pitch,
                fl_bus=(0.0, bus_h),
                vsh_bus=(height - bus_h, height),
                fingers=fingers,
                via_x=via_positions(width, bus_h),
                bus_h=bus_h)


def via_positions(width: float, bus_h: float = BUS_H,
                  size: float = VIA, space: float = VIA_SPACE,
                  edge: float = VIA_EDGE) -> list[float]:
    """Centres of a single row of vias that fits inside a bus of ``width``."""
    out = []
    x = edge + size / 2.0
    while x + size / 2.0 <= width - edge + 1e-9:
        out.append(x)
        x += size + space
    return out


def stitch(emit, layers: dict, geo: dict, bus_h: float = BUS_H,
           size: float = VIA) -> None:
    """Draw the two bus via rows.  ``emit(layer_key, x1, y1, x2, y2)``."""
    for x in geo["via_x"]:
        for y0, y1 in (geo["fl_bus"], geo["vsh_bus"]):
            yc = 0.5 * (y0 + y1)
            emit(layers["VIA1"], x - size / 2, yc - size / 2,
                 x + size / 2, yc + size / 2)

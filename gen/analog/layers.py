#!/usr/bin/env python3
"""PDK abstraction layer: one layer/rule map per PDK, loaded by every
layout_*_array.py instead of each file hardcoding its own (layer,datatype)
pairs and um DRC constants.

Each ``gen/pdk/<pdk>.layers.json`` declares

  layers   -- alias -> {"drawing":[l,d], "pin":[l,d], "text":[l,d]}
  rules_um -- the geometric constants the array needs (pitch, stagger, via
              size, strap bands, ...)
  momcap   -- the metal-fringe unit-capacitor recipe used by the MOM
              generator (Bug3: replaces the non-linear MOS gate cap)

Usage:
    import layers
    L = layers.load(profile_or_pdk_id)
    cell.shapes(L["M1"]).insert(...)          # drawing layer
    text_on  = L.layer("M1", "pin")           # a named purpose
    pitch    = L.rule("row_pitch")            # um constant
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PDK_DIR = os.path.normpath(os.path.join(HERE, "..", "pdk"))


def _pdk_id(profile_or_id):
    if isinstance(profile_or_id, dict):
        return profile_or_id["id"]
    return str(profile_or_id)


def load(profile_or_id):
    pid = _pdk_id(profile_or_id)
    path = os.path.join(PDK_DIR, f"{pid}.layers.json")
    if not os.path.exists(path):
        raise SystemExit(
            f"layers: no {pid}.layers.json -- the PDK abstraction map is "
            f"missing for this PDK (add layer/rule data, do not hardcode)")
    with open(path, encoding="utf-8") as fh:
        return LayerMap(json.load(fh), path)


class LayerMap:
    def __init__(self, data, path):
        self._d = data
        self.path = path

    @property
    def pdk(self):
        return self._d["pdk"]

    # --- layer access -------------------------------------------------
    def layer(self, alias, purpose="drawing"):
        """(layer, datatype) tuple for a KLayout ``cell.layout().layer()``."""
        ent = self._d["layers"].get(alias)
        if ent is None or purpose not in ent:
            raise KeyError(f"{self.pdk}: no layer alias {alias!r} purpose "
                           f"{purpose!r} in {self.path}")
        return tuple(ent[purpose])

    def __getitem__(self, alias):
        return self.layer(alias)

    # --- rule access --------------------------------------------------
    def rule(self, name, default=None):
        v = self._d.get("rules_um", {}).get(name, default)
        return v

    # --- momcap recipe -------------------------------------------------
    def momcap(self):
        return self._d.get("momcap") or {}

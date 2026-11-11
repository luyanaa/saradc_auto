#!/usr/bin/env python3
"""Print the tables of RESULTS.md straight from the runs.

    python3 backend/collect.py            # matrix + per-PDK tables
    python3 backend/collect.py --list     # every run that has metrics.json

Every number is read from `configs/<pdk>/runs/<tag>/final/metrics.json`; nothing
is typed in.  `RUNS` names the run quoted in RESULTS.md for each (PDK, arch), so
a re-run with a new tag only shows up here once it is named below.
"""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")

ARCHS = ("ramp", "flash", "pipeline", "dual_slope", "vco", "sdm", "ti")
PDKS = ("sky130A", "gf180mcuD", "ihp-sg13g2", "ics55")

RUNS = {
    "sky130A": {a: f"{a}-pin1" for a in ARCHS},
    "gf180mcuD": {a: f"{a}-pin1" for a in ARCHS},
    "ihp-sg13g2": {a: f"{a}-pin1" for a in ARCHS},
    # pin2 = pin1 + `DECAP_CELLS: []` (the released decap cells short routed
    # MET1; see ICS55_SIGNOFF.md part 2) + `ERROR_ON_LVS_ERROR: true`.
    # ti runs one tag later: pin3 = pin2 + the two CDL series-order hunks that
    # part 3 describes.  Only `ti` instantiates either cell.
    "ics55": {a: f"{a}-pin2" for a in ARCHS} | {"ti": "ti-pin3"},
}

# ics55 cannot reach KLayout DRC 0: the released standard cells and the M2 pads
# of abutting cells violate the deck's own area minima.  The floor below is what
# the pinned runs measure; a count above it means the layout changed, not that
# the library got worse.  See ICS55_SIGNOFF.md.
ICS55_KLAYOUT_FLOOR = {
    "ramp": 343,
    "flash": 728,
    "pipeline": 333,
    "dual_slope": 437,
    "vco": 508,
    "sdm": 1644,
    "ti": 1185,
}


def load(pdk, arch):
    tag = RUNS[pdk][arch]
    path = os.path.join(ROOT, pdk, "runs", tag, "final", "metrics.json")
    if not os.path.exists(path):
        return None, tag
    with open(path) as fh:
        return json.load(fh), tag


def corners_clear(d, kind):
    ks = [k for k in d if k.startswith(f"timing__{kind}__wns__corner:")]
    return bool(ks) and all(d[k] >= 0 for k in ks)


def verdict(pdk, arch, d):
    if d is None:
        return "no run"
    ok = (
        d.get("route__drc_errors") == 0
        and d.get("antenna__violating__nets", 0) == 0
        and d.get("antenna__violating__pins", 0) == 0
        and d.get("timing__setup__wns", 0) >= 0
        and d.get("timing__hold__wns", 0) >= 0
        and corners_clear(d, "setup")
        and corners_clear(d, "hold")
    )
    if pdk != "ics55":
        ok = ok and d.get("magic__drc_error__count") == 0
        ok = ok and d.get("magic__illegal_overlap__count") == 0
        ok = ok and d.get("klayout__drc_error__count") == 0
        ok = ok and d.get("design__lvs_error__count") == 0
        return "clean" if ok else "**MISS**"
    # ics55: KLayout-only signoff, judged against the recorded library floor.
    ok = ok and d.get("design__lvs_error__count") == 0
    floor = ICS55_KLAYOUT_FLOOR.get(arch)
    if floor is not None:
        ok = ok and d.get("klayout__drc_error__count", 0) <= floor
    return "clean" if ok else "**MISS**"


def fmt(d, key, spec="{v}"):
    v = d.get(key)
    return "–" if v is None else spec.format(v=v)


def main():
    if "--list" in sys.argv:
        for pdk in PDKS:
            for run in sorted(os.listdir(os.path.join(ROOT, pdk, "runs"))):
                p = os.path.join(ROOT, pdk, "runs", run, "final", "metrics.json")
                print(f"{pdk:<12} {run:<16} {'yes' if os.path.exists(p) else 'no'}")
        return

    print("| arch | " + " | ".join(PDKS) + " |")
    print("|---|" + "---|" * len(PDKS))
    for a in ARCHS:
        print(f"| `{a}` | " + " | ".join(verdict(p, a, load(p, a)[0]) for p in PDKS) + " |")

    for pdk in PDKS:
        print(f"\n### {pdk}\n")
        print("| arch | run tag | inst | area µm² | die µm² | density | route DRC | "
              "KLayout DRC | LVS err | setup ws ns | hold ws ns |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for a in ARCHS:
            d, tag = load(pdk, a)
            if d is None:
                print(f"| `{a}` | `{tag}` | – | – | – | – | – | – | – | – | – |")
                continue
            die = d.get("design__die__area") or 0
            den = (d.get("design__instance__area", 0) / die * 100) if die else 0
            print(
                f"| `{a}` | `{tag}` | {fmt(d,'design__instance__count')} | "
                f"{d.get('design__instance__area', 0):.1f} | {die:.1f} | {den:.0f}% | "
                f"{fmt(d,'route__drc_errors')} | {fmt(d,'klayout__drc_error__count')} | "
                f"{fmt(d,'design__lvs_error__count')} | "
                f"{d.get('timing__setup__wns', 0):.3f} | {d.get('timing__hold__wns', 0):.3f} |"
            )


if __name__ == "__main__":
    main()

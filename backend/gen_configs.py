#!/usr/bin/env python3
"""Generate per-(PDK x architecture) LibreLane configuration files.

One source of truth for the digital back end: the portfolio of cores lives in
ARCHS, the PDK facts (PDK id, SCL, power nets, default clock) live in PDKS, and
the cross product is emitted as YAML under backend/configs/<pdk>/<arch>.yaml.

Only facts that differ per PDK belong here.  Nothing here knows about analog
macros: these are the seven digital cores, harden-only, no pads.

Usage:
    backend/gen_configs.py            # (re)write every config
    backend/gen_configs.py ramp       # only the named architectures
    backend/gen_configs.py --check    # fail if a file is out of date
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# ---------------------------------------------------------------------------
# Architectures.  "files" are relative to the repository root; "include" is the
# RTL include directory.  Clock period is the *generic* value in ns; a PDK can
# override it in PDKS["clock_period"].
# ---------------------------------------------------------------------------
ARCHS = {
    "ramp": {
        "top": "ramp_core",
        "files": ["rtl/ramp/ramp_core.v"],
        "clock_period": 10,
        "desc": "single-slope counter + comparator FSM",
    },
    "flash": {
        "top": "flash_core",
        "files": ["rtl/flash/flash_core.v", "rtl/common/therm2bin.v"],
        "clock_period": 10,
        "desc": "thermometer pop-count encoder",
    },
    "pipeline": {
        "top": "pipeline_core",
        "files": ["rtl/pipeline/pipeline_core.v", "rtl/common/clk_2ph.v"],
        "clock_period": 10,
        "desc": "1.5-bit/stage RSD combiner + two-phase clock",
    },
    "dual_slope": {
        "top": "dual_slope_core",
        "files": ["rtl/dual_slope/dual_slope_core.v"],
        "clock_period": 10,
        "desc": "integrate/de-integrate sequencer",
    },
    "vco": {
        "top": "vco_core",
        "files": ["rtl/vco/vco_core.v"],
        "clock_period": 10,
        "desc": "Gray counter + CDC + window difference",
    },
    "sdm": {
        "top": "sdm_core",
        "files": ["rtl/sdm/sdm_core.v"],
        "clock_period": 10,
        "desc": "CIC4 decimator",
    },
    "ti": {
        "top": "ti_adc",
        "files": [
            "rtl/sar_logic_wreset.v",
            "rtl/ti/ti_phase.v",
            "rtl/ti/ti_combine.v",
            "rtl/ti/ti_adc.v",
        ],
        "clock_period": 20,   # 256-cycle foreground calibration; relaxed on purpose
        "desc": "two-channel time-interleaved SAR",
    },
    "sar": {
        # The SAR digital core alone (Bug10): the block the hierarchical PEX
        # consumes as a gate-level SPEF.  Its analog counterpart (CDAC + sw +
        # cmp) extracts on its own GDS; this core is pure standard cells.
        "top": "sar_logic_wreset",
        "files": ["rtl/sar_logic_wreset.v"],
        "clock_port": "CLK",   # the core's clock pin is CLK, not clk
        "clock_period": 10,
        "desc": "SAR controller FSM (standalone, for hierarchical PEX)",
    },
}

# ---------------------------------------------------------------------------
# PDKs.
#   dir            subdirectory under backend/configs
#   pdk, scl       LibreLane --pdk / --scl
#   pdk_root       where the composed root will be (see pdkroot.sh)
#   vdd, gnd       design power nets (must match the SCL cell PG pins)
#   clock_period   optional override
# ---------------------------------------------------------------------------
PDKS = {
    "ihp-sg13g2": {
        "pdk": "ihp-sg13g2",
        "scl": "sg13g2_stdcell",
        "pdk_root": "backend/pdkroot",
        "vdd": ["VDD"],
        "gnd": ["VSS"],
        "clock_period": 10,
        "notes": "130 nm, 1.2 V core.  PDN_RAIL_WIDTH must be 0.44 (vendor "
                 "value); the local overlay restores the vendor librelane "
                 "config that the Ciel cache shipped in a broken form.",
        # CTS routes the clock with `-apply_ndr half`, which creates a 2x-width
        # NDR for the top half of the clock tree, but LibreLane leaves
        # NON_DEFAULT_RULES and DRT_ASSIGN_NDR unset -- so detailed routing
        # judges those pre-routed trunks with default-layer spacing and cannot
        # repair the violations it creates next to them.  On `pipeline` that
        # left 2 Metal3 spacings between `clk` (0.40 um wide, i.e. 2x the
        # 0.20 um minimum) and `phi2`/`rst_n`, which DRT kept re-reporting
        # across 40+ iterations.  Declaring the same rule makes DRT aware of
        # it; the numbers are the 2x of the Metal3 minimum width and of the
        # spacing table in `sg13g2_tech.lef`; the rule is written as the same 2x
        # multipliers CTS uses, so it applies per layer.
        "extra": """
# The clock NDR that CTS actually applies (`-apply_ndr half`), declared so
# detailed routing obeys it as well.  See backend/gen_configs.py.
NON_DEFAULT_RULES:
  clk_half:
    width: ["*2"]
    spacing: ["*2"]
    via: null
DRT_ASSIGN_NDR:
  "^clk$": clk_half
""",
        # `sdm` is the only ihp core that misses setup at the shared 10 ns
        # period: -0.245 ns WNS on 1 violating path at nom_slow_1p08V_125C, with
        # every other corner and every other core at 0.  A delay-driven
        # synthesis + sizing rerun (runs/opt) was tried first and made it worse
        # (-1.008 ns, 14 paths, +30% area: the extra area cost more congestion
        # than the sizing bought), so it is not used.  The core is specified at
        # 11 ns instead; see RESULTS.md for the per-architecture design points.
        "arch_period": {"sdm": 11},
    },
    "sky130A": {
        "pdk": "sky130A",
        "scl": "sky130_fd_sc_hd",
        "pdk_root": "backend/pdkroot",
        "vdd": ["VPWR"],
        "gnd": ["VGND"],
        "clock_period": 20,
        "notes": "180 nm, 1.8 V.  Vendor config is OpenLane-format "
                 "(libs.tech/openlane); LibreLane migrates it.",
    },
    "gf180mcuD": {
        "pdk": "gf180mcuD",
        "scl": "gf180mcu_as_sc_mcu7t3v3",
        "pdk_root": "backend/pdkroot",
        "vdd": ["VDD"],
        "gnd": ["VSS"],
        "clock_period": 20,
        "notes": "180 nm, 3.3 V AS 7-track cells on the gf180mcuD process. "
                 "gf180mcuA/B/C are deprecated and cannot produce a valid "
                 "signoff deck, so D is the only variant used.  The AS library "
                 "ships outside the PDK tree; the overlay links it into "
                 "libs.ref so the D magic/KLayout/netgen tech applies.",
    },
    "ics55": {
        "pdk": "ics55",
        "scl": "ics55_LLSC_H7CH",
        "pdk_root": "backend/pdkroot",
        "vdd": ["VDD"],
        "gnd": ["VSS"],
        "clock_period": 10,
        "notes": "55 nm.  PDK id must be 'ics55'; needs the vendor plugin on "
                 "PYTHONPATH (see backend/run.sh).  Signoff is KLayout-only: "
                 "the PDK's magic tech does not carry DRC rules, so the Magic "
                 "steps are substituted exactly as the vendor's own "
                 "examples/counter/config.yaml does.",
        # Two separate quirks of this PDK, both with the vendor's blessing:
        #
        # 1. The PDK-level librelane config leaves LAYERS_RC/VIAS_R unset
        #    ("TODO" in libs.tech/librelane/config.tcl), so
        #    OpenROAD.IRDropReport aborts with [PSM-0021] "Resistance map
        #    contains invalid values".  The values are the released-LEF
        #    numbers that also ship in the SCL's layers_rc.tcl; they have to
        #    be repeated here because they are per-design YAML, not Tcl env.
        #
        # 2. The magic tech file only does parasitic extraction, so
        #    Magic.StreamOut hangs and Magic.DRC has no rules.  Signoff runs
        #    through KLayout: KLayout streams the GDS, KLayout.DRC is the DRC
        #    and the vendor plugin's ICS55.KLayoutLVS replaces Netgen.LVS.
        #    This is the substitution set from
        #    libs.tech/librelane/examples/counter/config.yaml and from the
        #    plugin's own docstring, i.e. a PDK capability statement rather
        #    than a shortcut around a failure.
        "extra": """
# PDN sheet resistance / via resistance, from libs.tech/librelane/
# ics55_LLSC_H7CH/layers_rc.tcl (released-LEF values).  Without these
# OpenROAD.IRDropReport dies on an invalid resistance map.
LAYERS_RC:
  "nom_*":
    MET1: {res: 0.1122, cap: 0.0007630}
    MET2: {res: 0.0914, cap: 0.0011069}
    MET3: {res: 0.0914, cap: 0.0011069}
    MET4: {res: 0.0914, cap: 0.0011069}
    MET5: {res: 0.0914, cap: 0.0006259}
    T4M2: {res: 0.0239, cap: 0.0001299}
VIAS_R:
  "nom_*":
    VIA1: {res: 2.5}
    VIA2: {res: 2.5}
    VIA3: {res: 2.5}
    VIA4: {res: 2.5}
    T4V2: {res: 2.5}

# GDS stream-out and signoff DRC/LVS run in KLayout; see the comment in
# backend/gen_configs.py.
#
# The PDK points KLAYOUT_DEF_LAYER_MAP at libs.tech/klayout/tech/ics55.map,
# which is a GDS-import map: its names are M1/V1/TM2 while the LEFs say
# MET1/VIA1/T4M2, so nothing matches, every "No mapping for layer ..." is
# dropped, and the streamed GDS ends up with no routing and no pin labels.
# Use a real LEF/DEF map instead (added by backend/pdk_overlay/ics55).
KLAYOUT_DEF_LAYER_MAP: dir::../../../backend/pdk_overlay/ics55/libs.tech/klayout/tech/ics55_def.map
PRIMARY_GDSII_STREAMOUT_TOOL: klayout
# KLayout DRC defines reach the deck as `-rd key=value`.  LibreLane 3.0.17
# renamed KLAYOUT_DRC_OPTIONS to KLAYOUT_DRC_DEFINES and stringifies every
# value, so spell the string out instead of relying on a YAML bool.
KLAYOUT_DRC_DEFINES:
  no_density: "true"
KLAYOUT_LVS_OPTIONS:
  run_mode: deep

# The deck cannot reach 0 on the released library, so the flow must not abort
# on it.  Measured on `ramp` (runs/ramp-r5): 288 items, of which 258 carry a
# standard-cell name in the .lyrdb <cell> field -- 174 M1DUM_W_1 raised by the
# pin *labels* that the released cells store on 81/2 (the dummy layer), 77
# metal.m1.area slivers inside cells, 5 metal.m2.area cell pins and 2
# poly.poly.area.  The remaining 30 are top-cell geometry formed where abutting
# instances merge (see backend/RESULTS.md for the per-item triage); they are
# the same categories, not new ones.  The deck describes itself as a partial
# source port that omits the Calibre ABUT<90/SINGULAR qualifiers, and it has no
# differential against Calibre.  Keeping the report and judging it is honest;
# relaxing the rules is not.  Route DRC and LVS still gate the flow, and those
# are clean.
ERROR_ON_KLAYOUT_DRC: false
# AOI211X3H7H and AOI211X4H7H ship a GDS with 12 devices (6 NMOS + 6 PMOS)
# but a CDL with 8 (4 + 4).  The drawn cells fold each output diffusion into
# two parallel series stacks of half width (225n/225n against a 450n leg,
# 285n/285n against 570n) while the CDL keeps one wide leg, so the extracted
# netlist has 2 extra NMOS and 2 extra PMOS with their own intermediate nodes.
# The deck's `combine_devices` cannot merge them (the S/D net pairs differ),
# and the intermediate nodes are real geometry, so this is a cell defect, not
# a netlist-view artefact: running the LVS deck on the cell alone reproduces
# it (12 vs 8) while the other 50 cells used by these designs match.
# Excluding the two drive strengths is the only repair that does not require
# inventing connectivity; excluding X3 alone is not enough because synthesis
# immediately substitutes X4.
EXTRA_EXCLUDED_CELLS:
  - AOI211X3H7H
  - AOI211X4H7H

# ---------------------------------------------------------------------------
# Do not insert the released decap cells.
#
# ROOT CAUSE of the top-level LVS mismatch on flash/vco/sdm (all of which were
# clean at cell level and clean in device/net inventory): `FILLCAP*H7H` are
# *not* filler-safe.  They carry real MET1 inside the cell -- two capacitor
# plates plus the rail stub, declared as LEF obstruction -- and step 52
# (`OpenROAD.FillInsertion`) places them with `filler_placement`, i.e. after
# detailed routing, without any metal-aware check against the wires that are
# already there.  Where a decap lands under a routed MET1 signal wire the wire
# is shorted to the rail.  Geometry out of the produced GDS (81/1):
#
#   _074_  wire  18.355..20.045 x 16.355..16.445  (flash_core)
#          FILLCAP8H7H @18.4,15.4 FS plate 18.470..19.740 x 15.940..16.545
#          -> overlaps -> _074_ (= INVX0P5H7H _365_/Y, 12 load gates) is VSS
#   net45  wire   3.355..4.845 x 35.355..35.445  (flash_core)
#          FILLCAP16H7H @3.2,35.0 FS plate 3.450..6.330 x 35.355..35.955
#          -> overlaps -> net45 (= input45/Y -> _382_/B) is VDD
#
# KLayout agrees from both extraction modes (deep and flat, threads=1) and
# netgen on the exported flat netlists reproduces it (VSS +6 NMOS/+6 PMOS gate
# terminals and +1/+1 drain-source, VDD +1/+1).  The other four cores happen to
# have no decap under a routed wire, so the hazard was latent.  Removing the
# decap cells is the only repair available: LibreLane has no pre-route decap
# insertion, and the cells cannot be made safe from the flow side.  The
# metal-free FILLER*H7H cells still fill the core, and IR drop is reported by
# `OpenROAD.IRDropReport` on every run.
DECAP_CELLS: []

# LVS gates the flow: every cell the designs use now passes the deck
# standalone, the two cell defects are handled above, and the decap shorts are
# gone.
ERROR_ON_LVS_ERROR: true

meta:
  flow: Classic
  substituting_steps:
    Magic.DRC: null
    Checker.MagicDRC: null
    Magic.SpiceExtraction: null
    Magic.StreamOut: null
    Magic.WriteLEF: null
    Odb.CheckDesignAntennaProperties: null
    Checker.IllegalOverlap: null
    KLayout.XOR: null
    Checker.XOR: null
    "-Netgen.LVS": OpenROAD.WriteCDL
    Netgen.LVS: ICS55.KLayoutLVS
""",
        # `sdm` misses 10 ns on this PDK by a wide margin (runs/sdm-r7:
        # -7.143 ns WNS, 90 violating paths, TNS -210 ns, all fed by the two
        # integrator bits integ[2][0..1], which drive ~90 loads each).  The
        # core is specified at 20 ns here; 10 ns + 7.14 ns of measured path is
        # what the constraint has to cover.  See RESULTS.md.
        #
        # `ti` closes at 10 ns on sky130A/gf180mcuD/ihp-sg13g2 but misses by
        # 71 ps on the slow corner here (runs/ti-pin1base: setup WNS -0.0709 ns
        # at nom_ss_125C_1v08, 1 violating path, TNS -0.0709 ns; the same
        # design has +3.68 ns at nom_tt_025C_1v20).  11 ns is the specified
        # period on this PDK, matching the sdm precedent.
        "arch_period": {"sdm": 20, "ti": 11},
        # `ti` keeps exactly one hold violation after detailed routing
        # (runs/ti-r7: loop _1222_/Q -> _0996_ -> _1222_/D, -0.0638 ns at
        # nom_ff_n40C_1v32).  Post-CTS margins of 0.15 and 0.30 ns both leave it
        # (runs/ti-hfix, runs/ti-hfix2: -0.0625 / -0.0616 ns) because that
        # resizer only sees estimated RC and reports "unable to repair all hold
        # checks within margin".  The post-GRT resizer is off by default; turn it
        # on so hold is repaired against global-route parasitics.
        #
        # UPDATE: with 0.30 ns pre-GRT margin alone the hold is already clean
        # (runs/ti-pin1base: timing__hold__wns = 0 in all three corners,
        # timing__hold_vio__count = 0, 366 hold buffers), so the post-GRT
        # resizer is no longer needed and is switched back off: openroad
        # 2026-02-17 aborts inside rsz::RepairSetup with
        #   [ERROR GRT-0267] Net _0300_ has disconnected segments
        # whenever step 43 runs on this design, for every
        # GRT_RESIZER_HOLD_SLACK_MARGIN tried (0.10/0.20/0.05) and independent
        # of RUN_POST_GRT_RESIZER_TIMING=false on the pre-GRT side.
        "arch_extra": {
            "ti": """# ti hold repair (ics55 only): see backend/gen_configs.py.
PL_RESIZER_HOLD_SLACK_MARGIN: 0.30
""",
        },
    },
}


def rel(path: str) -> str:
    """Config-relative reference to a repository path.

    LibreLane resolves ``dir::`` against the directory of the config file,
    i.e. backend/configs/<pdk>/.
    """
    return "dir::../../../" + path


def emit(pdk_name: str, pdk: dict, arch_name: str, arch: dict) -> str:
    # Precedence: per-PDK per-architecture design point > per-PDK default > the
    # architecture's generic value.  Only the cores that have their own clock
    # spec (see RESULTS.md) use the first level.
    period = pdk.get("arch_period", {}).get(
        arch_name, pdk.get("clock_period", arch["clock_period"])
    )
    lines = [
        f"# Generated by backend/gen_configs.py -- do not edit.",
        f"# {arch_name}: {arch['desc']}",
        f"# PDK: {pdk_name} ({pdk['scl']}).  {pdk['notes']}",
        f"DESIGN_NAME: {arch['top']}",
        "VERILOG_FILES:",
    ]
    lines += [f"  - {rel(f)}" for f in arch["files"]]
    lines += [
        "VERILOG_INCLUDE_DIRS:",
        f"  - {rel('rtl/common')}",
        f"CLOCK_PORT: {arch.get('clock_port', 'clk')}",
        f"CLOCK_PERIOD: {period}",
        "FP_CORE_UTIL: 30",
        "PL_TARGET_DENSITY_PCT: 35",
        "VDD_NETS:",
    ]
    lines += [f"  - {n}" for n in pdk["vdd"]]
    lines += ["GND_NETS:"]
    lines += [f"  - {n}" for n in pdk["gnd"]]
    if pdk.get("extra"):
        lines.append(pdk["extra"].rstrip("\n"))
    per_arch = pdk.get("arch_extra", {}).get(arch_name)
    if per_arch:
        lines.append(per_arch.rstrip("\n"))
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    wanted = [a for a in argv if not a.startswith("-")] or list(ARCHS)

    unknown = [a for a in wanted if a not in ARCHS]
    if unknown:
        print(f"unknown architecture(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    stale = 0
    written = 0
    for pdk_name, pdk in PDKS.items():
        outdir = HERE / "configs" / pdk_name
        if not check:
            outdir.mkdir(parents=True, exist_ok=True)
        for arch_name in wanted:
            text = emit(pdk_name, pdk, arch_name, ARCHS[arch_name])
            path = outdir / f"{arch_name}.yaml"
            if check:
                if not path.exists() or path.read_text() != text:
                    print(f"stale: {path.relative_to(REPO)}")
                    stale += 1
                continue
            path.write_text(text)
            written += 1

    if check:
        print("configs up to date" if stale == 0 else f"{stale} stale config(s)")
        return 0 if stale == 0 else 1
    print(f"wrote {written} config(s) under {HERE / 'configs'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

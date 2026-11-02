# Per-PDK layout flow status (DRC / LVS / PEX)

Probe target: one PDK-shipped inverter, pulled out of the standard-cell library
with `gen/analog/extract_cell.py`. A shipped cell is the right first probe: if
the toolchain cannot clear it, nothing we draw will.

```
SRC=<lib.gds> CELL=<cell> OUT=<one.gds> klayout -b -r gen/analog/extract_cell.py
```

## Tool version is the first blocker

The installed `/usr/bin/klayout` is **0.30.0**. It is too old for two of the
four PDK decks:

- gf180mcu DRC fails with `undefined method 'evaluate_nets'` (flat mode) and
  `Heap lost: the DeepShapeStore container no longer exists` (deep mode). The
  gf180 README requires KLayout >= 0.30.9.
- ihp-sg13g2 LVS fails with `undefined local variable or method
  'flag_missing_ports' for LVS::LVSEngine`.

There is a **0.30.12** install in the Nix store:

```
KLAYOUT=/nix/store/1whjbzm6gw6l4h0sjxdv92lyw7lq1ar8-klayout-0.30.12/bin/klayout
```

Every PDK deck below is run with that binary. Using 0.30.0 gives either a
crash or a false result, so the flow must pin the version.

## Matrix

| PDK | cell | DRC | LVS | PEX |
|-----|------|-----|-----|-----|
| sky130A_hd | our `SARADC_CAP_UNIT`, `SARADC_SW_TG` | 0 items | match | 6 / 14 caps |
| ihp-sg13g2 | `sg13g2_inv_1` | 0 items | Netlists match | 6 caps, 2 devices |
| gf180mcuD | `gf180mcu_fd_sc_mcu7t5v0__inv_1` | 2 leaf rules left | **don't match** | 15 caps, 2 devices |
| ics55_LLSC_H7CH | not resolved | not run | not run | Magic tech present |

## Commands that work

### sky130A

Already in `gen/analog/layout_check.py` (`--device cap|tg`). KLayout 0.30.0 is
enough here because the deck does not use the newer framework.

### ihp-sg13g2

```
KLAYOUT -b -r libs.tech/klayout/tech/drc/ihp-sg13g2.drc \
    -rd input=inv_1.gds -rd topcell=sg13g2_inv_1 -rd report=drc.lyrdb
# 0 items, 48 s

KLAYOUT -b -r libs.tech/klayout/tech/lvs/sg13g2.lvs \
    -rd input=inv_1.gds -rd topcell=sg13g2_inv_1 -rd report=lvs.lvsdb \
    -rd schematic=sch.cir -rd disable_tap_extraction=true
# INFO: Congratulations! Netlists match.

PDK_ROOT=<ihp-sg13g2> magic -dnull -noconsole \
    -rcfile libs.tech/magic/ihp-sg13g2.magicrc pex.tcl
# 6 caps, 2 devices
```

Notes:
- `disable_tap_extraction=true` is required for a leaf cell. By default the
  deck extracts `ntap1`/`ptap1` and then the schematic must contain matching
  taps; a standard cell has none.
- The extracted subckt pin order (`Y A VSS VDD`) differs from the CDL. The
  native KLayout LVS handles it; Magic+netgen did **not** and ended in "Top
  level cell failed pin matching".

### gf180mcuD

```
KLAYOUT -b -r libs.tech/klayout/tech/drc/gf180mcu.drc \
    -rd input=inv_1.gds -rd topcell=gf180mcu_fd_sc_mcu7t5v0__inv_1 \
    -rd report=drc.lyrdb -rd variant=gf180mcuD
# 10 items

KLAYOUT -b -r libs.tech/klayout/tech/drc/gf180mcu.drc \
    ... -rd decks=all,-density
# 2 items: DF.13_MV, DF.14_MV (max well-tap distance)

KLAYOUT -b -r libs.tech/klayout/tech/lvs/gf180mcu.lvs \
    -rd input=inv_1.gds -rd topcell=gf180mcu_fd_sc_mcu7t5v0__inv_1 \
    -rd report=lvs.lvsdb -rd schematic=sch.cir -rd variant=gf180mcuD
# ERROR: Netlists don't match   <- open

PDK_ROOT=<versions/sha> MAGIC_TECH_PATH=.../libs.tech/magic \
    magic -rcfile libs.tech/magic/gf180mcuD.magicrc pex.tcl
# 15 caps, 2 devices
```

`PDK_ROOT` for gf180 Magic is the **version directory** (the parent of
`gf180mcuD`), not `gf180mcuD` itself; the rc appends `gf180mcuD/...`.

Reading the gf180 DRC result: the 8 density items (DCF.1b, PL.8, M1.4-M5.4,
MT.3) are full-die coverage rules and cannot be satisfied by any leaf cell.
The 2 remaining items are maximum well-tap distance, i.e. block-integration
rules that expect tap cells in the row. So the shipped inverter is clean at
the geometry level; the deck is not claiming a layout defect.

The open item is LVS: the deck completes and reports a mismatch. Next step is
to read `lvs.lvsdb` for the instance/net diff. Likely candidates are the
CDL `W=`/`L=` units (the CDL is SI, the deck may expect microns) or the
`VNW`/`VPW` well pins, which a standard cell does not bring out the way the
schematic does.

### ics55

Decks exist (`libs.tech/drc/ics55_drc.drc`, `libs.tech/lvs/run_lvs.py`,
`libs.tech/pex`, plus a Magic tech). The blocker is naming: the GDS library
contains `INVX1H7H`/`INVX2P5H7H`/... while the CDL uses `INV`, and there is no
one-to-one mapping obvious from the names. A cell-level proof needs that
mapping (or a device-level cell) before DRC/LVS can run.

## Next steps, in order

1. Pin `KLAYOUT=0.30.12` in the flow runner and make it a hard requirement;
   0.30.0 must fail with a clear message instead of a deck traceback.
2. Triage gf180 LVS from `lvs.lvsdb` (units vs well pins). Until it matches,
   gf180 is "DRC clean at leaf level, LVS open".
3. Resolve ics55 CDL-to-GDS cell names, then run its DRC/LVS and Magic PEX.
4. Only after those three pass: draw our own `cap_unit`/`switch_tg` leaves per
   PDK. A shipped-cell pass proves the deck, not our generator.

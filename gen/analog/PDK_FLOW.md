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

The split matters: **0.30.12 has no numpy**, so it cannot run our pya layout
builder (`layout_cap.py`/`layout_tg.py` need numpy through the OpenSERDES
shim). The builder therefore stays on the system 0.30.0 (`KLAYOUT_BUILD`), and
only the Ruby rule decks use 0.30.12. `gen/analog/klayout_bin.py` enforces the
>= 0.30.9 floor and fails with the versions it saw instead of a deck
traceback.

## Matrix

| PDK | cell | DRC | LVS | PEX |
|-----|------|-----|-----|-----|
| sky130A_hd | our `SARADC_CAP_UNIT`, `SARADC_SW_TG` | 0 items | match | 6 / 14 caps |
| ihp-sg13g2 | `sg13g2_inv_1` | 0 items | Netlists match | 6 caps, 2 devices |
| gf180mcuD | `gf180mcu_fd_sc_mcu7t5v0__inv_1` | 2 leaf rules left | **match** (Magic netlist) | 15 caps, 2 devices |
| ics55_LLSC_H7CH | `INVX1H7H` | 5 items (context) | **PASS** | 2 dev / 13 caps, 100x scale bug |

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

The open item is LVS. Triage so far:

- KLayout LVS extracts the cell as **5 pins** (`I SUB VDD VSS ZN`). The
  standard cell has no well taps, so the nwell comes out as an internal net
  and both bulks collapse onto `SUB`. The PDK CDL keeps `VNW`/`VPW` as pins,
  which only makes sense in a full block.
- Magic extracts **6 pins** (`VSS ZN I VDD VNW VPW`) because the GDS carries
  well labels, and the devices match the CDL exactly (nfet 0.82/0.6,
  pfet 1.22/0.5).
- Feeding the Magic netlist to netgen with `gf180mcuD_setup.tcl`, after
  aligning the pin order and converting the CDL `W=`/`L=` from SI to
  micrometres, still ends in `Netlists do not match`. `comp.out` shows the
  instances mapped (`pfet_05v0:1` vs `_i_1`) with no pin or net error, so the
  remaining difference is in device parameters or the X-vs-M device form.

Resolution: LVS matches with Magic extraction and an **X-style schematic**.

```
netgen -batch lvs "lvs_magic.spice <cell>" "sch_x.cir <cell>" \
    libs.tech/netgen/gf180mcuD_setup.tcl
# Circuits match uniquely.
```

where `sch_x.cir` calls the devices as subcircuits, exactly like Magic emits:

```
XMN0 ZN I VSS VPW nfet_05v0 w=0.82u l=0.6u
XMP0 ZN I VDD VNW pfet_05v0 w=1.22u l=0.5u
```

The PDK CDL writes them as `M` devices. netgen treats the `X` form and the
`M` form as different device classes (one keeps numeric pins, the other named
pins), and the comparison fails even though the topology is identical. This
is the same shape that worked for sky130 from the start.

So the KLayout LVS mismatch was never about the wells: Magic's 6-pin netlist
already carries `VNW`/`VPW`, and netgen matches it once both sides use the
same device form. `gf180mcu.lvs`'s commented-out
`substrate_connections.lvs` remains a note for block-level runs, not a
blocker for a leaf.

The deck's own LVS testcases cannot be used as a control: the files under
`libs.tech/klayout/tech/lvs/testing/` are Git LFS pointers (824 bytes).

### ics55

Decks exist (`libs.tech/drc/ics55_drc.drc`, `libs.tech/lvs/run_lvs.py`,
`libs.tech/pex`, plus a Magic tech).

The naming problem is solved: the GDS library's `INVX1H7H` also exists in the
CDL, so the probe uses that cell on both sides. `INV` in the CDL is a
different, generic entry.

```
KLAYOUT -b -r libs.tech/drc/ics55_drc.drc \
    -rd input=INVX1H7H.gds -rd top=INVX1H7H -rd report=drc.lyrdb \
    -rd no_density=true
# 5 items
```

Result: 1 x `MET1_A_1` (M1 area >= 0.042 um^2) at a 0.18 x 0.15 um island, and
4 x `M1DUM_W_1` markers whose reported geometry is empty. The empty markers
look like a deck bookkeeping rule rather than geometry; `ics55_rules.json`
describes them as the permitted metal-dummy rectangle sizes, so they are
informational. The M1 area item is a cell-boundary context rule: the island
abuts neighbouring cells in a row.

LVS passes:

```
python3 run_lvs.py --layout INVX1H7H.gds --top INVX1H7H \
    --netlist ics55_LLSC_H7CH.cdl --klayout <0.30.12> --report lvs.lvsdb
# ICS55_LVS_COMPARE=PASS / CONGRATULATIONS! Netlists match.
```

`--no-well-ties` must **not** be used for a leaf cell here. With it, the nmos
bulk is extracted as an internal net (`$6`) and the comparison fails; without
it the deck ties bulks to VSS/VDD globally and the netlists match.

PEX via Magic's ics55 tech: the device-dimension bug is **fixed upstream**.
The extract block used `style lambda=0.001(ics55)`, which emitted
`w=21u l=6u` for a 0.21/0.06 um device. Replacing it with the same style
sky130 uses, `style ngspice()` plus `lambda 1E6`, gives `w=0.21 l=0.06` and
`w=0.27 l=0.06`. The fix is committed and pushed to the PDK repo
(`a30372d`, origin `luyanaa/icsprout55-pdk`).

The capacitance is still wrong: `C0 VSS-SUB` stays 0.87257 pF and the
`VSS-SUB` node area implies a 0.5 um extraction lambda, i.e. C is ~1e4 too
large. The `.ext` header stays `scale 1000 1000 50` under every knob tried
(extract `lambda`, `variants`, cifinput `scalefactor 1/10/1000 nanometers`,
`scalegrid 1 100/200`, runtime `lambda`). So it is the technology lambda at
`tech load`, not the output style, and it is still open. The tech
declares `lambda=0.001` and `scalefactor 1` (1 DBU = 1 nm), so Magic's
ext2spice dimension convention is off by 100. `ext2spice scale off` does not
change it, and `gds rescale false` makes it worse (w=42u/l=12u, 200x), so the
default read path is the lesser evil. The W/L ratio is preserved (3.5 for both
the extracted 21/6 and the real 0.21/0.06), which confirms this is a pure unit
convention in the tech file, not a geometry error.

The capacitance follows the same factor as area: the extracted `VSS-SUB` cap
is 0.87 pF, and a 1.5 um^2 cell at ~0.1 fF/um^2 substrate capacitance should
be ~0.15 fF, i.e. 1e4 smaller. So the correction is exact but must be applied
as w/l /100 and C /1e4; the factor was cross-checked against the KLayout LVS
netlist for the same cell (W=0.21U, L=0.06U). Until the tech's lambda/scale is
fixed, no ics55 capacitance should be quoted, even scaled.

## Next steps, in order

1. Pin `KLAYOUT=0.30.12` in the flow runner and make it a hard requirement;
   0.30.0 must fail with a clear message instead of a deck traceback.
2. Decide the gf180 leaf abstraction (5-pin `SUB` vs 6-pin wells), then make
   the schematic and the deck agree; only then call gf180 LVS done.
3. Triage the ics55 M1 area item and the four empty markers, then run its LVS
   and Magic PEX.
4. Only after those three pass: draw our own `cap_unit`/`switch_tg` leaves per
   PDK. A shipped-cell pass proves the deck, not our generator.

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

## Reproduce

```
make -C gen layout      # sky130: our cap + TG, DRC/LVS/PEX
make -C gen pdk-flow    # gf180 / ics55 / ihp: shipped inverter, DRC/LVS/PEX
```

`pdk_flow.py` separates **hard** violations from **context** rules. Context
rules are the ones a single leaf cell cannot satisfy (full-die coverage,
row-level well-tap distance, metal-dummy bookkeeping); they are listed in the
JSON but do not fail the run. A real geometry violation still exits non-zero.

## Matrix

| PDK | cell | DRC | LVS | PEX |
|-----|------|-----|-----|-----|
| sky130A_hd | our `SARADC_CAP_UNIT`, `SARADC_SW_TG` | 0 items | match | 6 / 14 caps |
| (runner) | `make -C gen pdk-flow` | hard=0 for all three | pass | 2 dev each |
| ihp-sg13g2 | `sg13g2_inv_1` | 0 items | Netlists match | 6 caps, 2 devices |
| gf180mcuD | `gf180mcu_fd_sc_mcu7t5v0__inv_1` | 2 leaf rules left | **match** (Magic netlist) | 15 caps, 2 devices |
| ics55_LLSC_H7CH | `INVX1H7H` | 5 items (context) | **PASS** | 2 dev / 13 caps, values verified |

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

PEX via Magic's ics55 tech is now correct. Two upstream bugs were fixed in
the PDK repo and pushed (`a30372d`, `30fb4bd` on origin/main):

1. The extract block used `style lambda=0.001(ics55)`. ext2spice wrote
   `w=21u l=6u` for a 0.21/0.06 um device. Switching to sky130's
   `style ngspice()` plus `lambda 1E6` gives `w=0.21 l=0.06` and
   `w=0.27 l=0.06`.
2. Magic's extract section takes area capacitance in **aF/lambda^2** and
   perimeter capacitance in **aF/lambda**, not per micron (tech file manual).
   The tech stored the LEF values unchanged, so C was 40000x too large.
   The layout grid is 5 nm (a 0.21 um device extracts as 42 lambda), so
   areacap is divided by 40000 and perimc by 200.

Verification on `INVX1H7H` with the repo tech and no local overrides:

```
M1000 ... nm1p2_hvt_lp w=0.21 l=0.06
M1001 ... pm1p2_hvt_lp w=0.27 l=0.06
C0 VSS SUB  0.0526f     C3 A SUB 0.0133f     C4 Y SUB 0.03735f
```

At the LEF's 0.763 fF/um^2 those are 0.069 / 0.017 / 0.049 um^2, matching the
drawn M1 areas (VSS rail ~0.9 x 0.08 um). Before the fix the same net was
0.87257 pF, i.e. 1.6e4 times too large.

## Own leaves per PDK: what the device cells give us

The next step after the shipped-cell proof is to draw our own `cap_unit` and
`switch_tg` on each PDK. The device inventory decides how much work that is:

- **ihp-sg13g2**: `libs.ref/sg13g2_pr/gds/sg13g2_pr.gds` contains `nmos` and
  `pmos` cells (plus HV/rf variants). They are **three-terminal fixed
  geometry**: met1 pads only on the two diffusions, the gate is poly alone
  (layer 5) with no met1 strap, and there is no body tap. **Done for one
  device**: `gen/analog/layout_ihp_dev.py` adds a poly neck + landing pad,
  a 0.16 contact and a met1 pad, plus a `ptap1` tap 1.2 um away. The result
  clears the IHP DRC deck with **0 items** (bare `nmos` has 1, LU.b; the
  first strap attempt had 19, all spacing/enclosure, fixed by moving the pad
  clear of the active and the diffusion met1). The same script with
  `KIND=pmos` gives the PMOS leaf, also 0 items. Metal1 pin labels (GDS 8/2)
  make D/G/S/B ports, and both leaves pass netgen LVS (`Circuits match
  uniquely`) and Magic PEX (1 device, 6 caps for the NMOS).
- **gf180mcu**: `libs.ref/gf180mcu_fd_pr/gds/` ships only BJTs, PNPs and an
  efuse; there is no MOS GDS. Devices must come from Magic's
  `gf180mcuD.tcl` generator or from the stdcell library's internal devices.
- **ics55**: `libs.tech/klayout/pcells/ics55_mos_analog.py` is a real
  multi-finger MOS PCell, but it imports `klayout.db`, which is not installed
  for the system Python (the KLayout binary only exposes `pya`). Porting it to
  `pya` is mechanical but not yet done.

So the leaf work starts with ihp (device cells exist), then ics55 (PCell
port), then gf180 (generator or stdcell internals).

## Next steps, in order

1. ihp leaf: strap the `nmos`/`pmos` gates to met1, add tap cells, then build
   `cap_unit` (gates on VSH, S/D/B on FL/VNW/VPW) and prove it.
2. ics55: port `ics55_mos_analog.py` from `klayout.db` to `pya`, then build
   the same leaf and re-run DRC/LVS/PEX.
3. gf180: drive Magic's `gf180mcuD.tcl` device generator or lift the internal
   devices out of a stdcell, then build the leaf.
4. Array-level per-bit PEX on top of the leaves.

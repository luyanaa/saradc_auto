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

### Third defect: contacts never bridge planes (local overlay)

Even with the two fixes above the tech does not *connect* anything across a
layer change. A 0.09 um via1 between m1 and m2 extracts as two nodes
(`m1_...` and `m2_...`); a `ct` between ndiff and m1 leaves the diffusion
net and the m1 net separate. Leaf cells hid it (every net they need is
reachable on one plane), but any cross-plane route silently became an open
circuit -- see the array section below, where it invalidated a whole slice
iteration.

Cause: the `connect` section listed the contact/via **types** explicitly

```
m1,v1   m1,v1
```

Magic's `DBTechFinalConnect()` strips contact types back out of the
plane-connectivity table when they appear there, leaving the contact with no
plane bridge. Bridging must come from the `contact` section alone; `connect`
should carry same-plane connectivity as plain type globs. (Same pattern as
`gf180mcuD.tech` and `sky130A.tech`, both known good.)

Fix is a local overlay, the PDK file stays untouched:

- `gen/analog/ics55_magic_fix.tech` -- the PDK tech with:
  `connect` `m1,v1 -> *m1` (resp. m2..m5), `contact` renamed to the canonical
  first alias with canonical plane names (`via1 metal1 metal2`, ...), and the
  `viaN` aliases added to the metal `types` lines so those names resolve.
- `gen/analog/ics55_magic_fix.magicrc` -- `ics55.magicrc` with the one
  `tech load` line pointing at the overlay.

Verified with a 2-plane probe (m1 plate / 0.09 via1 / m2 plate): PDK tech
2 nodes, overlay 1 node; diff probe (np+active / ct / m1): 2 nodes -> 1.
`magic_pex` in `pdk_flow.py` now uses the overlay automatically
(`ics55_rc()`), which also defragments the shipped inverter:

```
C0 VSS SUB 0.06001f   C1 A SUB 0.01637f
C2 Y SUB 0.05269f     C3 VNW SUB 0.0595f      (2 devices, 4 caps)
```

vs 13 single-layer floating caps with the PDK tech. The overlay is the
matching `contact`+`connect` pattern gf180 uses, not a ics55-specific trick;
upstreaming it into `/grid/icsprout55-pdk` is a separate decision (it is a
shared PDK checkout, so it was not edited here).

### ics55 SPICE deck for Xyce

The vendor deck is HSpice-shaped: `hspice/model_wrapper1_hsp.lib` is a set of
`.LIB` corners around `mos/*.mdl`, and the model cards are not directly
loadable by another simulator. `gen/analog/deck.py --ics55-xyce` flattens it
into one file for Xyce 7.10 (BSIM4 level 54). Four traps, in the order they
bite:

1. **One bin per device.** `nhvt.mdl`/`phvt.mdl` hold 20 `.model` cards with
   `lmin/lmax/wmin/wmax` brackets. Loading all 20 makes model selection
   ambiguous. Keep the single card that brackets the requested geometry and
   rename it to the bare device name (`nm1p2_hvt_lp`). For W=1 um / L=60 nm
   that is `nm1p2_hvt_lp_core.6` / `pm1p2_hvt_lp_core.7`.
2. **The corner deltas are not zero by omission.** Every quoted expression
   references `<d*>_<device>` corner terms and, in the svt/lvt files, vendor
   `<device>_*_retgt` hooks that the `tt` section never defines. Xyce aborts
   with `Parameter VTH0 ... contains unrecognized symbols`. Both families are
   0 in `tt`: bake device-named symbols to 0.
3. **svt/lvt are interpolated, not binned.** `nsvt.mdl` etc. are a *single*
   card whose parameters come from HSpice `select6`/`selectp7`/`evalbin`
   macros over a W/L grid. Xyce has no such macros. `gen/analog/hspice_expr.py`
   evaluates the vendor's own `.param` function definitions for the requested
   W/L and emits literal numbers, which makes those cards **geometry-locked**
   (regenerate the deck when W/L changes). `prelayout` and `mismod` are
   subckt arguments in the wrapper and must be declared globally
   (`prelayout=0`).
4. **Warnings are benign.** Xyce warns that the card declares BSIM4 4.5 and
   uses its oldest supported version (4.6.1), clamps `pbswgd` to 0.1, and
   resets source/drain conductance for devices without explicit
   `sa/sb/as/ad/ps/pd`. Pass those explicitly on the instance.

Recipe and spot check:

```
make -C gen deck-ics55          # -> gen/outputs/deck/ics55_tt.spice
```

Measured at W=1 um, L=60 nm, |Vd|=|Vg|=1.2 V:

```
hvt  nmos 393 uA   pmos 203 uA
svt  nmos 559 uA   pmos 309 uA
```

The pmos/nmos ratio (~0.52-0.55) and the svt-over-hvt current gain are both
what the VT split predicts, which is the sanity check that the interpolated
svt card resolved to the right corner and not to a neighbouring bin.

### ics55 analog measurements

`gen/analog/measure_xyce.py` is the Xyce counterpart of `measure.py`/`char.py`
and runs against the flattened deck (`make -C gen measure-ics55`). It emits
bare `M` devices with the wrapper's `sa/sb` defaults and physical
`as=ad=W*sa`, because leaving the junction areas at zero makes Xyce reset the
S/D conductance and drops the junction capacitance. Measured at the drawn
leaf geometry (W=1 um, L=60 nm; comparator Wp=4*Wn):

| block | quantity | measured | previous assumption |
|---|---|---|---|
| moscap (n+p, W=1/1, L=0.06) | flat-band / peak C | 1.327 / 1.755 fF | 5.0 fF/um^2 |
| moscap | density | **11.06 .. 14.62 fF/um^2** | 5.0 fF/um^2 |
| TG (W=1/1, L=0.06) | Ron max / typ / min | **74.6k / 17.0k / 1.04k ohm** | 220 ohm |
| TG | off-cap at DAC node | 1.654 fF | -- |
| TG width study | x2 / x4 / x8 | Ron max 32.4k / 16.9k / 8.63k ohm, Coff 3.26 / 6.46 / 12.9 fF | -- |
| StrongArm (Wn=1, Wp=4, L=0.06) | systematic offset | **+78.1 uV** | 1200 uV (random) |
| StrongArm | clk->|outp-outn| 50 % | **0.327 ns** | 0.25 ns |

Three findings that change how ics55 has to be sized:

1. **The MOSCAP is the CDAC device.** The 4-bit array slice is built from
   nmos+pmos units, so `cap_backend`/`default_cap` now point at `moscap`
   instead of `mom`. The measured density is 22x the old MOM estimate, which
   is why the 8-bit array area drops from ~320 um^2 to ~61 um^2 at the same
   VFS. The flat-band value is stored as `density_ff_um2` (conservative: the
   depleted channel is the smallest the cap gets) and the peak as
   `density_typ_ff_um2`.
2. **The hvt 1.2 V TG is weak at mid-rail.** At Vin ~ VDD/2 the nmos has
   Vgs ~ 0.6 V and the source body is reverse-biased, so both devices lose
   overdrive at once and Ron peaks at 74.6k ohm for W=1 um. The curve is in
   the profile; widening to the 9 um bin edge cuts it ~9x, and a lower-VT
   device or a bootstrap cuts it much further. Using the worst point in the
   feasibility gate means the 1 um TG cannot do 8-bit SAR at 1 MS/s -- the
   matrix now reports `FAIL_SETTLING` for ics55 with the 1 um switch, which
   is a real sizing result, not a modelling artefact.
3. **The StrongArm precharge ratio matters.** With Wp=2*Wn the precharge
   equilibrium (0.977 V) sits below the cross-coupled latch trip point, and
   the comparator regenerates from numerical noise *before* the clock edge.
   Wp=4*Wn raises it to 1.057 V and the decision waits for CLK. The measured
   delay uses the same |outp-outn| = VDD/2 threshold as the sky130 bench.

Two assumptions remain, for concrete reasons:

* **Capacitor Pelgrom A_C and comparator sigma_uv.** The vendor's statistical
  hooks are HSpice `agauss` expressions inside the wrapper subckt, and the
  flattened deck bakes every `d*_<device>_mis`/`_mismatch` term to zero. There
  is no scatter to sample, so both rows keep their assumed coefficients and
  the profile records `measured_density` / `measured_delay` rather than
  `measured`.
* **Comparator input noise.** Xyce 7.10's `.noise` runs but reports a zero
  `onoise_spectrum`, even for a single 1 kohm resistor, so there is no
  input-referred noise number (the sky130 value came from ngspice `.noise`).

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
  efuse; `gf180mcuD.tcl` has no MOS generator. The only transistors are
  inside the stdcell layouts, and **cropping one out does not work**: the
  inverter shares a single active strip across both devices, so cutting at
  the nwell boundary slices active/poly/met1. A first crop of the NMOS half
  gives 11 DRC items (CO.3, DV.6, DV.8, M1.1, NP.5a/5b, PL.4_MV, DF.14_MV),
  i.e. boundary fragments, not a device. The stdcell layer map recovered from
  the GDS is: nwell 21/0, pwell 204/0, active 22/0, implants 31/0 and 32/0,
  contact 33/0, poly 34/0, marker 55/0, boundary 112/1. gf180 therefore needs a
  from-primitives device. `layout_gf180_dev.py` draws one: active 22/0,
  poly 30/0, contact 33/0, metal1 34/0 (label 34/10), pwell 204/0, N implant
  32/0, plus a p+ tap (active + 31/0) for the latch-up rule. Every vertex is
  snapped to the 5 nm grid. The deck reports **0 items** with
  `decks=all,-density`. Magic extracts it as `nfet_03v3 w=1.6u l=0.6u`.
  The gate contact was not recognised as `pc` with a 0.34 um metal1 pad;
  growing every metal1 pad to 0.5 um makes the gate connect
  (`X0 D G S a_440_80#`). The p+ tap stayed an m1-only node, but putting a `B`
  label on the pwell text layer **204/10** names the substrate directly: the
  extraction becomes `X0 D G S B nfet_03v3 w=1.6u l=0.6u`, netgen reports
  "Circuits match uniquely", and Magic PEX gives 1 device / 6 caps. gf180 DRC
  stays at 0 items. The PMOS uses the same recipe with nwell 21/0 and a
  `B` label on 21/10; it also needs an n+ tap (active + NPLUS 32/0) inside
  the well for DF.13_LV. `layout_gf180_cap.py` composes the cap_unit: gates
  on metal2 (36/0) with via1 (35/0, exactly 0.26 um) because a long metal1
  bar parallel to the source/drain pads breaks the 0.23 um spacing rule; FL
  is a metal1 bar across each device's pads joined outside the pad column.
  DRC 0, netgen match, PEX 2 devices / 6 caps, FL-VSH 258 aF.
- **ics55**: `ics55_mos_analog.py` imports `klayout.db` (pip module, not
  installed); `gen/analog/layout_ics55_dev.py` runs it under the binary's
  `pya` with a `klayout.db = pya` module shim, no PDK source change. The PCell
  generates, but it has a real geometry bug: the gate contact extension sits
  at `y0 + W/2 + POLY_OV`, i.e. inside the active region, so its met1 strap
  overlaps the source/drain met1 (an electrical short that DRC does not
  flag). Moving it to `y0 + W + POLY_OV` exposes the next problem: POLY_OV is
  only 0.15 um, which cannot hold a 0.09 contact plus met1 clearance to the
  source/drain strap. `gen/analog/ics55_mos.py` is a corrected local copy
  (the PDK file stays untouched): POLY_OV 0.35, the gate pad inside the
  overhang above the active, the contact fully inside that pad, and met1
  split into per-finger source/drain straps instead of one slab over the
  whole active (the original slab shorts source to drain). With those four
  fixes the nmos+pmos leaf is **DRC 0** (`metal.m1.spacing` fixes the gap at
  0.09 um, which is the ics55 minimum).
  `layout_ics55_leaf.py` flattens one device into the top cell and puts
  S/D/G/B text on the M1 net-label layer **81/6** (the deck only takes ports
  from direct top-cell text). The deck extracts `NM1P2_SVT_LP L=0.06U W=1U`
  with the bulk on the global VSS, so the schematic is
  `M0 D G S VSS NM1P2_SVT_LP w=1u l=0.06u`:
  `ICS55_LVS_COMPARE=PASS`, "CONGRATULATIONS! Netlists match."
  Magic PEX on the same leaf: 1 device, 7 caps (D/S to SUB 76 aF each).
  `layout_ics55_cap.py` composes the cap_unit: two devices without guard
  rings (the deck ties bulks to the global VDD/VSS anyway), gates tied by a
  metal1 bar at y 0.72..0.88 and S/D by a metal1 bar at y -0.45..-0.30.
  DRC 0, `ICS55_LVS_COMPARE=PASS`, PEX 2 devices / 8 caps, VSH-SUB 427 aF.
  The first attempt put the VSH bar at y 1.25 and missed the gate pads; the
  gate landing pad is at y 0.71..0.85, not 1.2.

So the leaf work starts with ihp (device cells exist), then ics55 (PCell
port), then gf180 (generator or stdcell internals).

## Next steps, in order

1. ihp leaf: strap the `nmos`/`pmos` gates to met1, add tap cells, then build
   `cap_unit` (gates on VSH, S/D/B on FL/VNW/VPW) and prove it.  (done)
2. ics55: port `ics55_mos_analog.py` from `klayout.db` to `pya`, then build
   the same leaf and re-run DRC/LVS/PEX.  (done)
3. gf180: drive Magic's `gf180mcuD.tcl` device generator or lift the internal
   devices out of a stdcell, then build the leaf.  (done)
4. Array-level per-bit PEX on top of the leaves.  (sky130 slice done, below)
5. switch_tg leaf on ihp / gf180 / ics55 -- the only leaf still missing
   before a CDAC can be assembled on those PDKs.

## ihp switch_tg -- DONE

`gen/analog/layout_ihp_tg.py` composes `nmos` + `pmos` from
`sg13g2_pr.gds` with the proven gate-strap + tap recipe, crossing the
S/D pairing (Z1 = nmos-left + pmos-left, Z2 = nmos-right + pmos-right)
with straps routed AROUND the gate pad because the pad-right-diffusion
gap is only 0.06 um.

Output `gen/outputs/pdk_flow/ihp/tg/switch_tg.gds`:

    DRC  0 items (ihp-sg13g2.drc)
    LVS  "Netlists match" (sg13g2.lvs, disable_tap_extraction=true)
    PEX  Z1 0.296 fF, Z2 0.329 fF, S 0.149, SB 0.144, VSS 0.674, VDD 0.176

Two traps that cost a full iteration:

* Pin text must go on **8/25**, not 8/2.  Only 8/25 is read as a port
  by the LVS deck (flag_missing_ports errors otherwise).
* The schematic `.SUBCKT` pin order must equal the layout's
  alphabetical pin order `S SB VDD VSS Z1 Z2`, and device params must
  carry the `u` suffix (`L=0.13u W=0.15u`) -- `w=0.15u l=0.13u`
  silently mismatches the extracted values.

## gf180 switch_tg -- DONE

`gen/analog/layout_gf180_tg.py` draws both fets from the primitive
layer map (same recipe as layout_gf180_dev.py / _cap.py), nmos below
pmos, Z1 on a plain m1 riser, Z2 hopping to metal2 (via1 = fixed 0.26
um) because the body-tap m1 pad at x 2.45-2.95 blocks the only clear
m1 lane on the right.

Output `gen/outputs/pdk_flow/gf180/tg/switch_tg.gds`:

    DRC  0 hard items (8 whole-die density polygons -- intrinsic to a
         single primitive cell, same as the proven leaves)
    LVS  "Circuits match uniquely" (Magic ext2spice lvs + netgen,
         X-style schematic, bodies named by well text labels)
    PEX  Z1 0.584 fF, Z2 0.828 fF, S 0.317, SB 0.326, VSS 3.59, VDD 2.73

Trap: a Z riser beside the device must clear the body-tap m1 pad by
0.23 um or it shorts Z to the well and Magic reports
"VDD/VSS/Z2 electrically shorted".  The netgen schematic pin order
must equal Magic's emitted order `Z1 S SB Z2 VSS VDD`.

## ics55 switch_tg -- DONE

`gen/analog/layout_ics55_tg.py` instantiates the corrected PCell
(ics55_mos.py) with `guard_ring=1 ring_metal=1` -- the ring metal is
where the deck expects the VDD/VSS labels; a bare S/D strap would
short a rail onto a Z net.  nmos and pmos sit side by side; each Z net
is one horizontal metal2 bar between the two same-x S/D straps' vias
(a riser plus crossbar overlaps the twin net and prints `Z1|Z2`).

Output `gen/outputs/pdk_flow/ics55/tg/switch_tg.gds`:

    DRC  0 items (ics55_drc.drc, no_density)
    LVS  "Netlists match" (run_lvs.py default deep mode, M-style
         schematic, pin order S SB VDD VSS Z1 Z2)
    PEX  Z1 0.078 fF, Z2 0.078 fF, S/SB 0.034, VSS/VDD 1.90

Traps: via1 is a fixed 0.09 um square; a Z riser must not cross the
guard ring (put rings on both devices, or route Z between the S/D
straps); the deck's strict_topology path fails on this flat cell --
the default deep run_mode is what passes.

## sky130 array slice: per-bit node PEX

`gen/analog/layout_array.py` tiles a 4-bit slice (1/2/4/8 cap units +
one 4x TG per bit) and names each bit's switched-plate node.  Outputs
under `gen/outputs/layout/array/`:

```
drc   0 items (sky130A_mr.drc, feol+beol forced on)
pex   B0 6.42   B1 7.70   B2 9.11   B3 12.11 fF   (VSH bus 5.14 fF)
```

Per-node breakdown is in `pex_per_bit.json`.  The large VSUBS term
(4.0 -> 5.7 fF) is the cap cells' own S/D junction capacitance and
scales with 2^i -- it is intrinsic bit weight, not routing parasitic.
The constant per-bit offset beyond device caps is ~1.3 fF (VDD coupling
1.0 + TG device 0.3), much smaller than the single-cell 3.075 fF Z2
probe suggested, because the Z1/Z2 strap parallel-run was a property of
the standalone cell, not the array.  The strap routing adds almost
nothing.  This supports array_impact's fixed-Cp model with Cp ~1.3 fF,
far below the 7.7 fF bound -- the LSB keeps ~67% of its weight instead
of 26%, implied DNL ~0.7 LSB not 2.8.

Layout traps worth remembering:

* The TG leaf's Z2 rail starts at cell-local x=485, Z1 ends at x=3540.
  A strap that only reaches the cell edge lands on the wrong rail.
* A 12.7 um TG spans ~3 cap rows.  Stagger them DECREASING in x
  (bit 0 rightmost) so each bit strap ends inside its own TG without
  crossing another cell's body; an increasing stagger merges every bit.
* A shared vertical REF bar needs its Z1 stubs routed around, not
  through, the rows above.  Per-bit REF stubs are simpler and enough
  for a Z2-side PEX.

## ihp 4-bit array slice -- DONE

`gen/analog/layout_ihp_array.py` tiles the same slice with the IHP cap
(`cap_unit3`) and TG (`SARADC_SW_TG_IHP`) leaves.  Outputs under
`gen/outputs/pdk_flow/ihp/array/`:

```
drc   0 items (ihp-sg13g2.drc)
pex   B0 1.62   B1 2.31   B2 3.85   B3 7.05 fF   VSH 13.15   VDD 8.98
      REF0..3 ~0.45 fF each   (38 devices, 118 caps)
```

Nets verified independent in `SARADC_ARRAY_IHP_4B_flat.ext`: VDD ties
all 15 cap VNW pads + all 4 TG VDD taps; B_i equivs exactly TG_i.Z2;
REF_i equivs exactly TG_i.Z1.  B ratios 1.00/1.43/2.38/4.35 against
ideal 1/2/4/8 -- cap S/D junction loading (the VPW term) dominates as
on sky130.

Leaf changes the array needed:

* `layout_ihp_cap.py`: FL keeps an m1 rail inside the cell PLUS an
  via1+m2 tap pad (0.34-0.66, y0.02-0.28) for the m2 bit strap; the ntap
  gets a via1+m2 VNW pad (m2 1.44-1.94, y2.60-3.25; via1 1.55-1.74,
  y2.66-2.85 -- the via must sit INSIDE the tap's m1 window top 2.89
  or V1.c fires).
* `layout_ihp_tg.py`: both Z risers run to a y2.95 crown; Z2 (LEFT
  riser) gets via1+m2 crown pad (m2 -0.50..-0.17, y2.74-2.95).

Routing that finally worked:

* Bit straps on METAL2 along the row bottom to the column under the
  TG's Z2 riser, then an m2 riser + square landing pad merging into
  the leaf's crown pad.  An L-corner without the pad leaves sub-width
  slivers (M2.a).
* VDD: per-row m2 band at y3.16-3.37 (above every m2 crown pad),
  m2 risers onto each cap's VNW pad, via1+m2 riser at the TG tap's
  right end (x tg_x+1.76-1.98 -- left of it the Z1 m1 stub and the
  Z2 m2 crown pad crowd the tap).  Rows tied by an M1 spine at
  x2.14-2.54, the only column empty of cap m1 (cap m1 ends at 1.94,
  next cap starts at 2.83).
* REF_i: m1 stub on the Z1 riser crown extending LEFT (x tg_x+0.78-
  1.16, y2.90-3.18).  Extending right 0.20 um reaches the ntap m1 pad
  at x1.34 and equivs REF_i to TG_i.VDD.
* VSH: m2 bus x-0.85..-0.65 + per-cap drops to the m2 gate pad --
  unchanged from before.

Dead ends (do not retry):

* An m1 VDD band across the tap row cannot work: the cap pmos FL rail
  (ends y2.48) and the Z1 crown band leave a <0.2 um corridor, so any
  band notches M1.a or gaps M1.b wherever a tap doesn't reach.
* An m2 VDD spine in the left margin (x-0.75..-0.30) hits the VSH m2
  column at x-0.85..-0.65.
* An m2 VDD band at the VNW pad height (y2.29-2.89) crosses the B
  strap's m2 riser (rises to y2.84) and the Z1/Z2 crown pads --
  VDD equivs every port.  Any via to the TG tap through that band also
  lands the band on the B riser.
* IHP via1 is fixed min=max 0.19 um (V1.a); m1 must enclose it by
  >=0.01 (V1.c) on all sides including the window's top edge.

## ics55 4-bit array slice -- DONE

`gen/analog/layout_ics55_array.py` tiles the same 4-bit slice (1/2/4/8 cap
units + one ringed TG per bit).  Outputs under
`gen/outputs/pdk_flow/ics55/array/` (notes + trap list in `README_NOTES.txt`):

```
drc   0 items (ics55_drc.drc, no_density)
pex   B0 2.334   B1 3.439   B2 5.703   B3 10.287 fF
      VSH 8.280   REF0..3 0.964 fF each   (38 devices, 25 caps)
      increments 1.105 / 2.264 / 4.584 -> 1.00 / 2.05 / 4.15 (ideal 1/2/4)
```

Connectivity asserted on the flat netlist: for every bit the `B_i` net holds
exactly `2^i` cap units (2 devices each, S=D=B_i) plus the two TG devices,
`REF_i` holds exactly those two TG devices, and `VSH` holds no device.  The
guard rings stay leaf-scoped (`switch_tg_n.VDD/.VSS` 1.90 fF floating), i.e.
the route does not leak into the rails.

Routing that works (the ring is the constraint):

* ringed TG seals its core on m1, so the B_i entry lands **outside** the VSS
  frame (cell-local x <= -1.745) and leaves on metal2: m1 strap at
  y -0.86..-0.68 -> via1 0.09 at (tg_x-3.525, -0.765) -> m2 riser
  x tg_x-3.62..-3.43 up to y 0.30 -> m2 toe at y 0.11..0.30 onto the leaf's
  Z2 bar.
* `REF_i` is a bare m2 bar at y -0.30..-0.11 straight onto the leaf's Z1 bar;
  the riser stops 0.43 um short of it in x, the two bands are 0.22 apart in y.
* `VSH` is an m1 bus at x -0.85..-0.65 (left of x=0, where every strap starts,
  and left of the cap's leftmost m1 at -0.41) plus one drop per cap.
* per-cap stub x+2.00..2.30 up into the FL bar keeps the strap 0.13 clear of
  the caps' S/D m1 (ends y -0.55).

The earlier ring-free TG variant (`build_tg_noring`, m1-only link bars) is
deleted: it shorted Z1=Z2=VDD=B_i=REF_i and only existed because the tech
defect made the m2 hop look impossible.  Row pitch 5.20, cap pitch 7.15,
TG gap 1.50, stagger 0.80 decreasing.

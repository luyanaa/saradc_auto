# Physical closure — DRC / LVS / PEX for every layout model

This is the status of the sign-off proofs for the analog generator's **layout
models**. It is produced by a single driver that regenerates every GDS from its
source script and re-runs the proofs, so the numbers are not read from stale
artifacts.

```
make -C gen closure          # == python3 gen/analog/closure.py --pdk all
```

Outputs: `gen/outputs/closure/closure.json`, one subdirectory per
`pdk/design` with the DRC report, LVS log/database, extracted netlist and PEX
SPICE.

## Scope

The SAR family is the only architecture with layouts. The other seven
architectures (`ramp`, `sdm`, `vco`, `dual_slope`, `flash`, `pipeline`, `ti`)
are behavioural/feasibility models only — no GDS exists, so DRC/LVS/PEX are
**N/A**, not failed (see `GAPS.md` L3 enablers E1–E6).

Layout-bearing models per PDK:

| model | contents | source |
|---|---|---|
| `cap_unit` | MOS-cap unit (NMOS+PMOS, d=s=b on FL, gate on VSH) | `layout_<pdk>_cap.py` |
| `switch_tg` | complementary transmission gate | `layout_<pdk>_tg.py` |
| `array4b` | 4-bit CDAC slice: 2^i caps + 1 TG per bit (1+2+4+8 caps, 4 TGs) | `layout_<pdk>_array.py` |

## Matrix

Regenerated and re-proved on 2026-11-09. DRC "0" = 0 hard violations (context
rules that a single leaf cannot satisfy are listed separately in the JSON and
are all 0 here).

| PDK | model | layout | DRC | LVS | PEX |
|---|---|---|---|---|---|
| sky130A_hd | cap_unit | ok | **PASS** (0) | **PASS** (2 dev) | 6 caps, VSH +0.958 fF constant |
| sky130A_hd | switch_tg | ok | **PASS** (0) | **PASS** (2 dev) | 14 caps, Coff 7.708 fF |
| ihp-sg13g2 | cap_unit | ok | **PASS** (0) | **PASS** (2 dev) | 2 dev / 6 caps |
| ihp-sg13g2 | switch_tg | ok | **PASS** (0) | **PASS** (2 dev) | 2 dev / 15 caps |
| ihp-sg13g2 | array4b | ok | **PASS** (0) | **PASS** (16 dev) | 38 dev / 118 caps |
| gf180mcuD | cap_unit | ok | **PASS** (0) | **PASS** (2 dev) | 2 dev / 6 caps |
| gf180mcuD | switch_tg | ok | **PASS** (0) | **PASS** (2 dev) | 2 dev / 15 caps |
| gf180mcuD | array4b | ok | **PASS** (0) | **PASS** (38 dev) | 38 dev / 125 caps |
| ics55_LLSC_H7CH | cap_unit | ok | **PASS** (0) | **PASS** (2 dev) | 2 dev / 2 caps |
| ics55_LLSC_H7CH | switch_tg | ok | **PASS** (0) | **PASS** (2 dev) | 2 dev / 6 caps |
| ics55_LLSC_H7CH | array4b | ok | **PASS** (0) | **PASS** (16 dev) | 38 dev / 25 caps |

Summary: **11 / 11 fully closed** (DRC + LVS + PEX). The ihp and gf180 array
slices are matched against independent binary-weighted references, and the
gf180 slice needed a real layout fix (below).

## LVS conventions discovered (reproducibility)

The decks are not interchangeable; a schematic that matches on one PDK fails
on another for reasons unrelated to the layout.

* **Device form.** KLayout `sg13g2.lvs` and the ics55/gf180 KLayout decks
  compare `M` devices; Magic+netgen (`gf180` leaves, ihp `cap_unit`) want the
  `X` subcircuit form. Netgen/KLayout ignore instance names, so `M$1` is fine.
* **ics55 directive case.** `libs.tech/lvs/ics55.lvs` de-duplicates with a
  case-sensitive `/^\s*\.SUBCKT/`; lowercase `.subckt`/`.ends` leaves the
  parser with `params = nil`. All ics55 references use uppercase
  `.SUBCKT`/`.ENDS`.
* **ihp port layer.** `sg13g2.lvs` reads top-level pins only from purpose
  **25 of the net's own metal** — `8/25` for met1 nets (`REF*`, `VDD`) and
  `10/25` for met2 nets (`B*`, `VSH`). Text on 8/2 or 10/2 is a net label,
  not a port. The ihp `cap_unit` has no 8/25 pins, so it is matched through
  **Magic+netgen**; the array now has explicit 8/25/10/25 pins.
* **ihp array combine.** The SG13G2 extractor combines the parallel unit caps
  of a bit into one device with `W = 2^i x 0.15u`, and leaves each TG's PMOS
  nwell bulk as its own floating net. `ihp_array_ref()` mirrors that.
* **ics55 array combine.** The ics55 extractor merges the parallel unit caps
  of each bit into one device with `W = 2^i x 1u`; `ics55_array_ref()` is
  written the same way.
* **gf180 hierarchical text.** `gf180mcu.lvs` reads every `34/10` and `36/10`
  text in the hierarchy and `make_top_level_pins` promotes each named net.
  Leaf labels therefore surface as ports (`S/SB/Z2`, `B0|FL`, `REF0|Z1`). The
  driver regenerates a **label-stripped copy** of the identical geometry for
  LVS (`GF180_ARRAY_LVS_STRIP=1`); the Magic PEX copy keeps the labels because
  its flat extractor needs them.
* **gf180 reference.** The deck runs with `COMBINE` off, so
  `gf180_array_ref()` writes 38 devices: 2^i (pmos+nmos) MOSCAP pairs per bit,
  one TG pair, per-bit cap PMOS nwells (`NWi`), per-TG PMOS nwells (`NWTi`)
  and the global `SUB`; `SUB` is a deck-named net and must also appear as a
  port.
* **sky130 build tool.** sky130's layout scripts import `numpy`, which the
  0.30.12 KLayout build lacks; `build_gds` uses the system `klayout` (0.30.0)
  while DRC/LVS/PEX use 0.30.12 through `klayout_bin.py`.

## Array LVS closure

### ihp `array4b`

`layout_ihp_array.py` now emits top-level pins on 8/25 (met1: `REF*`, `VDD`)
and 10/25 (met2: `B*`, `VSH`), and strips the TG leaf's `S/SB/VDD/VSS/Z1/Z2`
8/25 text so it cannot surface as a port. The deck then extracts the intended
ten ports:

```
.SUBCKT SARADC_ARRAY_IHP_4B B0 B1 B2 B3 REF0 REF1 REF2 REF3 VDD VSH
```

Against `ihp_array_ref()` (16 devices: 4 combined cap pairs + 4 TG pairs) the
deck prints `INFO : Congratulations! Netlists match.`

### gf180 `array4b`

During closure a **real open** was found: the m2 hop from the bit strap ended
at y≈0.1 while the TG Z2 riser starts at y≈0.53, so `B_i` never reached the
TG. `layout_gf180_array.py` now adds the missing vertical m2 segment
(`Z2_LO`). With that fix the stripped LVS copy extracts

```
.SUBCKT SARADC_ARRAY_GF_4B B0 B1 B2 B3 REF0 REF1 REF2 REF3 SUB VSH
```

and matches `gf180_array_ref()` (38 devices). DRC stays at 0 and PEX drops
from 159 to 125 coupling caps because the TG no longer floats on its own net.

## Electrical characterization

`gen/analog/char_all.py` runs the same three benches on all four PDKs at the
drawn leaf geometry (unit MOSCAP C(V)/density, TG Ron/Coff, StrongArm
offset/delay). Outputs: `gen/outputs/char/characters.{json,md}` plus one
`char_<pdk>.json` per PDK. See `PDK_FLOW.md` for the deck recipes.

## PDK installs used

```
ihp    ~/.ciel/ciel/ihp-sg13/versions/89d42ee6.../ihp-sg13g2
gf180  ~/.ciel/ciel/gf180mcu/versions/792ef670.../gf180mcuD
ics55  /grid/icsprout55-pdk
sky130 ~/.ciel/ciel/sky130/versions/8afc8346.../sky130A
klayout 0.30.12 (DRC/LVS/PEX), 0.30.0 (sky130 layout build)
magic, netgen, ngspice, Xyce 7.10 on PATH
```

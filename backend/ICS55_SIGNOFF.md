# ics55 sign-off: KLayout-only DRC/LVS

`ics55` is the only PDK here whose sign-off does not run Magic.  The vendor's
`libs.tech/magic` carries no DRC rules, and the vendor's own LibreLane example
(`libs.tech/librelane/examples/counter/config.yaml`) substitutes the Magic steps
out for the same reason.  What runs instead:

| step | tool | report |
| --- | --- | --- |
| route DRC | OpenROAD/`TritonRoute` | `route__drc_errors` |
| antenna | OpenROAD | `antenna__violating__{nets,pins}` |
| layout DRC | KLayout + `libs.tech/drc/ics55_drc.drc` | `klayout__drc_error__count` |
| LVS | KLayout + `libs.tech/lvs/ics55.lvs` | `design__lvs_error__count` |
| stream-out | KLayout | `final/gds/` |

So `PRIMARY_GDSII_STREAMOUT_TOOL: klayout`, `Netgen.LVS -> ICS55.KLayoutLVS`
(from the vendor plugin), and `ERROR_ON_KLAYOUT_DRC: false` — the released
library cannot reach 0, see [the DRC floor](#the-drc-floor-is-library-geometry)
below.

## Local repairs, all outside the PDK tree

| repair | where | why |
| --- | --- | --- |
| real LEF/DEF layer map | `pdk_overlay/ics55/libs.tech/klayout/tech/ics55_def.map` | the shipped `ics55.map` is a GDS-import map: it names `M1`/`V1`/`TM2` while the LEFs say `MET1`/`VIA1`/`T4M2`.  Every `No mapping for layer …` was dropped on stream-out, so the GDS had no routing and no pin labels. |
| `LAYERS_RC`, `VIAS_R` | `gen_configs.py` | the PDK's `libs.tech/librelane/config.tcl` leaves these as a TODO; without them `OpenROAD.IRDropReport` aborts on an invalid resistance map.  Values from `ics55_LLSC_H7CH/layers_rc.tcl`. |
| `EXTRA_EXCLUDED_CELLS` | `gen_configs.py` | `AOI211X3H7H`, `AOI211X4H7H`: their GDS has 12 devices against a CDL of 8 — the same "drawn fingers the CDL does not describe" class as `OAI32X3H7H` below. These two are never needed, so they are excluded from synthesis/PnR rather than repaired. Not an LVS waiver: the key is a `set_dont_use`. |
| CDL device-list patch | `pdk_overlay/ics55/libs.ref/ics55_LLSC_H7CH/cdl/ics55_LLSC_H7CH.cdl.patch` | eight circuits whose CDL does not describe the drawn cell; see [part 1](#lvs-part-1-cell-cdl-defects) and [part 3](#lvs-part-3-two-series-orders-the-comparer-could-not-reconcile). |
| `DECAP_CELLS: []` | `gen_configs.py` | `FILLCAP*H7H` are not filler-safe: `FillInsertion` places them after detailed routing and their internal MET1 plates short routed signal wires to the rails.  See [part 2](#lvs-part-2-decap-fillers-short-routed-met1). |

`pdkroot.sh` supports `<vendor file>.patch` overlay entries: the vendor file is
copied into `pdkroot/` and the unified diff applied to the copy, so a vendor
defect stays a reviewable diff instead of a vendored 870 kB file.

## LVS part 1: cell CDL defects

The deck builds the schematic as

```
design CDL (OpenROAD.WriteCDL) + cdl_convert(libs.ref/…/ics55_LLSC_H7CH.cdl)
                               + cdl_convert(libs.ref/ICSIOA_N55_3P3/cdl/…)
```

and the layout from the GDS this run streamed out.  Rather than chase the
cells a particular core happens to instantiate, every standard cell the seven
cores use is extracted as its own top cell and compared against the converted
library CDL (a 15-line wrapper around `KLAYOUT_BIN -b -r ics55.lvs`, one cell
at a time).  The seven designs use 138 distinct cell types; **131 pass, seven
do not**, in three defect classes:

| cell(s) | devices layout / CDL | defect |
| --- | --- | --- |
| `NAND2X0P5H7H`, `NAND2X6H7H`, `NAND2X12H7H`, `NAND2X16H7H` | 4 / 4 | the two input devices sit on the wrong pins: the vendor CDL puts `B` on the outer device, the GDS draws `A` there |
| `NAND3BBX0P5H7H` | 10 / 10 | the three NMOS series devices are ordered the other way round: gate `C` is on the VSS-end device in the CDL and on the Y-end device in the GDS, and the `A1N` / `C` gates are exchanged |
| `OAI32X3H7H` | 15 / 10 | the drawn cell has two parallel copies of each PMOS branch (285 nm fingers), the CDL lists a single 570 nm device |
| `AOI22X2H7H` | 12 / 8 | each of the four 300 nm NMOS legs is drawn as two 150 nm fingers in two parallel series stacks; the CDL lists one 300 nm device per leg, with the gates in the other order |

The first audit pass covered 123 cells only — the union of the cells used by
six of the seven cores.  When `ti` still failed after the decap fix, the cell
sets were recomputed from all seven `61-openroad-writecdl/*.cdl` files and
15 cell types used by `ti` (plus four metal-free `FILLER*`) had never been
audited.  Fourteen of those pass; `AOI22X2H7H` is the seventh defect.  The
audit set now covers every cell type any of the seven cores instantiates.

`NAND2X2H7H` reported one `Can't find a schematic counterpart` error during a
three-way concurrent audit and passes when run alone (4 / 4 devices); that is an
audit artifact, not a cell defect.

First cell as an example:

```
.SUBCKT NAND2X0P5H7H A B VDD VSS Y
  MMN0 Y B net6 VSS nm1p2_hvt_lp W=150n L=60n   # vendor: B on the outer device
  MMN1 net6 A VSS VSS nm1p2_hvt_lp W=150n L=60n # vendor: A on the inner device
```

The extracted layout netlist binds pin A to the device the vendor CDL calls `B`
(and vice versa), so every instance of the cell reports a pin mismatch.  With
the patch the schematic agrees with the layout and the comparison passes — on
three different KLayout builds, see below.

`ics55_LLSC_H7CH.cdl.patch` therefore covers seven circuits / seven hunks at
this point:

- `NAND2X0P5H7H` — 4 device lines (input-device swap)
- `NAND2X12H7H`, `NAND2X16H7H` — 8 device lines (same swap)
- `NAND2X6H7H` — 4 device lines (same swap)
- `NAND3BBX0P5H7H` — 2 device lines, the gate fields of `MMM3` and `MMM7`
  exchanged (`Y C net25` / `net33 A1N VSS`)
- `OAI32X3H7H` — 5 lines become 10: each PMOS branch is written twice at
  `W=285n` with private internal nodes, matching the two drawn fingers
- `AOI22X2H7H` — 4 lines become 8: each 300 nm NMOS leg becomes a pair of
  150 nm devices, and both legs are written twice (two parallel series
  stacks per branch, exactly as drawn)

Every one of the seven is verified at cell level against the composed pdkroot
CDL: `ICS55_LVS_COMPARE=PASS` with matching device counts (4/4, 4/4, 4/4, 4/4,
10/10, 15/15, 12/12).  A later eighth hunk (`AOI22X1H7H`) and a PMOS-order
extension of the `AOI22X2H7H` hunk close the last top-level mismatch of `ti`
without changing any device, width or count; see [part 3](#lvs-part-3-two-series-orders-the-comparer-could-not-reconcile).  The `OAI32X3H7H` and `AOI22X2H7H` repairs are schematic
corrections, not waivers: the deck's `combine_devices` pass merges *parallel
single devices* but cannot merge two parallel series branches, so a CDL that
lumps the fingers can never compare equal to the extraction.  For `AOI22X2H7H`
the drawn cell is also *not* electrically equal to the CDL abstraction (two
parallel 150 nm series stacks have a quarter of the resistance of one 300 nm
series stack), so the CDL was wrong about the cell, not only about the
comparison.

### Evidence: cell CDL defects

`ramp` is the test case.  Same deck, same two GDS files, the schematic varied
between the vendor CDL and the patched one, and the KLayout binary varied
between all three builds on this machine:

| layout GDS | schematic | KLayout | devices | verdict |
| --- | --- | --- | --- | --- |
| `ramp-pin1` stream-out | patched | 0.30.7 (pin) | 1846 | **PASS** |
| `ramp-pin1` stream-out | patched | 0.30.12 (3.0.14) | 1846 | **PASS** |
| `ramp-pin1` stream-out | patched | 0.30.0 (system) | 1846 | **PASS** |
| `ramp-r7` stream-out (3.0.14 flow) | patched | 0.30.12 | 1774 | **PASS** |
| `ramp-r7` stream-out (3.0.14 flow) | vendor | 0.30.12 | 1774 | **FAIL** |

The two `ramp-r7` rows differ only in the schematic and flip the verdict; the
layout netlist is 1774 devices either way.  The flow reached the same
conclusion on its own: `69-ics55-klayoutlvs` (patched library CDL, 20:16) is
`ICS55_LVS_COMPARE=PASS / CONGRATULATIONS! Netlists match.` while
`62-ics55-klayoutlvs` (vendor CDL, 13:11) is `FAIL`.  No toolchain input is
implicated — the same pass is obtained from three different KLayout builds.

Reproduce with `KLAYOUT_BIN` from `backend/toolchain.sh`, e.g.

```sh
. backend/toolchain.sh
"$KLAYOUT_BIN" -b -zz -r backend/pdkroot/ics55/libs.tech/lvs/ics55.lvs \
  -rd input=<gds> -rd schematic=<merged.cdl> -rd report=/tmp/lvs.lyrdb \
  -rd target_netlist=/tmp/out.spice -rd run_mode=deep
```

## LVS part 2: decap fillers short routed MET1

`ramp`, `pipeline` and `dual_slope` closed LVS with the cell patches alone.
`flash`, `vco` and `sdm` did not, and the mismatch had none of the signs of a
cell defect: both sides carried the same device histogram (flash: 8411 devices
per side, identical model/W pairs down to the last transistor), every cell
the cores instantiate pass the deck standalone, and the only difference was two
missing nets.  It was a **real short in the streamed GDS**, introduced after
detailed routing.

### Finding the two nets

`netmatch.py` describes every net by the multiset of `(model, W, role)` of its
terminals, so the comparison does not depend on net names.  On the flat
netlists dumped out of `.lvsdb` it reports eight schematic-only nets for
`flash`, of which two matter:

| schematic net | terminals | driver | loads |
| --- | --- | --- | --- |
| `\_074_` | 14 | `_365_/Y` (`INVX0P5H7H`) | 12 gates of `_444_`, `_600_`, `_473_`, `_602_`, `_603_` |
| `net45` | 2 | `input45/Y` (`BUFX1P4H7H`) | `_382_/B` |

(The other six are the internal plates of three `FILLCAP*H7H` instances.)  On
the layout side both nets are absent — they *are* `VSS` and `VDD`.

Second opinion, netgen 1.5.325 on the same two netlists exported to SPICE and
stripped of every `FILLCAP` device: the only remaining difference is the rails.

```
Net: VSS            layout                              schematic
  NM1P2_HVT_LP/(drain|source)  2563                 2562
  NM1P2_HVT_LP/bulk            3519                 3519
  NM1P2_HVT_LP/gate               6                    -
  PM1P2_HVT_LP/gate               6                    -
  PM1P2_HVT_LP/(drain|source)     1                    -
Net: VDD
  PM1P2_HVT_LP/(drain|source)  2719                 2718
  NM1P2_HVT_LP/(drain|source)     1                    -
  NM1P2_HVT_LP/gate               1                    -
```

Six NMOS gates plus six PMOS gates plus one NMOS and one PMOS drain/source are
exactly the twelve loads and the output device of `\_074_`; one gate plus one
drain/source is exactly `net45`.  `VSS` gained `\_074_`, `VDD` gained `net45`.

### Ruling out the extraction

Every reduction knob the deck exposes was tried on the same GDS, plus a second
extraction mode:

| variant | devices | verdict |
| --- | --- | --- |
| default (`deep`, `simplify`, `combine_devices`, `purge`) | 8411 | FAIL |
| `no_simplify=true` | 8987 | FAIL |
| `combine_devices=true` | 8411 | FAIL |
| `purge=true` | 8411 | FAIL |
| `no_simplify=true combine_devices=true` | 8411 | FAIL |
| `run_mode=flat`, `threads=1` | 8411 | FAIL |

So the merge is in the geometry, not in a netlist-reduction pass and not in
hierarchical extraction.

### The geometry

Shapes on `81/1` (MET1) read back from `57-klayout-streamout/flash_core.gds`:

| what | rectangle (µm) | from |
| --- | --- | --- |
| `_074_` routed wire | `18.355 .. 20.045 × 16.355 .. 16.445` | `flash_core` |
| `FILLCAP8H7H` plate | `18.470 .. 19.740 × 15.940 .. 16.545` | `FILLCAP8H7H` @ 18.4, 15.4 FS |
| `FILLCAP8H7H` second plate | `18.650 .. 19.930 × 15.755 .. 16.355` | same |
| `FILLCAP8H7H` rail side | `18.400 .. 20.000 × 15.320 .. 15.755` | same |
| `VSS` rail | `2.400 .. 70.600 × 15.320 .. 15.480` | `flash_core` |
| `net45` routed wire | `3.355 .. 4.845 × 35.355 .. 35.445` | `flash_core` |
| `FILLCAP16H7H` plate | `3.450 .. 6.330 × 35.355 .. 35.955` | `FILLCAP16H7H` @ 3.2, 35.0 FS |
| `FILLCAP16H7H` rail side | `3.200 .. 6.400 × 34.920 .. 35.355` | same |
| `VDD` rail | `2.400 .. 70.600 × 34.920 .. 35.080` | `flash_core` |

The routed wire and the capacitor plate occupy the same metal: the wire is
short-circuited to one plate, the other plate is tied to the rail, so the whole
signal net becomes the rail.  The offending instances are `FILLER_7_80`
(`FILLCAP8H7H`) under `\_074_` and `FILLER_21_4` (`FILLCAP16H7H`) under
`net45`, both named `FILLER_*` because that is how `OpenROAD.FillInsertion`
places everything in its list.

### Cause and repair

`FILLCAP*H7H` do declare their internal metal as LEF obstruction, so this is
not a LEF/GDS view mismatch: `filler_placement` (step 52,
`OpenROAD.FillInsertion`) runs *after* detailed routing (step ~47) and does not
look at the wires already in the block, so a decap can be dropped straight on
top of a routed MET1 signal.  `ramp`, `pipeline`, `dual_slope` and `ti` simply
did not happen to get a decap under a routed wire, which is why the defect
looked design-specific.

There is no way to place decaps before routing in this flow, so the repair is
`DECAP_CELLS: []` in `gen_configs.py`: `FILLER1H7H`/`FILLER2H7H`/`FILLER64H7H`
carry no metal beyond the rails and stay in use, `FILLTAPH7H` still comes from
`WELLTAP_CELL`, and `OpenROAD.IRDropReport` still reports the (now decap-free)
power grid on every run.  With that single change `flash-pin2` and `ramp-pin2`
close at `design__lvs_error__count = 0` with `ERROR_ON_LVS_ERROR: true`, i.e.
LVS now gates the flow.



### Withdrawn: the "signal net absorbed into VSS" theory

An earlier investigation recorded a second root cause: that layout extraction
merged the signal net `net34` into `VSS`, leaving 9 devices with `gate=VSS`
inside `NAND2X0P5H7H` instances, and that ~13 cells could not match because the
CDL lumps fingers that the GDS draws as parallel devices ("B class").

That does not reproduce on `ramp`.  In every `ramp` extraction in the table
above — both GDS files, all three KLayout builds — the extracted netlist
contains **zero** devices whose gate is `VSS` or `VDD`:

```
$ awk '/^M/{if($3=="VSS")v++;else if($3=="VDD")d++} END{printf "VSS=%d VDD=%d\n",v+0,d+0}' <netlist>
VSS=0 VDD=0
```

The number came from a side experiment with `net_only=true`, which extracts a
hierarchical netlist (424 devices, cells left unexpanded) rather than the
sign-off netlist, and it was read as a defect in the comparison.  It is not
one: the device-level netlists for `ramp` match completely (1774 and 1846
devices, 0 mismatches) and the "B class" list has no effect on the verdict.
Nothing in the flow depends on either claim any more.

Note that gates *can* end up on a rail — that is exactly what
[part 2](#lvs-part-2-decap-fillers-short-routed-met1) found for `flash`, `vco`
and `sdm` — but there it is a real metal short caused by decap filler
insertion, reproducible in flat extraction and readable in the GDS geometry,
not an extraction artefact.

## LVS part 3: two series orders the comparer could not reconcile

With `DECAP_CELLS: []` and the seven cell repairs in place, six cores closed LVS
and `ti` did not: `design__lvs_error__count = 1` on 13558 devices and 7027 nets
per side, with the flow's own report reading

```
LayoutVsSchematic#xref: NetPair   7003 Match / 4 MatchWithWarning / 20 Mismatch
                        DevicePair 13539 Match / 19 Mismatch
                        PinPair      55 Match
```

The 55 top-level pins match, so the boundary is clean and everything else is
internal.  Reading the `.lvsdb` net pairs, the comparer had paired the two nets
that carry the failure crosswise:

| KLayout pair | layout terms | schematic terms |
| --- | --- | --- |
| `$6493` ↔ `NET233` | 11 | 8 |
| `$6326` ↔ `_0176_` | 8 | 11 |

Describing each net by the multiset of `(model, W, terminal role)` of its
terminals shows `$6326` *is* `NET233` (8 terms, equal) and `$6493` *is*
`_0176_` (11 terms, equal except one PMOS drain/source label).  With that one
correction the device imbalance drops from 19 to **6**, and those six are all
inside two cells:

| cell (instance) | devices | layout | CDL |
| --- | --- | --- | --- |
| `AOI22X2H7H` (`_1037_`) | 4× PMOS `W=380n` | `Y` —[A0,A1]— `net31` —[B1,B0]— `VDD` | `Y` —[B]— `net31` —[A]— `VDD` |
| `AOI22X1H7H` (`_1008_`) | 2× NMOS `W=210n` | `VSS` —[B0]— `net14` —[B1]— `Y` | `VSS` —[B1]— `net14` —[B0]— `Y` |

Both are orderings of two stacked devices, so both are electrically identical to
what the CDL says, and the deck says so at cell level: `AOI22X2H7H` and
`AOI22X1H7H` compare `PASS` (12/12 and 8/8 devices) against the seven-hunk CDL,
in both GDS variants.  The cell-level comparer reorders series devices freely;
the top-level one does not once it has given up on `$6326`/`$6493`, so the six
devices stay unmatched and take the two nets with them.

The repair writes the *drawn* order into the reference instead of leaving an
equivalence for the comparer to find.  `ics55_LLSC_H7CH.cdl.patch` is now
8 hunks / 8 circuits: the `AOI22X2H7H` hunk additionally writes the PMOS branch
as `MMP2 Y A0 net31` / `MMP3 Y A1 net31` / `MMP1 net31 B1 VDD` /
`MMP0 net31 B0 VDD`, and a new hunk writes `MMN5 net14 B0 VSS` /
`MMN0 Y B1 net14` for `AOI22X1H7H`.  No device, no width and no device count
changes; only which of two equivalent series positions each gate sits in.

Isolation, same `ti-pin2` stream-out GDS, deck and options, one schematic
variant per row:

| schematic | verdict |
| --- | --- |
| vendor CDL | FAIL |
| 7-hunk CDL (what `ti-pin2` ran) | FAIL |
| 7 hunks + `AOI22X2H7H` PMOS order | FAIL |
| 7 hunks + `AOI22X1H7H` NMOS order | FAIL |
| 7 hunks + both (8 hunks, now on disk) | **PASS** — `CONGRATULATIONS! Netlists match.` |

Only `ti` instantiates either cell, so the reference change cannot move any
other core's verdict.  That was checked rather than assumed: the six other
`ics55` cores were re-run through the deck on their own `pin2` stream-out GDS
with the new (8-hunk) merged CDL and all six still report
`ICS55_LVS_COMPARE=PASS`.

### What the earlier note got wrong

An intermediate note claimed a contradiction — that cell-level extraction put
`AOI22X2H7H`'s A group at `VDD` while the flattened `ti` layout put it at `Y`,
and suspected `ics55_LLSC_H7CH_M2.gds` differed from `ics55_LLSC_H7CH.gds`.
Both GDS files extract **identically** for both cells (B group at `VDD`, A group
at `Y`; `VSS`–B0–B1–`Y`).  The "A group at VDD" reading came from the CDL, not
from the extraction.  The GDS-variant question is closed; there is one physical
cell per name, and the CDL disagreed with it in two places.

## The DRC floor is library geometry

Nothing in the 343 items of `ramp-pin1` is our routing.  Split by the `<cell>`
field of the `.lyrdb` (100 % of items carry one):

| category | `ramp-r7` 0.30.12 | `ramp-pin1` 0.30.7 | rule |
| --- | --- | --- | --- |
| inside standard cells | 267 | 279 | |
| — `M1DUM_W_1` | 182 | 180 | pin *labels* the released cells keep on the dummy layer |
| — `metal.m1.area` | 79 | 94 | M1 shape < 0.042 µm² |
| — `metal.m2.area` | 4 | 2 | M2 shape < 0.052 µm² |
| — `implant.np.enclosed_area` | 0 | 0 | — |
| — `poly.poly.area` | 2 | 3 | poly shape < 0.04 µm² |
| in the top cell (`ramp_core`) | 30 | 64 | |
| — `metal.m2.area` | 17 | 49 | M2 pin/via pads of abutting cells, joined after placement |
| — `metal.m2.spacing` | 2 | 4 | two pads of abutting cells closer than 0.1 µm |
| — `implant.np.enclosed_area` | 11 | 11 | NP enclosure under the fill/cap cells |
| **total** | **297** | **343** | |

Minima are from `libs.tech/drc/rule_decks/ics55_rules.json` (`m1_area 0.042`,
`m2_area 0.052`, `poly_area 0.04`).

The 297 → 343 delta is not a deck change: the two runs are different layouts
(die 1409.69 → 1427.46 µm², instance area 870.24 → 876.12 µm², different
openroad/yosys versions), and 34 of the 46 extra items are top-cell M2 pad
slivers where abutting cells meet — i.e. they track placement, not the flow.
They cannot be fixed from the flow side without editing released cells or
inflating utilisation, so `klayout__drc_error__count` is judged against a
recorded floor rather than against zero, and `ERROR_ON_KLAYOUT_DRC: false`.

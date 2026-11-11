# Digital hardening results — measured, not claimed

Twenty-eight runs: seven cores × four PDKs, all closing.  Every number below is
read from `configs/<pdk>/runs/<tag>/final/metrics.json` of a run that streamed
its own GDS out of its own database (`final/gds/<core>.gds`); no figure is
copied from an earlier run.  The run tag is quoted in every row so the evidence
can be opened.

`backend/collect.py` prints the tables below straight from those files; the
`ICS55_KLAYOUT_FLOOR` it compares against is the count the pinned runs measure.

## Toolchain

All runs in this file were produced with the pinned toolchain — LibreLane 3.0.17
built from `path:/opt/librelane` (`backend/.toolchain/librelane-3.0.17`, a GC
root), nix-eda 6.11.0, nixpkgs `b3aad468604d`:

| tool | version | | tool | version |
|---|---|---|---|---|
| klayout | 0.30.7 | | magic | 8.3.623 |
| openroad | 2026-02-17 | | netgen | 1.5.316 |
| yosys | 0.62 | | verilator | 5.044 |
| python | 3.13.9 | | iverilog | s20250103 |

`sta` is `/usr/bin/sta` (Debian OpenSTA 2.0.17), which is not part of the nix
pin.  `backend/toolchain.sh` resolves `LIBRELANE_BIN` / `KLAYOUT_BIN`; anything
that touches GDS by hand must use `$KLAYOUT_BIN`, never `/usr/bin/klayout`
(0.30.0).  See `TOOLCHAIN.md`.

Run tags:

| tag | meaning |
|---|---|
| `<arch>-pin1` | first full sweep under the 3.0.17 pin — `sky130A`, `gf180mcuD`, `ihp-sg13g2`, and `ics55` before its LVS repairs |
| `<arch>-pin2` | `ics55` pin1 + `DECAP_CELLS: []` + `ERROR_ON_LVS_ERROR: true` (plus the per-arch periods below) |
| `ti-pin3` | `ti-pin2` + the two CDL series-order hunks; only `ti` instantiates either cell |
| `ti-pin2-nocdlfix`, `ti-pin2`, `ti-pin1base` | kept as the LVS evidence trail behind `ICS55_SIGNOFF.md` |
| `*-ndr`, `*-t11`, `smoke1`/`smoke2`, `opt`, `*-r7`/`*-r3` | pre-pin experiments quoted only where the text says so |

## What “clean” means

For `sky130A`, `gf180mcuD` and `ihp-sg13g2` a run is clean only when all of:

- `route__drc_errors == 0` and antenna violating nets/pins `== 0`;
- `magic__drc_error__count == 0`, `magic__illegal_overlap__count == 0`,
  `klayout__drc_error__count == 0`;
- `design__lvs_error__count == 0`;
- `timing__setup__wns >= 0` and `timing__hold__wns >= 0` in **every** signoff
  corner.

For `ics55` sign-off is KLayout-only (see `README.md`): route DRC 0, antenna 0,
`design__lvs_error__count == 0` and every corner `>= 0` are required as above,
but `klayout__drc_error__count` is judged against a *recorded floor* instead of
0 — the released library cannot reach 0, see
[the ics55 section](#ics55-klayout-drc-is-not-zero-and-exactly-what-is-left).
`ERROR_ON_LVS_ERROR: true` is set, so LVS gates those runs.

`density` is `design__instance__area / design__die__area`.  It is used instead
of `design__instance__utilization` because the `ics55` flow sizes the core to
the instance area, which makes that metric identically 1 there.

## Matrix

| arch | sky130A | gf180mcuD | ihp-sg13g2 | ics55 |
|---|---|---|---|---|
| `ramp` | clean | clean | clean | clean² |
| `flash` | clean | clean | clean | clean² |
| `pipeline` | clean | clean | clean | clean² |
| `dual_slope` | clean | clean | clean | clean² |
| `vco` | clean | clean | clean | clean² |
| `sdm` | clean | clean | clean | clean² |
| `ti` | clean | clean | clean | clean² |

Legend: **clean** = every criterion above; **clean²** = ics55 criteria (route
DRC / antenna / timing / LVS clean, KLayout DRC within the recorded floor);
**MISS** = at least one criterion unmet.  There is no MISS.

## Per-PDK runs

### sky130A

| arch | run tag | inst | area µm² | die µm² | density | route DRC | KLayout DRC | LVS err | setup ws ns | hold ws ns |
|---|---|---|---|---|---|---|---|---|---|---|
| `ramp` | `ramp-pin1` | 649 | 3363.2 | 5657.9 | 59% | 0 | 0 | 0 | 0.000 | 0.000 |
| `flash` | `flash-pin1` | 3976 | 18705.4 | 23748.9 | 79% | 0 | 0 | 0 | 0.000 | 0.000 |
| `pipeline` | `pipeline-pin1` | 750 | 3716.1 | 6198.9 | 60% | 0 | 0 | 0 | 0.000 | 0.000 |
| `dual_slope` | `dual_slope-pin1` | 892 | 4414.2 | 7085.4 | 62% | 0 | 0 | 0 | 0.000 | 0.000 |
| `vco` | `vco-pin1` | 2296 | 10840.4 | 14750.5 | 73% | 0 | 0 | 0 | 0.000 | 0.000 |
| `sdm` | `sdm-pin1` | 12720 | 57695.3 | 66264.6 | 87% | 0 | 0 | 0 | 0.000 | 0.000 |
| `ti` | `ti-pin1` | 6396 | 30509.3 | 36727.8 | 83% | 0 | 0 | 0 | 0.000 | 0.000 |

### gf180mcuD

| arch | run tag | inst | area µm² | die µm² | density | route DRC | KLayout DRC | LVS err | setup ws ns | hold ws ns |
|---|---|---|---|---|---|---|---|---|---|---|
| `ramp` | `ramp-pin1` | 631 | 9933.3 | 15289.5 | 65% | 0 | 0 | 0 | 0.000 | 0.000 |
| `flash` | `flash-pin1` | 2733 | 46966.3 | 57690.2 | 81% | 0 | 0 | 0 | 0.000 | 0.000 |
| `pipeline` | `pipeline-pin1` | 653 | 9933.3 | 15351.6 | 65% | 0 | 0 | 0 | 0.000 | 0.000 |
| `dual_slope` | `dual_slope-pin1` | 815 | 13895.6 | 19781.0 | 70% | 0 | 0 | 0 | 0.000 | 0.000 |
| `vco` | `vco-pin1` | 1801 | 31314.5 | 39973.7 | 78% | 0 | 0 | 0 | 0.000 | 0.000 |
| `sdm` | `sdm-pin1` | 8834 | 153664.0 | 172078.0 | 89% | 0 | 0 | 0 | 0.000 | 0.000 |
| `ti` | `ti-pin1` | 4565 | 82047.8 | 95682.1 | 86% | 0 | 0 | 0 | 0.000 | 0.000 |

### ihp-sg13g2

| arch | run tag | inst | area µm² | die µm² | density | route DRC | KLayout DRC | LVS err | setup ws ns | hold ws ns |
|---|---|---|---|---|---|---|---|---|---|---|
| `ramp` | `ramp-pin1` | 461 | 5377.9 | 9159.1 | 59% | 0 | 0 | 0 | 0.000 | 0.000 |
| `flash` | `flash-pin1` | 2219 | 25528.6 | 33041.9 | 77% | 0 | 0 | 0 | 0.000 | 0.000 |
| `pipeline` | `pipeline-pin1` | 490 | 5343.4 | 9066.1 | 59% | 0 | 0 | 0 | 0.000 | 0.000 |
| `dual_slope` | `dual_slope-pin1` | 608 | 7185.0 | 11449.3 | 63% | 0 | 0 | 0 | 0.000 | 0.000 |
| `vco` | `vco-pin1` | 1315 | 16656.2 | 22695.3 | 73% | 0 | 0 | 0 | 0.000 | 0.000 |
| `sdm` | `sdm-pin1` | 7311 | 82874.5 | 95869.8 | 86% | 0 | 0 | 0 | 0.000 | 0.000 |
| `ti` | `ti-pin1` | 3834 | 47159.9 | 57500.4 | 82% | 0 | 0 | 0 | 0.000 | 0.000 |

### ics55

| arch | run tag | inst | area µm² | die µm² | density | route DRC | KLayout DRC | LVS err | setup ws ns | hold ws ns |
|---|---|---|---|---|---|---|---|---|---|---|
| `ramp` | `ramp-pin2` | 425 | 876.1 | 1427.5 | 61% | 0 | 343 | 0 | 0.000 | 0.000 |
| `flash` | `flash-pin2` | 2127 | 4583.0 | 5805.0 | 79% | 0 | 728 | 0 | 0.000 | 0.000 |
| `pipeline` | `pipeline-pin2` | 449 | 864.4 | 1394.3 | 62% | 0 | 333 | 0 | 0.000 | 0.000 |
| `dual_slope` | `dual_slope-pin2` | 587 | 1155.8 | 1794.5 | 64% | 0 | 437 | 0 | 0.000 | 0.000 |
| `vco` | `vco-pin2` | 1069 | 2735.0 | 3690.7 | 74% | 0 | 508 | 0 | 0.000 | 0.000 |
| `sdm` | `sdm-pin2` | 7041 | 14303.8 | 16439.9 | 87% | 0 | 1644 | 0 | 0.000 | 0.000 |
| `ti` | `ti-pin3` | 3462 | 7327.3 | 8790.6 | 83% | 0 | 1185 | 0 | 0.000 | 0.000 |

## Design points

The cores share one clock period per PDK, with two measured exceptions:

| PDK | `ramp` | `flash` | `pipeline` | `dual_slope` | `vco` | `sdm` | `ti` |
|---|---|---|---|---|---|---|---|
| `sky130A` | 20 ns | 20 ns | 20 ns | 20 ns | 20 ns | 20 ns | 20 ns |
| `gf180mcuD` | 20 ns | 20 ns | 20 ns | 20 ns | 20 ns | 20 ns | 20 ns |
| `ihp-sg13g2` | 10 ns | 10 ns | 10 ns | 10 ns | 10 ns | **11 ns** | 10 ns |
| `ics55` | 10 ns | 10 ns | 10 ns | 10 ns | 10 ns | **20 ns** | **11 ns** |

`arch_period` in `backend/gen_configs.py` is the only place these live.

**`sdm`** is the one architecture that misses the shared period on both 100 MHz
PDKs, so **its area is not comparable across PDKs** and its row in the matrix is
not a like-for-like 10 ns result:

- `ihp-sg13g2`: at 10 ns the core missed setup by −0.245 ns on a single path
  (`runs/sdm`, `runs/sdm-ndr`).  A delay-driven retry — `SYNTH_STRATEGY`
  `"DELAY 0"`, `SYNTH_SIZING`, `PL_RESIZER_ALLOW_SETUP_VIOS false` — was run
  first (`runs/opt`) and made it *worse*: −1.008 ns, 14 paths, +30 % area, the
  extra area costing more congestion than the upsizing bought.  That retry is
  not used; the core is specified at 11 ns (`runs/sdm-t11`), where it is clean
  at the same area as the 10 ns netlist.
- `ics55`: at 10 ns the core missed by −7.143 ns with 90 violating paths and
  TNS −210 ns (`runs/sdm-r7`), all of them fed by the two integrator bits
  `integ[2][0]` / `integ[2][1]`, which drive about 90 loads each.  At 20 ns
  (`sdm-pin2`) every corner is 0.

**`ics55` `ti`** misses 10 ns on the slow corner by 71 ps (`ti-pin1base`:
setup WNS −0.0709 ns at `nom_ss_125C_1v08`, one path; +3.68 ns at
`nom_tt_025C_1v20`), so it is specified at 11 ns here — same precedent as
`sdm`.  It also carries a per-architecture hold repair,
`PL_RESIZER_HOLD_SLACK_MARGIN: 0.30`:

- The core leaves one post-route hold violation on the loop
  `_1222_/Q -> _0996_ -> _1222_/D` (`ti-r7`, −0.0638 ns).  The only hold step
  the flow runs by default is `OpenROAD.ResizerTimingPostCTS`, which sees
  estimated RC, so the pre-GRT margin is what has to cover it: at 0.30 ns
  `ti-pin1base` reports hold 0 in all three corners with 366 hold buffers.
- `RUN_POST_GRT_RESIZER_TIMING` is **not** used: openroad 2026-02-17 aborts in
  `rsz::RepairSetup` with `[ERROR GRT-0267] Net _0300_ has disconnected
  segments` whenever that step runs on this design, for every
  `GRT_RESIZER_HOLD_SLACK_MARGIN` tried (0.05 / 0.10 / 0.20).

## ihp-sg13g2: the two repairs, and what they cost if skipped

Both are local overlays (`pdk_overlay/ihp-sg13g2/`), never PDK edits.

1. **Power-rail width.** The Ciel cache ships a rebuilt `libs.tech/librelane/
   config.tcl` in which `PDN_RAIL_WIDTH` is 1 instead of the vendor 0.44 µm
   (it also reorders the buffer lists and changes the decap/diode cells).
   Running the same `ramp_core` against the cache directly and against the
   composed root (`smoke1` vs `smoke2`, identical RTL, identical constraints,
   pre-pin) differs by 5062 Magic DRC, 96 illegal overlaps, 916 KLayout DRC and
   16 LVS errors versus 0/0/0/0; the violations are Metal1 spacing and rail
   shorts, which is what a rail 0.56 µm too wide produces.
2. **Clock NDR.** CTS applies a doubled-width/spacing rule to the clock
   (`-apply_ndr half`) but the router does not inherit it, which left
   `dual_slope` with 1 route DRC error (`dual_slope` versus 0 in
   `dual_slope-ndr`, pre-pin).  Declaring the same rule as
   `NON_DEFAULT_RULES: clk_half` plus `DRT_ASSIGN_NDR: {"^clk$": clk_half}`
   makes detailed routing obey it.

Both repairs are in `backend/pdk_overlay/` and `backend/gen_configs.py`;
`README.md` lists what each one restores.

## ics55: KLayout DRC is not zero, and exactly what is left

The KLayout deck cannot reach 0 on this library.  The reference is the PDK's own
`counter` example, hardened once with this flow, this deck and these options as
a control (it is not part of this repo): 55 instances, `route__drc_errors 0`,
`design__lvs_error__count 0`, `klayout__drc_error__count 83`.

The pinned runs, every item attributed by the `<cell>` field of
`drc.klayout.lyrdb` (100 % of items carry one):

| design | total | inside standard cells | in the top cell |
|---|---|---|---|
| `ramp` | 343 | 279 | 64 |
| `flash` | 728 | 493 | 235 |
| `pipeline` | 333 | 307 | 26 |
| `dual_slope` | 437 | 346 | 91 |
| `vco` | 508 | 337 | 171 |
| `sdm` | 1644 | 747 | 897 |
| `ti` | 1185 | 658 | 527 |

Inside cells, three rules only:

| rule | `ramp` | `flash` | `pipeline` | `dual_slope` | `vco` | `sdm` | `ti` |
|---|---|---|---|---|---|---|---|
| `M1DUM_W_1` | 180 | 326 | 194 | 228 | 240 | 490 | 454 |
| `metal.m1.area` | 94 | 160 | 107 | 112 | 94 | 251 | 191 |
| `poly.poly.area` | 3 | 2 | 2 | 2 | 2 | 3 | 8 |
| `metal.m2.area` | 2 | 5 | 4 | 4 | 1 | 3 | 5 |

`M1DUM_W_1` is pin *labels* the released cells keep on the dummy layer;
`metal.m1.area` is M1 shapes below 0.042 µm² inside the cells; `poly.poly.area`
is poly below 0.04 µm².  Nothing there is ours to fix.

In the top cell, three rules only:

| rule | `ramp` | `flash` | `pipeline` | `dual_slope` | `vco` | `sdm` | `ti` |
|---|---|---|---|---|---|---|---|
| `metal.m2.area` | 49 | 118 | 13 | 57 | 93 | 496 | 317 |
| `metal.m2.spacing` | 4 | 17 | 2 | 8 | 20 | 95 | 53 |
| `implant.np.enclosed_area` | 11 | 100 | 11 | 26 | 58 | 306 | 157 |

The `implant.np.enclosed_area` items are where two cells abut under the fill
cells; they scale with placement, not with routing.

### The top-cell MET2 items are isolated VIA1 landing pads

Measured on the streamed GDS itself (MET2 `82/1` merged, sub-minimum below the
deck's `m2_area` of 0.052 µm²):

| design | sub-minimum MET2 polygons | of which touch exactly one VIA1 | deck `metal.m2.area` | max area µm² | mean area µm² |
|---|---|---|---|---|---|
| `ramp` | 50 | 50 | 51 | 0.0445 | 0.0374 |
| `flash` | 118 | 118 | 123 | 0.0445 | 0.0378 |
| `pipeline` | 32 | 32 | 17 | 0.0380 | 0.0364 |
| `dual_slope` | 60 | 60 | 61 | 0.0445 | 0.0379 |
| `vco` | 93 | 93 | 94 | 0.0445 | 0.0379 |
| `sdm` | 472 | 472 | 499 | 0.0480 | 0.0372 |
| `ti` | 326 | 326 | 322 | 0.0445 | 0.0373 |

Every one of them is a single-via island, never a routed wire: zero polygons
touch no via, zero touch more than one.  `metal.m2.spacing` items are the same
pads — every edge pair is two pad edges 0.095 µm apart.

The cause is in the PDK.  `libs.tech/librelane/N551P6M_ecos.lef` sets
`LAYER MET2 { AREA 0.052 }` and gives the largest `MET2_MET1_VIA1_*` variants a
MET2 pad of 0.15 × 0.15 µm (0.0225 µm²); the default `_0` is 0.10 × 0.17 µm
(0.0170 µm²).  No single VIA1 on MET2 can satisfy the MET2 area rule, so any via
that does not land on a wider MET2 wire is illegal by construction — the router
obeys the same LEF the deck reads and cannot know it.

This is not fixable from the flow side.  Closing it needs either a PDK
erratum/waiver on via-only MET2, or a post-route step that widens isolated VIA1
pads.  Neither is claimed here, so `klayout__drc_error__count` is judged against
the floor recorded above and `ERROR_ON_KLAYOUT_DRC: false`.

## ics55: LVS is closed

`design__lvs_error__count` is 0 on all seven `ics55` runs, with
`ERROR_ON_LVS_ERROR: true`, so the check gates the flow.  Three independent
causes had to be fixed, all documented with geometry in
`ICS55_SIGNOFF.md`:

1. **Eight cells whose CDL does not describe the drawn cell** — four NAND2
   input-device swaps, `NAND3BBX0P5H7H`'s NMOS stack order, `OAI32X3H7H`'s
   folded PMOS fingers and `AOI22X2H7H`'s folded NMOS fingers, plus the
   `AOI22X1H7H` / `AOI22X2H7H` PMOS and NMOS series orders.  Repaired with
   `pdk_overlay/ics55/libs.ref/ics55_LLSC_H7CH/cdl/ics55_LLSC_H7CH.cdl.patch`
   (8 hunks / 8 circuits), applied by `pdkroot.sh` to a *copy* of the vendor
   CDL.  Every circuit verifies `ICS55_LVS_COMPARE=PASS` at cell level with
   matching device counts.
2. **Decap fillers shorting routed MET1** — `FILLCAP*H7H` are placed after
   detailed routing by `OpenROAD.FillInsertion`, and their internal MET1 plates
   land on routed signal wires: `flash` lost `_074_` to VSS and `net45` to VDD
   with the rectangles measured in the GDS.  Repaired with `DECAP_CELLS: []`;
   `FILLER*H7H` (no metal beyond the rails) stay in use.
3. **Two series orders the top-level comparer could not reconcile** in `ti` —
   19 of 13558 devices, all inside `AOI22X2H7H` and `AOI22X1H7H`.  Both are
   orderings that the cell-level comparer accepts, but the top-level one gives
   up on the two nodes they sit between; the CDL is now written in the drawn
   order.  Same GDS, four schematic variants, only the last one passes — the
   isolation table is in `ICS55_SIGNOFF.md`.

The flow places cells from `ics55_LLSC_H7CH_M2.gds` (`CELL_GDS`); both GDS
variants of the two repaired cells extract identically, so the variant choice
does not affect any verdict.

## Reproducing

```sh
make -C backend check            # 28 YAMLs current, PDK roots composed
backend/pdkroot.sh ics55         # compose + patch the PDK copies
backend/run.sh ics55 ti ti-pin3  # one job: <pdk> <arch> [run-tag]
python3 backend/collect.py       # the matrix and per-PDK tables, from the runs
```

A GDS is only evidence together with the run that produced it: `final/gds/` and
`final/metrics.json` in the same run directory, plus the `*.rpt` /
`drc.klayout.json` / `lvs.klayout.lvsdb` of the steps that produced the numbers.

# saradc_auto: notes for Claude

5-bit differential SAR ADC generator for IHP sg13g2 (`define NBITS 5` in `rtl/saradc.v` and
`rtl/sar_logic_wreset.v`). The ADC is assembled from custom cells (`cells/`): unit capacitors are MOS
gates (`SARADC_CELL_INVX16_ASCAP`, 720 instances), switches `SARADC_CELL_INVX0_ASSW` (1452), plus the
`sg13g2f` standard cells for the SAR logic and comparator. It is a macro of the ASICONE chip
(parent repo `asicone_sg13g2`, which has this repo as a submodule). The chip is fabricated and measured.

## Environment
- `~/init_openlane.sh`, `/opt/ext/OpenLane` and `/opt/ext/OpenPDKs` are gone -- the
  environment migrated to a nix profile + ciel. Do NOT source init scripts; the tools
  are already on PATH: `librelane` (~/.nix-profile, pin 3.0.17), `magic` 8.3.685,
  `netgen`, `klayout` (use `$KLAYOUT_BIN`, 0.30.7 -- see below).
- PDK lives under ciel now: `PDK_ROOT=$HOME/.ciel/ciel/ihp-sg13/versions/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008`,
  `PDK=ihp-sg13g2`. The ngspice decks are under `$PDK_ROOT/$PDK/libs.tech/ngspice/`.
- ngspice: version is now 44.2 (not 47). To run the IHP Verilog-A models you MUST
  export `SPICEINIT=$PDK_ROOT/$PDK/libs.tech/ngspice/.spiceinit` plus `PDK_ROOT`/`PDK`
  -- `pre_osdi` inside `.control` is too late. `src/run_contention.sh` wraps this.
  Note: this ciel tree's `libs.tech/ngspice/osdi/` is missing `cap_cmomi.osdi` and
  `cap_cmomf.osdi`; the .spiceinit prints load errors for them. Harmless for the MOS
  benches here, but anything that instantiates MOM caps needs those OSDIs fixed.
- Gotcha: `~/.spiceinit` globally pre-loads the IHP OSDIs. When running a NON-IHP
  deck (sky130 flattened, ics55 BSIM4) it will complain about missing `psp103.osdi`
  paths -- run with `HOME=/tmp/opencode/emptyhome ngspice ...` to isolate.
- KLayout: `/usr/bin/klayout` is 0.30.0 (too old for the PDK decks). Use the nix
  store 0.30.7 build; `gen/analog/klayout_bin.py` resolves it and `run.py` already
  exports the right binary via `$KLAYOUT_BIN`.
- Simulators: ngspice-44.2 (PSP103 via OSDI, KLU). Xyce: the flattened decks in
  `gen/outputs/deck/` (sky130_tt.spice, ics55_tt.spice) run in ngspice directly;
  ics55's deck is native BSIM4 `.model` cards, no OSDI needed.
- Flow: `make -C syn all`, `make -C pnr all`, `make -C signoff all` (see README.md).
  `settings.mk` holds the die size (X, Y) and the source list.

## Taped-out layout: back it up before re-running anything
`pnr/outputs/` and `signoff/outputs/` (SARADC.def/.cdl/.v/.lef, SARADC.gds/.spice, all dated
2025-10-17) are the taped-out version and are **git-ignored**. Copy them somewhere safe (e.g.
`pnr/outputs.tapeout/`, `signoff/outputs.tapeout/`) before re-running syn/pnr/signoff. A new PnR is only
useful if it reproduces this layout: compare the new GDS with the old one (KLayout XOR) and confirm
there are no differences in the DAC/comparator region before trusting extractions of it.

## Simulation (schematic level, done)
- `src/adc_char.py` (Makefile targets `char_static`, `char_sine`, `char_report` in `sim/`): static
  transfer (staircase + bisection) and coherent sine records, run as many short ngspice jobs in parallel.
  It flattens `pnr/outputs/SARADC.cdl` and merges parallel MOS (24198 -> 2902 devices). ngspice needs
  `--trtol 7` (default for ngspice). `--netlist` accepts another netlist, which is how a post-layout
  netlist should be fed in.
- Board conditions used for the measurement comparison: `--vdd 1.2 --vrefl 0.4 --vrefh 0.8 --vcm 0.6`,
  fclk 10 MHz (7 clocks per conversion), `--frontend <GBW>` for the board front-end model.
  Results: `sim/outputs/char/ngspice_SARADC.flat_mos_tt_10MHz_vdd1.2_ref0.4-0.8_cm0.6/` (git-ignored).
- Report: `sim/report/make_sim_report.py <char dir> --ref <char dir 1.8 V>/static.json`, writes
  `<char dir>/report/report.pdf`. It also writes `sim_summary.json`, which the measurement report in
  `RISCVConsole/software/mso/report/make_report.py` consumes (copy it to
  `RISCVConsole/software/mso/results/sweep_20261022/`).
- Schematic result: ideal (weights 1.000/1.998/3.999/8.001/15.992, DNL/INL within 0.01 LSB, ENOB 4.8).
  The chip measures bit weights -8 % to +7 % off binary, INL about 0.5 LSB rms, ENOB 3.8 at low
  frequency. Candidate causes: DAC layout parasitics (routing from the switched plates of each bit to
  the top plates), the undecoupled board references, unit-capacitor mismatch. **The post-layout
  simulation is the open task.**

## Layout extraction: what failed
- Magic 8.3.623 (`signoff/pex/`): device counts match the schematic (12093 nMOS, 12105 pMOS), but
  capacitances are about 100x too large (AVDD 4.27 nF, about 415 pF on one switch output, against about
  10 pF for the whole DAC), and net names are generic. Netgen 1.5.287 cannot compare it: the extracted
  transistors are calls to undefined `sg13_lv_nmos`/`sg13_lv_pmos` subcircuits, so their pins become 1-4.
- KLayout-PEX 0.3.12 / KLayout 0.30.10 (`signoff/kpex/`): LVS only matches with the standard cells
  flattened in the schematic (`SARADC.flatstd.spice`) because the `sg13g2f` cell layouts have no pin
  labels. The 2.5D engine (`--2.5D --mode CC`) is single-threaded on the flat layout and never finished:
  4 h, 12 h, 10 h (`--halo 1`) runs timed out, and a run on `SARADC.nofill.gds` (6530
  `SARADC_FILL1_NOPOWER` fillers removed, halo 1, no limit, started 2026-10-24 01:00) was still in the
  engine after 16 h with no progress output and was stopped.

## Hierarchical PEX (Bug10 -- implemented, replaces the flat-chip plan above)
The flat whole-chip extraction is abandoned.  Three domains never see each
other's internals:
- analog core (CDAC + sw + cmp): `python3 gen/analog/hier_pex.py --pdk ihp
  --cdac-gds <gds> --top <cell> --root <pdk_root> [--digital-spef <spef>]`.
  Reuses `closure.pex` (Magic, leaf-aware).  Verified on the ihp 4-bit array:
  158-line netlist, 24 MOS + 118 parasitic caps, seconds (not the 16 h flat
  KLayout run).
- digital core: gate-level SPEF from LibreLane.  `backend/gen_configs.py` has a
  `sar` arch (`backend/configs/<pdk>/sar.yaml`, clock_port=CLK); run
  `backend/run.sh ihp-sg13g2 sar` -> `configs/ihp-sg13g2/runs/sar/final/spef/
  nom/sar_logic_wreset.nom.spef`.
- merged netlist: `saradc_hier.spice` concatenates the three with a black-box
  boundary; top-level interconnect extraction is the remaining piece.

## MOM capacitor (Bug3 -- implemented, wired into RTL)
- Schematic: `topo_cap_mom` in `gen/analog/macro_gen.py` (ideal fringe cap +
  Csub/CWnw parasitics, voltage-independent).
- **RTL select:** leaf choice is by `` `ifdef SARADC_CAP_MOM `` inside
  `rtl/cap.v` (SARADC_CAP) and `rtl/cdac_unit.v` (switch leaf), NOT by
  generate-if parameters, so the flattened instance paths stay identical to
  the taped-out floorplan.  `CAP_MULT = NPW*NPH` for BOTH kinds (a MOM leaf
  replaces the whole 6-cap gate cluster per unit position) and the RTL dummy
  ring stays enabled (ndummy = nall-nu = 28/side).  Ports are unchanged.
- Flow switch: `CAP_KIND=MOM` in `settings.mk` / env -> `syn/tcl/synthesis_analog.tcl`
  does `verilog_defines -DSARADC_CAP_MOM`.  Verified with yosys:
  `-DSARADC_CAP_MOM` build has 720 `SARADC_CAP` (all MOM leaf, .VSH/.FL only),
  363 `SARADC_SW`, 56 dummy units -- same counts as the MOSCAP golden.
- Abstract cell: `cells/SARADC_CAP_MOM.{gds,lef,ckt,cdl,port.csv}`.  LEF is
  1.85x4.21 um, exactly TWO pins VSH/FL (pure metal leaf, no body tap); the
  RTL/cdl/LVS view is 2-terminal for that reason, and the sim model
  (`.ckt`) ties the plate-to-substrate caps to SPICE node 0.  Values are
  Magic PEX of the shipped GDS: `C(VSH,FL)=3.758 fF`, `Cfl=Cvsh=0.631 fF`
  (voltage-independent).  The 1.879 fF in older notes was the
  pre-VIA1-stitch revision (M1 comb floated; only M2 contributed).
  Registered in `cells/settings.tcl` (LEFS/CDLS).
- Geometry: `gen/analog/layout_mom_cap.py` (leaf) and `layout_mom_array.py`
  (array) share `gen/analog/mom_geom.py`, so leaf/array pitch/margins cannot
  drift.  Leaf = interdigitated M1/M2 combs, both buses VIA1-stitched; no
  per-leaf ring (ring is drawn once around the array so leaves abut).
  ihp: leaf and 8-bit array DRC 0 (array 240.9x48.1 um).
- Array topology: one row per bit, 2^bit leaves abutted at the leaf pitch;
  rows expose `B<bit>` (switched bottom plate) + one `VSH` rail/spine.  The
  VREFH/VREFL TG is **not** in the array -- it is instantiated by the analog
  macro (`SARADC_SW` in RTL / `SARADC_SW_TG_<pdk>` layout).
- PDK abstraction: `gen/analog/layers.py` + `gen/pdk/<pdk>.layers.json`
  (layer/datatype map + um DRC constants + `momcap` recipe incl. LEF layer
  names); the three `layout_*_array.py` read it instead of hardcoding layers.
- Behavioral evidence (`--cv` in `gen/verify/behavioral_sar.py`): cv=0.8 MOSCAP
  -> ENOB 2.53 / INL 37 LSB; cv=0 MOM -> ENOB 7.54 / INL 0.2 LSB.
- Magic PEX caveat: hierarchical extraction does NOT merge a parent M2 strap
  lying over a child's M2 bus -- run `gds flatten true` before `extract` (or
  give the leaf M2 its own port) or the strap extracts as a floating `_uq0`
  node.  Flat PEX of the 8-bit array reads B0..B7 vs VSH as
  1.958/4.147/8.456/17.07/34.31/68.78/137.72/275.60 fF (B1+ within 2% of 2x).
- IHP via rule: V1.a is min=max 0.19 um, so stitching via cuts are exactly
  0.19 (a 0.20 cut fails "max").  Array rails carry no vias of their own --
  they join through the leaf's via row; adding rail vias on the leaf vias
  forms 0.24-wide partial-overlap cuts that violate V1.a.

## Bootstrap switch (implemented at RTL + SPICE, no physical leaf yet)
- **RTL select:** `SARADC`/`SARADC_ANALOG`/`SARADC_LSB_CDAC`/`SARADC_MSB_CDAC`/
  `SARADC_CDAC_UNIT` take `parameter SW_KIND = "TG"`.  `SW_KIND="BOOT"`
  instantiates the `SARADC_SW_BOOT` blackbox for all three CDAC switch groups
  (VI->VSH, VSH->VOUTH, VSH->VOUTL).  The six-port contract (VDD, VSS, SB, S,
  Z1, Z2) is identical to `SARADC_SW`, so no CDAC wiring or port changes; S/SB
  are the two clock phases a bootstrap needs.  `cells/SARADC_CELL_INVX0_ASSW`
  is still emitted for the OR paths / SARADC_SW_MULT (VOUTH-VOUTL merge).
- Flow switch: `SW_KIND=BOOT` in `settings.mk` / env -> `synthesis_analog.tcl`
  does `verilog_defines -DSARADC_SW_BOOT`, which flips `SARADC`'s default.
  yosys: default 4271 cells / 2178 ASSW; BOOT 3119 cells / 192
  `SARADC_SW_BOOT` / 1026 ASSW.  iverilog elaborates all three variants
  (TG, BOOT, BOOT+MOM).
- **No physical leaf.**  `SARADC_SW_BOOT` is a blackbox with no GDS/LEF: the
  netlist will not place.  `synthesis_analog.tcl` prints a WARNING for this.
  Default stays "TG" until a leaf exists.
- Bench model: `topo_switch_bootstrap` in `gen/analog/macro_gen.py`
  (8 devices, `.subckt SARADC_SW_BOOT CLK VIN VOUT VDD VSS`), characterized by
  `char.switch_boot`.  Same bench for both kinds (IHP tt, l=0.13 um, 50 uA DC
  load, 20 ns transient, sampled at 12 ns), Ron = (Vin-Vout)/Iload:
  ```
  Vin     TG wn2/wp1   BOOT wn2/wp1 cb500   BOOT wn5/wp2 cb500
  0.18       449            395                 152
  0.39       849            425                 165
  0.60      2395            564                 227
  0.81      4755           1116                 484
  1.02      3059           3328                2120
  overdrive   --     1.11..0.75 V         1.09..0.65 V
  ```
  The bootstrap removes the mid-rail |Vgs| collapse (2-4x lower Ron at 0.4-0.8 V)
  but does NOT flatten Ron: at high Vin the gate only reaches ~VDD+0.4 V because
  the bottom-plate device (MX, gate=CLK) saturates at VDD-Vth, and the residual
  rise is NMOS body effect.  Full-rail VDD+Vin would need 3.3 V devices and an
  isolated-well strategy; not attempted.  A DC conductance probe of the same TG
  (gates rail-to-rail, no load) peaks at 27.4 kOhm at mid-rail.
- Verdict: the abstraction and the simulation evidence are real, but this is
  not sign-off quality and not tape-out ready.  Treat the 2-4x mid-range Ron
  improvement as the deliverable and the flat-Ron goal as open work.

## Comparator (StrongARM behind CMP_KIND)
- `SARADC_COMP` takes `parameter CMP_KIND = "LATCH"`.  `"STRONGARM"` swaps the
  OAI211X16 cross-coupled latch for the `SARADC_CMP_STRONGARM` blackbox; the
  output buffers and polarity (OUTP=1 when VP>VN) are unchanged, and both cores
  drive the same pre-nodes.  Plumbed through `SARADC_ANALOG`/`SARADC`; the flow
  switch is `CMP_KIND=STRONGARM` -> `-DSARADC_CMP_STRONGARM`.
- yosys: default has 2 `SARADC_CELL_OAI211X16`; STRONGARM has
  1 `SARADC_CMP_STRONGARM` and 0 OAI211X16.  iverilog elaborates both, and both
  together with SW_KIND/CAP_KIND.
- Bench evidence (IHP tt, wn=1.0/wp=2.0/l=0.13, `gen/analog/char.py
  comparator()`): systematic offset -78 uV (bisection over +-40 mV, 9
  iterations) and clk->out regeneration delay 0.159 ns.  The `.noise` run
  produced no spectrum (all densities 0), so the integrated-noise field is 0 by
  artifact, **not** a measurement -- do not quote it.
- **No physical leaf** for `SARADC_CMP_STRONGARM` (same status as the
  bootstrapped switch): synthesis works, placement does not.  The script warns.

## Asynchronous SAR sequencer (integrated behind SEQ_KIND)
- `SARADC` takes `parameter SEQ_KIND = "SYNC"`; `SEQ_KIND="ASYNC"` selects the
  self-timed `sar_logic_async` controller.  Both sequencers are instantiated
  and a small mux picks RESULTP/N/SAMPLE/VALID.  Flow switch `SEQ_KIND=ASYNC`
  -> `-DSARADC_SEQ_ASYNC`.
- `rtl/sar_logic_async.v`: the **synthesizable** controller.  GO -> SAMPLE ->
  per-bit evaluate/precharge on the comparator's RDY (= NAND(OUTP,OUTN)).  It
  is written as a state register clocked on RDY plus two combinational flags
  (`armed` = GO&~RST, `req`), so yosys maps it to real DFFs + gates (15 cells:
  `$adff`/`$adffe`/mux), not a blackbox.  Emits `CMPSTART` (1=evaluate,
  0=precharge), which the top muxes onto the analog's `CMPCLK` in place of the
  system clock.  `armed`/`req` are combinational -- putting `done` in the
  reset path glitched `clr` on the LSB's RDY edge, so `done` only gates VALID
  and `req`.
- `rtl/sar_logic_async_behav.v`: the level-sensitive reference model kept for
  mixed-signal testbenches and equivalence checks; identical port contract.
- `rtl/sar_logic_conv_async.v`: the async control path for
  `saradc_logic_buf`.  Same port contract as `saradc_logic_conv`, so the buf
  instantiates it unchanged behind the SEQ_KIND generate.  Mapping: PRE=SAMPLE,
  HOLD=~SAMPLE, CMP=CMPSTART delayed by three DEL4X4 (the LATCH core's compare
  edge), CLKBUF=CMPSTART (the STRONGARM core's clock), CR* from RESULTP/N via
  re2cr exactly like the sync path.
- `SARADC_ANALOG` gained a `CMPSTART` input and `CMPOB` output (the
  comparator's OUTN leg, previously left unconnected) -- both are SEQ_KIND
  plumbing, no existing port changes.
- Verified: `iverilog -DSARADC_SEQ_ASYNC` elaborates (16 kind combinations OK),
  and `rtl/saradc_async_tb.v` runs the full SARADC (both sequencers live) 4/4
  codes correct, 18 ns conversion in the model.  Standalone `rtl/async_sar_tb.v`
  passes 4/4, and the behavioural model is code-equivalent to the synthesizable
  one on the same stimulus.  yosys synthesizes the controller alone to 15
  cells and the full ASYNC top without errors.
- **GO contract.**  GO is a level, not a pulse: the host holds GO high until
  VALID, then drops it to retire the conversion.  A GO-low->high cycle
  re-arms; holding GO high repeats conversions (continuous mode, same as the
  sync controller).
- **Caveats.**  (a) CR* change combinationally on the RESULT write at the same
  RDY level that drops CMPSTART, so the DAC's charge injection and the next
  request are separated only by the comparator's own precharge time; a hardened
  design adds a settle phase.  (b) The conversion timing is set by the
  comparator's regeneration/precharge delay, which SPICE -- not STA -- must
  bound.

## Architecture options (behavioural, `gen/verify/sar_arch_options.py`)
- `switching_activity()`: conventional differential trials cost 2 events/bit
  plus 2 per cleared bit (24 events/conversion at 8 bit); monotonic
  (Vcm-based) costs exactly 1/bit (8 events).  The trade-off is explicit: the
  conventional scheme holds the common mode at Vcm (0 walk), monotonic walks it
  up to N steps off Vcm toward one rail.
- `bridge_weights()` + best-fit INL: a series-bridge sub-DAC with ratio error
  eps adds a real INL step at the bridge boundary -- at 8 bit, split_bit=4:
  eps=0 -> 0.006 LSB, 0.5% -> 0.045 LSB, 1% -> 0.083 LSB.
- `calibrate()`/`apply_trims()`: per-bit analog trims (switchable trim caps,
  clipped to +-25%) computed from the measured weights.  On a bridge+1% unit
  mismatch array the INL falls 0.107 -> 0.006 LSB.
- `gen/verify/sar_arch_options_test.py` checks all of the above (all PASS).
  Note the INL here is best-fit and drops the unreachable top code -- the raw
  cumulative-DNL-from-bin-0 number carries a 1-LSB gain artifact.

## MOM array: common-centroid order + dummy rows
- `gen/analog/layout_mom_array.py` takes `order=binary|centroid`.  Binary
  left-aligns every bit row, so bit b sat at a different x and a linear process
  gradient became a bit-weight error.  `centroid` centres every row on the
  array's x centroid and adds `dummy_rows=1` full-width dummy rows at the top
  and bottom; a dummy's FL rail is strapped to the VSH spine, so both plates sit
  at VSH and it carries no switched charge.
- ihp DRC 0 for both orders.  Flat Magic PEX (flatten true) of the centroid
  array: B0..B7 / VSH = 1.940 / 4.099 / 8.424 / 17.083 / 34.401 / 69.037 /
  138.31 / 276.92 fF (binary: 1.958 / 4.147 / 8.456 / 17.07 / 34.31 / 68.78 /
  137.72 / 275.60 fF).  Ratios from B1 up are 2.11 / 2.06 / 2.03 / 2.01 / 2.01
  / 2.00 / 2.00, so centring did not disturb the binary ratio.
- Real bug found while doing this: dragging the FL rail back to `X0` in the
  centred layout ran it parallel to the row's (necessarily long) VSH rail over
  empty metal, and B0 read 5.92 fF instead of ~1.94 fF.  The FL rail must span
  the active block only; that is why `put_row()` starts it at `x0 - 0.3`.

## P&R vs pinned OpenROAD 2026-02-17 (dcf36133): known crashes + workarounds
- **DPL pixel-grid abort.**  `detailed_placement` builds its pixel grid over
  the *block core area*; our ANALOG rows sit outside the digital core
  (x=18 vs core xMin=452) so dpl indexes a negative pixel and aborts.
  Fix in `pnr/tcl/pnr.tcl`: a `detailed_placement` proc wrapper
  (`rename`+redefine) widens core->die for every call, restores after.
- **CTS stack overflow (the hard one).**  `clock_tree_synthesis` ends in
  `writeDataToDb` -> `countSinksPostDbWrite`, which recurses
  iterm->`getFirstOutput()`->net with NO visited set; it stops only at
  sequentials/blocks.  Our netlist has pure-combinational loops reachable
  from net CLK through `analog/.../smpcs.clkbuf.impl` (BUFFD16, treated as
  clock-net buffer, not a sink): the comparator's cross-coupled OAI latch
  (`analog/\cmp.OUTPp <-> OUTNp`) and the VALID feedforward
  (`VALID -> cmpbegin.CCMPNR -> ... -> comparator -> CMPO -> VALID`).
  Result: unbounded recursion -> SIGSEGV (top 40 gdb frames all
  `countSinksPostDbWrite`).  The tape-out flow survived only because its
  older OpenROAD lacked this pass.  Workaround in `pnr/tcl/pnr.tcl`: all
  `analog*` INPUT iterms on net CLK are disconnected for the CTS window
  (`$it disconnect`), reconnected after `repair_clock_nets`.  Clock tree =
  17 digital FFs only, same as golden.  A second contributor -- the
  `CMPO -> ND2D1(RDY) -> digital_async -> CMPSTART -> analog.CMPCLK -> CMPO`
  loop -- is removed at the RTL source: `rtl/saradc.v` gates CMPSTART by
  `& (SEQ_KIND == "ASYNC")`, so a SYNC build folds analog.CMPSTART to 1'b0.
- **Flow markers.**  All stage boundaries print `[Flow] ...` to *stderr*
  (stdout is block-buffered and the crash swallowed the last lines).
  Debug DEFs: `dbg_post_place.def` (pre-CTS, pair with
  `/tmp/opencode/repro_cts.tcl` for ~2min CTS iterations), `dbg_post_cts.def`,
  `dbg_post_spc.def`.
- `pnr/Makefile` has `OR_CMD ?= openroad -exit` so the same target can run
  under gdb: `make -C pnr OR_CMD=/tmp/opencode/openroad_gdb.sh`
  (`nix-shell -p gdb`, stack trace lands in the log).
- **RSZ-0074 in repair_design.**  After global_route, `repair_design`
  aborted with "failed to build tree from gloubal routes: found route to 5
  pins, expected 8".  Cause: with `-global_routing` parasitics the resizer
  rebuilds each driver->load tree from the global-route guide set
  (`rsz::Resizer::makeBufferedNetGroute`), and some 8-load net's guides did
  not cover all its pins.  Fix in `pnr/tcl/pnr.tcl`: re-estimate with
  `estimate_parasitics -placement` (NOT `-global_routing`) immediately
  before both `repair_design` and `repair_timing`.  That (a) clears the
  parasitics-invalid flag the IncrementalParasiticsGuard checks (EST-0104)
  and (b) makes the repair build load trees with FLUTE over pin
  coordinates instead of from the incomplete guides.  Verified: the
  repro `repro5.tcl` runs repair_design to 0 remaining violations.
- `ODB-0047` (dbInst::getFirstOutput wants DIRECTION OUTPUT non-supply) was
  fixed by flipping `SARADC_CELL_INVX0_ASSW.lef` pin `zn` to
  `DIRECTION OUTPUT ; USE SIGNAL`.  `DPL-0033` after detailed placement is
  pre-existing golden behaviour (caught and ignored).

## Signoff DRC/LVS root causes (MOM build)

The first complete MOM build reported **6791 DRC items / 23 rules** and a
2-cell LVS mismatch. They come from exactly three root causes; all three are
flow bugs, none of them is in the golden analog topology.

### 1. Standard cells placed in the orientation of the opposite row parity

OpenROAD writes N / FS rows alternately from the core margin upwards
(`ROW_0 = N`, `ROW_1 = FS`, ...), and every instance must carry *its own*
row's orientation. `comp_pos.tcl` was placing the fixed-position cells with
`up -> MX` / `dw -> R0`, which is the opposite of the DEF convention
(`MX` == DEF `FS`), so 7 instances ended up mirrored in an unmirrored row
(or not mirrored in a mirrored row):

| instance | position (um) | placed | row |
|---|---|---|---|
| `sg13g2f_BUFFD16` | (427.78, 159.12) | N | FS |
| `BUFFD0` / `BUFFD2` / `TAPCELL` | (70.04 / 72.76 / 76.50, 159.12) | N | FS |
| `BUFFD0` / `BUFFD2` / `TAPCELL` | (70.04 / 72.76 / 76.50, 165.24) | FS | N |

A mirrored cell butts its NWell / pSD strip into the neighbouring row's N+
diffusion, which produces the whole non-routing block of the report:
`NW.a 2, NW.c 16, NW.d 46, NW.e 44, NW.f 4, pSD.d 7, pSD.e 14, pSD.f 12,
pSD.g 3, NBL.e 9, Cnt.g1 5, Cnt.g2 4, LU.a/b` in three vertical strips at
x ~ 18-37 / 70 / 428, y ~ 159-178 - plus, in KLayout deep LVS, 92 NMOS
promoted to the top level (the flipped masters' devices no longer match the
CDL).  LOCKED instances are never re-oriented by the legalizer, so this must
be right at placement time.

Fix: `comp_pos.tcl:row_orient` derives the orientation from the row index
(`round((y-corey)/row) % 2` -> `MX`, else `R0`) instead of a direction
heuristic, and all 8 `place_inst` calls use it.  Checked:
`row_orient(159.12) = MX` (DEF `FS`), `row_orient(165.24) = R0`,
`row_orient(146.88) = MX`.

### 2. `tapcell -distance` is a radius, the realised pitch is ~2x it

Measured on this design by dropping the inserted taps and re-running
`tapcell` on the placed DEF (`/tmp/opencode/tap_exp.tcl`, one minute):

| `-distance` | taps | realised pitch in an analog row | worst half-pitch |
|---|---|---|---|
| 40 (original) | 164 | 78.54 um | 39.27 |
| 20 | 300 | 38.08 um (one row 20.06) | 19.55 |
| 15 | 373 | 28.56 um | 14.28 |

IHP's latch-up rules allow **20 um** from any diffusion to a well tie
(`LU.a`: P+Activ in NWell -> nSD-NWell tie, `LU.b`: the N+Activ/PWell
mirror), so `-distance 40` left up to 39 um and produced 51 `LU.a` + 48
`LU.b` items in the analog stdcell band (y = 134.6-183.6, the only rows
carrying logic on the analog side).  `-distance 15` gives ~14.3 um plus the
<= 6.12 um row offset, i.e. ~15.5 um worst case.  Cost: +209 `TAPCELL`
(2,180 um^2, 1.7 % of the analog core).

### 3. The metal MOM leaf cannot take any vertical Metal2

`SARADC_CAP_MOM` is pure metal: both plates (`FL` bottom, `VSH` top) span the
full 1.85 um cell width, and the cell is only 4.21 um tall.  The MOSCAP leaf
tolerated a vertical Metal2 rail through the cell (its gate and body are
separate terminals); for the MOM leaf *any* vertical Metal2 rail inside the
cell crosses both plates and merges VSH and FL into one polygon.  That is
both the DRC source (`M2.b 3373, V1.b 1332, V1.a 1080, V2.a 360, M1.b 136,
M2.c1 120, M3.c1 120` in the cluster region) and an electrical short: CPRE
shorted to the bottom plate breaks the comparator.

Fix in `pnr/tcl/saradc_conn.tcl`: when the leaf's terminals are `VSH`/`FL`
(`mom_leaf`) no Metal2 is drawn at all.  Instead each net is reached with a
horizontal **Metal3** rail laid straight over one of its own plates, with the
Via2 array dropped into the plate (`-orthogonal_only false` is required:
`add_vias_over_area` skips geoms whose long axis matches the stripe's, and
the plates are horizontal like the rails):

- `VSH`: one rail centred in the bottom row's VSH plate (y 3.71-4.21);
- `FL`: one rail in the bottom FL plate (0.00-0.50) and one in the top row's
  mirrored FL plate (7.92-8.42).

Rails are 0.30 um wide (Via2 needs 0.19 cut + 0.05 endcap = 0.29) and span
the block width, so the block's own Metal4 stripes - one per net, spanning
the block height - pick them up with their own Via3 pass and tie the two FL
rails together.  No Metal4 is added inside the cluster.

Validated standalone before spending a 3 h P&R: `/tmp/opencode/proto/mkcap.py`
builds a 3x2 cluster plus these rails, `run_drc.py --topcell=TESTCAP
--run_mode=flat` reports **0 violations**, and a KLayout `LayoutToNetlist`
connectivity run (M1/V1/M2/V2/M3/V3/M4) yields **exactly 2 nets**, i.e. VSH
and FL are separate and each plate group is fully connected.

### 4. LVS must run in deep mode

`--run_mode=flat` loses ~370 devices on this design and promotes 92 NMOS to
the top level, so "flat clean" is not evidence.  `make -C signoff lvs` now
runs `--run_mode=deep`; `lvs_flat` remains for quick iteration only.

## Detailed-route status of the MOM build
- `-droute_end_iter` is 64.  The earlier "plateau at ~51 violations, 11 min
  per stubborn-tile round" was a CPU-contention artefact of this shared
  machine; the same 64-iteration budget has finished at 32 violations.  Left
  the full budget in place now that the two clusters that used to refuse to
  converge (the cap-leaf plate shorts and the mis-oriented cells) are fixed.
- Remaining known weak spot: `CPRE/CPREB` against `VOUTH/VOUTL` in the
  `sw_vouth2voutl` group next to the comparator, at pin edges (e.g. the left
  sliver of `pgn_lz2.vdd`, a 1.70 x 0.30 um full-width rail, where a gate
  wire enters the cell by 0.06 um).  This is a pin-access problem in a
  0.34 um inter-leaf gap, not congestion.  Check the router report and the
  GDS DRC for it after each P&R run.

## Hierarchical PEX target
- `make -C signoff pex` runs `signoff/tcl/pex.tcl`: Magic per-cell extraction
  (`extract do local on` + `extract all`, `ext2spice hierarchy on`,
  `cthresh/rthresh 0`, `format ngspice`, `subcircuit top on`) over the
  assembled `signoff/outputs/SARADC.gds` -> `SARADC.pex.spice`.  Replaces the
  flat-chip plan that never finished (KLayout 16h+, Magic 100x rail caps).
- `src/adc_char.py flatten()` now keeps leaf `C` elements and auto-appends
  `cells/SARADC_CAP_MOM.ckt` when the P&R CDL mentions SARADC_CAP_MOM --
  without it the MOM CDAC flattens to zero capacitance (the leaf is an empty
  2-pin subcircuit in the LVS CDL).

## Conventions
- Commit only when the user asks; end commit messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

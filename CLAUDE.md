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

## Next task: extract the layout in parts
Re-do the PnR so the analog core can be extracted on its own, then simulate post-layout:
1. Back up the taped-out outputs (above).
2. Produce a layout of just the DAC (cap array + switches + top-plate routing) and the comparator,
   with the SAR logic and fillers removed, either as a separate hierarchy level in PnR or by clipping
   the taped-out GDS to that region with its pins labeled. Labeled pins at the cut are what make LVS
   and the extracted netlist usable.
3. Extract it with kpex 2.5D (CC mode) and check that the totals are plausible (whole DAC about 10 pF),
   then simulate with `adc_char.py --netlist` (static first; it shows the weight errors directly) and
   compare the bit weights with the measured ones.
4. Add the post-layout results to `sim/report` and to the measurement report in RISCVConsole.

## Conventions
- Commit only when the user asks; end commit messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

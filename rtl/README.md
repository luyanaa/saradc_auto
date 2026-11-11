# RTL family for the non-SAR ADC architectures

This directory holds the **technology-independent** RTL for the seven non-SAR
architectures in `gen/ARCH_STUDY.md`: `ramp`, `flash`, `pipeline`,
`dual_slope`, `vco`, `sdm`, `ti`.

The rule that makes four-PDK parallelism affordable:

> RTL is plain Verilog-2005 and contains no PDK primitive, no standard cell,
> no vendor attribute and no hard-coded geometry.  A PDK changes the synthesis
> library, the constraints and the analog macro generator; it never edits a
> file in this tree.

## Layers

| path | contents | synthesizable |
|---|---|---|
| `common/` | package, reset synchroniser, clock enables, two-phase clock, binary/thermometer helpers | yes |
| `analog_models/` | `real`-valued behavioral models for comparator, DAC, S/H, integrator, SC integrator, VCO, MDAC, flash front end | **no** (co-simulation only) |
| `<arch>/` | one digital core per architecture + self-checking testbench + file list | core yes, testbench no |

`analog_models/` is excluded from synthesis and from every hardening run.  The
models exist so an architecture can be verified end to end before a transistor
exists; the digital core is unchanged when the real macro arrives.

## Digital / analog contract

Every architecture follows the same split:

```
          digital core (this tree)                 analog macro (generator)
          ------------------------                 ------------------------
  start -> FSM / counters / encoders / filters  -> control buses
  cmp   <- sampled comparator decision          <- comparator + front end
  sample-> S/H or CDAC switch control           -> switched-cap network
  dout  -> N-bit result + valid/status          (no digital state)
```

The digital core only ever sees 1-bit comparator decisions, control strobes,
and (for VCO/TI) a clock or a sub-ADC valid/data bus.  It never sees a `real`.
That is what keeps it synthesizable and PDK-neutral.

## Top-level port convention

Each `<arch>/<arch>_core.v` uses this reset/clock convention:

* single clock `clk`, asynchronous active-low `rst_n`;
* all state on `posedge clk`; no derived clocks, no gated clocks;
* `start` is a one-cycle request (the core may ignore it while `busy`);
* `valid` is a one-cycle pulse qualified by a stable `dout`;
* `busy` is high from the accepted `start` until the result is registered.

The testbench instantiates the core and the behavioral analog macros.  The
`<arch>_tb.v` is the only place where `real` appears.

## Simulation

```sh
make -C rtl lint                 # compile every core and testbench
make -C rtl sim-ramp             # one architecture
make -C rtl sim                  # all seven
```

Each `<arch>/filelist.f` lists the files for that architecture; the Makefile
does not guess.  `iverilog -g2005` is the reference simulator; `verilator
--lint-only` is the second opinion.

## Architectures and their digital work

| arch | synthesizable digital core | analog macro it drives |
|---|---|---|
| `ramp` | ramp counter, sample/compare FSM, result register | DAC, S/H, comparator |
| `flash` | bubble-tolerant thermometer encoder, calibration | 2^N-1 comparator bank, ladder |
| `pipeline` | 1.5-bit/stage redundant-signed-digit combiner, two-phase clock, calibration | MDAC (OTA + 2C/1C + TG) |
| `dual_slope` | 2^(N+1) counter, auto-zero/integrate/de-integrate FSM | integrator (OTA + R + C), comparator |
| `vco` | edge counter, differentiator, decimating filter | ring VCO, quantizer |
| `sdm` | CIC4 + halfband decimator, clock enables | SC integrator, 1-bit comparator |
| `ti` | N-phase generator, mux control, offset/gain calibration, combine | N SAR channels (reuses `sar_logic_wreset`) |

## Status

- common layer: present.
- all seven architecture cores: first pass present and simulated.
  `make -C rtl lint` compiles every core and bench; `make -C rtl sim` runs the
  seven self-checking benches and prints `PASS <arch>` for each.
- every core also passes `yosys synth -top <core>` in generic mode (no PDK),
  which is the synthesizability gate before a per-PDK hardening run:

| arch | top | generic cells (Yosys) | bench result |
|---|---|---|---|
| ramp | `ramp_core` | 87 | PASS ramp |
| flash | `flash_core` | 317 | PASS flash (clean=32 bubbled=31) |
| pipeline | `pipeline_core` | 413 | PASS pipeline |
| dual_slope | `dual_slope_core` | 203 | PASS dual_slope |
| vco | `vco_core` | 233 | PASS vco |
| sdm | `sdm_core` | 1882 | PASS sdm |
| ti | `ti_adc` | 617 | PASS ti |

  (cell counts are generic `synth` results, not a mapped area; the per-PDK
  numbers belong to the hardening runs.)
- analog layout generators: next tranche, starting from the leaves that already
  close (`cap_unit`, `switch_tg`, StrongArm comparator) and adding the
  precision resistor, OTA and ring VCO.

Nothing here is a signoff claim.  A core is "verified" only when `make -C rtl
sim-<arch>` passes with the checked-in testbench.

# Dual-slope RTL

`dual_slope_core` owns the three-phase conversion cycle of an integrating ADC:
auto-zero, integrate for a fixed `TINT` clocks, then de-integrate against the
reference until the comparator crosses zero.  The de-integration count is the
result.  The integrator (OTA + R + C), the precision resistor pair and the
comparator are analog macros.

## Interface

```
dual_slope_core #(.N(8), .AZ_CYCLES(4), .TINT(2^N))
  clk, rst_n, start, cmp,
  az, integrate, deintegrate,
  dout[N:0], valid, busy, status[1:0]
```

`dout` is `N+1` bits because de-integration may run to `2*TINT`; a crossing
after the limit reports `ADC_ST_TIMEOUT` with dout all ones.  The internal
counter is `N+2` bits.

## Timing

```
IDLE --start--> AZ (AZ_CYCLES) --> INT (TINT) --> DEINT (until cmp or 2*TINT)
```

`az` resets the integrating capacitor, `integrate`/`deintegrate` select the
input and reference switches.  The three strobes are mutually exclusive.

## What the test proves

`make -C rtl sim-dual_slope` closes the loop with the behavioral RC integrator
and comparator, converts codes 4/16/64/128/192/252 and checks each result, then
forces `cmp=0` and checks the timeout status and data.  Result: `PASS
dual_slope`.

## Limitations

- ideal integrator: dielectric absorption, finite OTA gain and comparator
  offset are modelled in the architecture budget (`gen/arch_study/dual_slope.md`)
  but not in this bench;
- comparator polarity is fixed by the bench (`cmp` = integrator has crossed
  zero); a real comparator with hysteresis needs its own model;
- the resistor ratio error is the dominant static error and lives entirely in
  the analog leaf.

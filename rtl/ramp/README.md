# Ramp (single-slope) RTL

`ramp_core` is the digital half of a single-slope ADC: a DAC code counter, a
sample/ramp/compare FSM, and the result register.  The DAC, the S/H and the
comparator are analog macros; the core only drives `dac_code`/`sample` and
reads `cmp`.

## Interface

```
ramp_core #(.N(8))
  clk, rst_n, start, cmp,
  dac_code[N], sample, dout[N], valid, busy, status[1:0]
```

`cmp` is 1 when the DAC output has reached the held input.  `status` is
`ADC_ST_OK`, or `ADC_ST_OVERRANGE` when the counter reaches full scale without
a comparator hit (dout is then all ones).

## Timing

```
IDLE --start--> SAMPLE (sample=1) --> RAMP (DAC settles) --> CHECK (read cmp)
CHECK: hit -> dout=dac_code, valid, busy=0, back to IDLE
       miss at full scale -> overrange
       miss -> dac_code+1, back to RAMP
```

Three clocks per code: one to apply the code, one for the comparator, one to
decide.  A conversion is at most `3*2^N + 3` clocks.  The conservative settling
is a parameter of the FSM, not of the ports, so a faster comparator only
removes states.

## What the test proves

`make -C rtl sim-ramp` sweeps all 256 codes through the behavioral S/H, DAC and
comparator and checks every decoded code, then forces an over-range input and
checks the status.  Result: `PASS ramp`.

## Limitations

- one comparator read per code; no meta-stability handling (a real
  implementation would double-sample or use a synchroniser on `cmp`);
- no foreground offset/gain calibration;
- DAC settling is assumed to be one clock at the target frequency.

# Sigma-delta RTL

`sdm_core` is the digital decimator for a 1-bit sigma-delta modulator: a
CIC filter of order `CIC_ORDER` and decimation `OSR`, producing an N-bit signed
sample.  The modulator (switched-capacitor integrator + comparator) and its OTA
are analog macros.

## Interface

```
sdm_core #(.N(16), .OSR(64), .CIC_ORDER(4))
  clk, rst_n, start, bit_in,
  dout[N] (signed two's complement), valid, busy, status[1:0]
```

`start` clears the integrators, combs and counter.  `valid` pulses once per
decimated sample; `busy` drops after the first one.

## Gain and scaling

A CIC of order M and rate R has DC gain R^M.  The input maps to +1/-1, so a
constant input c gives `c*R^M`; `SHIFT = M*log2(R) - (N-1)` brings that to an
N-bit signed full scale of `2^(N-1)`.  For N=16, OSR=64, M=4: gain 2^24, shift 9,
full scale 2^15.  A negative `SHIFT` shifts left (out-of-range cases only).

The decimated sample is the last integrator's value **including** the current
cycle's input; with non-blocking updates that is `integ[last] + integ[last-1]`.

## What the test proves

`make -C rtl sim-sdm` generates a first-order modulator bitstream for DC levels
0.25/0.5/0.75, averages eight decimated samples per level and checks the mean
against `(2d-1)*2^15` and monotonicity.  Result:

```
SDM d=0.25 avg=-16384 expected~-16384
SDM d=0.50 avg=0 expected~0
SDM d=0.75 avg=16384 expected~16384
PASS sdm
```

## Limitations

- CIC only.  The architecture study's halfband 2:1 stage is **not** implemented
  in this pass; the CIC4 output at OSR=64 is already a usable 16-bit sample, and
  adding the halfband later sits between `dcnt` and `dout` without changing the
  ports.
- the modulator is the testbench's, not a transistor model; the analog loop
  (OTA gain, thermal noise, clock jitter) lives in the L2 model and the
  `sc_integrator_model`;
- no dither, no DC-blocking high-pass, no calibration.

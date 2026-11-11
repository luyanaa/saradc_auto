# Flash RTL

`flash_core` is the digital back end of a flash ADC: latch the comparator bank,
encode the thermometer word, register the code.  The comparator bank and the
reference ladder are analog macros.

## Interface

```
flash_core #(.N(6))
  clk, rst_n, start,
  cmp_therm[2^N-2:0], dout[N], valid, busy, status[1:0]
```

## Encoder choice

The encoder is the pop-count `therm2bin` from `rtl/common/`.  A flash bank never
produces a perfect thermometer word: a late or metastable comparator leaves a
1-0-1 bubble.  A priority encoder turns a bubble into an arbitrary jump; the
pop count is monotone in the number of decided comparators, so one bubble costs
one code.  That is the entire bubble-tolerance strategy: no scrubbing logic, no
extra comparator, and the cost is a bounded adder tree (2^N-1 adders).

Latency is two clocks (latch, encode).  `start` is accepted in `S_IDLE` only;
`busy` covers the conversion.

## What the test proves

`make -C rtl sim-flash` sweeps all 64 codes through the behavioral front end
(ideal comparator bank at `(i+1)*LSB` thresholds), then injects a real bubble by
clearing one comparator in a valid word and checks the decoded code moves by at
most one.  Result: `PASS flash (clean=32 bubbled=31)`.

## Limitations

- over-range is **not** detected: with `2^N-1` comparators the top code and an
  input above VREFH both give an all-ones thermometer word.  Detecting it needs
  one extra comparator at VREFH, i.e. a front-end change, not an encoder change.
  `status` is therefore always `ADC_ST_OK` in this pass;
- no comparator-offset calibration or bubble-count statistics;
- the encoder is sized for N <= 8 (adder tree grows as `2^N`).

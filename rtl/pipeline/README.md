# Pipeline RTL

`pipeline_core` is the digital half of a 1.5-bit/stage pipeline ADC: the
redundant-signed-digit combiner and the non-overlapping two-phase clock for the
switched-capacitor stages.  The MDAC (OTA + 2C/1C + transmission gates) is an
analog macro.

## Interface

```
pipeline_core #(.S(7), .N(8))
  clk, rst_n, start,
  stage_bits[2*S-1:0], stage_valid[S-1:0],
  dout[N], valid, busy, status[1:0], phi1, phi2
```

Each 1.5-bit stage reports two bits: `2'b10 -> +1`, `2'b01 -> 0`,
`2'b00 -> -1`.  The last stage reports a plain 2-bit flash code.  `phi1`/`phi2`
are clock enables from `clk_2ph`, not generated clocks.

## Combiner weights

Derived, not copied:

```
vin  = 2^-(S-1) r_{S-1} + (VREF/2) sum_i d_i 2^(-1-i)
code = sum_i d_i 2^(S-1-i) + code_flash + (2^S - 2)
```

So `BIAS = 2^S - 2`.  The usual `2^S - 1` is one LSB high; the bench would hide
that inside its tolerance, which is why the derivation is written down here.

## What the test proves

`make -C rtl sim-pipeline` computes the ideal stage decisions for all 256 input
codes with the standard recurrence and checks the combined output against the
ideal code.  Result: `PASS pipeline`.

## Limitations

- stage alignment is assumed: the bench drives all decisions as one aligned
  vector.  A real pipeline needs one skew register per stage so that stage i's
  decision arrives with stage i+1's residue.  That is a latency change only; the
  arithmetic above is unaffected.
- no digital gain calibration (the `GAIN_ERR` of the MDAC model);
- `phi1`/`phi2` run at `clk/4` with one dead cycle between phases.  If a 2x
  clock exists, `clk_2ph` produces full-rate non-overlap from it.

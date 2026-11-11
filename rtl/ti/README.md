# TI (time-interleaved) RTL

`ti_adc` = N copies of the existing, synthesizable `sar_logic_wreset` plus the
TI-specific digital logic.  No PDK primitive appears anywhere in the core.

## Interface

```
ti_adc #(.N(8), .NCH(2), .CAL_CYCLES(256), .GAIN_SHIFT(0))
  clk, rst_n, start, cal_en,
  cmp[NCH], sample[NCH], resultp[NCH*N], resultn[NCH*N],
  dout[N], valid, busy, phase[NCH], mux_sel
```

The analog side sees the same `sample` / `resultp` / `resultn` / `cmp` pins the
existing SAR core already defines, so each channel can be closed with the same
CDAC + comparator leaf generator on every PDK.

## Blocks

| file | function |
|---|---|
| `ti_phase.v` | round-robin channel sequencer; one-cycle `go` per channel, one-hot `phase`, `mux_sel` |
| `ti_combine.v` | foreground per-channel offset calibration (mean of `CAL_CYCLES` samples), gain trim by right shift, output combine |
| `ti_adc.v` | top: `ti_phase` + N `sar_logic_wreset` + `ti_combine` |
| `sar_frontend_model.v` | **simulation only**: reconstructs the SAR trial bit from `resultp|resultn`, ideal CDAC + comparator |
| `ti_tb.v` | self-checking: calibrates a +20 mV / −10 mV channel offset, then converts two codes and checks both channels |

## What the test proves

- the phase sequencer issues one `go` per channel and advances only on that
  channel's `VALID`;
- the combined output is the channel's SAR code;
- a +20 mV and a −10 mV channel offset are measured during `cal_en` and removed
  in normal mode (result within ±2 LSB).

## Limitations (first pass)

- sequential round-robin, not sample-interleaved; the overlap schedule is a
  follow-on and touches only `ti_phase.v`;
- offset calibration is foreground and assumes a zero input during `cal_en`;
- gain trim is a shift, not a per-channel multiplier;
- no skew/jitter model in RTL; that belongs to the clock tree and PEX.

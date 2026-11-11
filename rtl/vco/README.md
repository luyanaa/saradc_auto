# VCO-based ADC RTL

`vco_core` is the digital back end of a VCO-based ADC: a Gray-coded counter in
the VCO clock domain, a two-flop synchroniser into `clk`, and a window
difference that is the conversion result.  The ring VCO is an analog macro.

## Interface

```
vco_core #(.N(8), .DECIM(16), .CW(N+4))
  clk, rst_n, start, vco_clk,
  dout[N] (signed two's complement), valid, busy, status[1:0]
```

## Why Gray

The VCO counter crosses a clock domain.  A binary counter would need a
multi-bit handshake; a Gray counter changes one bit per VCO edge, so a two-flop
synchroniser is sufficient and no word can be sampled mid-transition.  Gray is
converted back to binary combinationally after the synchroniser.

The conversion is a frequency measurement: count VCO edges in `DECIM` clocks and
report the difference from the previous window.  The first window after `start`
reports zero because there is no previous count.

## What the test proves

`make -C rtl sim-vco` drives the behavioral ring VCO at 0.3/0.6/0.9 V and checks
the window difference against the tuning law and monotonicity.  Result:

```
VCO vin=0.30 avg=11 expected~11.2
VCO vin=0.60 avg=16 expected~16.0
VCO vin=0.90 avg=21 expected~20.8
PASS vco
```

## Limitations

- one synchroniser latency is absorbed by the window difference, but the
  sampling instant still jitters by +/-1 VCO edge; that is the intrinsic
  quantisation of the method;
- no calibration: the measured F0/KVCO table (`gen/outputs/char`) is the
  calibration input for a later offset/gain stage;
- the VCO model has no 1/f or supply-induced jitter beyond the `JITTER_S`
  parameter;
- `dout` is a difference, so its DC term is removed by construction; absolute
  frequency needs the calibration layer.

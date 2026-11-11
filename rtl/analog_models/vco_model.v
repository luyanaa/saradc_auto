// Behavioral ring-VCO model for the VCO-based ADC.
//
// Frequency is f0 + KVCO * (vin - VDD/2).  The oscillator loop recomputes the
// half period each edge, so it tracks a changing input without needing a
// sensitivity list on a `real`.  `enable` freezes the output for the reset and
// calibration windows.  Simulation only.
`timescale 1ns/1ps

module vco_model #(
  parameter real F0_HZ   = 1.0e9,
  parameter real KVCO_HZ = 1.0e9,
  parameter real VDD     = 1.2,
  parameter real MIN_HZ  = 1.0e6,
  parameter real JITTER_S = 0.0
)(
  input  real vin,
  input  wire enable,
  output reg  clkout
);
  real freq;
  real half_ns;
  real jitter;

  initial begin
    clkout = 1'b0;
    freq   = F0_HZ;
    half_ns = 1.0e9 / (2.0 * F0_HZ);
  end

  always begin
    if (enable) begin
      freq = F0_HZ + KVCO_HZ * (vin - VDD / 2.0);
      if (freq < MIN_HZ) freq = MIN_HZ;
      half_ns = 1.0e9 / (2.0 * freq);
      jitter = 0.0;
      if (JITTER_S > 0.0)
        jitter = (($random % 2001) / 2000.0) * JITTER_S * 1.0e9;
      #(half_ns + jitter) clkout = ~clkout;
    end else begin
      #1.0 clkout = 1'b0;
    end
  end
endmodule

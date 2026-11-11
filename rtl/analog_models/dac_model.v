// Behavioral DAC for ramp / dual-slope / calibration benches.
// Ideal by default; GAIN_ERR and OFFSET inject the static errors that the
// architecture-level error budget talks about.  Simulation only.
`timescale 1ns/1ps

module dac_model #(
  parameter integer N       = 8,
  parameter real VREFH      = 1.2,
  parameter real VREFL      = 0.0,
  parameter real GAIN_ERR   = 0.0,
  parameter real OFFSET     = 0.0
)(
  input  wire clk,
  input  wire [N-1:0] code,
  output real vout
);
  real lsb;

  always @* begin
    lsb  = (VREFH - VREFL) / (1 << N);
    vout = VREFL + OFFSET + (1.0 + GAIN_ERR) * (code * 1.0) * lsb;
  end
endmodule

// -----------------------------------------------------------------------------
// Behavioral analog models for RTL co-simulation.
//
// These files are simulation-only: they use `real`, delays and $random, and are
// excluded from synthesis and from every per-PDK hardening run.  The digital
// cores they connect to are plain Verilog-2005 and harden unchanged.
//
// Port contract is deliberately the same on every PDK; a PDK changes the
// parameter values (VDD, offsets, capacitances), never the ports.
// -----------------------------------------------------------------------------
`timescale 1ns/1ps

module cmp_model #(
  parameter real VDD        = 1.2,
  parameter real OFFSET     = 0.0,   // systematic input offset, volts
  parameter real NOISE_RMS  = 0.0,   // uniform [-1,1] * NOISE_RMS
  parameter real DELAY_NS   = 0.2,   // decision delay after the clock edge
  parameter real HYST       = 0.0    // input hysteresis, volts
)(
  input  real inp,
  input  real inn,
  input  wire clk,
  input  wire enable,
  output reg  out
);
  real diff;
  real noise;

  initial out = 1'b0;

  always @(posedge clk) begin
    if (enable) begin
      noise = 0.0;
      if (NOISE_RMS > 0.0)
        noise = (($random % 2001) / 2000.0) * NOISE_RMS;
      diff = (inp - inn) + OFFSET + noise;
      if (HYST > 0.0 && out)
        diff = diff - HYST;
      #(DELAY_NS);
      out <= (diff >= 0.0) ? 1'b1 : 1'b0;
    end
  end
endmodule

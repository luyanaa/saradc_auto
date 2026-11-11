// Sample-and-hold.  Ideal acquisition on `sample`; optional droop per clock.
`timescale 1ns/1ps

module sah_model #(
  parameter real VDD       = 1.2,
  parameter real DROOP_V_S = 0.0,   // held-node droop, volts per second
  parameter real CLK_NS    = 10.0
)(
  input  real vin,
  input  wire sample,
  input  wire clk,
  output real vout
);
  real held;

  initial held = 0.0;

  always @(posedge sample) begin
    held = vin;
    vout = held;
  end

  always @(posedge clk) begin
    if (!sample && DROOP_V_S > 0.0) begin
      held = held * (1.0 - DROOP_V_S * CLK_NS * 1e-9 / VDD);
      vout = held;
    end
  end
endmodule

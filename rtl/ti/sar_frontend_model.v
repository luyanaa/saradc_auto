// Behavioral single-ended SAR front end for the TI testbench.
//
// The digital SAR core does not expose its trial bit; it does expose RESULTP
// and RESULTN, and the trial bit is by construction the highest bit not yet in
// either word.  The model reconstructs it, drives an ideal CDAC and compares
// the held input against the CDAC voltage.  Simulation only.
`timescale 1ns/1ps

module sar_frontend_model #(
  parameter integer N = 8,
  parameter real VREF = 1.0,
  parameter real OFFSET = 0.0
)(
  input  real vin,
  input  wire sample,
  input  wire [N-1:0] resultp,
  input  wire [N-1:0] resultn,
  output wire cmp
);
  real held;
  reg [N-1:0] decided;
  reg [N-1:0] mask;
  reg [N-1:0] trial;
  real vdac;
  integer k;

  initial held = 0.0;

  always @(posedge sample) held = vin;

  always @* begin
    decided = resultp | resultn;
    mask    = {N{1'b0}};
    trial   = {N{1'b0}};
    for (k = N-1; k >= 0; k = k - 1)
      if (!decided[k] && mask == {N{1'b0}})
        mask = 1 << k;
    trial = resultp | mask;
    vdac  = trial * (VREF / (1 << N));
  end

  assign cmp = ((held + OFFSET) >= vdac);
endmodule

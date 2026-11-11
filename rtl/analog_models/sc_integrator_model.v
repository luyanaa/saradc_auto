// Behavioral switched-capacitor integrator for the sigma-delta loop.
//
// phi1 samples the input onto Cs, phi2 transfers the charge onto Ci.  The
// model is single-ended and inverting; the testbench closes the 1-bit loop
// around it with a comparator and a feedback reference.  Simulation only.
`timescale 1ns/1ps

module sc_integrator_model #(
  parameter real CS_PF  = 1.0,
  parameter real CI_PF  = 1.0,
  parameter real VREF   = 1.0,
  parameter real VDD    = 1.2,
  parameter real VCM    = 0.6,
  parameter real A0     = 10.0e3
)(
  input  real vin,
  input  wire clk,
  input  wire rst_n,
  input  wire phi1,
  input  wire phi2,
  output real vout
);
  real q;
  real vint;

  initial begin
    q    = 0.0;
    vint = 0.0;
    vout = 0.0;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      q    = 0.0;
      vint = 0.0;
    end else if (phi1) begin
      q = CS_PF * (vin - VCM) * 1e-15;
    end else if (phi2) begin
      vint = vint - q / (CI_PF * 1e-15);
      q    = 0.0;
    end

    vout = vint * (A0 / (A0 + 1.0));
    if (vout > VDD) vout = VDD;
    if (vout < 0.0)  vout = 0.0;
  end
endmodule

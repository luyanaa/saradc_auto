// Behavioral RC integrator for dual-slope.
//
// `az` resets the integrating capacitor, `integrate` ramps by -vin (or +vin
// depending on the chosen polarity) and `deintegrate` ramps by the reference.
// The comparator in the testbench watches the 0-crossing.  Finite A0, output
// clamp and a first-order settling approximation are included; DA is a
// separate parameter in the architecture model and is not faked here.
`timescale 1ns/1ps

module integrator_model #(
  parameter real R_OHM  = 100.0e3,
  parameter real C_F    = 1.0e-9,
  parameter real VREF   = 1.0,
  parameter real VDD    = 1.2,
  parameter real A0     = 100.0e3,
  parameter real CLK_NS = 100.0,
  parameter real POLARITY = 1.0     // +1: integrate up, -1: integrate down
)(
  input  real vin,
  input  wire clk,
  input  wire az,
  input  wire integrate,
  input  wire deintegrate,
  output real vout
);
  real dt;
  real rc;
  real vint;

  initial begin
    vint = 0.0;
    vout = 0.0;
  end

  always @(posedge clk) begin
    dt = CLK_NS * 1e-9;
    rc = R_OHM * C_F;
    if (az)
      vint = 0.0;
    else if (integrate)
      vint = vint + POLARITY * (vin / rc) * dt;
    else if (deintegrate)
      vint = vint - POLARITY * (VREF / rc) * dt;

    vout = vint * (A0 / (A0 + 1.0));
    if (vout > VDD) vout = VDD;
    if (vout < 0.0)  vout = 0.0;
  end
endmodule

// Behavioral flash front end: resistor-ladder thresholds plus one comparator
// per tap.  Outputs a thermometer word the digital encoder consumes.
//
// OFFSET_RMS is the per-comparator random offset (uniform approximation);
// deterministic offsets can be injected by editing the offset array in a
// derived testbench.  Simulation only.
`timescale 1ns/1ps

module flash_frontend_model #(
  parameter integer N         = 6,
  parameter real VREFH        = 1.2,
  parameter real VREFL        = 0.0,
  parameter real OFFSET_RMS   = 0.0,
  parameter real VDD          = 1.2
)(
  input  real vin,
  input  wire clk,
  input  wire enable,
  output reg  [(1<<N)-2:0] therm
);
  integer i;
  real vth;
  real off;

  always @(posedge clk) begin
    if (enable) begin
      for (i = 0; i < (1<<N)-1; i = i + 1) begin
        // Comparators sit on code boundaries: tap i trips for vin >= (i+1)*LSB.
        // The pop-count of the thermometer word is then exactly the code.
        vth = VREFL + (VREFH - VREFL) * (i + 1) / (1 << N);
        off = 0.0;
        if (OFFSET_RMS > 0.0)
          off = (($random % 2001) / 2000.0) * OFFSET_RMS;
        therm[i] <= ((vin - vth) + off >= 0.0) ? 1'b1 : 1'b0;
      end
    end
  end
endmodule

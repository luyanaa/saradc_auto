// Behavioral 1.5-bit multiplying DAC stage for the pipeline ADC.
//
// phi1 samples vin and makes the two comparator decisions; phi2 produces the
// residue 2*(vin - d*VREF/2).  `bits` encodes the decision as
//   2'b10 -> d = +1,  2'b01 -> d = 0,  2'b00 -> d = -1
// which is exactly what the digital redundant-signed-digit combiner expects.
// Simulation only.
`timescale 1ns/1ps

module mdac_1p5_model #(
  parameter real VREF     = 1.0,
  parameter real VDD      = 1.2,
  parameter real GAIN_ERR = 0.0,
  parameter real OFFSET   = 0.0   // input offset of both comparators, volts
)(
  input  real vin,
  input  wire clk,
  input  wire rst_n,
  input  wire phi1,
  input  wire phi2,
  output reg  [1:0] bits,
  output real residue
);
  real held;
  real dac;

  initial begin
    held    = 0.0;
    dac     = 0.0;
    bits    = 2'b01;
    residue = 0.0;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      held    = 0.0;
      dac     = 0.0;
      bits    <= 2'b01;
      residue = 0.0;
    end else if (phi1) begin
      held = vin + OFFSET;
      if (held >= VREF / 4.0) begin
        bits <= 2'b10;
        dac   =  VREF / 2.0;
      end else if (held >= -VREF / 4.0) begin
        bits <= 2'b01;
        dac   =  0.0;
      end else begin
        bits <= 2'b00;
        dac   = -VREF / 2.0;
      end
    end else if (phi2) begin
      residue = 2.0 * (held - dac) * (1.0 + GAIN_ERR);
      if (residue >  VREF) residue =  VREF;
      if (residue < -VREF) residue = -VREF;
    end
  end
endmodule

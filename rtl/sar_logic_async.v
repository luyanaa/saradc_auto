// Asynchronous (self-timed) SAR sequencer.
//
// The taped-out controller (sar_logic_wreset.v) advances one bit per CLK.  An
// asynchronous SAR advances as soon as the comparator has decided, which is
// what lets the conversion run at the comparator's own speed instead of the
// slowest-case clock period.
//
// Protocol (no clock):
//   GO rises, state IDLE -> CONV and SAMPLE pulses.
//   For every bit: CMPSTART high requests an evaluation; the comparator
//   raises RDY when its outputs have regenerated.  On the RDY level the bit is
//   latched and CMPSTART is dropped, which precharges the comparator; RDY
//   falls and the next bit is requested.  After the LSB, VALID rises and the
//   result is held until the next GO.
//
// RDY for a StrongArm is NAND(OUTP,OUTN): both outputs sit high while the
// comparator is precharged and one of them falls after regeneration.

module sar_logic_async #(
  parameter NBITS = 5
) (
  input RST,
  input GO,
  input CMP,        // comparator decision
  input RDY,        // comparator settled (NAND of the two outputs)
  output SAMPLE,
  output VALID,
  output CMPSTART,  // 1 = evaluate, 0 = precharge
  output reg [NBITS-1:0] RESULTP,
  output reg [NBITS-1:0] RESULTN
);

  localparam sIdle = 2'd0, sConv = 2'd1, sDone = 2'd2;

  reg [1:0] state;
  reg [NBITS-1:0] mask;
  reg pre;          // 1 = comparator currently being precharged

  assign CMPSTART = ~pre;
  assign SAMPLE = (state == sIdle) && GO;
  assign VALID = (state == sDone);

  // Level-sensitive, self-timed state machine.  RDY is the only event that
  // advances a bit; GO is only looked at from sIdle/sDone.
  always @(RDY or GO or posedge RST) begin
    if (RST) begin
      state <= sIdle;
      mask <= {1'b1, {(NBITS-1){1'b0}}};
      RESULTP <= 'd0;
      RESULTN <= 'd0;
      // precharged, so CMPSTART is 0 until GO requests the first evaluation
      pre <= 1'b1;
    end else if (state == sDone) begin
      // GO low retires the result; the comparator stays precharged so the
      // next GO produces a clean CMPSTART edge.
      if (!GO) begin
        state <= sIdle;
        pre <= 1'b1;
      end
    end else if (state == sIdle) begin
      if (GO) begin
        state <= sConv;
        mask <= {1'b1, {(NBITS-1){1'b0}}};
        RESULTP <= 'd0;
        RESULTN <= 'd0;
        pre <= 1'b0;                     // request the MSB evaluation
      end
    end else if (RDY) begin              // sConv, comparator settled
      if (CMP)
        RESULTP <= RESULTP | mask;
      else
        RESULTN <= RESULTN | mask;
      mask <= mask >> 1;
      pre <= 1'b1;                       // precharge for the next bit
      if (mask[0])
        state <= sDone;
    end else begin
      pre <= 1'b0;                       // sConv, comparator re-armed
    end
  end

endmodule

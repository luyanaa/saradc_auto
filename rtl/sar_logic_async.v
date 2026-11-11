// Asynchronous (self-timed) SAR sequencer.
//
// The synchronous controller (sar_logic_wreset.v) advances one bit per CLK
// edge.  An asynchronous SAR advances as soon as the comparator has decided,
// so the conversion runs at the comparator's own regeneration speed instead
// of the slowest-case clock period.
//
// Handshake (no clock):
//   GO rises, sIdle -> sConv.  In sConv, CMPSTART is low (precharged).  The
//   controller drops `pre`, CMPSTART rises and the comparator evaluates; when
//   its outputs regenerate it raises RDY.  On that RDY level the bit is
//   latched, `pre` goes back high and CMPSTART falls to precharge.  RDY
//   falls and the next bit is requested.  After the LSB, VALID rises and the
//   result is held until the next GO.
//
// CMPB does not need to be generated here: the analog's saradc_cmpcs2cmp
// already derives the latch's complementary phase from the delayed CMPSTART.
//
// RDY for a two-output dynamic comparator is NAND(OUTP, OUTN): both outputs
// sit high while precharged and one falls after regeneration.

module sar_logic_async #(
  parameter NBITS = 5
) (
  input RST,
  input GO,
  input CMP,        // comparator decision
  input RDY,        // comparator settled (NAND of OUTP and OUTN)
  output SAMPLE,
  output VALID,
  output CMPSTART,  // 1 = evaluate, 0 = precharge (feeds CLK of conv_async)
  output reg [NBITS-1:0] RESULTP,
  output reg [NBITS-1:0] RESULTN
);

  localparam sIdle = 2'd0, sConv = 2'd1, sDone = 2'd2;

  reg [1:0] state;
  reg [NBITS-1:0] mask;
  reg pre;          // 1 = comparator is being precharged (CMPSTART low)

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
      // start precharged, so CMPSTART is 0 until GO requests the first
      // evaluation (otherwise the comparator model never sees a request edge
      // and the handshake never begins)
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

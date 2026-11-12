// Synthesizable asynchronous (self-timed) SAR sequencer.
//
// Same handshake as the behavioural model rtl/sar_logic_async_behav.v, but
// written as a clocked state register on the comparator's RDY line plus two
// combinational flags.  It maps to DFFs + gates in yosys; the digital
// techmap places it.
//
// Timing (self-timed, no system clock):
//   GO arms the conversion.  `req` (CMPSTART) is high while armed and the
//   comparator is idle, so the first request goes out at once.  On each RDY
//   posedge the decided bit is captured and `mask` shifts toward the LSB;
//   on the LSB's RDY `done` sets -> VALID=1.  `req` is low while RDY is up
//   (precharge) and after `done`.
//
// `armed` is just GO & ~RST (a combinational flag, not a latch).  The
// conversion registers clear on `clr = ~armed`, so they sit in reset while
// GO is low, run through the bits while GO is high, and hold their result
// until GO drops.  `done` only gates VALID and `req`; it is NOT in the reset
// path -- putting it there glitched `clr` on the LSB's RDY edge and wiped
// the registers before they committed.
//
// The GO contract is a level: the host holds GO high from the request until
// VALID (it may drop it any time after that), then drops it to retire the
// conversion before the next one.  A GO->low->high cycle re-arms cleanly;
// holding GO high across done simply repeats conversions, which is the
// continuous-convert mode the sync controller also implements.
//
// Storage: armed/req are combinational; mask/RESULT*/done are DFFs clocked
// on RDY with async clear on `clr`.  The behavioural model in
// rtl/sar_logic_async_behav.v is kept for mixed-signal testbenches and the
// equivalence TB; this file is what SYN_DIG_SRC uses for SEQ_KIND=ASYNC.

module sar_logic_async #(
  parameter NBITS = 5
) (
  input RST,
  input GO,
  input CMP,        // comparator decision
  input RDY,        // comparator settled (NAND of OUTP and OUTN)
  output SAMPLE,
  output VALID,
  output CMPSTART,  // 1 = evaluate, 0 = precharge
  output reg [NBITS-1:0] RESULTP,
  output reg [NBITS-1:0] RESULTN
);

  // -- armed: a conversion is active from the GO edge until GO retires ----
  // Set on GO high, cleared on RST or GO low.  Clearing on GO low (not on
  // done) lets a fresh GO->low->high cycle re-arm for the next conversion
  // while keeping `clr` low for the whole conversion once it starts.
  reg armed;
  always @* begin
    if (RST)      armed = 1'b0;
    else if (!GO) armed = 1'b0;   // GO low retires the conversion
    else          armed = 1'b1;   // GO high arms it
    // (GO is a level here; no hold state -> armed just tracks GO&~RST)
  end

  // -- req: CMPSTART ------------------------------------------------------
  // High while armed and the comparator is idle (RDY low); low while RDY is
  // up so the comparator precharges.  Once done it stays low.
  reg req, done;
  always @* begin
    if (RST)                 req = 1'b0;
    else if (armed && !done) req = ~RDY;
    else                     req = 1'b0;
  end
  assign CMPSTART = req;

  // -- conversion registers: mask, RESULTP/N, done -------------------------
  // Async clear on `clr` (low while armed).  Every RDY captures one bit.
  reg [NBITS-1:0] mask;
  wire clr = ~armed;
  always @(posedge RDY or posedge clr) begin
    if (clr) begin
      mask    <= {1'b1, {(NBITS-1){1'b0}}};
      RESULTP <= 'd0;
      RESULTN <= 'd0;
      done    <= 1'b0;
    end else begin
      mask <= {1'b0, mask[NBITS-1:1]};   // MSB first, shift toward the LSB
      if (CMP) RESULTP <= RESULTP | mask;
      else     RESULTN <= RESULTN | mask;
      if (mask[0]) done <= 1'b1;
    end
  end
  assign VALID = done;

  // SAMPLE while GO is up and the conversion has not finished.
  assign SAMPLE = GO & ~done;

endmodule

// Asynchronous control-path converter: saradc_logic_conv for SEQ_KIND=ASYNC.
//
// Same port contract as saradc_logic_conv (so saradc_logic_buf instantiates
// it unchanged) but the inputs are the self-timed phases from
// sar_logic_async, not the synchronous sequencer:
//   CLK  <- CMPSTART   (comparator request: 1 = evaluate, 0 = precharge)
//   RESULTP/N, SAMPLE, VALID are the async FSM's registers/outputs.
//
// Mapping:
//   HOLD  = ~SAMPLE   (same as sync)
//   PRE   = SAMPLE    (precharge/track only during the sample window)
//   CMP   = CMPSTART delayed by three DEL4X4 -- the LATCH core's compare edge
//   CLKBUF = CMPSTART -- the STRONGARM core's request clock
//   CRH[j]/CRHB[j] = RESULTN[j+1] (bit decided low -> VREFH), via re2cr
//   CRL[j]/CRLB[j] = RESULTP[j+1] (bit decided high -> VREFL), via re2cr
//
// One structural caveat: CR* change combinationally on the RESULT write, which
// happens at the same RDY level that drops CMPSTART, so the DAC's charge
// injection and the next request are separated only by the comparator's own
// precharge time.  A real asynchronous design adds a settle phase there.

module saradc_logic_conv_async #(
  parameter NBITS = 8
) (
  inout VDD, VSS,
  input CLK,                    // CMPSTART from sar_logic_async
  input [NBITS-1:1] RESULTP,
  input [NBITS-1:1] RESULTN,
  input SAMPLE,
  input VALID,

  output CLKBUF,
  output PRE, HOLD, CMP,
  output [NBITS-2:0] CRH, CRHB, CRL, CRLB
);

  genvar i;
  generate
    for (i = 1; i < NBITS; i = i + 1) begin : re2cr
      saradc_re2cr ren2crh(
        .VDD(VDD), .VSS(VSS),
        .I(RESULTN[i]), .Z(CRH[i-1]), .ZN(CRHB[i-1])
      );
      saradc_re2cr rep2crl(
        .VDD(VDD), .VSS(VSS),
        .I(RESULTP[i]), .Z(CRL[i-1]), .ZN(CRLB[i-1])
      );
    end
  endgenerate

  // HOLD = ~SAMPLE; PRE = SAMPLE (no clock qualification -- the async
  // controller does not sample on a clock edge).
  SARADC_CELL_INVX8 invsmp(.VDD(VDD), .VSS(VSS), .I(SAMPLE), .ZN(HOLD));
  SARADC_CELL_BUFFX16 prebuf(.VDD(VDD), .VSS(VSS), .I(SAMPLE), .Z(PRE));

  // CMP is CMPSTART delayed, so the LATCH core's OAI211 pair sees its compare
  // edge a beat after the request rises.
  wire CS1, CS2;
  SARADC_CELL_DEL4X4 csd1(.VDD(VDD), .VSS(VSS), .I(CLK),  .Z(CS1));
  SARADC_CELL_DEL4X4 csd2(.VDD(VDD), .VSS(VSS), .I(CS1),  .Z(CS2));
  SARADC_CELL_DEL4X4 csd3(.VDD(VDD), .VSS(VSS), .I(CS2),  .Z(CMP));
  SARADC_CELL_BUFFX16 clkbuf(.VDD(VDD), .VSS(VSS), .I(CLK), .Z(CLKBUF));

endmodule

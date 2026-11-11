// Definition of the SARADC
// Combining the analog and the digital

// The digital SAR controller lives in sar_logic_wreset.v and is a real
// parameterized module (NBITS is passed explicitly, not through a global
// `define).  There is intentionally no blackbox stub here: a stub would
// shadow the implementation and silently drop the parameter.
//
// SEQ_KIND selects which controller drives the analog:
//   "SYNC"  (default) -- sar_logic_wreset, one bit per CLK edge.
//   "ASYNC"           -- sar_logic_async, self-timed on the comparator's RDY.
// Both are instantiated; the unused one's outputs are ignored.  Only the
// async controller produces CMPSTART/CMPB; the sync path ties them off.

(* keep_hierarchy = "yes" *)
module SARADC #(
`ifdef SARADC_CAP_MOM
  parameter CAP_KIND = "MOM",
`else
  parameter CAP_KIND = "MOSCAP",
`endif
`ifdef SARADC_SW_BOOT
  parameter SW_KIND = "BOOT",
`else
  parameter SW_KIND = "TG",
`endif
`ifdef SARADC_CMP_STRONGARM
  parameter CMP_KIND = "STRONGARM",
`else
  parameter CMP_KIND = "LATCH",
`endif
`ifdef SARADC_SEQ_ASYNC
  parameter SEQ_KIND = "ASYNC",
`else
  parameter SEQ_KIND = "SYNC",
`endif
  parameter integer NBITS = 5,
  parameter integer NPW = 3, // Number of caps in X for each CDAC unit
  parameter integer NPH = 2 // Number of caps in Y for each CDAC unit
) (
  inout AVDD, VDD, VSS, // NOTE: VDD is the digital VDD
  // Analog signals
  inout VREFH, VREFL, VIN, VIP,
  // Digital signals
  input CLK, RST,
  input GO, 
  output VALID, SAMPLE,
  output [NBITS-1:0] RESULT
);

  // TODO: Is buffering the CLK through the analog the RIGHT WAY?
  wire CMPO, CMPOB, CLKBUF;

  // Each sequencer produces its own RESULTP/N and SAMPLE/VALID; the mux
  // picks the one SEQ_KIND selects.  Both controllers see GO, so in SYNC
  // mode the async FSM also converts -- its outputs are just ignored (it
  // toggles a few dead wires; a real chip would gate it).
  wire [NBITS-1:0] SYNC_RP, SYNC_RN, ASYNC_RP, ASYNC_RN;
  wire SYNC_VALID, SYNC_SAMPLE, ASYNC_VALID, ASYNC_SAMPLE;
  wire CMPSTART;

  wire [NBITS-1:0] AN_RP   = (SEQ_KIND == "ASYNC") ? ASYNC_RP   : SYNC_RP;
  wire [NBITS-1:0] AN_RN   = (SEQ_KIND == "ASYNC") ? ASYNC_RN   : SYNC_RN;
  wire             AN_VAL  = (SEQ_KIND == "ASYNC") ? ASYNC_VALID  : SYNC_VALID;
  wire             AN_SMP  = (SEQ_KIND == "ASYNC") ? ASYNC_SAMPLE : SYNC_SAMPLE;

  SARADC_ANALOG #(
    .CAP_KIND(CAP_KIND),
    .SW_KIND(SW_KIND),
    .CMP_KIND(CMP_KIND),
    .SEQ_KIND(SEQ_KIND),
    .NBITS(NBITS),
    .NPW(NPW),
    .NPH(NPH)
  ) analog (
    .VDD(AVDD), .VSS(VSS), 
    .VREFH(VREFH), .VREFL(VREFL), .VIN(VIN), .VIP(VIP),
    .CLK(CLK), .CMPSTART(CMPSTART),
    .VALID(AN_VAL), .SAMPLE(AN_SMP),
    .RESULTP(AN_RP), .RESULTN(AN_RN),
    .CMPO(CMPO), .CMPOB(CMPOB), .CLKBUF(CLKBUF)
  );

`ifndef DIGTOP
`define DIGTOP sar_logic_wreset
`endif
  `DIGTOP #(
    .NBITS(NBITS)
  ) digital (
    .CLK(CLK), .RST(RST),
    .GO(GO),
    .VALID(SYNC_VALID), .SAMPLE(SYNC_SAMPLE),
    .RESULTP(SYNC_RP), .RESULTN(SYNC_RN),
    .CMP(CMPO)
  );

  sar_logic_async #(
    .NBITS(NBITS)
  ) digital_async (
    .RST(RST), .GO(GO),
    .CMP(CMPO), .RDY(~(CMPO & CMPOB)), // NAND(OUTP,OUTN): 1 once one fell
    .SAMPLE(ASYNC_SAMPLE), .VALID(ASYNC_VALID),
    .CMPSTART(CMPSTART),
    .RESULTP(ASYNC_RP), .RESULTN(ASYNC_RN)
  );

  assign VALID  = AN_VAL;
  assign SAMPLE = AN_SMP;
  assign RESULT = AN_RP;

endmodule

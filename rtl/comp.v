// Latched comparator definition
//
// CMP_KIND selects the comparator core:
//   "LATCH"     (default) -- the taped-out OAI211X16 cross-coupled latch.
//                           Precharge sets the pre-nodes from VP/VN, then the
//                           cross-coupled pair regenerates on CMP.
//   "STRONGARM"           -- the StrongArm dynamic comparator leaf
//                           (gen/analog/macro_gen.py topo_cmp_strongarm).
// Both drive the same pre-nodes, so the output buffers and the digital
// interface are identical.  Polarity is preserved: OUTP=1 when VP>VN.

module SARADC_COMP #(
  parameter CMP_KIND = "LATCH"
) (
  inout VDD, VSS,
  input CMP, CMPB, CK, // Negated and non-negated of compare start
  inout VP, VN, // Differential inputs
  output OUTN, OUTP // Differential output
);

  wire OUTNp, OUTPp; // Pre-signals
  wire OUTNpb, OUTPpb; // Pre-signals buffered 1 time

  generate
    if (CMP_KIND == "STRONGARM") begin : strongarm
      // StrongArm: one clock, rail-to-rail outputs.  CK is the comparator
      // clock; CMP/CMPB are the latched control phases and are unused here.
      SARADC_CMP_STRONGARM cmp_sa (
        .VDD(VDD), .VSS(VSS),
        .CLK(CK), .INP(VP), .INN(VN),
        .OUTP(OUTPp), .OUTN(OUTNp));
    end else begin : latch
      // Compare stage: a single OAI211X16 cross-coupled latch drives each
      // pre-node.  The pair of "reinforcing" NAND2X2 cells that used to sit
      // on OUTPp/OUTNp shared the driver and fought the OAI outputs at every
      // decision edge (~745 uA shoot-through, 140 mV droop, metastability and
      // supply noise).  They have been removed -- the OAI211X16 (4x OAI211D4)
      // is already the strongest driver in the cell library.
      SARADC_CELL_OAI211X16 vp_cmp (
        .VDD(VDD), .VSS(VSS),
        .A0(CMPB), .A1(VP), .B0(CMP), .C0(OUTPp), .ZN(OUTNp));
      SARADC_CELL_OAI211X16 vn_cmp (
        .VDD(VDD), .VSS(VSS),
        .A0(CMPB), .A1(VN), .B0(CMP), .C0(OUTNp), .ZN(OUTPp));
    end
  endgenerate
  
  // Buffering stage
  SARADC_CELL_BUFFX0 buf_n0 (
    .VDD(VDD), .VSS(VSS), 
    .I(OUTNp), .Z(OUTNpb));
  SARADC_CELL_BUFFX2 buf_n1 (
    .VDD(VDD), .VSS(VSS), 
    .I(OUTNpb), .Z(OUTN));
  SARADC_CELL_BUFFX0 buf_p0 (
    .VDD(VDD), .VSS(VSS), 
    .I(OUTPp), .Z(OUTPpb));
  SARADC_CELL_BUFFX2 buf_p1 (
    .VDD(VDD), .VSS(VSS), 
    .I(OUTPpb), .Z(OUTP));
  
endmodule


// Latched comparator definition
// Uses two OAI211X16 for the latch
// We do not have X16, so we use 4x the X4
// Also, we use NAND2 for reinforcing the output?
// Also also, what the hell is clkbuf for?

module SARADC_COMP (
  inout VDD, VSS,
  input CMP, CMPB, CK, // Negated and non-negated of compare start
  inout VP, VN, // Differential inputs
  output OUTN, OUTP // Differential output
);
  
  wire OUTNp, OUTPp; // Pre-signals
  wire OUTNpb, OUTPpb; // Pre-signals buffered 1 time
  
  // Compare stage: a single OAI211X16 cross-coupled latch drives each
  // pre-node.  The pair of "reinforcing" NAND2X2 cells that used to sit on
  // OUTPp/OUTNp shared the driver and fought the OAI outputs at every
  // decision edge (~745 uA shoot-through, 140 mV droop, metastability and
  // supply noise).  They have been removed -- the OAI211X16 (4x OAI211D4) is
  // already the strongest driver in the cell library, so nothing else needs
  // to touch these nodes.
  SARADC_CELL_OAI211X16 vp_cmp (
    .VDD(VDD), .VSS(VSS),
    .A0(CMPB), .A1(VP), .B0(CMP), .C0(OUTPp), .ZN(OUTNp));
  SARADC_CELL_OAI211X16 vn_cmp (
    .VDD(VDD), .VSS(VSS),
    .A0(CMPB), .A1(VN), .B0(CMP), .C0(OUTNp), .ZN(OUTPp));
  
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


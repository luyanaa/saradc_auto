// CDAC unit definition
// Three switches, and a capacitor
//
// SW_KIND selects the pass device:
//   "TG"   (default) -- the inverter-derived transmission gate (SARADC_SW).
//                       Ron peaks around mid-rail because V_GS collapses.
//   "BOOT"           -- the bootstrapped switch leaf (SARADC_SW_BOOT): the
//                       gate is boosted to VDD+Vin so the overdrive (and Ron)
//                       stays roughly constant over the input range.
// Both kinds keep the same six ports, so the CDAC wiring is identical.
//
// The switch leaf is selected with `ifdef SARADC_SW_BOOT (set by the flow
// from SW_KIND), not with a generate-if, so the instance paths stay exactly
// "vi2cap[i].sw_vi2cap" / "cap2vouth[i].sw_cap2vouth" / "cap2voutl[i].sw_cap2voutl"
// as the taped-out floorplan scripts expect.
//
// The cap leaf is selected the same way inside SARADC_CAP.

module SARADC_CDAC_UNIT # (
  parameter SW_KIND = "TG",
  parameter integer NSW_VI = 1,
  parameter integer NSW_VOUTH = 1,
  parameter integer NSW_VOUTL = 1,
  parameter integer NSW_CAP = 1
) (
  inout VDD, VSS,
  input CRI, CRIB, CRH, CRHB, CRL, CRLB, // Negated and non-negated
  inout VI, VOUTH, VOUTL, // Inouts of the switches
  inout VSH, FL // Debug signals (Capacitor terminals)
);

  genvar i;

  generate
    for(i = 0; i < NSW_VI; i = i + 1) begin : vi2cap
`ifdef SARADC_SW_BOOT
      SARADC_SW_BOOT sw_vi2cap (
        .VDD(VDD), .VSS(VSS),
        .SB(CRIB), .S(CRI),
        .Z1(VI), .Z2(VSH)
      );
`else
      SARADC_SW sw_vi2cap (
        .VDD(VDD), .VSS(VSS),
        .SB(CRIB), .S(CRI),
        .Z1(VI), .Z2(VSH)
      );
`endif
    end
  endgenerate


  generate
    for(i = 0; i < NSW_VOUTH; i = i + 1) begin : cap2vouth
`ifdef SARADC_SW_BOOT
      SARADC_SW_BOOT sw_cap2vouth (
        .VDD(VDD), .VSS(VSS),
        .SB(CRHB), .S(CRH),
        .Z1(VSH), .Z2(VOUTH)
      );
`else
      SARADC_SW sw_cap2vouth (
        .VDD(VDD), .VSS(VSS),
        .SB(CRHB), .S(CRH),
        .Z1(VSH), .Z2(VOUTH) // NOTE: Maybe the order of the pins matter.
      );
`endif
    end
  endgenerate


  generate
    for(i = 0; i < NSW_VOUTL; i = i + 1) begin : cap2voutl
`ifdef SARADC_SW_BOOT
      SARADC_SW_BOOT sw_cap2voutl (
        .VDD(VDD), .VSS(VSS),
        .SB(CRLB), .S(CRL),
        .Z1(VSH), .Z2(VOUTL)
      );
`else
      SARADC_SW sw_cap2voutl (
        .VDD(VDD), .VSS(VSS),
        .SB(CRLB), .S(CRL),
        .Z1(VSH), .Z2(VOUTL)
      );
`endif
    end
  endgenerate


  generate
    for(i = 0; i < NSW_CAP; i = i + 1) begin : cap
      SARADC_CAP cap (
        .VNW(VDD), .VPW(VSS),
        .VSH(VSH), .FL(FL)
      );
    end
  endgenerate

endmodule

// Switch definition
// Uses the smallest inverter to create two pass-gates
// The passgates created between VDD-ZN of one INV, and the ZN-VSS of other INV

(* keep_hierarchy = "yes" *)
module SARADC_SW (
  inout VDD, VSS,
  input SB, S, // Negated and non-negated
  inout Z1, Z2 // Inouts of the switch
);

  // Transmission gate built from the smallest inverter cell.  Each
  // INVX0_ASSW is an inverter; tying its NMOS half (zn->vss pass device) and
  // PMOS half (zn->vdd pass device) across Z1/Z2 with complementary gates
  // (S on the NMOS side, SB on the PMOS side) gives a real transmission
  // gate.  The old comment called this a "crime": the crime was not the
  // gate, it was shipping it with no charge-injection compensation.
  SARADC_CELL_INVX0_ASSW pgp_lz1 (
//`ifdef WITH_BODY
    .vnw(VDD), .vpw(VSS),
//`endif
    .vdd(Z2), .zn(Z1), .vss(Z1), .i(SB)   // PMOS half: gate SB
  );
  SARADC_CELL_INVX0_ASSW pgn_lz1 (
//`ifdef WITH_BODY
    .vnw(VDD), .vpw(VSS),
//`endif
    .vdd(Z1), .zn(Z1), .vss(Z2), .i(S)    // NMOS half: gate S
  );

  SARADC_CELL_INVX0_ASSW pgp_lz2 (
//`ifdef WITH_BODY
    .vnw(VDD), .vpw(VSS),
//`endif
    .vdd(Z1), .zn(Z2), .vss(Z2), .i(SB)   // PMOS half: gate SB
  );
  SARADC_CELL_INVX0_ASSW pgn_lz2 (
//`ifdef WITH_BODY
    .vnw(VDD), .vpw(VSS),
//`endif
    .vdd(Z2), .zn(Z2), .vss(Z1), .i(S)    // NMOS half: gate S
  );

  // Charge-injection dummy: a half-width shorted switch driven by the
  // complementary clock.  When S/SB open the main gate its channel charge
  // spills onto Z1/Z2; the dummy (gate on the opposite phase, drain+source
  // shorted so it never conducts) absorbs a matching packet and cancels the
  // pedestal/feedthrough the uncompensated gate injected into the CDAC.
  SARADC_CELL_INVX0_ASSW dum_z1 (
//`ifdef WITH_BODY
    .vnw(VDD), .vpw(VSS),
//`endif
    .vdd(Z1), .zn(Z1), .vss(Z1), .i(SB)   // shorted dummy, gate on SB
  );
  SARADC_CELL_INVX0_ASSW dum_z2 (
//`ifdef WITH_BODY
    .vnw(VDD), .vpw(VSS),
//`endif
    .vdd(Z2), .zn(Z2), .vss(Z2), .i(S)    // shorted dummy, gate on S
  );

endmodule

// Switch for testing purposes
(* keep_hierarchy = "yes" *)
module SARADC_SWDUM (
  inout VDD, VSS,
  input SB, S, // Negated and non-negated
  inout Z1, Z2 // Inouts of the switch
);
  
  wire Z3;
  
  SARADC_CELL_INVX0_ASSW sw1 (
//`ifdef WITH_BODY
    .vnw(VDD), .vpw(VSS),
//`endif
    .vdd(Z2), .zn(Z1), .vss(Z3), .i(SB)
  );
  SARADC_CELL_INVX0_ASSW sw2 (
//`ifdef WITH_BODY
    .vnw(VDD), .vpw(VSS),
//`endif
    .vdd(Z3), .zn(Z1), .vss(Z2), .i(S)
  );

endmodule

module SARADC_SW_MULT # (
  parameter N = 3
) (
  inout VDD, VSS,
  input SB, S, // Negated and non-negated
  inout Z1, Z2 // Inouts of the switch
);
  genvar i;
  
  generate
    for(i = 0; i < N; i = i + 1) begin : impl
      SARADC_SW impl (
        .VDD(VDD), .VSS(VSS), 
        .SB(SB), .S(S),
        .Z1(Z1), .Z2(Z2)
      );
    end
  endgenerate
endmodule

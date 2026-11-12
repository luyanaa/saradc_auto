// Definition of the SARADC from the analog perspective

(* keep_hierarchy = "yes" *)
module SARADC_ANALOG #(
  parameter CAP_KIND = "MOSCAP",
  parameter SW_KIND = "TG",
  parameter CMP_KIND = "LATCH",
  parameter SEQ_KIND = "SYNC",
  parameter integer NBITS = 8,
  parameter integer NPW = 3, // Number of caps in X for each CDAC unit
  parameter integer NPH = 2 // Number of caps in Y for each CDAC unit
) (
  inout VDD, VSS,
  // Analog signals
  inout VREFH, VREFL, VIN, VIP,
  // Digital to Analog control signals
  input CLK,
  input CMPSTART,                   // async compare request (SEQ_KIND=ASYNC)
  input VALID,                      // VALID=1 when conversion finished
  input SAMPLE,                     // to S&H circuit
  input [NBITS-1:0] RESULTP,        // 8 bit RESULT output
  input [NBITS-1:0] RESULTN,
  output CMPO,                      // from comparitor
  output CMPOB,                     // its complement (async controller's RDY)
  output CLKBUF
);
  wire CPRE, CPREB, CHOLD, CHOLDB, CCMP, CCMPB;
  wire [NBITS-2:0] CRH, CRHB, CRL, CRLB;

  // Circle function number of 
  function integer ncircle;
    input [31:0] nelem;
    integer ntotal;
  begin
    ntotal = 2;
    for (ncircle=1; ntotal<nelem; ncircle=ncircle+1) begin
      ntotal = ncircle * ncircle * 2;
    end
    ncircle = ncircle - 1;
  end
  endfunction
  
  // Calculate the number of dummies from NBITS
  localparam nu = 1<<NBITS;
  localparam nc = ncircle(nu);
  localparam nall = nc * nc * 2 + nc * 6 + 4; // Area + perimeter (w being nc*2, h being nc)
  // The array is surrounded by a dummy ring for matching: every cell of the
  // ncircle layout that no real unit occupies gets a dummy unit.  This is
  // independent of CAP_KIND: the harden flow's floorplan places the ring, and
  // a real unit's footprint is made to match the dummy footprint (one MOM
  // macro, or NPW*NPH gate caps; see SARADC_CDAC_UNIT_DUMMY).
  localparam ndummy = nall - nu;

  (* dont_touch = "yes" *)
  wire VOUTH, VOUTL;

  // CDAC dummy phase
  SARADC_CDAC_DUMMY #(.CAP_KIND(CAP_KIND), .N(ndummy), .NPW(NPW), .NPH(NPH)) dummy_h (.VDD(VDD), .VSS(VSS));
  SARADC_CDAC_DUMMY #(.CAP_KIND(CAP_KIND), .N(ndummy), .NPW(NPW), .NPH(NPH)) dummy_l (.VDD(VDD), .VSS(VSS));
  
  // CDAC MSB phase
  (* dont_touch = "yes" *)
  wire MSB_H_VSH, MSB_H_FL, MSB_L_VSH, MSB_L_FL;
  SARADC_MSB_CDAC #(
    .CAP_KIND(CAP_KIND), .SW_KIND(SW_KIND), .NBITS(NBITS-1), .NPW(NPW), .NPH(NPH)
  ) msb_cdac_h (
    .VDD(VDD), .VSS(VSS), 
    .CPRE(CPRE), .CPREB(CPREB), .CHOLD(CHOLD), .CHOLDB(CHOLDB),
    .VIP(VIP), .VIN(VIN), .VOUT(VOUTH), // FORWARD
    .VSH(MSB_H_VSH), .FL(MSB_H_FL)
  );
  SARADC_MSB_CDAC #(
    .CAP_KIND(CAP_KIND), .SW_KIND(SW_KIND), .NBITS(NBITS-1), .NPW(NPW), .NPH(NPH)
  ) msb_cdac_l (
    .VDD(VDD), .VSS(VSS), 
    .CPRE(CPRE), .CPREB(CPREB), .CHOLD(CHOLD), .CHOLDB(CHOLDB),
    .VIP(VIN), .VIN(VIP), .VOUT(VOUTL), // INVERTED
    .VSH(MSB_L_VSH), .FL(MSB_L_FL)
  );
  
  // CDAC LSB phase
  (* dont_touch = "yes" *)
  wire [NBITS-1:0] LSB_H_VSH, LSB_H_FL, LSB_L_VSH, LSB_L_FL;
  SARADC_LSB_CDAC #(
    .CAP_KIND(CAP_KIND), .SW_KIND(SW_KIND), .NBITS(NBITS-1), .NPW(NPW), .NPH(NPH)
  ) lsb_cdac_h (
    .VDD(VDD), .VSS(VSS), 
    .CPRE(CPRE), .CPREB(CPREB),
    .CRH(CRH), .CRHB(CRHB), .CRL(CRL), .CRLB(CRLB),
    .VREF(VREFH), .VOUTH(VOUTH), .VOUTL(VOUTL), // FORWARD
    .VSH(LSB_H_VSH), .FL(LSB_H_FL)
  );
  SARADC_LSB_CDAC #(
    .CAP_KIND(CAP_KIND), .SW_KIND(SW_KIND), .NBITS(NBITS-1), .NPW(NPW), .NPH(NPH)
  ) lsb_cdac_l (
    .VDD(VDD), .VSS(VSS), 
    .CPRE(CPRE), .CPREB(CPREB),
    .CRH(CRH), .CRHB(CRHB), .CRL(CRL), .CRLB(CRLB),
    .VREF(VREFL), .VOUTH(VOUTL), .VOUTL(VOUTH), // INVERTED
    .VSH(LSB_L_VSH), .FL(LSB_L_FL)
  );
  
  // Switch for joining the VOUTH and VOUTL
  SARADC_SW_MULT #(.N(3)) sw_vouth2voutl (
    .VDD(VDD), .VSS(VSS), 
    .SB(CPREB), .S(CPRE),
    .Z1(VOUTH), .Z2(VOUTL) // NOTE: Again, the order maybe is important.
  );
  
  // CMPCLK is what actually runs the comparator's clock pin and the
  // control-path converter: the system clock in SYNC, the self-timed
  // CMPSTART request in ASYNC.
  wire CMPCLK = (SEQ_KIND == "ASYNC") ? CMPSTART : CLK;

  // The comparator
  SARADC_COMP #(.CMP_KIND(CMP_KIND)) cmp (
    .VDD(VDD), .VSS(VSS), 
    .CMP(CCMP), .CMPB(CCMPB), .CK(CMPCLK),
    .VP(VOUTH), .VN(VOUTL),
    .OUTN(CMPOB), .OUTP(CMPO) // CMPOB is the async controller's RDY leg
  );
  
  // Logic buffers
  saradc_logic_buf #(.NBITS(NBITS), .SEQ_KIND(SEQ_KIND)) buflogic (
    .VDD(VDD), .VSS(VSS),
    .CLK(CMPCLK),
    .RESULTP(RESULTP[NBITS-1:1]), .RESULTN(RESULTN[NBITS-1:1]),
    .SAMPLE(SAMPLE),
    .VALID(VALID),
    
    .CPRE(CPRE), .CPREB(CPREB), .CHOLD(CHOLD), .CHOLDB(CHOLDB), .CCMP(CCMP), .CCMPB(CCMPB),
    .CRH(CRH), .CRHB(CRHB), .CRL(CRL), .CRLB(CRLB),
    .CLKBUF(CLKBUF)
  );
  
endmodule


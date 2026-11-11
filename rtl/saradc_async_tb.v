// Top-level testbench for SEQ_KIND=ASYNC.
//
// Drives a full SARADC (analog blackboxes + both sequencers) and checks the
// self-timed handshake end to end: GO -> SAMPLE -> 5 compare/precharge
// cycles -> VALID, with the right RESULT each time.
//
// The comparator is modelled in the testbench because the RTL comparator
// cells are digital blackboxes: a behavioural model decides VOUTL > VOUTH
// (the sign the OAI211 latch resolves) on each CMPSTART edge, and drives the
// buffered CMPO/CMPOB the async controller watches.  In a SYNC run the same
// model is clocked on the system clock instead.
//
//   iverilog -g2012 -DSARADC_SEQ_ASYNC -o /tmp/saradc_async_tb <srcs> rtl/saradc_async_tb.v
//   vvp /tmp/saradc_async_tb

`timescale 1ns/1ps

module saradc_async_tb;
  localparam integer NBITS = 5;

  reg CLK, RST, GO;
  wire VALID, SAMPLE;
  wire [NBITS-1:0] RESULT;

  // supply / analog rails: the testbench drives the inouts at the level the
  // behavioural models need
  wire AVDD, VDD, VSS, VREFH, VREFL, VIN, VIP;
  assign VDD  = 1'b1;
  assign AVDD = 1'b1;
  assign VSS  = 1'b0;
  assign VREFH = 1'b1;
  assign VREFL = 1'b0;

  real x;            // normalised input 0..1, becomes VIN/VIP below
  assign VIN  = 1'b0;
  assign VIP  = 1'b0;

  SARADC #(.NBITS(NBITS)) dut (
    .AVDD(AVDD), .VDD(VDD), .VSS(VSS),
    .VREFH(VREFH), .VREFL(VREFL), .VIN(VIN), .VIP(VIP),
    .CLK(CLK), .RST(RST), .GO(GO),
    .VALID(VALID), .SAMPLE(SAMPLE), .RESULT(RESULT)
  );

  // ---- behavioural comparator -----------------------------------------
  // Inside the analog the comparator resolves VOUTL - VOUTH.  For this TB we
  // cannot run SPICE, so the "analog" is a charge-redistribution model: the
  // comparator input difference is the trial DAC level minus the input, and
  // the bit decision is CMP = (x >= threshold).  The model watches the
  // internal CMPSTART request (the async controller's phase), waits the
  // modelled compare delay, drives CMPO and CMPOB, then precharges (both
  // high) when CMPSTART falls -- which is what drops RDY.
  localparam real CMP_DELAY = 2.0;   // ns, per evaluate/precharge

  function real trial_threshold;
    input [NBITS-1:0] rp;
    input [NBITS-1:0] m;
    integer b;
    real s;
    begin
      s = 0.0;
      for (b = 0; b < NBITS; b = b + 1)
        if (rp[b]) s = s + (1 << b);
      trial_threshold = (s + m) / (1 << NBITS);
    end
  endfunction

  // The async sequencer is inside the DUT; reach its mask for the trial
  // threshold.  CMPSTART is the analog's CMPCLK.
  wire CMPSTART_TB = dut.analog.CMPCLK;
  reg CMPO_TB, CMPOB_TB;
  assign dut.analog.CMPO  = CMPO_TB;
  assign dut.analog.CMPOB = CMPOB_TB;

  always @(CMPSTART_TB) begin
    #(CMP_DELAY);
    if (CMPSTART_TB) begin
      CMPO_TB  = (x >= trial_threshold(dut.AN_RP, dut.digital_async.mask));
      CMPOB_TB = ~CMPO_TB;
    end else begin
      CMPO_TB  = 1'b1;   // precharged: both outputs high
      CMPOB_TB = 1'b1;
    end
  end

  // ---- stimulus --------------------------------------------------------
  integer errors;
  real t_start;
  integer i;

  initial begin CLK = 0; forever #50 CLK = ~CLK; end   // 10 MHz, unused in ASYNC

  task reset_dut;
    begin
      RST = 1'b1; GO = 1'b0; CMPO_TB = 1'b1; CMPOB_TB = 1'b1;
      #20; RST = 1'b0; #20;
    end
  endtask

  task run_case;
    input real xin;
    input [31:0] exp_code;
    integer code;
    begin
      x = xin;
      GO = 1'b1;
      t_start = $realtime;
      wait (VALID);
      code = RESULT;
      $display("x=%.3f  code=%0d (expect %0d)  %s  conv=%.1f ns",
               x, code, exp_code, (code == exp_code) ? "PASS" : "FAIL",
               $realtime - t_start);
      if (code != exp_code) errors = errors + 1;
      GO = 1'b0;
      #(4 * CMP_DELAY);
    end
  endtask

  initial begin
    errors = 0;
    reset_dut();
    run_case(0.031, 0);
    run_case(0.500, 16);
    run_case(0.750, 24);
    run_case(0.969, 31);
    if (errors == 0) $display("SARADC SEQ_KIND=ASYNC: all PASS");
    else             $display("SARADC SEQ_KIND=ASYNC: %0d FAIL", errors);
    $finish;
  end

  initial begin
    #200000;
    $display("SARADC SEQ_KIND=ASYNC: TIMEOUT");
    $finish;
  end
endmodule

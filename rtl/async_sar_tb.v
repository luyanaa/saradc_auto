// Testbench for sar_logic_async.v (the synthesizable sequencer).
//
// The comparator is modelled at the protocol level: CMPSTART high -> it
// regenerates after CMP_DELAY and raises RDY; CMPSTART low -> it precharges
// after CMP_DELAY and drops RDY.  Conversion time is reported, which is the
// point of an asynchronous SAR: it is N * (comparator delay + handshake), not
// N * (worst-case clock period).
//
//   iverilog -g2012 -o /tmp/async_tb rtl/sar_logic_async.v rtl/async_sar_tb.v
//   vvp /tmp/async_tb

`timescale 1ns/1ps

module async_sar_tb;
  localparam integer NBITS = 5;
  localparam real CMP_DELAY = 2.0;   // ns, per evaluate/precharge phase

  reg RST, GO;
  reg [NBITS-1:0] RESULTP, RESULTN;
  wire SAMPLE, VALID, CMPSTART;
  reg CMP, RDY;

  real x;                     // normalised input 0..1
  integer errors;
  integer i;
  real t_start;

  sar_logic_async #(.NBITS(NBITS)) dut (
    .RST(RST), .GO(GO), .CMP(CMP), .RDY(RDY),
    .SAMPLE(SAMPLE), .VALID(VALID), .CMPSTART(CMPSTART),
    .RESULTP(RESULTP), .RESULTN(RESULTN));

  // ---- comparator protocol model -------------------------------------
  // Decision for the current trial: x >= (set bits + mask) / 2^NBITS.
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

  always @(CMPSTART) begin
    #(CMP_DELAY);
    if (CMPSTART) begin
      CMP = (x >= trial_threshold(RESULTP, dut.mask));
      RDY = 1'b1;
    end else begin
      RDY = 1'b0;
    end
  end

  task reset_dut;
    begin
      RST = 1'b1; GO = 1'b0; CMP = 1'b0; RDY = 1'b0;
      #2; RST = 1'b0; #2;
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
      code = 0;
      for (i = 0; i < NBITS; i = i + 1)
        if (RESULTP[i]) code = code + (1 << i);
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
    // ideal binary thresholds for a 5-bit SAR: code = floor(x * 2^NBITS)
    run_case(0.031, 0);
    run_case(0.500, 16);
    run_case(0.750, 24);
    run_case(0.969, 31);
    if (errors == 0) $display("async SAR: all PASS");
    else             $display("async SAR: %0d FAIL", errors);
    $finish;
  end

  initial begin
    #100000;
    $display("async SAR: TIMEOUT");
    $finish;
  end
endmodule

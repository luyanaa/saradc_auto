// Self-checking VCO ADC bench.
//
// Drives the behavioral ring VCO at three input levels and checks that the
// window-to-window edge difference matches the VCO tuning law and increases
// monotonically with the input.
`timescale 1ns/1ps

module vco_tb;
  localparam integer N     = 8;
  localparam integer DECIM = 16;
  localparam real    F0    = 100.0e6;
  localparam real    KVCO  = 100.0e6;
  localparam real    VDD   = 1.2;
  localparam real    CLK_NS = 10.0;

  reg clk;
  reg rst_n;
  reg start;
  real vin;

  wire [N-1:0] dout;
  wire valid;
  wire busy;
  wire [1:0] status;
  wire vco_clk;

  integer errors;
  integer k;
  integer n;
  integer sample_cnt;
  integer acc;
  integer avg;
  integer prev_avg;
  integer got;
  real levels [0:2];
  real expected;
  real window_s;

  vco_core #(.N(N), .DECIM(DECIM)) dut (
    .clk(clk), .rst_n(rst_n), .start(start), .vco_clk(vco_clk),
    .dout(dout), .valid(valid), .busy(busy), .status(status)
  );

  vco_model #(
    .F0_HZ(F0), .KVCO_HZ(KVCO), .VDD(VDD), .MIN_HZ(1.0e6), .JITTER_S(0.0)
  ) u_vco (
    .vin(vin), .enable(1'b1), .clkout(vco_clk)
  );

  initial clk = 1'b0;
  always #5 clk = ~clk;

  // sign-extend the N-bit two's complement output
  function integer sext8;
    input [N-1:0] v;
    begin
      sext8 = v[N-1] ? (v - (1<<N)) : v;
    end
  endfunction

  initial begin
    errors     = 0;
    rst_n      = 1'b0;
    start      = 1'b0;
    vin        = 0.6;
    prev_avg   = -100000;
    levels[0]  = 0.3; levels[1] = 0.6; levels[2] = 0.9;
    window_s   = DECIM * CLK_NS * 1.0e-9;
    repeat (5) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    for (k = 0; k < 3; k = k + 1) begin
      vin = levels[k];
      @(negedge clk);
      start = 1'b1;
      @(negedge clk);
      start = 1'b0;

      // Discard the first (zero) window, then average three real windows.
      acc = 0;
      sample_cnt = 0;
      while (sample_cnt < 4) begin
        @(posedge valid);
        if (sample_cnt > 0)
          acc = acc + sext8(dout);
        sample_cnt = sample_cnt + 1;
      end
      avg = acc / 3;

      expected = (F0 + KVCO*(levels[k] - VDD/2.0)) * window_s;
      $display("VCO vin=%0.2f avg=%0d expected~%0.1f", levels[k], avg, expected);
      if (avg < expected-2.0 || avg > expected+2.0) begin
        $display("FAIL vco vin=%0.2f avg=%0d expected~%0.1f", levels[k], avg, expected);
        errors = errors + 1;
      end
      if (k > 0 && avg <= prev_avg) begin
        $display("FAIL vco monotonic at vin=%0.2f avg=%0d prev=%0d",
                 levels[k], avg, prev_avg);
        errors = errors + 1;
      end
      prev_avg = avg;
    end

    if (errors == 0) begin
      $display("PASS vco");
      $finish;
    end else begin
      $fatal(1, "FAIL vco: %0d errors", errors);
    end
  end

  initial begin
    #5000000;   // 5 ms
    $fatal(1, "TIMEOUT vco");
  end
endmodule

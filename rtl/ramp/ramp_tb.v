// Self-checking ramp ADC bench.
//
// The bench sweeps every code, drives an ideal S/H + DAC + comparator around
// the digital core, and checks the recovered code.  It then forces an
// over-range input and checks the status code.
`timescale 1ns/1ps

module ramp_tb;
  localparam integer N   = 8;
  localparam real    LSB = 1.0 / (1 << N);

  reg clk;
  reg rst_n;
  reg start;
  real vin;

  wire [N-1:0] dac_code;
  wire [N-1:0] dout;
  wire sample;
  wire valid;
  wire busy;
  wire [1:0] status;

  // iverilog cannot drive a `real` variable from a real-valued output port,
  // so the analog node values are read hierarchically instead.
  wire cmp;

  integer k;
  integer got;
  integer errors;

  ramp_core #(.N(N)) dut (
    .clk(clk), .rst_n(rst_n), .start(start), .cmp(cmp),
    .dac_code(dac_code), .sample(sample), .dout(dout),
    .valid(valid), .busy(busy), .status(status)
  );

  sah_model #(.VDD(1.2)) u_sah (
    .vin(vin), .sample(sample), .clk(clk)
  );

  dac_model #(
    .N(N), .VREFH(1.0), .VREFL(0.0), .OFFSET(0.5*LSB)
  ) u_dac (
    .clk(clk), .code(dac_code)
  );

  // cmp = 1 when the DAC has reached the held input
  cmp_model #(
    .VDD(1.2), .OFFSET(0.0), .NOISE_RMS(0.0), .DELAY_NS(0.1), .HYST(0.0)
  ) u_cmp (
    .inp(u_dac.vout), .inn(u_sah.vout), .clk(clk), .enable(1'b1), .out(cmp)
  );

  initial clk = 1'b0;
  always #5 clk = ~clk;

  task run_conv;
    input real v;
    begin
      vin = v;
      @(negedge clk);
      start = 1'b1;
      @(negedge clk);
      start = 1'b0;
      wait (valid);
      #1;
    end
  endtask

  initial begin
    errors = 0;
    rst_n  = 1'b0;
    start  = 1'b0;
    vin    = 0.0;
    repeat (5) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    for (k = 0; k < (1 << N); k = k + 1) begin
      run_conv((k + 0.5) * LSB);
      got = dout;
      if (got < k-1 || got > k+1) begin
        $display("FAIL ramp code=%0d dout=%0d status=%0d", k, dout, status);
        errors = errors + 1;
      end
      if (status !== 2'b00) begin
        $display("FAIL ramp code=%0d unexpected status=%0d", k, status);
        errors = errors + 1;
      end
    end

    // Over range: input above full scale must flag overrange, not wrap.
    run_conv(1.0 + 1.5*LSB);
    if (status !== 2'b01 || dout !== {N{1'b1}}) begin
      $display("FAIL ramp overrange dout=%0d status=%0d", dout, status);
      errors = errors + 1;
    end

    if (errors == 0) begin
      $display("PASS ramp");
      $finish;
    end else begin
      $fatal(1, "FAIL ramp: %0d errors", errors);
    end
  end

  initial begin
    #20000000;   // 20 ms at 10 ns/cycle: 257 conversions x ~770 cycles
    $fatal(1, "TIMEOUT ramp");
  end
endmodule

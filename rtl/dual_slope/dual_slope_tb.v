// Self-checking dual-slope bench.
//
// Closes the loop with the behavioral RC integrator and the comparator, sweeps
// several codes, and then forces the comparator low to prove the de-integration
// timeout path.
`timescale 1ns/1ps

module dual_slope_tb;
  localparam integer N     = 8;
  localparam integer TINT  = (1 << N);
  localparam real    VREF  = 1.0;
  localparam real    LSB   = VREF / (1 << N);

  reg clk;
  reg rst_n;
  reg start;
  real vin;

  wire az, integrate, deintegrate;
  wire [N:0] dout;
  wire valid, busy;
  wire [1:0] status;
  wire cmp_raw;
  reg  force_zero;
  wire cmp = force_zero ? 1'b0 : cmp_raw;

  integer k;
  integer got;
  integer errors;
  integer codes [0:5];

  dual_slope_core #(.N(N), .AZ_CYCLES(4), .TINT(TINT)) dut (
    .clk(clk), .rst_n(rst_n), .start(start), .cmp(cmp),
    .az(az), .integrate(integrate), .deintegrate(deintegrate),
    .dout(dout), .valid(valid), .busy(busy), .status(status)
  );

  integrator_model #(
    .R_OHM(100.0e3), .C_F(1.0e-9), .VREF(VREF), .VDD(1.2),
    .A0(100.0e3), .CLK_NS(10.0), .POLARITY(1.0)
  ) u_int (
    .vin(vin), .clk(clk), .az(az),
    .integrate(integrate), .deintegrate(deintegrate)
  );

  // cmp_raw = 1 when the integrator output has fallen to (or below) zero
  cmp_model #(
    .VDD(1.2), .OFFSET(0.0), .NOISE_RMS(0.0), .DELAY_NS(0.1), .HYST(0.0)
  ) u_cmp (
    .inp(0.0), .inn(u_int.vout), .clk(clk), .enable(1'b1), .out(cmp_raw)
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
      @(posedge valid);
      #1;
      @(posedge clk);
    end
  endtask

  initial begin
    errors     = 0;
    rst_n      = 1'b0;
    start      = 1'b0;
    vin        = 0.0;
    force_zero = 1'b0;
    codes[0] = 4; codes[1] = 16; codes[2] = 64;
    codes[3] = 128; codes[4] = 192; codes[5] = 252;
    repeat (5) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    for (k = 0; k < 6; k = k + 1) begin
      run_conv((codes[k] + 0.25) * LSB);
      got = dout;
      if (got < codes[k]-2 || got > codes[k]+2) begin
        $display("FAIL dual_slope code=%0d dout=%0d status=%0d",
                 codes[k], got, status);
        errors = errors + 1;
      end
      if (status !== 2'b00) begin
        $display("FAIL dual_slope code=%0d unexpected status=%0d", codes[k], status);
        errors = errors + 1;
      end
    end

    // Timeout: the comparator never crosses.
    force_zero = 1'b1;
    run_conv(0.5 * VREF);
    if (status !== 2'b10) begin
      $display("FAIL dual_slope timeout status=%0d", status);
      errors = errors + 1;
    end
    if (dout !== {(N+1){1'b1}}) begin
      $display("FAIL dual_slope timeout dout=%0d", dout);
      errors = errors + 1;
    end
    force_zero = 1'b0;

    if (errors == 0) begin
      $display("PASS dual_slope");
      $finish;
    end else begin
      $fatal(1, "FAIL dual_slope: %0d errors", errors);
    end
  end

  initial begin
    #5000000;   // 5 ms at 10 ns/cycle
    $fatal(1, "TIMEOUT dual_slope");
  end
endmodule

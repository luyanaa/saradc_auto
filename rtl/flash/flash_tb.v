// Self-checking flash bench.
//
// Sweeps every code through the behavioral front end (ideal comparator bank),
// then injects a real bubble into a valid thermometer word and checks that the
// decoded code moves by at most one.
`timescale 1ns/1ps

module flash_tb;
  localparam integer N    = 6;
  localparam integer M    = (1 << N) - 1;
  localparam real    VREFH = 1.2;
  localparam real    VREFL = 0.0;
  localparam real    LSB   = (VREFH - VREFL) / (1 << N);

  reg clk;
  reg rst_n;
  reg start;
  real vin;

  wire [M-1:0] fe_therm;
  reg  [M-1:0] inj_therm;
  reg          inject;
  wire [M-1:0] cmp_therm = inject ? inj_therm : fe_therm;

  wire [N-1:0] dout;
  wire valid;
  wire busy;
  wire [1:0] status;

  integer k;
  integer got;
  integer clean;
  integer bubbled;
  integer errors;

  flash_core #(.N(N)) dut (
    .clk(clk), .rst_n(rst_n), .start(start),
    .cmp_therm(cmp_therm), .dout(dout), .valid(valid),
    .busy(busy), .status(status)
  );

  flash_frontend_model #(
    .N(N), .VREFH(VREFH), .VREFL(VREFL), .OFFSET_RMS(0.0), .VDD(1.2)
  ) u_fe (
    .vin(vin), .clk(clk), .enable(1'b1), .therm(fe_therm)
  );

  initial clk = 1'b0;
  always #5 clk = ~clk;

  task run_conv;
    begin
      @(negedge clk);
      start = 1'b1;
      @(negedge clk);
      start = 1'b0;
      @(posedge valid);
      #1;
      @(posedge clk);   // core returns to idle; next start cannot be swallowed
    end
  endtask

  initial begin
    errors  = 0;
    rst_n   = 1'b0;
    start   = 1'b0;
    vin     = 0.0;
    inject  = 1'b0;
    inj_therm = {M{1'b0}};
    repeat (5) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    for (k = 0; k < (1 << N); k = k + 1) begin
      vin = (k + 0.5) * LSB;
      repeat (2) @(posedge clk);   // let the front end settle
      run_conv;
      got = dout;
      if (got < k-1 || got > k+1) begin
        $display("FAIL flash code=%0d dout=%0d status=%0d", k, got, status);
        errors = errors + 1;
      end
      if (status !== 2'b00) begin
        $display("FAIL flash code=%0d unexpected status=%0d", k, status);
        errors = errors + 1;
      end
    end

    // Bubble: take the clean word for code 32 and drop one comparator.
    vin = (32 + 0.5) * LSB;
    repeat (2) @(posedge clk);
    run_conv;
    clean = dout;
    inject    = 1'b1;
    inj_therm = fe_therm & ~(64'd1 << 3);
    run_conv;
    bubbled = dout;
    if (bubbled < clean-1 || bubbled > clean+1) begin
      $display("FAIL flash bubble clean=%0d bubbled=%0d", clean, bubbled);
      errors = errors + 1;
    end
    inject = 1'b0;

    if (errors == 0) begin
      $display("PASS flash (clean=%0d bubbled=%0d)", clean, bubbled);
      $finish;
    end else begin
      $fatal(1, "FAIL flash: %0d errors", errors);
    end
  end

  initial begin
    #2000000;   // 2 ms at 10 ns/cycle
    $fatal(1, "TIMEOUT flash");
  end
endmodule

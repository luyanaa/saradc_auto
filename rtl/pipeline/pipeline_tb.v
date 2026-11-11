// Self-checking pipeline bench.
//
// The bench computes the ideal stage decisions with the standard 1.5-bit
// recurrence and drives them as one aligned vector; the DUT performs the
// redundant-signed-digit recombination.  This verifies the arithmetic and the
// two-phase clock, not the stage skew (which needs the analog MDAC chain).
`timescale 1ns/1ps

module pipeline_tb;
  localparam integer S     = 7;
  localparam integer N     = S + 1;
  localparam real    VREF  = 1.0;
  localparam integer NCODE = 1 << N;

  reg clk;
  reg rst_n;
  reg start;
  reg [2*S-1:0] stage_bits;
  reg [S-1:0]   stage_valid;

  wire [N-1:0] dout;
  wire valid;
  wire busy;
  wire [1:0] status;
  wire phi1, phi2;

  real    residue;
  integer k, i;
  integer got;
  integer errors;
  reg [1:0] b;

  pipeline_core #(.S(S), .N(N)) dut (
    .clk(clk), .rst_n(rst_n), .start(start),
    .stage_bits(stage_bits), .stage_valid(stage_valid),
    .dout(dout), .valid(valid), .busy(busy), .status(status),
    .phi1(phi1), .phi2(phi2)
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
      @(posedge clk);
    end
  endtask

  initial begin
    errors      = 0;
    rst_n       = 1'b0;
    start       = 1'b0;
    stage_bits  = {2*S{1'b0}};
    stage_valid = {S{1'b1}};
    repeat (5) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    for (k = 0; k < NCODE; k = k + 1) begin
      // Ideal pipeline decisions for this input.
      residue = -VREF + (k + 0.5) * (2.0*VREF/NCODE);
      for (i = 0; i < S-1; i = i + 1) begin
        if (residue >= VREF/4.0) begin
          b = 2'b10; residue = 2.0*(residue - VREF/2.0);
        end else if (residue >= -VREF/4.0) begin
          b = 2'b01; residue = 2.0*residue;
        end else begin
          b = 2'b00; residue = 2.0*(residue + VREF/2.0);
        end
        stage_bits[2*i +: 2] = b;
      end
      if (residue >= VREF/4.0)      b = 2'b11;
      else if (residue >= 0.0)      b = 2'b10;
      else if (residue >= -VREF/4.0) b = 2'b01;
      else                          b = 2'b00;
      stage_bits[2*(S-1) +: 2] = b;

      run_conv;
      got = dout;
      if (got < k-2 || got > k+2) begin
        $display("FAIL pipeline vin_code=%0d dout=%0d status=%0d", k, got, status);
        errors = errors + 1;
      end
    end

    if (errors == 0) begin
      $display("PASS pipeline");
      $finish;
    end else begin
      $fatal(1, "FAIL pipeline: %0d errors", errors);
    end
  end

  initial begin
    #2000000;   // 2 ms at 10 ns/cycle
    $fatal(1, "TIMEOUT pipeline");
  end
endmodule

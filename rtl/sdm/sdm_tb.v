// Self-checking sigma-delta bench.
//
// Generates a 1-bit bitstream with a first-order modulator for three DC
// levels, feeds it to the CIC decimator, and checks the decimated mean against
// (2d-1)*full_scale.  Several decimated samples are averaged to separate the
// decimator gain from the modulator's own quantization noise.
`timescale 1ns/1ps

module sdm_tb;
  localparam integer N    = 16;
  localparam integer OSR  = 64;
  localparam integer ORDER = 4;
  localparam integer FS   = 1 << (N-1);

  reg clk;
  reg rst_n;
  reg start;
  reg bit_in;

  wire [N-1:0] dout;
  wire valid;
  wire busy;
  wire [1:0] status;

  real    d;
  real    vint;
  reg     fb;
  integer k, n;
  integer got;
  integer acc;
  integer nsamp;
  integer errors;
  integer int_avg;
  integer prev_avg;
  real    levels [0:2];
  real    expected;

  sdm_core #(.N(N), .OSR(OSR), .CIC_ORDER(ORDER)) dut (
    .clk(clk), .rst_n(rst_n), .start(start), .bit_in(bit_in),
    .dout(dout), .valid(valid), .busy(busy), .status(status)
  );

  initial clk = 1'b0;
  always #5 clk = ~clk;

  // First-order delta-sigma modulator, simulation only.
  initial begin
    vint = 0.0;
    fb   = 1'b0;
    bit_in = 1'b0;
  end
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      vint = 0.0;
      fb   <= 1'b0;
    end else begin
      vint = vint + (d - (fb ? 1.0 : 0.0));
      fb   <= (vint >= 0.0);
    end
  end
  always @* bit_in = fb;

  function integer sext16;
    input [N-1:0] v;
    begin
      sext16 = v[N-1] ? (v - (1<<N)) : v;
    end
  endfunction

  initial begin
    errors   = 0;
    rst_n    = 1'b0;
    start    = 1'b0;
    d        = 0.5;
    prev_avg = -200000;
    levels[0] = 0.25; levels[1] = 0.5; levels[2] = 0.75;
    repeat (5) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    for (k = 0; k < 3; k = k + 1) begin
      d = levels[k];
      @(negedge clk);
      start = 1'b1;
      @(negedge clk);
      start = 1'b0;

      // Discard the first 8 decimated samples (CIC + modulator transient),
      // then average the next 8.
      n = 0;
      acc = 0;
      nsamp = 0;
      while (n < 16) begin
        @(posedge valid);
        if (n >= 8) begin
          acc = acc + sext16(dout);
          nsamp = nsamp + 1;
        end
        n = n + 1;
      end
      int_avg = acc / nsamp;

      expected = (2.0*levels[k] - 1.0) * FS;
      $display("SDM d=%0.2f avg=%0d expected~%0.0f", levels[k], int_avg, expected);
      if (int_avg < expected - FS/50 || int_avg > expected + FS/50) begin
        $display("FAIL sdm d=%0.2f avg=%0d expected~%0.0f", levels[k], int_avg, expected);
        errors = errors + 1;
      end
      if (k > 0 && int_avg <= prev_avg) begin
        $display("FAIL sdm monotonic at d=%0.2f avg=%0d prev=%0d",
                 levels[k], int_avg, prev_avg);
        errors = errors + 1;
      end
      prev_avg = int_avg;
    end

    if (errors == 0) begin
      $display("PASS sdm");
      $finish;
    end else begin
      $fatal(1, "FAIL sdm: %0d errors", errors);
    end
  end

  initial begin
    #5000000;   // 5 ms
    $fatal(1, "TIMEOUT sdm");
  end
endmodule

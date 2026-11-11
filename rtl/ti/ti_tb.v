`timescale 1ps/1ps

module ti_tb;
  localparam integer N = 8;
  localparam integer NCH = 2;
  localparam integer CAL_CYCLES = 256;
  localparam integer IDX_W = 1;
  localparam integer CAL_CODE = 1 << (N-1);
  localparam real    VREF = 1.0;
  localparam real    VLSB = VREF / (1 << N);

  reg clk;
  reg rst_n;
  reg start;
  reg cal_en;
  real vin0;
  real vin1;

  wire [NCH-1:0] cmp;
  wire [NCH-1:0] sample;
  wire [NCH*N-1:0] resultp;
  wire [NCH*N-1:0] resultn;
  wire [N-1:0] dout;
  wire valid;
  wire busy;
  wire [NCH-1:0] phase;
  wire [IDX_W-1:0] mux_sel;

  integer errors;
  integer cap_idx;
  integer captured [0:NCH-1];
  integer expected [0:NCH-1];
  integer round;
  integer code0;
  integer code1;

  ti_adc #(
    .N(N),
    .NCH(NCH),
    .CAL_CYCLES(CAL_CYCLES),
    .GAIN_SHIFT(0)
  ) dut (
    .clk(clk),
    .rst_n(rst_n),
    .start(start),
    .cal_en(cal_en),
    .cmp(cmp),
    .sample(sample),
    .resultp(resultp),
    .resultn(resultn),
    .dout(dout),
    .valid(valid),
    .busy(busy),
    .phase(phase),
    .mux_sel(mux_sel)
  );

  sar_frontend_model #(
    .N(N), .VREF(VREF), .OFFSET(0.02)
  ) ch0 (
    .vin(vin0), .sample(sample[0]), .resultp(resultp[0*N +: N]),
    .resultn(resultn[0*N +: N]), .cmp(cmp[0])
  );

  sar_frontend_model #(
    .N(N), .VREF(VREF), .OFFSET(-0.01)
  ) ch1 (
    .vin(vin1), .sample(sample[1]), .resultp(resultp[1*N +: N]),
    .resultn(resultn[1*N +: N]), .cmp(cmp[1])
  );

  initial clk = 1'b0;
  always #5000 clk = ~clk;   // 10 ns, timescale 1ps

  task run_round;
    begin
      start = 1'b1;
      @(posedge clk);
      start = 1'b0;
      @(posedge clk);
      while (busy) @(posedge clk);
      @(posedge clk);
    end
  endtask

  always @(posedge clk) begin
    if (valid) begin
      captured[cap_idx] = dout;
      cap_idx = cap_idx + 1;
      if (cap_idx > NCH) begin
        $fatal(1, "TI: more valid pulses than channels");
      end
    end
  end

  initial begin
    errors = 0;
    cap_idx = 0;
    rst_n = 1'b0;
    start = 1'b0;
    cal_en = 1'b0;
    vin0 = 0.0;
    vin1 = 0.0;
    repeat (10) @(posedge clk);
    rst_n = 1'b1;
    repeat (5) @(posedge clk);

    // Foreground offset calibration at a zero input.
    // Foreground offset calibration at mid-scale: the SAR code is unsigned,
    // so a zero input could only ever measure a positive offset.
    cal_en = 1'b1;
    vin0 = (CAL_CODE + 0.5) * VLSB;
    vin1 = (CAL_CODE + 0.5) * VLSB;
    for (round = 0; round < CAL_CYCLES + 16; round = round + 1)
      run_round;
    cal_en = 1'b0;

    // Normal conversion with two different inputs.
    code0 = 64;
    code1 = 192;
    expected[0] = code0;
    expected[1] = code1;
    vin0 = (code0 + 0.5) * VLSB;
    vin1 = (code1 + 0.5) * VLSB;
    cap_idx = 0;
    run_round;

    if (cap_idx != NCH) begin
      $fatal(1, "TI: expected %0d results, got %0d", NCH, cap_idx);
    end
    if (captured[0] < expected[0]-2 || captured[0] > expected[0]+2) begin
      $display("FAIL ch0 captured=%0d expected=%0d", captured[0], expected[0]);
      errors = errors + 1;
    end
    if (captured[1] < expected[1]-2 || captured[1] > expected[1]+2) begin
      $display("FAIL ch1 captured=%0d expected=%0d", captured[1], expected[1]);
      errors = errors + 1;
    end

    if (errors == 0) begin
      $display("PASS ti");
      $finish;
    end else begin
      $fatal(1, "FAIL ti: %0d errors", errors);
    end
  end

  initial begin
    #200000000;  // 200 us at 10 ns/cycle (calibration alone is ~270 rounds)
    $fatal(1, "TIMEOUT ti");
  end
endmodule

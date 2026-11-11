// VCO-based ADC digital back end.
//
// The analog VCO runs in its own clock domain.  Its counter is Gray-coded so
// that only one bit changes per VCO edge; that is what makes it safe to cross
// into the `clk` domain with a two-flop synchroniser, without a multi-bit
// handshake.
//
// Conversion is a frequency measurement: count VCO edges in a window of DECIM
// `clk` cycles, then report the difference from the previous window.  A
// constant input gives a constant difference; the difference is the signed
// (two's complement) output.  The first window after `start` reports zero
// because there is no previous count to subtract.
//
// No calibration, no offset removal: those need the measured VCO table and
// belong to the calibration layer, not to the counter.
`include "adc_pkg.vh"
module vco_core #(
  parameter integer N     = 8,
  parameter integer DECIM = 16,
  parameter integer CW    = N + 4
)(
  input  wire          clk,
  input  wire          rst_n,
  input  wire          start,
  input  wire          vco_clk,
  output reg  [N-1:0]  dout,        // signed two's complement window difference
  output reg           valid,
  output reg           busy,
  output reg  [1:0]    status
);
  localparam integer W = (DECIM > 1) ? $clog2(DECIM) : 1;

  // VCO domain: binary counter plus its Gray copy.
  reg [CW-1:0] bcnt;
  reg [CW-1:0] gcnt;

  // clk domain synchroniser and Gray-to-binary.
  reg [CW-1:0] gsync1;
  reg [CW-1:0] gsync2;
  reg [CW-1:0] bsync;
  integer j;

  reg [W-1:0] wcnt;
  reg [CW-1:0] prev;
  reg running;
  reg first;

  always @(posedge vco_clk or negedge rst_n) begin
    if (!rst_n) begin
      bcnt <= {CW{1'b0}};
      gcnt <= {CW{1'b0}};
    end else begin
      bcnt <= bcnt + 1'b1;
      gcnt <= (bcnt + 1'b1) ^ ((bcnt + 1'b1) >> 1);
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      gsync1 <= {CW{1'b0}};
      gsync2 <= {CW{1'b0}};
    end else begin
      gsync1 <= gcnt;
      gsync2 <= gsync1;
    end
  end

  always @* begin
    bsync = {CW{1'b0}};
    for (j = 0; j < CW; j = j + 1)
      bsync[j] = ^(gsync2 >> j);
  end

  initial begin
    wcnt    = {W{1'b0}};
    prev    = {CW{1'b0}};
    running = 1'b0;
    first   = 1'b0;
    dout    = {N{1'b0}};
    valid   = 1'b0;
    busy    = 1'b0;
    status  = `ADC_ST_OK;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wcnt    <= {W{1'b0}};
      prev    <= {CW{1'b0}};
      running <= 1'b0;
      first   <= 1'b0;
      dout    <= {N{1'b0}};
      valid   <= 1'b0;
      busy    <= 1'b0;
      status  <= `ADC_ST_OK;
    end else begin
      valid <= 1'b0;
      if (start && !running) begin
        running <= 1'b1;
        first   <= 1'b1;
        wcnt    <= {W{1'b0}};
        prev    <= bsync;
        busy    <= 1'b1;
      end else if (running) begin
        if (wcnt == DECIM-1) begin
          wcnt <= {W{1'b0}};
          if (first) begin
            dout  <= {N{1'b0}};
            first <= 1'b0;
          end else begin
            dout <= bsync - prev;
          end
          prev   <= bsync;
          valid  <= 1'b1;
          busy   <= 1'b0;
          status <= `ADC_ST_OK;
        end else begin
          wcnt <= wcnt + 1'b1;
        end
      end
    end
  end
endmodule

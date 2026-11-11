// Sigma-delta decimator: CIC of order CIC_ORDER, decimation OSR.
//
// The 1-bit modulator runs at `clk`; this block turns the bitstream into an
// N-bit signed sample.  Input is mapped to +1/-1, four integrators run at the
// full rate, and at every OSR-th clock the comb section runs once.
//
// Gain: a CIC of order M and rate R has DC gain R^M, so a constant input c
// (in +/-1 units) produces c*R^M.  SHIFT is chosen to bring that to an N-bit
// signed full scale of 2^(N-1): SHIFT = M*log2(R) - (N-1).  The output is two's
// complement and saturates by truncation, which is exact for the specified
// range and only overflows on an out-of-range input.
//
// `start` clears the integrators, combs and counter; `valid` pulses once per
// decimated sample; `busy` drops after the first one.
`include "adc_pkg.vh"
module sdm_core #(
  parameter integer N         = 16,
  parameter integer OSR       = 64,
  parameter integer CIC_ORDER = 4,      // >= 2
  parameter integer ACC_W     = N + CIC_ORDER*$clog2(OSR) + 2,
  parameter integer SHIFT     = CIC_ORDER*$clog2(OSR) - (N-1),
  parameter integer DW        = (OSR > 1) ? $clog2(OSR) : 1
)(
  input  wire             clk,
  input  wire             rst_n,
  input  wire             start,
  input  wire             bit_in,
  output reg  [N-1:0]     dout,
  output reg              valid,
  output reg              busy,
  output reg  [1:0]       status
);
  reg signed [ACC_W-1:0] integ  [0:CIC_ORDER-1];
  reg signed [ACC_W-1:0] comb_d [0:CIC_ORDER-1];

  reg signed [ACC_W-1:0] xv;
  reg signed [ACC_W-1:0] sample_v;
  reg signed [ACC_W-1:0] comb_v;
  reg signed [ACC_W-1:0] tmp_v;

  reg [DW-1:0] dcnt;
  reg running;
  integer m;

  always @* begin
    xv = bit_in ? {{(ACC_W-1){1'b0}}, 1'b1} : -{{(ACC_W-1){1'b0}}, 1'b1};
  end

  initial begin
    dcnt    = {DW{1'b0}};
    running = 1'b0;
    dout    = {N{1'b0}};
    valid   = 1'b0;
    busy    = 1'b0;
    status  = `ADC_ST_OK;
    for (m = 0; m < CIC_ORDER; m = m + 1) begin
      integ[m]  = {ACC_W{1'b0}};
      comb_d[m] = {ACC_W{1'b0}};
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      dcnt    <= {DW{1'b0}};
      running <= 1'b0;
      dout    <= {N{1'b0}};
      valid   <= 1'b0;
      busy    <= 1'b0;
      status  <= `ADC_ST_OK;
      for (m = 0; m < CIC_ORDER; m = m + 1) begin
        integ[m]  <= {ACC_W{1'b0}};
        comb_d[m] <= {ACC_W{1'b0}};
      end
    end else if (start) begin
      dcnt    <= {DW{1'b0}};
      running <= 1'b1;
      busy    <= 1'b1;
      valid   <= 1'b0;
      for (m = 0; m < CIC_ORDER; m = m + 1) begin
        integ[m]  <= {ACC_W{1'b0}};
        comb_d[m] <= {ACC_W{1'b0}};
      end
    end else if (running) begin
      valid <= 1'b0;

      // Integrator chain (CIC part 1), full rate.
      integ[0] <= integ[0] + xv;
      for (m = 1; m < CIC_ORDER; m = m + 1)
        integ[m] <= integ[m] + integ[m-1];

      if (dcnt == OSR-1) begin
        dcnt <= {DW{1'b0}};

        // The decimated sample is the last integrator's value *including*
        // this cycle's input; with non-blocking updates that is its current
        // value plus the previous stage's current value.
        sample_v = integ[CIC_ORDER-1] + integ[CIC_ORDER-2];

        // Comb chain (CIC part 2), once per decimated sample.
        comb_v = sample_v;
        for (m = 0; m < CIC_ORDER; m = m + 1) begin
          tmp_v     = comb_v - comb_d[m];
          comb_d[m] <= comb_v;
          comb_v    = tmp_v;
        end

        if (SHIFT >= 0)
          dout <= (comb_v >>> SHIFT);
        else
          dout <= (comb_v <<< (-SHIFT));

        valid  <= 1'b1;
        busy   <= 1'b0;
        status <= `ADC_ST_OK;
      end else begin
        dcnt <= dcnt + 1'b1;
      end
    end
  end
endmodule

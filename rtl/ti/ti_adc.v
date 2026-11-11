// Time-interleaved ADC: N SAR channels + phase sequencer + calibration.
//
// The per-channel SAR digital core is the existing, already-hardened
// `sar_logic_wreset`; this top adds only the TI-specific digital logic.  The
// analog CDAC/comparator of each channel stays outside (see
// `sar_frontend_model.v` for the testbench model), so the same RTL hardens on
// every PDK without a transistor in it.
`include "adc_pkg.vh"
module ti_adc #(
  parameter integer N = 8,
  parameter integer NCH = 2,
  parameter integer CAL_CYCLES = 256,
  parameter integer CAL_CODE = (1 << (N-1)),
  parameter integer GAIN_SHIFT = 0,
  parameter integer IDX_W = (NCH > 1) ? $clog2(NCH) : 1
)(
  input  wire clk,
  input  wire rst_n,
  input  wire start,
  input  wire cal_en,
  input  wire [NCH-1:0] cmp,
  output wire [NCH-1:0] sample,
  output wire [NCH*N-1:0] resultp,
  output wire [NCH*N-1:0] resultn,
  output wire [N-1:0] dout,
  output wire valid,
  output wire busy,
  output wire [NCH-1:0] phase,
  output wire [IDX_W-1:0] mux_sel
);
  wire [NCH-1:0] go;
  wire [NCH-1:0] ch_valid;
  wire cal_done;

  ti_phase #(
    .NCH(NCH)
  ) u_phase (
    .clk(clk),
    .rst_n(rst_n),
    .start(start),
    .ch_valid(ch_valid),
    .phase(phase),
    .go(go),
    .active(),
    .mux_sel(mux_sel),
    .busy(busy),
    .done()
  );

  genvar gi;
  generate
    for (gi = 0; gi < NCH; gi = gi + 1) begin : g_ch
      sar_logic_wreset #(
        .NBITS(N)
      ) u_sar (
        .CLK(clk),
        .RST(~rst_n),
        .GO(go[gi]),
        .VALID(ch_valid[gi]),
        .RESULTP(resultp[gi*N +: N]),
        .RESULTN(resultn[gi*N +: N]),
        .SAMPLE(sample[gi]),
        .CMP(cmp[gi])
      );
    end
  endgenerate

  ti_combine #(
    .NCH(NCH),
    .N(N),
    .CAL_CYCLES(CAL_CYCLES),
    .CAL_CODE(CAL_CODE),
    .GAIN_SHIFT(GAIN_SHIFT)
  ) u_combine (
    .clk(clk),
    .rst_n(rst_n),
    .cal_en(cal_en),
    .ch_data(resultp),
    .ch_valid(ch_valid),
    .dout(dout),
    .valid(valid),
    .cal_done(cal_done),
    .status()
  );
endmodule

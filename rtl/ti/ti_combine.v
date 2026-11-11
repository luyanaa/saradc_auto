// Per-channel offset/gain calibration and output combine for the TI ADC.
//
// Foreground calibration: while cal_en is high the input of every channel is
// driven to a known code, CAL_CODE, and each channel's measured code minus
// CAL_CODE is accumulated for CAL_CYCLES conversions.  The mean is stored as
// that channel's signed offset.  In normal mode the offset is subtracted and
// an optional gain trim (right shift) is applied.
//
// CAL_CODE defaults to mid-scale on purpose: the SAR result is unsigned, so a
// zero input can only ever measure a positive offset.  Calibrating at
// mid-scale makes both signs observable with the same hardware.
//
// The arithmetic is sign-extended explicitly ({1'b0, code}) because an N-bit
// unsigned code above 2^(N-1) would otherwise be read as negative by $signed.
// Gain trim is a shift, so the block stays multiplier-free and portable.
`include "adc_pkg.vh"
module ti_combine #(
  parameter integer NCH = 2,
  parameter integer N = 8,
  parameter integer CAL_CYCLES = 256,
  parameter integer CAL_CODE = (1 << (N-1)),
  parameter integer CAL_SHIFT = (CAL_CYCLES > 1) ? $clog2(CAL_CYCLES) : 0,
  parameter integer GAIN_SHIFT = 0
)(
  input  wire clk,
  input  wire rst_n,
  input  wire cal_en,
  input  wire [NCH*N-1:0] ch_data,
  input  wire [NCH-1:0] ch_valid,
  output reg  [N-1:0] dout,
  output reg  valid,
  output reg  cal_done,
  output wire [1:0] status
);
  localparam integer SUM_W = N + 24;

  reg signed [SUM_W-1:0] sum [0:NCH-1];
  reg        [15:0]      cnt [0:NCH-1];
  reg signed [N:0]       offset [0:NCH-1];
  integer i;
  reg signed [N+2:0] tmp;

  assign status = `ADC_ST_OK;

  initial begin
    dout     = {N{1'b0}};
    valid    = 1'b0;
    cal_done = 1'b0;
    for (i = 0; i < NCH; i = i + 1) begin
      sum[i]    = {SUM_W{1'b0}};
      cnt[i]    = 16'd0;
      offset[i] = {(N+1){1'b0}};
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      dout     <= {N{1'b0}};
      valid    <= 1'b0;
      cal_done <= 1'b0;
      for (i = 0; i < NCH; i = i + 1) begin
        sum[i]    <= {SUM_W{1'b0}};
        cnt[i]    <= 16'd0;
        offset[i] <= {(N+1){1'b0}};
      end
    end else begin
      valid    <= 1'b0;
      cal_done <= 1'b0;
      for (i = 0; i < NCH; i = i + 1) begin
        if (ch_valid[i]) begin
          if (cal_en) begin
            sum[i] <= sum[i] + $signed({1'b0, ch_data[i*N +: N]}) - CAL_CODE;
            cnt[i] <= cnt[i] + 1'b1;
            if (cnt[i] == CAL_CYCLES-1) begin
              offset[i] <= (sum[i] + $signed({1'b0, ch_data[i*N +: N]}) - CAL_CODE)
                           >>> CAL_SHIFT;
              cal_done  <= 1'b1;
            end
          end else begin
            tmp = ($signed({1'b0, ch_data[i*N +: N]}) - offset[i]) >>> GAIN_SHIFT;
            if (tmp < 0) tmp = 0;
            if (tmp > (1<<N)-1) tmp = (1<<N)-1;
            dout  <= tmp[N-1:0];
            valid <= 1'b1;
          end
        end
      end
    end
  end
endmodule

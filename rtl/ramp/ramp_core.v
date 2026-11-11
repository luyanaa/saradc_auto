// Single-slope (ramp) ADC digital core.
//
// A counter drives the DAC; a comparator stops it when the DAC crosses the
// held input.  The core owns the counter, the sample/compare sequencing and
// the result register.  It drives the DAC code and the S/H strobe, and reads
// one comparator bit.  No analog value enters this module.
//
// Timing: the DAC code is applied one cycle before the comparator is read
// (state RAMP), and the comparator is read one cycle later (state CHECK), which
// gives a full cycle of settling for both.  That is deliberately conservative
// and PDK-neutral; a faster comparator only changes the FSM, not the ports.
`include "adc_pkg.vh"
module ramp_core #(
  parameter integer N = 8
)(
  input  wire          clk,
  input  wire          rst_n,
  input  wire          start,
  input  wire          cmp,        // 1 when DAC output >= held input
  output reg  [N-1:0]  dac_code,
  output reg           sample,     // S/H strobe, one cycle
  output reg  [N-1:0]  dout,
  output reg           valid,
  output reg           busy,
  output reg  [1:0]    status
);
  localparam [1:0] S_IDLE  = 2'd0;
  localparam [1:0] S_SAMPLE= 2'd1;
  localparam [1:0] S_RAMP  = 2'd2;
  localparam [1:0] S_CHECK = 2'd3;

  reg [1:0] state;

  initial begin
    state    = S_IDLE;
    dac_code = {N{1'b0}};
    sample   = 1'b0;
    dout     = {N{1'b0}};
    valid    = 1'b0;
    busy     = 1'b0;
    status   = `ADC_ST_OK;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= S_IDLE;
      dac_code <= {N{1'b0}};
      sample   <= 1'b0;
      dout     <= {N{1'b0}};
      valid    <= 1'b0;
      busy     <= 1'b0;
      status   <= `ADC_ST_OK;
    end else begin
      case (state)
        S_IDLE: begin
          valid <= 1'b0;
          if (start) begin
            busy     <= 1'b1;
            sample   <= 1'b1;
            dac_code <= {N{1'b0}};
            state    <= S_SAMPLE;
          end
        end
        S_SAMPLE: begin
          sample <= 1'b0;
          state  <= S_RAMP;
        end
        S_RAMP: begin
          state <= S_CHECK;
        end
        S_CHECK: begin
          if (cmp) begin
            dout   <= dac_code;
            valid  <= 1'b1;
            busy   <= 1'b0;
            status <= `ADC_ST_OK;
            state  <= S_IDLE;
          end else if (dac_code == {N{1'b1}}) begin
            dout   <= {N{1'b1}};
            valid  <= 1'b1;
            busy   <= 1'b0;
            status <= `ADC_ST_OVERRANGE;
            state  <= S_IDLE;
          end else begin
            dac_code <= dac_code + 1'b1;
            state    <= S_RAMP;
          end
        end
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule

// N-phase sequencer for the time-interleaved ADC.
//
// This first pass is a sequential round-robin: channel 0 converts, its VALID
// advances the phase, channel 1 converts, ...  That is the functional subset
// the combine/calibration logic needs to be verified; the fully overlapped
// schedule (sample channel i+1 while channel i converts) is a follow-on and
// changes only this module.
`include "adc_pkg.vh"
module ti_phase #(
  parameter integer NCH = 2,
  parameter integer IDX_W = (NCH > 1) ? $clog2(NCH) : 1
)(
  input  wire clk,
  input  wire rst_n,
  input  wire start,
  input  wire [NCH-1:0] ch_valid,
  output reg  [NCH-1:0] phase,     // one-hot, current channel
  output reg  [NCH-1:0] go,        // one-cycle start pulse per channel
  output reg  [NCH-1:0] active,    // same as phase, kept for the analog side
  output reg  [IDX_W-1:0] mux_sel, // current channel index
  output reg  busy,
  output reg  done
);
  localparam integer S_IDLE = 2'd0;
  localparam integer S_RUN  = 2'd1;
  localparam integer S_DONE = 2'd2;

  reg [1:0] state;
  reg [IDX_W-1:0] idx;

  initial begin
    state   = S_IDLE;
    phase   = {NCH{1'b0}};
    go      = {NCH{1'b0}};
    active  = {NCH{1'b0}};
    mux_sel = {IDX_W{1'b0}};
    busy    = 1'b0;
    done    = 1'b0;
    idx     = {IDX_W{1'b0}};
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state   <= S_IDLE;
      phase   <= {NCH{1'b0}};
      go      <= {NCH{1'b0}};
      active  <= {NCH{1'b0}};
      mux_sel <= {IDX_W{1'b0}};
      busy    <= 1'b0;
      done    <= 1'b0;
      idx     <= {IDX_W{1'b0}};
    end else begin
      case (state)
        S_IDLE: begin
          done <= 1'b0;
          if (start) begin
            phase   <= {{(NCH-1){1'b0}}, 1'b1};
            go      <= {{(NCH-1){1'b0}}, 1'b1};
            active  <= {{(NCH-1){1'b0}}, 1'b1};
            mux_sel <= {IDX_W{1'b0}};
            idx     <= {IDX_W{1'b0}};
            busy    <= 1'b1;
            state   <= S_RUN;
          end
        end
        S_RUN: begin
          go <= {NCH{1'b0}};
          if (ch_valid[idx]) begin
            if (idx == NCH-1) begin
              phase  <= {NCH{1'b0}};
              active <= {NCH{1'b0}};
              busy   <= 1'b0;
              done   <= 1'b1;
              state  <= S_DONE;
            end else begin
              idx     <= idx + 1'b1;
              phase   <= ({{(NCH-1){1'b0}}, 1'b1} << (idx + 1'b1));
              active  <= ({{(NCH-1){1'b0}}, 1'b1} << (idx + 1'b1));
              mux_sel <= idx + 1'b1;
              go      <= ({{(NCH-1){1'b0}}, 1'b1} << (idx + 1'b1));
            end
          end
        end
        S_DONE: begin
          done <= 1'b0;
          if (!start)
            state <= S_IDLE;
        end
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule

// Flash ADC digital back end.
//
// A flash converter is a thermometer code plus an encoder.  The comparator
// bank never produces a perfect thermometer word: a late or metastable
// comparator leaves a 1-0-1 "bubble".  This core latches the bank and encodes
// it with the pop-count encoder in rtl/common/therm2bin.v, which is monotone
// in the number of decided comparators, so one bubble costs one code instead
// of an arbitrary jump.  That is the whole bubble-tolerance strategy: no
// separate bubble-scrubbing logic, no extra comparator.
//
// Latency: one cycle to latch the bank, one cycle to register the encoded
// code; dout and status hold until the next conversion.
`include "adc_pkg.vh"
module flash_core #(
  parameter integer N = 6
)(
  input  wire                 clk,
  input  wire                 rst_n,
  input  wire                 start,
  input  wire [(1<<N)-2:0]    cmp_therm,
  output reg  [N-1:0]         dout,
  output reg                  valid,
  output reg                  busy,
  output reg  [1:0]           status
);
  localparam S_IDLE  = 1'b0;
  localparam S_LATCH = 1'b1;

  reg state;
  reg [(1<<N)-2:0] latched;
  wire [N-1:0] bin_w;
  wire [N:0]   count_w;

  therm2bin #(.N(N)) u_enc (
    .therm(latched),
    .bin(bin_w),
    .count(count_w)
  );

  initial begin
    state   = S_IDLE;
    latched = {(1<<N)-1{1'b0}};
    dout    = {N{1'b0}};
    valid   = 1'b0;
    busy    = 1'b0;
    status  = `ADC_ST_OK;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state   <= S_IDLE;
      latched <= {(1<<N)-1{1'b0}};
      dout    <= {N{1'b0}};
      valid   <= 1'b0;
      busy    <= 1'b0;
      status  <= `ADC_ST_OK;
    end else begin
      case (state)
        S_IDLE: begin
          valid <= 1'b0;
          if (start) begin
            busy    <= 1'b1;
            latched <= cmp_therm;
            state   <= S_LATCH;
          end
        end
        S_LATCH: begin
          dout   <= bin_w;
          valid  <= 1'b1;
          busy   <= 1'b0;
          // 2^N-1 comparators cannot tell the top code from an input above
          // VREFH (both give an all-ones thermometer word).  Over-range
          // detection therefore needs one extra comparator at VREFH, which is
          // a front-end change, not an encoder change; status stays OK here.
          status <= `ADC_ST_OK;
          state  <= S_IDLE;
        end
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule

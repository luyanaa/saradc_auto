// 1.5-bit/stage pipeline ADC digital combiner.
//
// The analog pipeline produces, per stage, a 2-bit decision:
//   2'b10 -> d = +1,  2'b01 -> d = 0,  2'b00 -> d = -1
// and the last stage produces a plain 2-bit flash code.  The digital job is
// the redundant-signed-digit recombination and the two-phase clock for the
// switched-capacitor stages.
//
// Derivation of the weights (VREF = full-scale, N = S+1 output bits):
//   vin = 2^-(S-1) r_{S-1} + (VREF/2) * sum_i d_i 2^(-1-i)
//   code = sum_i d_i 2^(S-1-i) + code_flash + (2^S - 2)
// so BIAS is 2^S - 2, not 2^S - 1.  That one-LSB difference is why the
// combiner is derived here rather than copied.
//
// This pass consumes the stage decisions from one aligned vector
// (`stage_bits` + `stage_valid`).  Real pipelines need a skew register per
// stage so that stage i's decision arrives with stage i+1's; that is a
// latency/alignment addition, not a change to the arithmetic below, and is
// left as the documented follow-on.
`include "adc_pkg.vh"
module pipeline_core #(
  parameter integer S  = 7,              // stages; first S-1 are 1.5-bit
  parameter integer N  = S + 1,          // output bits
  parameter integer BIAS = (1 << S) - 2
)(
  input  wire           clk,
  input  wire           rst_n,
  input  wire           start,
  input  wire [2*S-1:0] stage_bits,
  input  wire [S-1:0]   stage_valid,
  output reg  [N-1:0]   dout,
  output reg            valid,
  output reg            busy,
  output reg  [1:0]     status,
  output wire           phi1,
  output wire           phi2
);
  localparam S_IDLE = 2'd0;
  localparam S_CALC = 2'd1;
  localparam S_DONE = 2'd2;

  reg [1:0] state;
  integer i;
  reg [1:0] dig;
  reg signed [16:0] acc;
  reg [1:0] last;

  clk_2ph #(.DEAD(1)) u_phi (
    .clk(clk), .rst_n(rst_n), .enable(1'b1), .phi1(phi1), .phi2(phi2)
  );

  initial begin
    state  = S_IDLE;
    dout   = {N{1'b0}};
    valid  = 1'b0;
    busy   = 1'b0;
    status = `ADC_ST_OK;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state  <= S_IDLE;
      dout   <= {N{1'b0}};
      valid  <= 1'b0;
      busy   <= 1'b0;
      status <= `ADC_ST_OK;
    end else begin
      valid <= 1'b0;
      case (state)
        S_IDLE: begin
          if (start) begin
            busy  <= 1'b1;
            state <= S_CALC;
          end
        end
        S_CALC: begin
          if (&stage_valid) begin
            acc = 17'sd0;
            for (i = 0; i < S-1; i = i + 1) begin
              dig = stage_bits[2*i +: 2];
              if (dig == 2'b10)
                acc = acc + (1 << (S-1-i));
              else if (dig == 2'b00)
                acc = acc - (1 << (S-1-i));
            end
            last = stage_bits[2*(S-1) +: 2];
            acc  = acc + last + BIAS;
            if (acc < 0)
              dout <= {N{1'b0}};
            else if (acc > (1<<N)-1)
              dout <= {N{1'b1}};
            else
              dout <= acc[N-1:0];
            valid  <= 1'b1;
            busy   <= 1'b0;
            status <= `ADC_ST_OK;
            state  <= S_DONE;
          end
        end
        S_DONE: state <= S_IDLE;
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule

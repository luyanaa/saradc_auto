// Non-overlapping two-phase clock generator for switched-capacitor stages.
//
// The phases are enables in the master clock domain, never divided clocks.
// With one dead cycle between phases the SC stage runs at clk/4; if a 2x clock
// is available, tie `clk2x` and the same FSM produces full-rate non-overlap.
// The RTL is honest about that: `phi1`/`phi2` are enables, and the analog
// testbench samples on their rising edges.
module clk_2ph #(
  parameter integer DEAD = 1
)(
  input  wire clk,
  input  wire rst_n,
  input  wire enable,
  output reg  phi1,
  output reg  phi2
);
  reg [1:0] state;

  initial begin
    state = 2'd0;
    phi1  = 1'b0;
    phi2  = 1'b0;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= 2'd0;
      phi1  <= 1'b0;
      phi2  <= 1'b0;
    end else if (enable) begin
      case (state)
        2'd0: begin phi1 <= 1'b1; phi2 <= 1'b0; state <= 2'd1; end
        2'd1: begin phi1 <= 1'b0; phi2 <= 1'b0; state <= 2'd2; end
        2'd2: begin phi1 <= 1'b0; phi2 <= 1'b1; state <= 2'd3; end
        default: begin phi1 <= 1'b0; phi2 <= 1'b0; state <= 2'd0; end
      endcase
    end
  end
endmodule

// Dual-slope integrating ADC digital core.
//
// The core owns the three-phase cycle: auto-zero (reset the integrating
// capacitor), integrate the unknown input for a fixed TINT clocks, then
// de-integrate against the reference until the comparator crosses zero.  The
// de-integration time, counted here, is the conversion result.
//
// The counter is N+2 bits because de-integration may run to 2*TINT before the
// timeout fires.  No analog value enters the core: it sees only the comparator
// bit and drives the three switch strobes.
`include "adc_pkg.vh"
module dual_slope_core #(
  parameter integer N  = 8,
  parameter integer AZ_CYCLES = 4,
  parameter integer TINT = (1 << N)
)(
  input  wire          clk,
  input  wire          rst_n,
  input  wire          start,
  input  wire          cmp,          // 1 when the integrator has crossed zero
  output reg           az,
  output reg           integrate,
  output reg           deintegrate,
  output reg  [N:0]    dout,
  output reg           valid,
  output reg           busy,
  output reg  [1:0]    status
);
  localparam [1:0] S_IDLE  = 2'd0;
  localparam [1:0] S_AZ    = 2'd1;
  localparam [1:0] S_INT   = 2'd2;
  localparam [1:0] S_DEINT = 2'd3;

  reg [1:0] state;
  reg [N+2:0] count;
  integer guard;

  initial begin
    state       = S_IDLE;
    az          = 1'b0;
    integrate   = 1'b0;
    deintegrate = 1'b0;
    count       = {(N+3){1'b0}};
    dout        = {(N+1){1'b0}};
    valid       = 1'b0;
    busy        = 1'b0;
    status      = `ADC_ST_OK;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state       <= S_IDLE;
      az          <= 1'b0;
      integrate   <= 1'b0;
      deintegrate <= 1'b0;
      count       <= {(N+3){1'b0}};
      dout        <= {(N+1){1'b0}};
      valid       <= 1'b0;
      busy        <= 1'b0;
      status      <= `ADC_ST_OK;
    end else begin
      valid <= 1'b0;
      case (state)
        S_IDLE: begin
          if (start) begin
            busy  <= 1'b1;
            az    <= 1'b1;
            count <= {(N+3){1'b0}};
            state <= S_AZ;
          end
        end
        S_AZ: begin
          if (count == AZ_CYCLES-1) begin
            az          <= 1'b0;
            integrate   <= 1'b1;
            count       <= {(N+3){1'b0}};
            state       <= S_INT;
          end else begin
            count <= count + 1'b1;
          end
        end
        S_INT: begin
          if (count == TINT-1) begin
            integrate   <= 1'b0;
            deintegrate <= 1'b1;
            count       <= {(N+3){1'b0}};
            state       <= S_DEINT;
          end else begin
            count <= count + 1'b1;
          end
        end
        S_DEINT: begin
          if (cmp) begin
            dout        <= count[N:0];
            valid       <= 1'b1;
            busy        <= 1'b0;
            status      <= `ADC_ST_OK;
            deintegrate <= 1'b0;
            state       <= S_IDLE;
          end else if (count == 2*TINT-1) begin
            dout        <= {(N+1){1'b1}};
            valid       <= 1'b1;
            busy        <= 1'b0;
            status      <= `ADC_ST_TIMEOUT;
            deintegrate <= 1'b0;
            state       <= S_IDLE;
          end else begin
            count <= count + 1'b1;
          end
        end
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule

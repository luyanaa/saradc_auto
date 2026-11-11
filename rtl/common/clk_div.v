// Clock-enable divider.  Keeps a single clock domain: consumers must gate on
// `en`, not on a divided clock.  This is the form that survives every PDK's
// CTS without creating a new clock tree.
`include "adc_pkg.vh"
module clk_div #(
  parameter integer DIV = 2
)(
  input  wire clk,
  input  wire rst_n,
  output reg  en
);
  localparam integer CW = (DIV > 1) ? $clog2(DIV) : 1;
  reg [CW-1:0] cnt;

  initial begin
    cnt = {CW{1'b0}};
    en  = 1'b0;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cnt <= {CW{1'b0}};
      en  <= 1'b0;
    end else if (DIV <= 1) begin
      en <= 1'b1;
    end else if (cnt == DIV-1) begin
      cnt <= {CW{1'b0}};
      en  <= 1'b1;
    end else begin
      cnt <= cnt + 1'b1;
      en  <= 1'b0;
    end
  end
endmodule

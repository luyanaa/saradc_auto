// Asynchronous assert, synchronous de-assert reset synchroniser.
// One per clock domain.  Portable Verilog-2005.
module reset_sync (
  input  wire clk,
  input  wire rst_async_n,
  output reg  rst_n
);
  reg meta;
  always @(posedge clk or negedge rst_async_n) begin
    if (!rst_async_n) begin
      meta  <= 1'b0;
      rst_n <= 1'b0;
    end else begin
      meta  <= 1'b1;
      rst_n <= meta;
    end
  end
endmodule

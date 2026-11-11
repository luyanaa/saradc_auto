// Thermometer -> binary, with bubble tolerance by construction.
//
// A flash comparator bank never produces a perfect thermometer code: a
// comparator can be late, metastable or offset the wrong way, producing a
// 1-0-1 "bubble".  A priority encoder turns that into a sparkle code.  The
// pop-count encoder below is monotone in the number of decided comparators and
// a single bubble costs one code instead of an arbitrary jump, which is the
// behaviour the architecture study asks for.  The adder tree is the only cost
// and is bounded by 2^N for the N used by flash (N <= 8).
module therm2bin #(
  parameter integer N = 8
)(
  input  wire [(1<<N)-2:0] therm,
  output reg  [N-1:0]      bin,
  output reg  [N:0]        count
);
  integer i;
  reg [N:0] acc;

  always @* begin
    acc = {(N+1){1'b0}};
    for (i = 0; i < (1<<N)-1; i = i + 1)
      acc = acc + { {(N){1'b0}}, therm[i] };
    count = acc;
    bin   = acc[N-1:0];
  end
endmodule

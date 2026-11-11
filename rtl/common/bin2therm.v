// Binary -> thermometer.  therm[i] = (bin > i), i = 0 .. 2^N-2.
// Used by flash/pipeline calibration benches and by the RTL self-checks; it is
// deliberately the exact inverse of therm2bin's pop-count for legal codes.
module bin2therm #(
  parameter integer N = 8
)(
  input  wire [N-1:0]       bin,
  output wire [(1<<N)-2:0]  therm
);
  genvar i;
  generate
    for (i = 0; i < (1<<N)-1; i = i + 1) begin : g_bit
      assign therm[i] = (bin > i);
    end
  endgenerate
endmodule

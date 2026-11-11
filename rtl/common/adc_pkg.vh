// -----------------------------------------------------------------------------
// adc_pkg.vh -- technology-independent defaults for the ADC RTL family.
//
// Verilog-2005 only.  Nothing in this file knows about a PDK, a standard-cell
// library or a transistor.  The same sources must harden on sky130, ihp-sg13g2,
// gf180mcu and ics55 without edits; per-PDK differences live in the synthesis
// and layout configuration, never here.
//
// Verilog-2005 has no file-scope functions: helpers are defined inside the
// modules that need them, or expressed with constant macros here.  `$clog2` is
// used only where the tool set (iverilog/Yosys/Verilator) supports it.
// -----------------------------------------------------------------------------
`ifndef ADC_PKG_VH
`define ADC_PKG_VH

// Bump when the digital interface below changes; the RTL testbenches and the
// Python golden models assert on it.
`define ADC_RTL_VERSION 32'h0001_0000

// status[1:0], returned with every sample.
`define ADC_ST_OK        2'b00
`define ADC_ST_OVERRANGE 2'b01
`define ADC_ST_TIMEOUT   2'b10
`define ADC_ST_NOTREADY  2'b11

`endif

// Analog-flow blackbox for the synchronous SAR sequencer.
//
// The analog hierarchy (SARADC_net.v) must instantiate only the analog core;
// the sequencer gates come from the separately-synthesized digital netlist
// (sar_logic_wreset_net.v), which P&R links into the same design.  So during
// analog synthesis sar_logic_wreset is a blackbox: hierarchy -check is happy,
// write_verilog emits a bare instance, and the real implementation is never
// duplicated into SARADC_net.v.
//
// Deliberately NOT parameterized: OpenROAD's read_verilog cannot parse
// parameter overrides (`#(.NBITS(..))`), and the hardened part is fixed at
// NBITS=5 (the floorplan hardcodes it).  The real module in
// rtl/sar_logic_wreset.v keeps its NBITS parameter for digital synthesis and
// simulation.
//
// Digital synthesis (syn/tcl/synthesis.tcl) reads rtl/sar_logic_wreset.v and
// never this stub, so the two flows do not collide.

(* blackbox *)
module sar_logic_wreset (
  input CLK,
  input RST,
  input GO,
  output VALID,
  output [4:0] RESULTP,
  output [4:0] RESULTN,
  output SAMPLE,
  input CMP
);
endmodule

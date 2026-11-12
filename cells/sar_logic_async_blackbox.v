// Analog-flow blackbox for the asynchronous SAR sequencer.
//
// Same role as sar_logic_wreset_blackbox.v: the analog netlist references the
// sequencer but the gates are synthesized separately (sar_logic_async_net.v)
// and linked at P&R.  rtl/sar_logic_async.v is the synthesizable
// implementation the digital flow reads; this stub is only for the analog
// hierarchy read in syn/tcl/synthesis_analog.tcl.
//
// Not parameterized on purpose: OpenROAD's read_verilog cannot parse parameter
// overrides, and the hardened part is NBITS=5.

(* blackbox *)
module sar_logic_async (
  input RST,
  input GO,
  input CMP,
  input RDY,
  output SAMPLE,
  output VALID,
  output CMPSTART,
  output [4:0] RESULTP,
  output [4:0] RESULTN
);
endmodule

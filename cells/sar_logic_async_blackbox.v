// Blackbox declaration of the asynchronous SAR sequencer for synthesis.
//
// sar_logic_async is a level-sensitive state machine (its `always` triggers
// on RDY and GO levels, not a clock edge), which yosys cannot map to
// flip-flops.  It is therefore a hard macro to the digital flow, exactly
// like SARADC_COMP is to the analog flow: this stub gives it ports and the
// `blackbox` attribute, and the behavioural model in rtl/sar_logic_async.v
// is what simulation and the testbench use.  The synthesizable, latch-based
// implementation of the same FSM is future work.
//
// Kept in a separate file so a simulation can read
// `rtl/sar_logic_async.v` (the model) instead of this stub, while synthesis
// reads only this one.

(* blackbox *)
module sar_logic_async #(
    parameter NBITS = 5
) (
    RST, GO, CMP, RDY, SAMPLE, VALID, CMPSTART, RESULTP, RESULTN
);
    input RST, GO, CMP, RDY;
    output SAMPLE, VALID, CMPSTART;
    output [NBITS-1:0] RESULTP, RESULTN;
endmodule

* cdac_weight_tb.sp -- per-bit weight measurement of the generated CDAC array.
*
* The analytic bound in gen/analog/array_impact.py claims the switch
* off-capacitance Cp attenuates every bit by C_i/(C_i+Cp): bit 0 keeps only
* 25.7% of its intended weight, bit 7 keeps 97.8%.  The AC check in the same
* file reports a flat 0.451 for every bit and so cannot be the same mechanism.
*
* This bench decides which is right.  It builds the real cdac_binary array
* (SARADC_CAP_UNIT + SARADC_SW_TG leaves from macro_gen), switches ONE bit's
* bottom plates from VREFL to VREFH while every other bit stays at VREFL, and
* integrates the charge delivered to the top plate.  The bit weight is that
* charge over the VREFH-VREFL step; the parasitic shows up as a per-bit
* deviation from the 2^i binary ladder.
*
* Two probe styles are compared:
*   stiff   -- top plate held by a voltage source (what array_impact does):
*              measures C(V) derating, NOT the switch off-cap bound.
*   float   -- top plate left floating with a small hold capacitor, so the
*              charge actually divides onto the bit node's parasitic.  This is
*              the condition the bound models.
*
*   ngspice -b cdac_weight_tb.sp
* expects: with a floating top plate the per-bit weight error follows
*          C_i/(C_i+Cp); with a stiff top plate it does not.
*
.options tnom=25 temp={temp} gminsteps=0 warn=1 klu=1 method=gear trtol=7
.inc {CELLS}/sg13g2f.ckt
.inc {DECK}

.PARAM supply={supply} vrefh=0.8 vrefl=0.4 vcm=0.6 nbits=8
Vdd vdd 0 DC {supply}
Vss vss 0 DC 0
Vrefh vrefh 0 DC {vrefh}
Vrefl vrefl 0 DC {vrefl}
Vnw vnw 0 DC {supply}
Vpw vpw 0 DC 0

* --- the generated leaves (emitted by `run.py analog --pdk <pdk>`) ---------
* SARADC_CAP_UNIT VSH FL VNW VPW and SARADC_SW_TG S SB Z1 Z2 VDD VSS
* are pulled in via {LEAVES} -- see run.sh.

* --- bit under test: 2^i unit caps, top plate on VSH ----------------------
* This deck parameterises on BIT via .PARAM; one instance per unit cap is
* emitted by the runner for the chosen bit.  Bottom plates n_bit.
* Switched high when sw_bit is driven to supply.

* The rest of the array (all other bits + dummy) ties bottom plates to VREFL.
* Represented as a single lump of (2^N-1-2^i) unit caps on VREFL for charge
* conservation, plus the dummy.

* driver for the switched bit
Vsw sw_bit 0 PULSE({vrefl} {vrefh} 20n 1n 1n 40n 100n)
Vswb sw_bitb 0 PULSE({vrefh} {vrefl} 20n 1n 1n 40n 100n)

* top-plate probe: zero-V source integrates the delivered charge
Vprobe vtop vtop_sense DC 0
* small hold cap only in the FLOAT case (runner comments it out for STIFF)
CHOLD vtop 0 {chold_f}

.tran 5n 100n uic
.print tran v(vtop) i(Vprobe) v(sw_bit)
.meas tran q_bit INTEG i(Vprobe) FROM=20n TO=70n
.meas tran dv_top MAX v(vtop) FROM=20n TO=70n
.end

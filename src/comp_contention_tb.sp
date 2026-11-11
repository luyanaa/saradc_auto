* comp_contention_tb.sp -- transient probe of the taped-out SARADC_COMP's
* shared-driver latch nodes OUTPp / OUTNp.
*
* Structure under test (rtl/comp.v): the cross-coupled OAI211X16 pair and a
* pair of NAND2X2 "reinforcing" cells all drive the same two nodes.  OUTPp is
* driven by vp_cmp.ZN AND n2p.ZN; OUTNp by vn_cmp.ZN AND p2n.ZN.  When the OAI
* and the NAND ever disagree they fight through the short -- this deck measures
* the contention current and the regeneration time so the 4.8-sim vs 3.8-meas
* ENOB gap can be attributed to a mechanism instead of assumed.
*
* The OAI211X16 output feeds back through C0 = the *other* pre-node, so during
* CMP low the latch is held; when CMP rises the NANDs also drive, and any skew
* between the OAI and NAND decisions shows up as a VDD->VSS shoot-through.
*
* The SG13G2 lv/hv MOS models are Verilog-A (PSP 103.6), resolved through the
* OSDI binaries.  Those are loaded by the PDK's own .spiceinit, so run with
* SPICEINIT pointing at the IHP ngspice init and PDK_ROOT/PDK exported:
*
*   export PDK_ROOT=.../ihp-sg13  PDK=ihp-sg13g2
*   SPICEINIT=$PDK_ROOT/$PDK/libs.tech/ngspice/.spiceinit \
*       ngspice -b comp_contention_tb.sp
*
* probes: v(OUTPp) v(OUTNp) i(Vdd_sense) -- regeneration + shoot-through.

.options tnom=25 temp={temp} gminsteps=0 warn=1 klu=1 acct=1 method=gear trtol=1

.lib {PDK}/libs.tech/ngspice/models/cornerMOSlv.lib {corner}
.lib {PDK}/libs.tech/ngspice/models/cornerMOShv.lib {corner}
.inc {PDK}/libs.tech/ngspice/models/diodes.lib
.inc {CELLS}/sg13g2f.ckt

* --- supplies -----------------------------------------------------------
.PARAM supply=1.2
Vdd vdd 0 DC {supply}
Vss vss 0 DC 0

* --- stimulus -----------------------------------------------------------
* CMP: the compare-enable.  Hold low (latch) then raise to start regeneration.
Vcmp cmp 0 PULSE(0 {supply} 10n 100p 100p 30n 200n)
Vcmpb cmpb 0 PULSE({supply} 0 10n 100p 100p 30n 200n)
* CK is unused by the latch core but part of the port contract
Vck ck 0 DC 0
* Differential input: a small overdrive, then a near-threshold overdrive to
* expose the metastable/contended region the ENOB is sensitive to.
.PARAM vin_diff=0.01
Vvp vp 0 DC {supply/2 + vin_diff}
Vvn vn 0 DC {supply/2}

* --- the comparator under test ------------------------------------------
* SARADC_COMP(vdd vss CMP CMPB CK VP VN OUTN OUTP) per rtl/comp.v port order:
*   inout VDD VSS; input CMP CMPB CK; inout VP VN; output OUTN OUTP.
* Vdd_sense (0 V) sits in the supply return so its current is the total
* shoot-through drawn by the whole cell.
Xcomp vdd_int vss cmp cmpb ck vp vn outn outp SARADC_COMP
Vdd_sense vdd vdd_int DC 0

* --- SARADC_COMP built from the same cell primitives as cell_def.v --------
* (mirrors rtl/comp.v + rtl/cell_def.v: OAI211X16 = 4x OAI211D4, NAND2X2 = ND2D2)
.SUBCKT SARADC_COMP VDD VSS CMP CMPB CK VP VN OUTN OUTP
* pre-signal nodes (the ones that are double-driven)
* subckt pin order is `a1 a2 b c vdd vss zn`; from rtl/cell_def.v:
*   vp_cmp: A0=CMPB A1=VP B0=CMP C0=OUTPp  ZN=OUTNp   (x4 OAI211D4)
Xvp0 CMPB VP CMP OUTPp VDD VSS OUTNp sg13g2f_OAI211D4
Xvp1 CMPB VP CMP OUTPp VDD VSS OUTNp sg13g2f_OAI211D4
Xvp2 CMPB VP CMP OUTPp VDD VSS OUTNp sg13g2f_OAI211D4
Xvp3 CMPB VP CMP OUTPp VDD VSS OUTNp sg13g2f_OAI211D4
*   vn_cmp: A0=CMPB A1=VN B0=CMP C0=OUTNp  ZN=OUTPp   (x4 OAI211D4)
Xvn0 CMPB VN CMP OUTNp VDD VSS OUTPp sg13g2f_OAI211D4
Xvn1 CMPB VN CMP OUTNp VDD VSS OUTPp sg13g2f_OAI211D4
Xvn2 CMPB VN CMP OUTNp VDD VSS OUTPp sg13g2f_OAI211D4
Xvn3 CMPB VN CMP OUTNp VDD VSS OUTPp sg13g2f_OAI211D4
*   reinforcing NAND pair (ND2D2 order: a1 a2 vdd vss zn) -- shares the nodes
*   n2p: A0=OUTNp A1=CMP -> OUTPp ; p2n: A0=OUTPp A1=CMP -> OUTNp
Xn2p OUTNp CMP VDD VSS OUTPp sg13g2f_ND2D2
Xp2n OUTPp CMP VDD VSS OUTNp sg13g2f_ND2D2
*   output buffers (BUFFDx order: i vdd vss z)
Xbn0 OUTNp VDD VSS OUTNpb sg13g2f_BUFFD0
Xbn1 OUTNpb VDD VSS OUTN sg13g2f_BUFFD2
Xbp0 OUTPp VDD VSS OUTPpb sg13g2f_BUFFD0
Xbp1 OUTPpb VDD VSS OUTP sg13g2f_BUFFD2
.ENDS

* --- measurements -------------------------------------------------------
* regeneration: |OUTPp - OUTNp| crossing half supply after CMP rises
* contention:   total supply current i(Vdd_sense) during the transition
.tran 20p 120n uic
.print tran v(Xcomp.OUTPp) v(Xcomp.OUTNp)
+     v(outp) v(outn) i(Vdd_sense) v(cmp)

* CMP rises at 10n; TRIG is on its first rising half-supply crossing, TARG is
* the first OUTPp crossing after it.  CROSS=1 makes the pairing explicit --
* without it ngspice picks the TARG crossing nearest in time regardless of
* direction and reports a negative regen time.
.meas tran t_regen TRIG v(cmp) VAL='supply/2' RISE=1 TD=0
+     TARG v(Xcomp.OUTPp) VAL='supply*0.8' RISE=1 CROSS=1
.meas tran i_peak MAX i(Vdd_sense) FROM=9n TO=40n
.meas tran v_min  MIN v(Xcomp.OUTPp) FROM=9n TO=60n
.meas tran v_max  MAX v(Xcomp.OUTPp) FROM=9n TO=60n
.end

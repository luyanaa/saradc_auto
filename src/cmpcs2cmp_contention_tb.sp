* cmpcs2cmp_contention_tb.sp -- transient probe of the input-port back-drive
* in saradc_cmpcs2cmp (rtl/sar_logic_buf.v lines 80-101), the cell that shapes
* the taped-out SAR's compare pulse CCMP.
*
* Suspicious structure: INVX6 ln1 inverts I -> IL; INVX6 ln2 inverts IL -> back
* onto the *input* port I.  The latch sits directly on the module's input pin,
* so the upstream driver (saradc_cmpbegin's CCMP output, a BUFFD16) must
* overpower ln2 on every transition.  This bench drives I through a realistic
* source and measures the fight: the input node voltage vs the ln2 contention
* current, and the resulting CCMP pulse width.
*
*   SPICEINIT=$PDK/libs.tech/ngspice/.spiceinit ngspice -b cmpcs2cmp_contention_tb.sp

.options tnom=25 temp={temp} gminsteps=0 warn=1 klu=1 acct=1 method=gear trtol=1

.lib {PDK}/libs.tech/ngspice/models/cornerMOSlv.lib {corner}
.lib {PDK}/libs.tech/ngspice/models/cornerMOShv.lib {corner}
.inc {PDK}/libs.tech/ngspice/models/diodes.lib
.inc {CELLS}/sg13g2f.ckt

.PARAM supply=1.2
Vdd vdd 0 DC {supply}
Vss vss 0 DC 0

* --- upstream driver -----------------------------------------------------
* saradc_cmpbegin drives CCMP through BUFFD8+BUFFD16 (NOR3 -> buf8 -> buf16).
* Model the driver as a BUFFD16 so the source impedance is realistic: the
* input port I is NOT an ideal voltage source -- the fight with ln2 is visible.
Vdrv drv_in 0 PULSE(0 {supply} 20n 200p 200p 25n 200n)
Xdrv drv_in vdd vss i_src sg13g2f_BUFFD16
* i_src is what the upstream cell presents to the saradc_cmpcs2cmp input I.

* --- device under test ---------------------------------------------------
* saradc_cmpcs2cmp: I, Z, ZN (VDD/VSS supplies)
Xdut vdd vss i_src z_out zn_out SARADC_CMPCS2CMP

* --- SARADC_CMPCS2CMP mirrored from sar_logic_buf.v ----------------------
*   INVX6 -> INVD6, DEL4X4 -> DEL4+BUFFD4, AND2X4 -> AN2D4,
*   BUFFX4 -> BUFFD4, BUFFX16 -> BUFFD16   (subckt order: i/a1 a2 vdd vss z/zn)
.SUBCKT SARADC_CMPCS2CMP VDD VSS I Z ZN
*   ln1: I -> IL ;  ln2: IL -> I  (output back onto the input port)
Xln1 I VDD VSS IL sg13g2f_INVD6
Xln2 IL VDD VSS I sg13g2f_INVD6
*   pulse: ID = delay(I) ; IP = I AND ID
Xd4  I VDD VSS ID1 sg13g2f_DEL4
Xd4b ID1 VDD VSS ID sg13g2f_BUFFD4
Xand I ID VDD VSS IP sg13g2f_AN2D4
*   output buffers
Xlb4  IL VDD VSS ILBUF sg13g2f_BUFFD4
Xlb16 ILBUF VDD VSS ZN sg13g2f_BUFFD16
Xdb16 IP VDD VSS Z sg13g2f_BUFFD16
.ENDS

* --- measurements --------------------------------------------------------
* The input-port fight: how far does ln2 pull i_src against the upstream
* buffer?  Watch i_src dip/overshoot and the CCMP (z_out) pulse width.
.tran 20p 120n uic
.print tran v(i_src) v(Xdut.IL) v(z_out) v(zn_out) v(drv_in)

* The input-port fight: ln2 drives back onto i_src.  `dip` is how far below
* ground it is pulled, `oshot` how far past the rail; both capture the
* driver-on-driver contention the upstream cell must absorb.
.meas tran dip   MIN v(i_src) FROM=20n TO=35n
.meas tran oshot MAX v(i_src) FROM=20n TO=35n
* shaped CCMP pulse: z_out is a pulse (0 -> supply -> 0).  The ln2 contention
* pinches the leading edge so the pulse is ragged and the peak itself exceeds
* the rail; report peak and full-width-at-half-max rather than a TRIG/TARG
* pair the glitching can corrupt.
.meas tran zpeak MAX v(z_out) FROM=15n TO=80n
* FWHM: parametric WHEN picks the LAST time z_out is above half supply in the
* window, and the crossing-time measures bound the pulse.
.meas tran z_rise WHEN v(z_out)='supply/2' RISE=1 TD=15n
.meas tran z_fall WHEN v(z_out)='supply/2' FALL=1 TD=15n
.end

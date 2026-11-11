# Suspicious-node transient analysis

Transient probes of the two "one output drives another output" structures the
audit flagged, run at transistor level on the real `sg13g2f` cell subcircuits
(PSP 103.6 via OSDI, `mos_tt`, 25 °C, VDD = 1.2 V).  The goal is to attribute
the measured 3.8-bit ENOB against the 4.8-bit schematic simulation to a concrete
mechanism instead of assuming one.

Run them with the PDK's own spice init so the Verilog-A models resolve:

```
export PDK_ROOT=.../ihp-sg13  PDK=ihp-sg13g2
SPICEINIT=$PDK_ROOT/$PDK/libs.tech/ngspice/.spiceinit \
    ngspice -b src/comp_contention_tb.sp
```

(`cap_cmomi`/`cap_cmomf` OSDI load errors are harmless; no capacitors are used.)

## 1. `comp.v` — the comparator latch (`SARADC_COMP`)

The cross-coupled `OAI211X16` pair and a pair of `NAND2X2` "reinforcing" cells
all drive the same two pre-nodes `OUTPp`/`OUTNp`.  When the OAI and the NAND
disagree they fight through the short.  Measured on `src/comp_contention_tb.sp`
(10 mV input overdrive, CMP rising at 10 ns):

| metric | with NAND pair | OAI pair only |
|---|---|---|
| peak supply shoot-through `i_peak` | 745 µA | 542 µA |
| `OUTPp` deepest excursion `v_min` | 0.81 V | 0.95 V |
| `OUTPp` settled `v_max` | 1.22 V | 1.21 V |
| regeneration | resolves at the edge | resolves at the edge |

The reinforcing NANDs add ~200 µA of extra shoot-through current and pull the
contested node ~140 mV deeper toward mid-rail during the transition.  At this
overdrive the latch still resolves, but the contention is real, dissipates
energy on every comparison, and its depth is a strong function of input
overdrive and PVT corner.  Near the comparator's metastable window the extra
driver skew shrinks the overdrive margin — a plausible contributor to the
sim-vs-measurement gap rather than a clean reinforcement.

Note: the deck rebuilds `SARADC_COMP` from the same primitives as
`rtl/cell_def.v` because `sg13g2f` ships `.subckt` (not Verilog).  Getting the
pin order right matters — `sg13g2f_OAI211D4` is `a1 a2 b c vdd vss zn`; wiring
`zn` first produced a 1.2 A false short before this was corrected.

## 2. `sar_logic_buf.v` — `saradc_cmpcs2cmp` (the CCMP pulse shaper)

`ln1` (INVX6) inverts `I`→`IL`; `ln2` (INVX6) inverts `IL` back onto the
module's **input** port `I`.  The latch sits on the input pin itself, so the
upstream `saradc_cmpbegin` driver must overpower `ln2` on every CCMP edge.
Bench: `src/cmpcs2cmp_contention_tb.sp` drives `I` through a realistic
`BUFFD16` source (so the fight is visible, not hidden behind an ideal voltage
source).  Measured:

| metric | value | meaning |
|---|---|---|
| input-node `dip` | **−0.016 V** | `ln2` pulls the input pin *below ground* |
| input-node `oshot` | 1.201 V | and overshoots to the full rail on the other edge |

A 16 mV below-ground excursion on a driven logic input is the unambiguous
signature of the back-driving latch: `ln2` is strong enough to push the pin
outside the supply range, not just slow its edge.  That below-ground kick
couples charge back into whatever drives CCMP and is a genuine substrate/
supply-noise injection at every compare, not merely a slow transition.

The sibling `saradc_sh2sbuf` (CPRE/CHOLD) puts the same latch *after* an input
buffer, so it does not back-drive its input port and is the safer structure.

## Relation to the 4.8 → 3.8 ENOB gap

The schematic ENOB of 4.8 was computed on a *correctly resolving* comparator.
These probes show the comparator's extra NAND drivers and the CCMP pulse shaper
both inject contention current at exactly the moment the SAR is making its
decision — noise and timing uncertainty the idealised schematic run does not
see.  They are second-order contributors next to the dominant causes already
identified (DAC routing parasitics, undecoupled board references, unit-cap
mismatch), and quantifying them requires the post-layout netlist that remains
the open task.

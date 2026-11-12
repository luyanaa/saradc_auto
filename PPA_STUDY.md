# PPA study — 5-bit SAR ADC (MOM variant)

Status: analysis + ranked plan.  Every number below is read from a checked-in
artifact or produced by the command named next to it; estimates are marked
*est.* and carry the assumption that would falsify them.

Baseline artifacts used:

| artifact | what it is |
|---|---|
| `pnr/outputs/dbg_post_dpl.def` | detailed-placed DEF (MOM build) → instance list + coordinates |
| `pnr/tcl/saradc_pos.tcl` | CDAC unit placement / circle algorithm |
| `cells/SARADC_CAP_MOM.{lef,ckt}` | cap leaf size + measured PEX value |
| `cells/SARADC_CELL_INVX0_ASSW.{lef,ckt}` | switch cell size + device W/L |
| `gen/outputs/char/char_ihp.json` | measured leaf Ron / comparator delay / MOSCAP C(V) |
| `gen/outputs/closure/ihp/switch_tg/pex.spice` | per-TG node parasitics (min-size leaf) |
| `settings.mk`, `pnr/FPlanFinal.size`, `pnr_mom21.log` | die/core dimensions |
| `CLAUDE.md` (sim section) | measured chip ENOB 3.8 vs schematic 4.8 |

Reproduce the area census:

```sh
python3 - <<'PY'
# parse pnr/outputs/dbg_post_dpl.def COMPONENTS + cells/*.lef SIZE
# (script in the session log; prints per-master count/area and CDAC pitch)
PY
```

---

## 1. Baseline

### 1.1 Area

| block | extent (um) | area (um^2) | share |
|---|---|---|---|
| die | 566.44 x 324.36 | 183,730 | 100 % |
| analog core | 433.56 x 287.64 | 124,711 | 68 % |
| digital core | 95.88 x 287.64 | 27,579 | 15 % |
| rings/margins | — | 31,440 | 17 % |

Analog placed instances: **18,074 cells / 80,288 um^2 nominal**

| item | area (um^2) | % of analog |
|---|---|---|
| `SARADC_FILL1_NOPOWER` (fill) | 29,508 | 37 % |
| `SARADC_CELL_INVX0_ASSW` (switch leaves) | 22,660 | 28 % |
| `SARADC_FILLTIE2` (tap/fill) | 11,594 | 14 % |
| `SARADC_CAP_MOM` (cap leaves) | 5,608 | 7 % |
| buffers / delay / inv / OAI / ties | 10,917 | 14 % |

Digital placed instances: **234 cells / 5,067 um^2 → 18.4 % of the digital core**

| item | area (um^2) |
|---|---|
| `sg13g2f_TAPCELL` x164 | 2,388 |
| `sg13g2f_DFQD1` x17 | 1,486 |
| all combinational logic (36 cells) | 1,193 |

Two facts stand out:

1. **The chip is 68 % analog, 15 % digital, and the digital is 18 % full.**
   The digital sea is 234 cells (17 flops). Any "digital PPA" work is noise;
   the die is an analog cap array with a digital corner.
2. **Half of the placed analog area is fill/tap, and the CDAC array is
   mostly empty.** The array spans 388.1 x 273.0 um (105,951 um^2) but its
   devices occupy 28,068 um^2 (26.5 %).

### 1.2 CDAC array decomposition

The array is 2 blocks (H and L), each 10 units wide x 6 units tall = 60
positions; 32 real + 28 dummy per block (two sides of the differential pair).
Measured unit pitch: **38.80 um (x) x 21.63 um (y) = 839 um^2 per unit**.

Where that 839 um^2 goes:

| part | size (um) | area (um^2) |
|---|---|---|
| cap bank (`SARADC_CAP_MOM` x6, 3x2) | 5.55 x 8.42 | 46.7 |
| switch row (18x `INVX0_ASSW` in ONE row) | 37.06 x 6.12 | 226.8 |
| taps / fill inside the unit | — | ~565 |
| **unit footprint** | **38.80 x 21.63** | **839** |

`pnr/tcl/saradc_pos.tcl:379` (`totalsizex = 6*3*swsizex + ...`) is why the unit
is 38.8 um wide: the three switch groups (`vi2cap`, `cap2vouth`, `cap2voutl`)
are placed **side by side in a single standard-cell row**. The cap bank above
them is only 5.55 um wide. The unit is therefore ~3.6x wider than its
contents, and the leftover is filled with `FILL1_NOPOWER` — that is the 29.5 k
um^2 of fill.

**47 % of the array footprint is the dummy ring**: 56 of 120 positions
(28 per block) are `SARADC_CDAC_DUMMY`, and each dummy is a *full* CDAC unit
(3 switches = 18 `INVX0_ASSW` + 6 caps) with its switches tied off
(`rtl/cdac_dummy.v`). Device area spent on dummies: 56 x 233.9 = 13,098 um^2;
footprint: 56 x 839 = 47,006 um^2.

### 1.3 Speed

| quantity | value | source |
|---|---|---|
| clock | 10 MHz | `CLAUDE.md`, `syn/tcl/SARADC.sdc.tcl` (`create_clock -period 10.0`) |
| clocks per conversion | 7 (sample + 6 decisions) | `src/adc_char.py`, `CLAUDE.md` |
| throughput | **1.43 MS/s** | 10 MHz / 7 |
| bit period | 100 ns | 1 / 10 MHz |
| comparator delay | 0.159 ns | `gen/outputs/char/char_ihp.json` (measured) |
| CDAC unit | 22.55 fF = 6 x 3.758 fF | `cells/SARADC_CAP_MOM.ckt` |
| total cap / side | 699 fF (1.40 pF differential) | 31 x 22.55 fF |
| TG Ron | ~11 kOhm *est.* | char min-size TG 4–27 kOhm scaled by W (2.4x) |
| settle / bit (ln 64) | ~1.0 ns *est.* | 11 kOhm x 22.55 fF x 4.16 |

Note the settling time constant is bit-independent: bit *i* has 2^i units in
parallel, so R shrinks as fast as C grows. 6 decisions therefore need
**~6.3 ns** of analog time (plus 1 ns of comparator), against a 700 ns
conversion budget: the current design point runs **~100x slower than the
analog can settle**.

### 1.4 Power

**There is no power number anywhere in this repo.** The bench saves node
voltages only (`src/adc_char.py:107` `.save V(...)`), and `pnr/tcl/pnr.tcl`
never calls `report_power`. Step 0 of any power work is therefore to measure.

Bounding terms (to be replaced by measurement):

| term | estimate | basis |
|---|---|---|
| CDAC switching | 0.45 pJ/conv → **0.6 uW** @ 1.43 MS/s | 0.5 x 1.4 pF x (0.8 V)^2 |
| digital dynamic | **0.7 uW** @ 10 MHz | 234 cells ≈ 470 fF, alpha 0.1, 1.2 V |
| comparator (OAI211X16 latch) | **unknown — likely dominant** | ratioed latch; crowbar current during every decision; needs simulation |

The latch in `rtl/comp.v` is an `OAI211X16` cross-coupled pair driven by
CMP/CMPB. It is a ratioed static structure: while the regeneration node is
resolving, both the pull-up and pull-down paths conduct. At 1.43 MS/s the
switching terms above are sub-uW, so if the comparator draws even 20 uA the
chip is comparator-dominated. This is the first thing to measure.

### 1.5 Quality (the PPA axis that matters most for an ADC)

- Schematic (ideal): ENOB 4.8, weights 1.000/1.998/3.999/8.001/15.992
  (`CLAUDE.md`).
- Measured chip: **ENOB 3.8**, bit weights −8 %…+7 %, INL ~0.5 LSB rms
  (`CLAUDE.md`).
- MOM behavioral: `--cv 0` → ENOB 7.54 / INL 0.2 LSB on an 8-bit model, vs
  ENOB 2.53 / INL 37 LSB with MOSCAP `C(V)` derating.

A 5-bit converter that measures 3.8 ENOB is losing a full bit. That is worth
more than any 20 % area reduction, so it belongs in the PPA plan.

---

## 2. What actually limits what

### 2.1 The unit cap is parasitic-driven, not noise- or mismatch-driven

| constraint | required C (5-bit) | design C | margin |
|---|---|---|---|
| kT/C (`12kT 2^2N / Vfs^2`, Vfs = 0.8 V) | 0.08 fF | 699 fF | ~8800x |
| Pelgrom mismatch (DNL 0.5 LSB, MOM ac 0.2 %·um *est.*) | ~1 fF | 699 fF | ~700x |
| **bottom-plate / switch off-capacitance** | must be << Cu | **~2 fF *est.* vs 22.5 fF** | **~9 %** |

The cap is 20–9000x larger than noise and matching require. What sets it is
the switch's off-capacitance at the LSB bottom plate, and that is also the
most plausible source of the measured −8 % LSB weight:

- one `INVX0_ASSW` pass device pair (Wn 0.36 / Wp 0.60) is ~2.4x the
  characterised min-size TG, whose signal-node PEX is 0.37 fF
  (`gen/outputs/closure/ihp/switch_tg/pex.spice`);
- the switch is built from 2 TGs in parallel (`rtl/sw.v`), so the LSB bottom
  plate sees ~2 fF *est.* of switch capacitance — 9 % of 22.5 fF, i.e. the
  same order as the measured bit-weight error.

*Falsification test:* PEX one `SARADC_CELL_INVX0_ASSW` and one unit
(6 caps + 3 switch groups) and extract the bottom-plate parasitic. If it is
<< 2 fF, this thesis is wrong and the weights come from routing instead.

### 2.2 The unit footprint is a placement artifact, not a device requirement

| content | area (um^2) | % of unit |
|---|---|---|
| devices (6 caps + 18 switch leaves) | 233.9 | 28 % |
| switch-row width (3 groups side by side) | 226.8 | 27 % |
| fill/tap inside the unit | ~565 | 67 % (overlaps rows above) |

The single-row switch placement is what forces 38.8 um pitch; tap cells are
inserted every unit (`insert_fill`, `saradc_pos.tcl:326`), which is another
~87 um^2 per unit.

### 2.3 Speed is nowhere near the analog limit

At 10 MHz clock the conversion budget is 700 ns and the analog needs ~7 ns.
The design is limited by the *chosen* clock, not by settling or the
comparator. Raising fclk is free until the digital sequencer or the CDAC
settling runs out.

---

## 3. Levers, ranked

Cost: S = hours, M = days, L = 1–2 weeks (layout/floorplan rework).
Gain is relative to the 1.4 MS/s, 0.184 mm^2, ENOB 3.8 baseline.

### Area

| # | lever | mechanism | expected gain | cost | risk |
|---|---|---|---|---|---|
| A1 | **Stack the 18 switch cells into 2–3 rows** | unit pitch 38.8 → ~13 um; array 106 k → ~40 k um^2 | die −25…35 % | L | routing (`saradc_conn.tcl`) assumes coordinates |
| A2 | **Dummy ring = cap-only geometry** | 56 units lose 18 switch cells each; saves 13 k um^2 of devices and up to 47 k um^2 of footprint | die −10…20 % | M | matching environment changes; verify with MC/PEX |
| A3 | **Custom compact TG macro** (1 LEF cell per TG + half-width dummy, not 6 `INVX0`) | switch area 22.7 k → ~8 k; lower off-cap | die −8…12 % **and** unlocks smaller Cu | L | new leaf needs LVS/DRC/PEX + LEF/blackbox |
| A4 | **Narrow the digital core** 96 → 40 um | digital needs 5 k um^2; taps drop 164 → ~60 | die −10 % | S | 10:1 aspect ratio, clock/power routing |
| A5 | **Compact the MOM array macro** (route over the caps instead of a 40 um channel) | gen 8-bit array 240.9 x 48.1 → ~240 x 12 | gen array −75 % | M | needs upper-metal routing over caps |

Combining A1+A2+A3+A4 puts the analog at ~40–50 k um^2 and the die at
**0.08–0.10 mm^2 (1.8–2.3x smaller)**, with the digital at ~5 k um^2.

### Power (measure first)

| # | lever | mechanism | expected gain | cost | risk |
|---|---|---|---|---|---|
| P1 | **Dynamic comparator (`CMP_KIND=STRONGARM`)** | removes the ratioed latch's crowbar/static current; dynamic charge only | −80…95 % of comparator power | M (layout leaf) | offset/noise re-characterisation |
| P2 | **Async sequencer (`SEQ_KIND=ASYNC`)** | no 7x clock, no clock tree at fclk; decisions self-timed | digital −5…7x; also removes the 7x clock from the analog | M | already implemented at RTL; needs timing closure |
| P3 | **Monotonic / Vcm-based CDAC switching** | halves CDAC switching energy | −0.3 uW @ 1.43 MS/s (noise level) | L | new switching logic + behavioral model |
| P4 | Clock gating in the sync sequencer | only 17 flops, but at 100 MHz the clock tree matters | −0.5x digital | S | — |

### Speed

| # | lever | expected fs | cost | risk |
|---|---|---|---|---|
| S1 | **Raise fclk 10 → 100 MHz** (same die) | 14.3 MS/s (10x) | S | digital timing (17 flops, 10 ns), CDAC settle 7 ns < 10 ns — OK |
| S2 | **Async** (`SEQ_KIND=ASYNC`) | 50–100 MS/s | M | self-timed loop must close at all corners |
| S3 | **Bootstrapped switch** (`SW_KIND=BOOT`) | Ron ~300 Ohm → settle 0.03 ns; comparator delay then dominates | M | new leaf; bootstrap clock power |
| S4 | **Smaller Cu** (after A3) | settle ∝ C; 22.5 → 10 fF halves it | — | tied to A3/quality |

S1 is essentially free and gives a 10x throughput increase at constant area;
combine S1 with P2 and the design lands at 50–100 MS/s for ~5-bit accuracy,
at which point the CDAC switching energy is ~10–25 uW and the comparator
becomes the only thing worth optimising.

### Quality (ENOB)

| # | lever | mechanism | expected gain | cost |
|---|---|---|---|---|
| Q1 | **MOM cap (already in this build)** + post-layout sim | removes MOSCAP `C(V)` derating (behavioral: ENOB 2.5 → 7.5 on 8-bit) | recovers the C(V) part of the 1-bit loss | S (PEX sim, already wired) |
| Q2 | **PEX the switch/unit, then shrink Cu or the switch** | removes the ~9 % LSB bottom-plate parasitic → the ±8 % weights | +0.5…1 ENOB | M |
| Q3 | **Weight calibration (3 trim bits)** | corrects residual bit weights in digital | +0.3…0.7 ENOB | M |
| Q4 | Common-centroid / dummy ring (already present) | keeps systematic gradient out | verify, don't rebuild | S |

---

## 4. Recommended sequence

0. **Measure before changing anything.** When the MOM P&R finishes:
   - run `make -C signoff gds drc lvs pex`;
   - `python3 src/adc_char.py --netlist signoff/outputs/SARADC.pex.spice ...`
     → static + sine → ENOB/INL **and** extract `I(AVDD)`/`I(VDD)` from the
     same run (add the supply currents to `.save`/`wrdata`);
   - add `report_power` after `detailed_route` (needs `set_power_activity`),
     so the next P&R run has a first-order number.
   This is the only way to know whether P1 or A1 is the real first lever.

1. **Free wins (hours).** Raise fclk to 100 MHz and re-run the schematic/PEX
   char; if ENOB holds, the chip is 10x faster for zero area. Then try
   `SEQ_KIND=ASYNC` at the same clock.

2. **Structural area (1–2 weeks).** A1 (stack switches) + A2 (cap-only
   dummies) + A4 (narrow digital). Expect ~1.5–2x smaller die. Do it as a new
   variant, not by editing the golden floorplan: `pnr.tcl` hardcodes
   `dig_to_ana = 451.92`, `X/Y`, and the instance paths that the connection
   script addresses.

3. **Power/quality (after the PEX measurement).** If the comparator is
   dominant, do P1; if the weights are parasitic-dominated, do A3/Q2 before
   Q3.

Ordering rule: **measure power and ENOB on the MOM PEX netlist first** —
every lever below is ranked on estimates, and two of them (P1, Q2) are
guesses about where the loss is.

---

## 5. What is already in the framework

| lever | knob | state |
|---|---|---|
| MOM cap | `CAP_KIND=MOM` | physically hardened (this flow) |
| bootstrapped switch | `SW_KIND=BOOT` | RTL + SPICE leaf, no layout leaf |
| StrongARM comparator | `CMP_KIND=STRONGARM` | RTL leaf, no layout leaf |
| async sequencer | `SEQ_KIND=ASYNC` | RTL + synthesis, no P&R run yet |
| segmented / monotonic / nonbinary CDAC | spec `cdac.backend` | schema only, evaluator fails closed (`gen/feasibility.py:_cdac_backend_guard`) |
| calibration | spec `calibration` | behavioural model only |
| common-centroid / dummies | spec `physical.array` | ring placement implemented in P&R |

So the three biggest power/speed levers (P1, P2, S3) are **RTL-complete and
layout-missing**; the three biggest area levers (A1, A2, A3) are
**floorplan/leaf work with no RTL change**.

# Gap register — what is missing, and what is implemented

Companion to [`ARCH_STUDY.md`](ARCH_STUDY.md) and the five deep dives in
[`arch_study/`](arch_study/). This is the execution register: every gap found
by the study is listed once, tagged `L1` (model/behavioural, implementable now)
or `L3` (silicon/layout, blocked on a missing leaf), with its status and the
command or file that evidences it.

Status legend: `DONE` = implemented and verified in this tree; `OPEN` = not
implemented; `BLOCKED` = needs a prerequisite that does not exist (the
prerequisite is named).

---

## 1. Cross-cutting model gaps

| ID | layer | gap | status | evidence / prerequisite |
|---|---|---|---|---|
| G-X1 | L1 | `spec.schema.json` arch enum only has `sar/ramp/sdm/vco`; the four new blocks would violate `additionalProperties:false` | **DONE** | `arch` enum now `sar, ramp, sdm, vco, dual_slope, flash, pipeline, ti`; blocks `dual_slope`, `flash`, `pipeline`, `ti`, `sdm.decimator` added |
| G-X2 | L1 | `run.py` `BEHAVIORAL` dispatch and `feasibility.human` only know the original four | **DONE** | new modules registered; `run.py behavioral --spec …` runs each |
| G-X3 | L1 | `matrix_table` hard-codes a 5-character arch column and SAR-shaped keys | **DONE** | widened; every evaluator supplies `cu_ff`, `c_tot_ff`, `area_cap_um2`, `fs_max`, `dnl_msb_sigma_lsb` |
| G-X4 | L1 | no schema validation is executed by `run.py`/`make check` (the schema is documentation only) | **DONE** | `spec_validate.py` (stdlib subset validator) + `run.py validate`; every command that consumes a spec validates on load and fails closed, and `make check` validates all 15 example specs |
| G-X5 | L1 | `spec.calibration.{offset,gain,weights}` are booleans with no model | **DONE** (pipeline/TI only) | `pipeline.cal_residual_lsb` and `ti.offset_residual_uv` now consume them; SDM/others still booleans |

---

## 2. Enablers — the five leaves every new architecture needs

These are the cross-architecture blockers from `ARCH_STUDY.md` §2. They are
listed once here and referenced by each architecture's L3 rows.

| ID | layer | gap | status | prerequisite |
|---|---|---|---|---|
| E1 | L3 | matched/precision resistor leaf (unit cell, dummies, Kelvin taps, voltage coefficient, Pelgrom) | **BLOCKED** | `dac-generator/analog/string` is pre-layout SPICE only; `analog/r2r` has a GDS with 7 open DRC density markers; no sign-off resistor on any of the five PDKs |
| E2 | L3 | OTA / op-amp leaf (A0 60–100 dB, GBW 200–400 MHz, low offset/1/f, ~1 V swing) | **BLOCKED** | no OTA leaf in this repo or the sibling repos; required by pipeline, dual-slope, SDM, and the flash reference buffer |
| E3 | L3 | N-phase clock / phase interpolator / DTC (< 1 ps) | **BLOCKED** | `adpll_gen` is integer-N single-output, has no PI/DTC; `multiphase_dco` is a `missing` catalog row |
| E4 | L3 | measured random comparator σ and moscap Pelgrom `A_C` | **BLOCKED** | ics55 Xyce `.noise` returns zeros and the vendor mismatch hooks are baked to 0; sky130 `sigma_uv` is systematic only; no PDK has a measured random offset distribution |
| E5 | L3 | dielectric absorption (DA) of the available capacitor devices | **BLOCKED** | unmeasured in every profile; decides the dual-slope 12 b point (needs ≤ 0.012 %) |
| E6 | L3 | layout corpus for non-SAR architectures | **BLOCKED** | only open SAR layouts exist (`lmm2024/sar-adc-8b-open` Apache-2.0, `Shadow515/sar-adc-sky130`, `ADC-TEAM2020/avsdadc_3v3`); no dual-slope, TI, pipeline/MDAC, OTA, flash-array or precision-resistor layout was found (search of 2026-11-09) |

---

## 3. Dual-slope integrating ADC

Deep dive: [`arch_study/dual_slope.md`](arch_study/dual_slope.md).

| ID | layer | gap | status | evidence / prerequisite |
|---|---|---|---|---|
| G-DS1 | L1 | no `evaluate_dual_slope`; the `ramp` evaluator is the wrong error model (its `dnl_lin + dnl_cmp` is single-slope) | **DONE** | `feasibility.evaluate_dual_slope`; `dual_slope` in `ARCHITECTURES`/dispatch/`human` |
| G-DS2 | L1 | no integrator `R·C` sizing; the kT/C-only clamp reports 257 fF at 12 b, which cannot hold the integration | **DONE** | evaluator solves `R·C = Vfs·T1/Vint,max`, sizes `C` from the cap floor, reports `rc_us`, `r_ohm`, `c_integ_pf`, `area_cap_um2` |
| G-DS3 | L1 | `A0`, DA, comparator offset/delay, input bias and `2^(N+1)` speed are unmodelled | **DONE** | gates `FAIL_RATE`, `FAIL_OPAMP_GAIN`, `FAIL_DA`, `FAIL_OFFSET`, `FAIL_INTEGRATOR`; report `a0_req_db`, `da_req_pct`, `offset_lsb`, `delay_lsb` |
| G-DS4 | L1 | no profile/spec block | **DONE** | profile `dual_slope` on all five PDKs; spec `dual_slope` in `spec.schema.json`; example `dualslope8_ics55.json` |
| G-DS5 | L1 | no behavioural model | **DONE** | `verify/behavioral_dualslope.py`: two-phase integrate/de-integrate, offset, finite `A0`, DA memory, delay, jitter, `dnl_bound` not a fabricated histogram |
| G-DS6 | L3 | precision resistor pair + integrator composition; no dual-slope layout exists to copy | **BLOCKED** | E1, E2, E5, E6 |

**Implemented result to quote:** 1 MS/s / 8 b needs `2^(N+1)+4 = 516` clocks,
i.e. ≥ 516 MHz; the fastest profile (ics55, 479 MHz) gives **928.3 kS/s**, so
the repo's 1 MS/s target is structurally unreachable for dual-slope on every
PDK. See §6 for the run.

---

## 4. Flash ADC

Deep dive: [`arch_study/flash.md`](arch_study/flash.md).

| ID | layer | gap | status | evidence / prerequisite |
|---|---|---|---|---|
| G-FL1 | L1 | no `evaluate_flash`; nothing computes the adjacent-comparator DNL or the area scaling `(σ_os/budget)^2` | **DONE** | `feasibility.evaluate_flash`; gate `FAIL_OFFSET` above the 8× area cap |
| G-FL2 | L1 | no resistor ladder model (`R_tap = 2^(N-2)·Ru`) and no `flash`/`resistors` profile data | **DONE** | profile `flash` + `resistors` on all five PDKs; `comparator.*.input_cap_ff`/`area_um2` added |
| G-FL3 | L1 | no behavioural model (offset draw, thermometer threshold, popcount, bubbles) | **DONE** | `verify/behavioral_flash.py`; caveat: `sigma_os` is a spec input (`flash8_sky130.json` uses 0.071 LSB), not a PDK measurement (G-FL5) |
| G-FL4 | L1 | no spec block/example | **DONE** | spec `flash` in schema; example `flash8_sky130.json` |
| G-FL5 | L3 | no measured random `sigma_os`; a flash on `source: measured` currently over-claims because `_provenance` only checks the word | **BLOCKED** | E4; the `flash` evaluator deliberately ignores `source` for the offset and reports `PASS_FEASIBLE_ASSUMPTION` on every PDK |
| G-FL6 | L3 | ladder array + comparator array + encoder layout | **BLOCKED** | E1, E4, E6 |
| G-FL7 | L1 | `behavioral_flash` always popcounted, so `flash.encoder` was ignored; the study's whole bubble/priority point was not modelled | **DONE** | `encode()` selects popcount (absorbs a bubble) or priority (highest set bit); `flash.encoder` is read and printed. At σ_os 0.5 LSB the two differ (2 vs 17 missing codes), which is the mechanism the spec field selects |

---

## 5. Time-interleaved ADC

Deep dive: [`arch_study/ti.md`](arch_study/ti.md).

| ID | layer | gap | status | evidence / prerequisite |
|---|---|---|---|---|
| G-TI1 | L1 | no `evaluate_ti`; no composition of a sub-ADC evaluator at `fs/N` | **DONE** | `feasibility.evaluate_ti` wraps `evaluate` without duplicating the sub-ADC |
| G-TI2 | L1 | offset/gain/skew spur algebra and the `N > 2` clock gate are unmodelled | **DONE** | `spur_offset_db`, `spur_gain_db`, `spur_skew_db`, `sfdr_db`, `enob_eff`; `FAIL_CLOCK` when `N > 2` and no multiphase clock |
| G-TI3 | L1 | no profile/spec block | **DONE** | profile `ti` on all five PDKs; spec `ti`; example `ti8_ics55.json` |
| G-TI4 | L1 | no behavioural mux model | **DONE** | `verify/behavioral_ti.py`: N channels, per-channel offset/gain/skew, FFT at `fs`; the measured SFDR (62.1 dB) is quantiser-limited — the mismatch spur predictions are the reported ones |
| G-TI5 | L3 | N-phase clock / PI / DTC | **BLOCKED** | E3 |
| G-TI6 | L3 | channel floorplan, matched clock/reference/input distribution | **BLOCKED** | E6 |

**Implemented result to quote (corrects the cross-study sketch):** at 1 MS/s
(`fin = 500 kHz`) 1 ps of skew is **−110.1 dBc**, not −50 dBc; offset and gain
bind. 12 b needs offset < 0.44 mV and gain < 0.05 %. `N = 2` on ics55-TG reaches
1.403 MS/s, but the bootstrap switch (60 Ω) gives 311 MS/s, so TI is not the
cheap fix at 1 MS/s.

---

## 6. Pipeline ADC

Deep dive: [`arch_study/pipeline.md`](arch_study/pipeline.md).

| ID | layer | gap | status | evidence / prerequisite |
|---|---|---|---|---|
| G-PL1 | L1 | no `evaluate_pipeline`; no stage count, `β`, or OTA/MDAC/switch budgeting | **DONE** | `feasibility.evaluate_pipeline`; reports all four limits and sets the winner |
| G-PL2 | L1 | no shared `opamp` profile block; `sdm.integrator_gbw_mhz` was being used as a load-independent proxy for every architecture | **DONE** | `opamp` block on all five PDKs; `_opamp()` falls back to the sdm scalar and flags it |
| G-PL3 | L1 | `calibration.gain` has no model; 10 b/12 b look impossible when they are only expensive | **DONE** | `pipeline.cal_residual_lsb` reduces `sigma_cu_req`; reported as `cal_residual_lsb` |
| G-PL4 | L1 | no behavioural model (stage cascade, redundancy, residue clipping) | **DONE** | `verify/behavioral_pipeline.py`: 1.5 b/stage only; 2.5/3.5 b remain feasibility-only (weights are fixed to `2^(N-1-stage_bits·(i+1))`) |
| G-PL5 | L1 | no spec block/example | **DONE** | spec `pipeline`; example `pipeline8_sky130.json` |
| G-PL6 | L3 | OTA leaf and AC/settling benches | **BLOCKED** | E2 |
| G-PL7 | L3 | MDAC composition + non-overlapping two-phase clock + layout | **BLOCKED** | E2, E3, E6 |
| G-PL8 | L1 | `behavioral_pipeline` silently accepted `bits_per_stage` 2.5/3.5 and produced >100 % clipping / 45–56 LSB DNL while printing the requested stage size | **DONE** | `run()` fails closed for `bits_per_stage != 1.5`; 2.5/3.5 b remain feasibility-only (`evaluate_pipeline`) |

---

## 7. Sigma-delta ADC (model fixes)

Deep dive: [`arch_study/sdm.md`](arch_study/sdm.md). The loop already runs;
these are the reported-number defects.

| ID | layer | gap | status | evidence / prerequisite |
|---|---|---|---|---|
| G-SD1 | L1 | `binding_constraint` is the max of the *cap* constraints only, never the real limit | **DONE** | picks the minimum margin among `osr`, `clock_ceiling`, `integrator_settling`, `decimator`, `cap_matching` |
| G-SD2 | L1 | `fs_max` echoes `fs_target` on PASS and ignores GBW on FAIL | **DONE** | `fs_max = min(clock_ceiling, 1/(2·t_int))/OSR` always |
| G-SD3 | L1 | `t_int` has no feedback factor and uses a load-independent GBW scalar | **DONE** | `t_int = ln(2^(q+L+1))/(2π·GBW·β)`, `β` from `sdm.feedback_beta` (default 0.5), GBW from `opamp` |
| G-SD4 | L1 | no `enob_thermal`; a 12 b point can claim 12.4 b on a 4 fF cap that supports 11.5 b | **DONE** | `c_ktc_ff`, `c_layout_ff`, `enob_thermal`; cap sized with `n_eff = min(N, loop ENOB)` |
| G-SD5 | L1 | no decimator model in the report | **DONE** | `decimator` block: `cic_order`, `cic_r1`, `halfbands`, `guard_band`, `alias_db`, `droop_db`, `taps`, `required_db`; `FAIL_DECIMATOR` gate |
| G-SD6 | L1 | behavioural SNDR uses a brick-wall decimator | **DONE** | `behavioral_sdm` runs the CIC from `sdm.decimator`, reports `sndr_ideal_db` vs `sndr_decimated_db`, `alias_db`, `droop_db`, optional thermal injection. The CIC is written as cascaded boxcars (identical magnitude response, bounded state): the literal integrator/comb form grew as n^order and lost the signal to float cancellation at R1=2. A tone and white noise pass the boxcar CIC in-band; the residual OSR64 gap (0–1.6 dB depending on tone bin) is CIC transition/measurement, not alias folding. The 12 b static DNL is measurement-floor limited (2.00/3.50 LSB floor) |
| G-SD7 | L1 | `dnl_msb_sigma_lsb = 0.0` unconditionally, wrong for a multibit quantizer | **DONE** | 0.0 only for `quantizer_bits == 1`; multibit uses the Pelgrom term. `sdm8_multibit_sky130.json` (order 2, 3-bit, OSR 32, CIC5/R1=2) exercises it: DNL 0.39 LSB, PASS_FEASIBLE_ASSUMPTION |
| G-SD9 | L1 | the spec `sdm` block was never merged into the profile block, so `sdm.order`, `sdm.quantizer_bits` and `sdm.integrator_gain_db` were accepted by the schema and then ignored by feasibility (the behavioural model honoured them, so the two disagreed) | **DONE** | `evaluate_sdm` now does `sdm = dict(profile["sdm"]); sdm.update(spec["sdm"])`; `integrator_gain_db` overrides the opamp gain; a multibit override drops the profile's 1-bit `loading_db` unless the spec sets it |
| G-SD10 | L1 | `behavioral_sdm.modulate` silently ran a 2nd-order loop for spec order 3/4 while printing the requested order | **DONE** | `run()` fails closed for order > 2 with an explicit message; order 3–4 is feasibility-only (the schema and evaluator still model it) |
| G-SD8 | L3 | decimator RTL + SC integrator leaf + OTA | **BLOCKED** | E2, E6 |

---

## 8. Implementation order used

1. dual-slope L1 (cheapest clone, produces the hard 1 MS/s negative)
2. flash L1 (offset budget + ladder timing)
3. TI L1 wrapper (composition; corrects the skew claim)
4. pipeline L1 + shared `opamp` block
5. SDM reported-number fixes + decimator

Each step is verified by a CLI run and the final `make -C gen check`; the
commands and outputs are in §9 below.

---

## 9. Verification evidence

Run from `/grid/saradc_auto`. All commands below were executed on 2026-11-09
and the numbers are their output, not estimates.

```
make -C gen check
python3 gen/run.py validate
python3 gen/run.py feasibility --spec gen/spec/examples/dualslope8_ics55.json --pdk ics55_h7ch
python3 gen/run.py feasibility --spec gen/spec/examples/flash8_sky130.json --pdk sky130A_hd
python3 gen/run.py feasibility --spec gen/spec/examples/ti8_ics55.json --pdk ics55_h7ch
python3 gen/run.py feasibility --spec gen/spec/examples/pipeline8_sky130.json --pdk sky130A_hd
python3 gen/run.py feasibility --spec gen/spec/examples/pipeline10_sky130.json --pdk sky130A_hd
python3 gen/run.py feasibility --spec gen/spec/examples/sdm8_sky130.json --pdk sky130A_hd
python3 gen/run.py feasibility --spec gen/spec/examples/sdm12_sky130.json --pdk sky130A_hd
python3 gen/run.py feasibility --spec gen/spec/examples/sdm8_multibit_sky130.json --pdk sky130A_hd
python3 gen/run.py behavioral --spec gen/spec/examples/dualslope8_ics55.json --samples 512 --trials 3
python3 gen/run.py behavioral --spec gen/spec/examples/flash8_sky130.json --samples 512 --trials 3
python3 gen/run.py behavioral --spec gen/spec/examples/pipeline8_sky130.json --samples 512 --trials 3
python3 gen/run.py behavioral --spec gen/spec/examples/pipeline10_sky130.json --samples 512 --trials 3
python3 gen/run.py behavioral --spec gen/spec/examples/ti8_ics55.json --samples 512 --trials 3
python3 gen/run.py behavioral --spec gen/spec/examples/sdm8_sky130.json
python3 gen/run.py behavioral --spec gen/spec/examples/sdm12_sky130.json
python3 gen/run.py behavioral --spec gen/spec/examples/sdm8_multibit_sky130.json
python3 gen/run.py matrix --spec gen/spec/examples/dualslope8_ics55.json
python3 gen/run.py report
```

### 9.1 Feasibility (L1 evaluators)

| spec / PDK | status | binding | key numbers |
|---|---|---|---|
| dualslope8_ics55 / ics55 | `FAIL_RATE` | clock | 479 MHz / 516 clocks = 928 kS/s (target 1000); C=100 pF, R=6.4 kΩ, R·C=0.64 µs, area 9043 µm²; A0 80 vs 54.2 dB, DA 0.02 % vs 0.1953 %, offset 0.02 LSB, delay 0.16 code |
| flash8_sky130 / sky130 | `PASS_FEASIBLE_ASSUMPTION` | comparator_offset | 255 comparators, 256 units at 1000 Ω, R_tap 64 kΩ, area 2918 µm²; σ 500 µV vs 829 µV budget; DNL 0.10 LSB; `fs_max` 588795 kS/s |
| ti8_ics55 / ics55 | `PASS_FEASIBLE_ASSUMPTION` | sub_adc_rate | 2× SAR, 701 → 1403 kS/s; spurs −80.0/−92.0/−110.1 dBc; SFDR 110.1 dB vs 48.2 needed; offset 120 µV, gain 0.005 %, skew 1000 fs; ENOB_eff 11.12; C_tot 679.4 fF |
| pipeline8_sky130 / sky130 | `PASS_FEASIBLE_ASSUMPTION` | cap_matching | 7×1.5 b, unit 27.407 fF, σ_Cu/Cu 0.0921 % vs 0.0921 %; DNL 0.17; A0 60 vs 54.2 dB; GBW 200 MHz; `fs_max` 61914 kS/s; area 13 µm² |
| pipeline10_sky130 / sky130 | `FAIL_OPAMP_GAIN` | ota_gain | calibrated path exercised; the shared `opamp` block supplies only 60 dB against the 10 b requirement |
| sdm8_sky130 / sky130 | `PASS_FEASIBLE_ASSUMPTION` | cap_matching | C_s 2.7 fF; SQNR 76.2 dB (12.36 b); thermal ENOB 11.21 b; `fs_max` 1770 kS/s; CIC4/R1=4 alias 115.9 dB vs 86.2 required, droop −0.04 dB |
| sdm12_sky130 / sky130 | `PASS_FEASIBLE_ASSUMPTION` | cap_matching | C_s 40.2 fF; SQNR 91.2 dB (14.86 b); thermal ENOB 13.66 b; `fs_max` 885 kS/s; alias 140.4 dB vs 101.2 required, droop −0.01 dB; area 9 µm² |
| sdm8_multibit_sky130 / sky130 | `PASS_FEASIBLE_ASSUMPTION` | cap_matching | order 2, 3-bit DAC, OSR 32; C_s 5.0 fF; SQNR 82.2 dB (13.36 b); CIC5/R1=2 alias 130.9 dB vs 92.2 required; DNL 0.39 LSB from DAC mismatch (non-zero, unlike the 1-bit row) |

`matrix` over all five PDKs confirms the same: dual-slope fails on every PDK
(fastest 928 kS/s on ics55), flash passes with the assumption flag, pipe8
fails only on ics55 (`FAIL_SETTLING`, 183 kS/s), and `report` renders all
specs (including the new `ti`, `sdm12`, `pipeline10` rows).

### 9.2 Behavioural (L1 models)

| model | result |
|---|---|
| dual-slope 8 b | max\|DNL\| 0.016 LSB, max\|INL\| 1.500 LSB, 0 missing; DNL bound 0.000 LSB (monotonic); SNDR 50.16 dB (8.04 b), worst harmonic −61.2 dBc |
| flash 8 b | max\|DNL\| 0.312 LSB, max\|INL\| 2.266 LSB, 0 missing, bubbles 0.00 %; 3σ prediction 0.301 vs 0.50 target; SNDR 49.72 dB (7.97 b) |
| pipeline 8 b | 7×1.5 b uncalibrated, 0.2 % gain error: max\|DNL\| 2.266 LSB, max\|INL\| 1.734 LSB, 1 missing code, in-range clipping 0 %; SNDR 49.40 dB (7.91 b) |
| pipeline 10 b | 9×1.5 b calibrated (0.0488 %): max\|DNL\| 2.250 LSB, max\|INL\| 1.750 LSB, 1 missing; SNDR 61.61 dB (9.94 b), worst harmonic −70.5 dBc |
| TI 8 b | SNDR 50.07 dB; measured SFDR 62.1 dB at 23.54·fs (quantiser-limited); predicted mismatch spurs −79.8/−92.0/−128.4 dBc |
| SDM 8 b | OSR64 ideal 70.10 / decimated 68.66 dB; alias 115.9 dB; droop −0.04 dB; static DNL 0.00 / INL 0.50 LSB |
| SDM 12 b | OSR128 ideal 86.46 / decimated 86.38 dB; alias 140.4 dB; droop −0.01 dB; static DNL 2.00 / INL 3.50 LSB, 590 missing (measurement floor 3.50 LSB) |
| SDM 8 b, 3-bit DAC | OSR32 ideal 55.00 / decimated 55.03 dB; CIC5/R1=2 alias 130.9 dB vs 92.2 required; feasibility DNL 0.39 LSB; the loop model is still 1-bit (caveat printed) |

### 9.3 Caveats recorded, not hidden

- Flash σ is a spec input; `PASS_FEASIBLE_ASSUMPTION` is deliberate (G-FL5).
- Pipeline behavioural is 1.5 b/stage only; 2.5/3.5 b remain feasibility-only.
- Pipeline DNL ≈ 2.27 LSB / 1 missing code at 8 b is the real uncalibrated
  0.2 % finite-gain residue error, not a modelling bug.
- TI measured SFDR is quantiser-limited; the reported spur ranking is the
  analytic offset/gain/skew prediction.
- SDM's CIC is implemented as cascaded boxcars: the literal integrator/comb
  form integrates `order` times at the full rate and loses the signal to
  float64 cancellation (the decimated SNDR collapsed to a few dB at R1=2).
  A tone and white noise pass the boxcar CIC in-band; the residual OSR64
  decimated-vs-ideal gap (0–1.6 dB depending on tone bin) is CIC
  transition/measurement, not alias folding.
- SDM's behavioural loop is 1-bit; `quantizer_bits > 1` is a feasibility-only
  DAC-mismatch path, and the human output says so.
- SDM 12 b static DNL/INL are floor-limited by the decimated 1-bit stream.
- The dual-slope `cap fF` column in `matrix`/`report` shows the sized
  integrator (100000 fF = 100 pF), not a CDAC unit.

---

## 10. Physical closure — DRC / LVS / PEX

`make -C gen closure` regenerates every layout GDS and re-runs DRC+LVS+PEX;
full matrix and per-PDK conventions are in
[`analog/CLOSURE.md`](analog/CLOSURE.md). Result: **11 / 11 layout models are
fully closed** (all DRC-clean, LVS-matched and PEX-extracted).

| ID | layer | gap | status | evidence / prerequisite |
|---|---|---|---|---|
| G-PH1 | L3 | ihp `array4b` LVS — top pins must sit on purpose 25 of the net's own metal (`8/25` met1, `10/25` met2); leaf TG pins must be stripped so they do not surface | **CLOSED** | `layout_ihp_array.py` emits 8/25+10/25 pins; `ihp_array_ref()` (16 dev); `Netlists match`, DRC 0, PEX 38 dev/118 caps |
| G-PH2 | L3 | gf180 `array4b` LVS — the KLayout deck promotes every hierarchical `34/10`/`36/10` text to a port, and the array had an m2 open between the bit strap and the TG Z2 riser | **CLOSED** | m2 riser segment added; label-stripped LVS copy + `gf180_array_ref()` (38 dev); `Netlists match`, DRC 0, PEX 38 dev/125 caps |

The method is now demonstrated on all four PDKs: sky130, ihp, gf180 and ics55
`cap_unit`/`switch_tg`/`array4b` all pass DRC + LVS + PEX. The gf180 closure is
also a correctness result — LVS exposed a floating TG that DRC could not see.

Electrical characterization of the generated leaves on all four PDKs is in
`analog/char_all.py` / `gen/outputs/char/` (unit cap, TG, comparator).


## 11. Digital hardening — DRC / LVS on the hardened cores

`backend/` hardens the seven synthesizable cores on all four PDKs; the matrix,
criteria and per-run evidence are in [`../backend/RESULTS.md`](../backend/RESULTS.md).
Result: **28 / 28 runs**, 21 of them completely clean (three PDKs x seven
cores) and the seven `ics55` ones clean on the KLayout-only criteria, with the
two ics55 exceptions below quantified rather than waived.

| ID | layer | gap | status | evidence / prerequisite |
|---|---|---|---|---|
| G-DH1 | L3 | ihp `PDN_RAIL_WIDTH` — the Ciel cache ships a rebuilt `libs.tech/librelane/config.tcl` with `1` instead of the vendor `0.44`, making every Metal1 rail 0.56 µm too wide | **CLOSED** | overlay restores the vendor file; same core against cache vs composed root: 5062 Magic DRC / 96 overlaps / 916 KLayout / 16 LVS vs 0 (README table) |
| G-DH2 | L3 | ihp clock NDR — CTS applies `-apply_ndr half` but detailed routing does not inherit it, leaving route DRC | **CLOSED** | `NON_DEFAULT_RULES: clk_half` + `DRT_ASSIGN_NDR: "^clk$"` in `gen_configs.py`; `dual_slope` 1 → 0 |
| G-DH3 | L3 | ihp `sdm` setup at the shared 10 ns: −0.245 ns / 1 path; delay-driven retry made it worse (−1.008 ns / 14 paths / +30 % area) | **CLOSED by design point** | 11 ns (`runs/sdm-t11`), all corners 0, same area as the 10 ns netlist; `runs/opt` kept as the counter-example |
| G-DH4 | L3 | ics55 stream-out — the PDK ships a GDS-import layer map (`M1`/`V1`/`TM2`) while the LEFs say `MET1`/`VIA1`/`T4M2`, so every route and pin label was silently dropped | **CLOSED** | overlay `ics55_def.map` + `KLAYOUT_DEF_LAYER_MAP`; electrical LVS on `cap_unit` etc. is the proof the labels survive |
| G-DH5 | L3 | ics55 `LAYERS_RC`/`VIAS_R` left as a TODO in the PDK config, aborting IR drop | **CLOSED** | values in `gen_configs.py`, `IRDropReport` runs on every ics55 job |
| G-DH6 | L3 | ics55 library cells whose GDS does not match their CDL (`AOI211X3H7H`: 12 vs 8 devices) | **CLOSED (excluded)** | `EXTRA_EXCLUDED_CELLS`; excluding them is what keeps the flow from instantiating a cell no LVS can match |
| G-DH7 | L3 | ics55 KLayout DRC cannot reach 0: the released cells alone produce dummy-layer pin-label and cell-internal slivers | **QUANTIFIED, not closed** | pinned run `ics55/ramp-pin1`: 343 items = 279 inside standard cells (M1DUM_W_1 180, metal.m1.area 94, metal.m2.area 2, poly.poly.area 3) + 64 in the top cell (metal.m2.area 49, metal.m2.spacing 4, implant.np.enclosed_area 11). No routed geometry involved. See `backend/ICS55_SIGNOFF.md` |
| G-DH8 | L3 | ics55 design-added DRC: the top-cell `metal.m2.area` (49) and `metal.m2.spacing` (4) items are MET2 pads of abutting cells, and no single-via MET2 pad in the LEF (max 0.15 × 0.15 = 0.0225 µm²) satisfies `LAYER MET2 { AREA 0.052 }` | **OPEN (PDK rule conflict)** | measured on the streamed GDS: 100 % of sub-minimum MET2 polygons touch exactly one VIA1; needs a PDK erratum or a pad-widening step. Count scales with placement (the `ramp` top-cell `metal.m2.area` items went 21 → 49 between the pre-pin and the pinned layout, while the in-cell floor did not move), so it is not a deck change |
| G-DH9 | L3 | ics55 top-level LVS failed on every core, localised to nets around `NAND2X0P5H7H` | **CLOSED** | three independent causes, all fixed. (a) **Schematic**: a cell-by-cell audit (each cell extracted as its own top cell) found seven defective circuits in three classes — `NAND2X0P5H7H`/`X6`/`X12`/`X16` have the A/B input devices on the wrong pins, `NAND3BBX0P5H7H` has its NMOS series stack ordered the other way round, `OAI32X3H7H` is drawn with two parallel copies of each PMOS branch where the CDL lists one 570 nm device, and `AOI22X2H7H` draws each 300 nm NMOS leg as two 150 nm fingers in two parallel stacks where the CDL lists one 300 nm device. Patch `pdk_overlay/ics55/…/ics55_LLSC_H7CH.cdl.patch`; all seven verify `PASS` at cell level with matching device counts. Same GDS, vendor vs patched schematic: `FAIL` vs `PASS` on three different KLayout builds (0.30.0 / 0.30.7 / 0.30.12). (b) **Layout**: `DECAP_CELLS: []` — the released `FILLCAP*H7H` are placed post-route by `OpenROAD.FillInsertion` and their internal MET1 plates short routed signal wires to the rails (`_074_`→VSS, `net45`→VDD in `flash`), which is a real short readable in the GDS. (c) **Schematic, series order**: after (a)+(b) six cores closed and `ti` still reported 19 of 13558 devices, all inside `AOI22X2H7H` (4 PMOS) and `AOI22X1H7H` (2 NMOS): electrically equivalent series orderings the cell-level comparer reorders freely but the top-level one cannot once it has given up on the two nodes they sit between (`$6326`/`$6493`, paired crosswise to `NET233`/`_0176_`). The patch is now 8 hunks / 8 circuits, writing the drawn order; both single-hunk variants still `FAIL`, only both together `PASS`. 7/7 cores now close at `design__lvs_error__count = 0` with `ERROR_ON_LVS_ERROR: true` (`ti-pin3`, rc 0). See `backend/ICS55_SIGNOFF.md` |
| G-DH10 | L3 | ics55 `sdm` setup at 10 ns: −7.14 ns, 90 paths, TNS −210 ns, all fed by `integ[2][0..1]` (≈90 loads each) | **CLOSED by design point** | 20 ns (`runs/sdm-t20`), all corners 0; noted in RESULTS.md that `sdm` is not comparable across PDKs |
| G-DH11 | L3 | ics55 `ti` hold: one post-route self-loop (−0.064 ns); post-CTS margins 0.15 / 0.30 both failed (`RSZ-0064`) because that resizer sees estimated RC | **CLOSED, other repair** | the post-GRT resizer is *not* used: on the pinned openroad 2026-02-17 it aborts inside `rsz::RepairSetup` (`[ERROR GRT-0267] Net _0300_ has disconnected segments`) for every `GRT_RESIZER_HOLD_SLACK_MARGIN` tried. A 0.30 ns pre-GRT margin alone already closes hold (`runs/ti-pin1base`: WNS 0, 0 violations, 366 hold buffers), and `ti-pin2` at an 11 ns period closes both setup and hold |

Cross-PDK reuse stayed high: the RTL is one file set per architecture, the flow
is one `gen_configs.py`, and only `arch_period`/`arch_extra` are per-architecture
per-PDK overrides (currently `sdm` and `ti`).

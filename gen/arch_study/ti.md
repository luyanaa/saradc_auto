# Deep dive 5 — Time-Interleaved ADC

Companion to `../ARCH_STUDY.md`. TI only: what it actually buys at this repo's
targets, the mismatch and skew budgets, the clock that does not exist, the
GitHub layout search, the L1 contract, the L3 plan, and a per-PDK go/no-go.
Study only; no code.

TI is not a conversion mechanism. It is `N` copies of an existing converter
(SAR, flash, pipeline or SDM) plus a sampling clock and a mismatch budget. The
architecture decision is therefore two decisions: *is the sub-ADC rate-limited
enough to need N channels*, and *can the mismatch be held below the target*.

---

## 1. What TI buys — and the honest answer at 1 MS/s

`fs_max = N · fs_max(sub-ADC)`, and every mismatch spur sits at `k·fs/N`.
So TI multiplies rate linearly and pays in spurs and input capacitance.

The current matrix has exactly one 1 MS/s failure:

| sub-ADC | PDK | switch | `fs_max` | 1 MS/s? |
|---|---|---|---|---|
| SAR | sky130 | TG 1.55 kΩ | 16.5 MS/s | pass |
| SAR | ihp | 50 Ω | 141.9 MS/s | pass |
| SAR | gf180 AS | 40 Ω | 105.3 MS/s | pass |
| SAR | gf180 FD | 35 Ω | 90.8 MS/s | pass |
| SAR | ics55 | bootstrap 60 Ω | 311.0 MS/s | pass |
| SAR | ics55 | **measured TG 74.5 kΩ** | **701.5 kS/s** | **FAIL** |

`N = 2` on ics55-TG gives 1.403 MS/s and recovers the target. But a
**bootstrapped switch** (the 60 Ω already assumed in `sar8_ics55.json`) gives
311 MS/s — 440× the target — for one leaf. TI is therefore *not* the cheapest
fix at 1 MS/s; the bootstrap is. Record this explicitly, because the
cross-architecture study's "N=2 recovers ics55" line is true but misleading
without the bootstrap comparison.

TI becomes the right answer when the sub-ADC is genuinely at its ceiling and
a bootstrap/faster comparator cannot move it: `fs ≥ 10–100 MS/s`, or a
resolution/rate pair that one channel cannot reach. That is where the rest of
this file applies.

---

## 2. Mismatch budget — the actual 1 MS/s numbers

For `N = 2`, `Vfs = 1.8 V` (full-scale sine amplitude `A = 0.9 V`):

| term | N=2 spur | formula | 1 mV / 0.1 % / 1 ps |
|---|---|---|---|
| offset `δ` | `k·fs/2` | `20log10(δ/(2A))` | **−65.1 dBc** at 1 mV |
| gain `ε` | `fs/2 ± fin` | `20log10(ε/2)` | **−66.0 dBc** at 0.1 % |
| skew `Δt` | `k·fs/2 ± fin` | `20log10(2π·fin·Δt)` | **−110.1 dBc** at 1 ps, `fin = 500 kHz` |

The skew number is the one that needs correcting from the cross-study sketch.
`1 ps → ≈ −50 dBc` is a **500 MHz input** number (i.e. a ~1 GS/s converter):
`20log10(2π·500 MHz·1 ps) = −50.1 dBc`. At the repo's 1 MS/s, `fin = 500 kHz`
and the same 1 ps is `−110.1 dBc` — irrelevant. **At 1 MS/s, offset and gain
bind; skew does not.** At 1 GS/s the order reverses.

Required mismatch for `spur ≤ 6.02·N` dB:

| N | offset | gain | skew @ 1 MS/s | skew @ 1 GS/s |
|---|---|---|---|---|
| 8 b | 7.04 mV | 0.78 % | 1.24 ns | 1.24 ps |
| 10 b | 1.76 mV | 0.195 % | 311 ps | 311 fs |
| 12 b | 0.440 mV | 0.049 % | 77.8 ps | 77.8 fs |
| 14 b | 0.110 mV | 0.012 % | 19.4 ps | 19.5 fs |

Read the 12 b row: at 1 MS/s, offset < 0.44 mV and gain < 0.05 % are the
requirements; 78 ps of skew is easy. Those offsets are at or below the
measured/assumed comparator offsets (sky130 500 µV, ics55 1200 µV), so
**offset calibration is mandatory at 12 b**; `spec.calibration.offset` exists
as a boolean and has no model. Gain can be trimmed in the digital back end or
by per-channel reference trim. Skew calibration is only needed above
~100 MS/s.

For `N > 2`, all three broaden by approximately `√(N/2)` for random mismatch
(the spurs spread over more lines), but the deterministic worst case does not
improve; model the deterministic case and report `N`.

---

## 3. The clock is the blocker

A TI converter needs `N` sampling phases with skew ≪ the requirement above.
At 1 MS/s the requirement is ~78 ps (12 b), which a divider and matched
buffers can meet. At 100 MS/s it is ~0.8 ps (12 b), which needs a phase
interpolator or DTC.

`adpll_gen` cannot supply either:

| need | `adpll_gen` state |
|---|---|
| N-phase output | none; the ADPLL is integer-N, single-output |
| phase interpolator / DTC | none |
| `multiphase_dco` | catalog row `implementation: missing` (`architecture_catalog.json:274`) |
| jitter/phase-noise signoff | none (deterministic period spread only) |

So the clock is new work, and the L1 model must treat skew as a **spec input**
with a profile-derived floor, not as something the generator produces. For
`N = 2` a divide-by-2 with complementary outputs is a small digital leaf; for
`N ≥ 4` it is a real multiphase clock, and the study should not pretend
otherwise.

---

## 4. Sub-ADC choice

| sub-ADC | best for | status |
|---|---|---|
| **SAR** | 8–12 b, 1–100 MS/s, the existing default | implemented, characterized; best layout corpus (§5) |
| flash | ≥ 8 b at ≥ 100 MS/s, lowest latency | L1 missing; deep dive 1 |
| pipeline | ≥ 10 b at ≥ 10 MS/s | L1 missing; needs OTA (deep dive 2) |
| SDM | high resolution at low rate; TI is not the natural fit | exists; see deep dive 3 |

TI does not change the sub-ADC's own error budget; it adds the three mismatch
terms and the mux. The cheapest credible TI point in this repo is **SAR +
N=2 + offset/gain calibration**, at 2× the SAR's `fs_max`.

---

## 5. Layout prior art on GitHub — the actual search

Searched 2026-11-09 with the authenticated GitHub API and `gh_grep`.

### 5.1 TI: no IC-level layout

| repo / query | license | what it contains |
|---|---|---|
| `AK1194/Time-Interleaved-SAR-ADC-Analog-VLSI-Design-Cadence-Virtuoso` | none | **1 file**; a Cadence Virtuoso design with no exported layout |
| `SJTU-YONGFU-RESEARCH-GRP/ti-adc-model` | CC-BY-4.0 | Python/Spectre/ngspice **behavioural** model, static + dynamic; no layout |
| `Shyiu/Time-Interleaved-ADC` | Apache-2.0 | Python model |
| `wlmoi/TI-Pipelined-SAR-ADC` | none | SystemVerilog digital model |
| repo search `time-interleaved ADC layout` | — | **0 results** |
| repo search `interleaved SAR ADC` | — | 3 results, none with layout |

**No open TI ADC layout exists.** The only TI "design" repo has one file and
no GDS. This is the same negative as dual-slope, and it means the mux +
clock-distribution layout is ours to write.

### 5.2 What is reusable: the sub-ADC layout corpus

The same SAR corpus as the dual-slope deep dive supplies the per-channel
converter and the matched structures:

| repo | license | PDK | layout | use for TI |
|---|---|---|---|---|
| `lmm2024/sar-adc-8b-open` | **Apache-2.0** | IHP SG13G2 | GDS/OAS + Magic PEX, 10 MS/s ENOB 7.96 | **the sub-ADC to instantiate N times** |
| `LewisLiuLiuLiu/open-analog-sar-8b` | no LICENSE (inspect only) | IHP SG13G2 | layout-as-code KLayout PCells, DRC 0/LVS | best flow reference; not copyable |
| `Shadow515/sar-adc-sky130` | no license | sky130 | Magic `.mag`, DRC 0/LVS, common-centroid CDAC | sky130 channel layout |
| `ADC-TEAM2020/avsdadc_3v3` | **Apache-2.0** | sky130 | 20 Magic `.mag` cells | reusable sky130 cells |

### 5.3 What is new

- **N-channel floorplan**: symmetric placement, identical orientation, common
  centroid for the channels; a shared input bus with matched stubs.
- **Clock distribution**: matched-length routes from the divider/PI to each
  channel; skew is a *layout* number, not just a schematic number. Measure it
  with a post-layout extraction, not a schematic transient.
- **Input mux / bootstrapping**: the shared front end and each channel's
  sampling switch must match; the mux is where gain mismatch enters.
- **Reference distribution**: a shared reference to N channels is a
  gain-mismatch source; use a symmetric tree and per-channel decoupling.

No open repo provides any of these four for a TI converter.

---

## 6. What the L1 model must do

### 6.1 `evaluate_ti(spec, profile, ...)`

TI is a wrapper, so compose an existing evaluator rather than copy it:

```
N          = spec.ti.channels
sub        = spec.arch                      # sar | flash | pipeline | sdm
sub_spec   = spec with fs_hz = fs_target/N
sub_res    = evaluate_<sub>(sub_spec, profile)
fs_max     = N * sub_res.fs_max
delta      = profile.ti.offset_mismatch_uv
eps        = profile.ti.gain_mismatch_pct / 100
skew       = spec.ti.skew_fs_rms
spur_off   = 20*log10(delta*1e-6/(2*A))
spur_gain  = 20*log10(eps/2)
spur_skew  = 20*log10(2*pi*(fs_target/2)*skew*1e-15)
sfdr       = -min(spur_off, spur_gain, spur_skew)
enob_eff   = min(sub_res.enob, (sfdr-1.76)/6.02)
```

Gates and `binding_constraint`:

| condition | status / binding |
|---|---|
| `fs_max < fs_target` | `FAIL_RATE`, binding `sub_adc_rate` |
| `spur_off > target` | `FAIL_OFFSET_MISMATCH`, binding `offset_mismatch` |
| `spur_gain > target` | `FAIL_GAIN_MISMATCH`, binding `gain_mismatch` |
| `spur_skew > target` | `FAIL_SKEW`, binding `skew` (only at high fs) |
| `N > 2` and no multiphase clock | `FAIL_CLOCK`, binding `clock` |
| any profile field assumed | `PASS_FEASIBLE_ASSUMPTION` |

Report: `n_channels`, `sub_arch`, `sub_fs_max`, `fs_max`, `spur_offset_db`,
`spur_gain_db`, `spur_skew_db`, `sfdr_db`, `enob_eff`, `c_in_total_ff`
(= `N·sub.c_tot`), `binding_constraint`, `assumed_inputs`.

`c_in_total` matters: N channels multiply the input capacitance and the
kickback, so the *driver* requirement grows with N even when the converter
does not.

### 6.2 Spec / profile

```jsonc
"ti": {
  "channels": 2,
  "sub_arch": "sar",
  "skew_fs_rms": 1000,
  "offset_mismatch_uv": 500,
  "gain_mismatch_pct": 0.05
}
```

Profile: `ti.offset_mismatch_uv` and `ti.gain_mismatch_pct` with
`"source": "assumption"`; `skew_fs_rms` is a spec input because no profile can
measure a layout skew. The calibration booleans already exist
(`spec.schema.json`): `offset: true` sets the post-cal residual, `gain: true`
the gain residual, `weights: false` (no per-channel weight model yet).

---

## 7. Behavioural model

`behavioral_ti.py`:

- instantiate `N` copies of the sub-ADC model (SAR first), each at `fs/N`;
- give each channel `offset`, `gain = 1+ε_n/2`, and a sampling time
  `n·T/N + Δt_n`;
- mux in order, FFT the `fs`-rate stream;
- report `sfdr`, `snr`, `enob`, and the spur locations `k·fs/N` and
  `fs/N ± fin`;
- sweep `N ∈ {2,4,8}` and the three mismatch terms separately, so the
  report shows which one binds.

The sub-ADC models already exist; this is a wrapper, not a new loop.

---

## 8. L3 plan

### 8.1 Leaves

| leaf | content | donor / status |
|---|---|---|
| sub-ADC | SAR (or flash/pipeline) channel | SAR layout corpus (§5.2) |
| `clk_nphase` | N-phase, matched delay; divider for N=2, PI/DTC for N≥4 | **none** (`adpll_gen` gap) |
| input mux + bootstrap | shared front end, matched switches | TG/bootstrap leaves exist; mux new |
| reference tree | symmetric distribution, per-channel decoupling | new, small |
| calibration | offset trim (DAC or comparator trim), gain trim | spec booleans only, no model |

### 8.2 Benches, in order

| # | bench | measures | note |
|---|---|---|---|
| 1 | channel matching | offset, gain, per-channel INL | the TI-specific bench |
| 2 | clock skew | post-layout delay spread across N phases | **must be extracted, not schematic** |
| 3 | mux feedthrough | input bandwidth, kickback, crosstalk | drives `c_in_total` |
| 4 | two-channel FFT | spurs at fs/2, fs/2±fin | the go/no-go measurement |
| 5 | PVT | skew, gain drift, offset drift | no profile exercises this |

### 8.3 Layout notes

- Channels: identical orientation, symmetric placement, common-centroid where
  the mismatch budget demands it; do not mirror one channel and copy it.
- Clock: matched-length routes, balanced buffers, and a shielded spine; the
  skew number in the report is only credible after PEX.
- Input: a shared bus with equal stubs; guard the sampling nodes from the
  clock spine.
- Reference: a tree, not a daisy chain; each channel gets its own decoupling.
- Digital: keep the mux select and calibration logic away from the sampling
  nodes.

---

## 9. Go/no-go

| PDK | credible TI point | N | why | blocker |
|---|---|---|---|---|
| **ics55** | 1.4 MS/s (TG) or 622 MS/s (bootstrap) | 2 | fastest sub-ADC; 701 kS/s TG is the only 1 MS/s gap | clock for N≥4; offset trim |
| sky130 | 33 MS/s | 2 | SAR 16.5 MS/s per channel | clock; mismatch at 12 b |
| ihp | 284 MS/s | 2 | SAR 141.9 MS/s | clock; only if the target moves up |
| gf180 AS | 210 MS/s | 2 | SAR 105.3 MS/s | clock |
| gf180 FD | 182 MS/s | 2 | SAR 90.8 MS/s | clock |

**Verdict:** TI is a *rate multiplier*, not a resolution architecture. At the
repo's 1 MS/s target it is dominated by the ics55 bootstrap switch and by the
already-passing SARs on the other PDKs; do **not** build it to hit 1 MS/s.
Build the L1 wrapper (it is ~150 lines and composes an existing evaluator),
because it is the only way to answer "what does 2× cost in spurs" — but keep
it off the critical path. The credible L3 point is `SAR + N=2 + offset/gain
calibration` at ≥ 10 MS/s, and there is no open TI layout to copy.

---

## 10. Risks, ranked

1. **No TI layout anywhere** — the mux/clock/reference distribution is new.
2. **Clock for N≥4 is absent** — `adpll_gen` has no N-phase/PI/DTC, and
   `multiphase_dco` is a missing catalog row.
3. **Skew must be a spec input, not a promise** — at 100 MS/s, 12 b needs
   0.8 ps; no profile measures it.
4. **Offset calibration is unmodelled** — 12 b needs < 0.44 mV at 1 MS/s, at
   or below the measured comparator offset.
5. **`c_in_total` grows with N** — the driver, not the converter, can become
   the limit.
6. **TI is the wrong answer at 1 MS/s** — the ics55 bootstrap is cheaper; the
   framework should say so rather than defaulting to N=2.

---

## 11. Effort

- L1 + behavioural: ~150 lines (a wrapper around an existing evaluator, plus
  a behavioural mux/FFT file) and the `ti` spec/profile block.
- L3: N-channel floorplan + clock distribution + mux/reference tree +
  calibration + five benches. The clock is the architecture-specific item and
  it does not exist today. This is the largest L3 in the study after the OTA,
  and it starts from no layout prior art.

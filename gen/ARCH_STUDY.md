# Five more ADC architectures in this generator — feasibility study

Companion register: [`GAPS.md`](GAPS.md) — every gap found here, its L1/L3
tag, status, and the command that evidences it.

Status of this file: the study below is the analysis; the L1 (model and
behavioural) parts have since been implemented and are marked DONE in
`GAPS.md`. The L3 (silicon/layout) parts remain BLOCKED on the named
prerequisites. Nothing here is a signoff claim. Numbers are computed from the checked-in PDK profiles
(`gen/pdk/*.json`) with the same source flags the generator already honours
(`measured` vs `assumption`), and from the two sibling IP repos
(`../adpll_gen`, `../dac-generator`) at their current HEAD.

Scope: **Flash, Pipeline, Sigma-Delta (extended), Dual-Slope Integrating,
Time-Interleaved**. For each: how it maps onto the existing seam, the
feasibility model and report contract, the behavioural model, the spec/schema
change, the analog/layout leaves it needs, the reusable IP, and a verdict.

Per-architecture deep dives live in `gen/arch_study/` and are linked from
their section (`flash.md`, `pipeline.md`, `sdm.md`, `dual_slope.md` and
`ti.md` are all written).

---

## 0. What "implement in the current framework" means

The generator is two independent ladders that meet only at the spec:

| | L1 — model | L2/L3 — circuit |
|---|---|---|
| entry | `gen/run.py feasibility | behavioral | matrix | report` | `gen/analog/*`, `gen/Makefile` targets |
| unit | one `evaluate_<arch>()` returning one dict | one leaf topology → deck → measured profile |
| cost of a new arch | ~100–200 lines + a schema enum + a profile block | new device leaf, new bench, DRC/LVS/PEX, per PDK |
| what it proves | the architecture closes/fails on paper with traceable inputs | the transistor netlist does what the model says |

Everything below separates the two. A new architecture at L1 is cheap and
useful even when L2 is impossible: the model's job is to fail honestly.
The five archs differ by orders of magnitude on the L2 side, not the L1 side.

### 0.1 The registration seam (identical for every new arch)

1. `gen/feasibility.py:88` `ARCHITECTURES` tuple.
2. `gen/feasibility.py:91` `evaluate()` dispatch + a new `evaluate_<arch>()`.
3. `gen/feasibility.py:635` `human()` renderer map.
4. `gen/run.py:30` `BEHAVIORAL` dict + a `gen/verify/behavioral_<arch>.py`
   exposing `run(...)` and `human(result)`.
5. `gen/spec/spec.schema.json:10` `arch` enum **and** the per-arch block
   (`additionalProperties` is `false` at `:118`, so a new block is illegal
   until declared — pattern at `ramp:88`, `sdm:97`, `vco:106`).
6. A profile block in each `gen/pdk/*.json`; missing → `KeyError` →
   `FAIL_UNCHARACTERIZED` (`cmd_matrix`, `run.py:103`).
7. `cmd_report` (`run.py:153`) hard-requires `fs_target, status, cu_ff,
   c_tot_ff, binding_constraint, fs_max, dnl_msb_sigma_lsb, area_cap_um2`
   (`run.py:174`). Return them even when meaningless (`vco` returns zeros and
   `cap="none"` — the precedent).
8. Example spec in `gen/spec/examples/`, or `report` never sees it
   (`specs()` `run.py:53`).

Only defects 5 and 7 are hard locks. Nothing validates the schema today
(there is no validator import), so the enum is documentary — but it is the
contract the reader trusts, so update it anyway.

---

## 1. Summary table

| arch | L1 model | behavioural | new analog leaves | reusable IP today | verdict |
|---|---|---|---|---|---|
| **Flash** | easy, one formula | easy (offset array) | resistor ladder, 2^N−1 comparator array | `dac-generator/analog/string` (255-unit string, pre-layout DNL 0.133 LSB) | L1 yes, silicon hard; useful as the "why flash stops at 6–8 bit" demo |
| **Pipeline** | moderate, new OTA block | moderate (per-stage residue) | OTA + MDAC | nothing in either repo; external 3-bit IHP pipeline (xschem) is the schematic reference | L1 yes with an `opamp` scalar block; silicon blocked on OTA |
| **Sigma-Delta** | **exists** (`sdm`) | exists (DT CIFB) | SC/CT integrator, decimator | `audiodac_dsmod` (digital CIFF), SAR boxcar FIR, external CT-DSM + CIC scripts | extend, don't add; the gap is decimator + integrator circuit |
| **Dual-slope** | easy, clone of `ramp` | easy (two-phase integrate) | OTA integrator, precision R, analog mux | `dac-generator/analog/string` and `r2r` (unit R, 350 Ω/sq, measured) | L1 cheapest of all five and gives a decisive negative on speed |
| **TI** | moderate (wrapper + mismatch terms) | moderate (N channels + mux) | N instances, phase generator, trim DAC | `adpll_gen` donor only (no N-phase, no <1 ps skew); external TI model (M=4) is portable | L1 yes; difficulty is rate-dependent, silicon hardest |

---

## 2. Cross-cutting enablers (ranked by architectures unblocked ÷ effort)

These are the real deliverables; the per-arch sections are consequences.

### E1. A resistor leaf and a `resistors` profile block
Unblocks: flash ladder, dual-slope reference, TI trim DAC, pipeline reference.
`gen/analog/` has **no resistor device at all** (`layout_check.DEVICES` is
`{cap, tg}`, `gen/analog/layout_check.py:202`), and no bench measures one.
`dac-generator/analog/string` is a ready recipe: 255 poly units `W=2 µm,
L=5.714 µm` (350 Ω/sq → 1 kΩ), 8-level binary TG tree, per-unit mismatch
sweep (`run_mismatch.py`), and a **pre-layout** SPICE result — DNL
**0.133 LSB** and ~8 µs tap settling, gf180 3.3 V, **tt/25 °C only**, closed
with an *ideal* unity-gain follower (`Ebuf`, gain 0.999). There is no GDS for
the string and its DRC/LVS/PEX are blocked (`analog/string/README.md`
§Status). The r2r leaf does have a KLayout GDS generator, but its DRC/LVS/PEX
are also open (7 density-style markers, no post-layout linearity, worst-corner
DNL 0.145 LSB) and it settles in ~1 µs. So the *netlist recipe* transfers and
the *evidence* does not: it is at the same maturity as this repo's own leaves
and must be re-measured here.

Cost: a `resistor` entry in `TOPOLOGIES` (`macro_gen.py:231`), a
`layout_<pdk>_res.py`, a `dc` bench that sweeps 2^N taps, and a
`resistors[]` profile block (`rsh_ohm_sq, unit_w_um, unit_l_um, ac_pct_um,
source`).

### E2. An OTA/op-amp leaf and an `opamp` profile block
Unblocks: pipeline MDAC, dual-slope integrator, SC sigma-delta integrator.
Nothing in this repo and **nothing in `dac-generator`** — its analog content
is resistor DACs plus digital PWM/ΔΣ. `sdm` gets away with a scalar
(`sdm.integrator_gbw_mhz`, `feasibility.py:392`) precisely because it never
closes a loop. The survey of 16 DAC references
(`dac-generator/docs/dac_topology_survey.md`) notes CDAC/current-steering need
"constrained device generation or manual layout" and excludes op-amps.

Minimal viable seam: do **not** start with a transistor OTA. Add
`pipeline.ota = {gain_db, gbw_mhz, noise_uv_rms, swing_v, source}` and
`dual_slope.opamp = {...}` to the profiles, exactly like
`sdm.integrator_gbw_mhz`. That is enough for L1 and it keeps the same
provenance downgrade. A real OTA leaf is a separate project (external
starting points: `ridvanumaz/2AC_Folded-Cascode-OTA-with-SKY130-PDK`,
`tdextrous/sky130_td_ip__opamp_hp`, `tapeout/analog-generators` BAG scripts —
the last is BAG/Cadence, not KLayout, so not directly reusable here).

### E3. Multi-instance and structure in the analog manifest
Unblocks: flash comparator array, TI channel array.
`cmd_analog` iterates a hardcoded five-tuple (`run.py:237`) and `macro_gen`
emits exactly one instance per topology. Needed: a topology parameter
`count` (or `array: {rows, cols}`) plus a manifest entry per instance. This
is mechanical for flash (same leaf × 2^N−1) and not mechanical for TI (each
channel needs its own clock phase).

### E4. Mismatch/noise injection in the behavioural models
Today only SAR and ramp inject comparator offset/noise
(`behavioral_sar.py:168`, `behavioral_ramp.py:159`); `sdm` injects none
(`behavioral_sdm.py:16` comment). Flash and TI are *entirely* mismatch
problems, so their models need a shared per-comparator / per-channel
mismatch source. Put it in one helper (e.g. `gen/verify/mismatch.py`) so
flash, pipeline and TI do not each invent it.

### E5. A decimator
Unblocks: sigma-delta only. Current SNDR is measured with an ideal brick-wall
decimator (`behavioral_sdm.py:95`). Two independent prior arts exist:
the MPW-8 SAR's programmable **boxcar FIR** OSR 1/4/16/64/256
(`dac-generator/README.md:36`) — sinc¹, not enough stopband; and
`michaelk99/SKY130_CT-DSM` `system-level/python/dsSim.py`, whose `Decimator`
has a `cascaded` (sinc²) option explicitly marked "not fully implemented".
CIC + halfband is still new work.

### E6. A clock/skew model
Unblocks: TI only. `adpll_gen` cannot supply it (see §7). The honest L1 move
is to make skew a *spec input* (`ti.skew_fs_rms`) with a profile-derived floor,
not a claim that the generator produces it.

---

## 3. Flash ADC

### 3.1 Fit
The cleanest L1 of the five, because the framework already carries the only
number that matters: `comparator.<backend>.sigma_uv`. Flash has no CDAC, no
bit cycling, no settling chain — the model is a single inequality, and then a
speed number that is almost never the binding constraint.

### 3.2 Model
Per tap, adjacent comparator offsets add in quadrature to DNL:

```
sigma_DNL/LSB = sqrt(2) * sigma_os / LSB
gate: 3 * sigma_DNL <= dnl_target        (3-sigma over process)
==>  sigma_os <= dnl_target * Vfs / (2^N * 3 * sqrt(2))
```

| PDK | Vfs | sigma_os | 6 b | 7 b | 8 b | 9 b | max N (3σ) |
|---|---|---|---|---|---|---|---|
| sky130A_hd | 1.8 | 500 µV | 3315 | 1657 | **829** | 414 | **8** |
| ics55_h7ch | 1.2 | 1200 µV | **2210** | 1105 | 552 | 276 | **6** |
| ihp_sg13g2 | 1.2 | 800 µV | 2210 | **1105** | 552 | 276 | **7** |
| gf180mcu_as_7t3v3 | 3.3 | 1500 µV | 6077 | 3038 | **1519** | 760 | **8** |
| gf180mcu_fd_7t5v0 | 5.0 | 2000 µV | 9207 | 4604 | **2302** | 1151 | **8** |

(budget in µV; bold = passes. Only sky130's comparator is measured; ics55
delay is measured but sigma is an assumption, so ics55 flash lands at
`PASS_FEASIBLE_ASSUMPTION` at best.)

**Important subtlety:** if the gate were the SAR half-LSB rule
(`sigma_os <= Vfs/2^(N+1)`, `feasibility.py:169`) instead of 3σ DNL, ics55
would "pass" at 8 bit (1200 µV vs 2344 µV). The SAR rule is a *decision*
budget with redundancy and averaging behind it; flash has neither, and DNL is
the printed spec. Use the 3σ-DNL gate and say so in the report notes, or the
model will claim an 8-bit ics55 flash that cannot exist.

Speed (secondary):
`t = t_ladder + t_cmp + t_encoder`, `fs_max = 1/t`;
`area ≈ (2^N-1) * A_cmp + ladder`.

### 3.3 Behavioural
`behavioral_flash.py`: draw 2^N−1 offsets from `sigma_os`, convert a ramp and
a sine (thermometer→binary), reuse `behavioral_sar.static_scan` /
`dnl_inl` / `spectral` — they are arch-agnostic counters and FFTs.
Report keys mirror `behavioral_ramp.human` (`arch, nbits, max_dnl_lsb,
max_inl_lsb, missing_codes, sndr_db, enob, sfdr_dbc`).

### 3.4 Spec
New enum value `"flash"`; block `flash: {comparators: "full"|"interpolated",
thermometer_bits, encoder}`. `resolution` stays 4–16.

### 3.5 Analog / layout
- Resistor ladder: **E1**, `dac-generator/analog/string` is the reference.
- Comparator array: **E3**, plus a shared-bias/kickback bench that does not
  exist. `cmp_strongarm` is one leaf (`macro_gen.py:173`); a flash tap needs
  input-cap and kickback numbers that no bench currently measures.
- ics55 cannot emit anything until `analog.status` leaves `"scaffold"`
  (`macro_gen.py:57`, `ics55_h7ch.json:336`).

### 3.6 Prior art
| repo | license | what it is | use |
|---|---|---|---|
| `muhammadaldacher/Modeling-of-4-bit-Flash-ADC-and-4-bit-DAC` | none stated | ideal-component 4-bit flash + DAC | teaching model, not reusable silicon |
| `muhammadaldacher/Analog-Design-of-Dynamic-Comparator` | none stated | clocked comparator for a 4-bit flash | comparator topology reference |
| `themperek/spicebind` `examples/adc/flash_adc8.v` | BSD-3 | 8-bit flash in Verilog-AMS bind | encoder/testbench shape |
No open-silicon flash **layout** with DRC/LVS was found in the searches run
(GitHub code search + repo search); this is the weakest prior-art area of the
five. Expect to write the ladder/array layout.

### 3.7 Verdict
Do it at L1. It is ~150 lines, it reuses the profile comparator sigma (with
the provenance caveat in the deep dive), and it produces the number a designer
actually needs first ("how many bits can this comparator buy?"). Do not start
the array layout until E1/E3 exist.

Deep dive: [`arch_study/flash.md`](arch_study/flash.md) — topology choice,
offset/area-scaling tables, timing, the L1 contract, benches and layout plan,
per-PDK go/no-go.

---

## 4. Pipeline ADC

### 4.1 Fit
The only architecture where the existing CDAC mismatch machinery
(`A_C`, Pelgrom, `sigma_target`, `feasibility.py:124`) applies almost
unchanged — an MDAC is a binary cap array with a residue gain. What is
missing is the amplifier, and that is the whole architecture.

### 4.2 Model
1.5 bit/stage, digital redundancy, `S = N - 1` stages (8 b → 7, 10 b → 9),
per-stage feedback factor `beta = C_f/(C_f+C_s)` (0.5 for a 2C sampling cap).

```
t_settle_stage = (m+1) * ln2 * tau,  tau = 1/(2*pi*GBW*beta)
fs_max         = 1 / (S * (t_settle_stage + t_cmp))
MDAC gain error: sigma_gain = A_C/sqrt(area)   (same Pelgrom block)
op-amp DC gain floor: A0 >= 2^(N_stage+1) / (2*beta)   (residue error < 0.5 LSB)
kT/C: per-stage sampling cap from 12kT*2^(2*res_bits)/Vfs^2
```

With the profile's existing `sdm.integrator_gbw_mhz` used as a placeholder
GBW (all assumption, GF180 AS 100 MHz → ics55 400 MHz) and
`t_cmp = 0.3 ns`:

| PDK | GBW | tau | t_settle | 8 b (7 st) | 10 b (9 st) |
|---|---|---|---|---|---|
| gf180mcu_fd_7t5v0 | 60 MHz | 5.305 ns | 7.355 ns | 18.7 MS/s | 14.5 MS/s |
| gf180mcu_as_7t3v3 | 100 MHz | 3.183 ns | 4.413 ns | 30.3 MS/s | 23.6 MS/s |
| ihp_sg13g2 | 200 MHz | 1.592 ns | 2.206 ns | 57.0 MS/s | 44.3 MS/s |
| sky130A_hd | 200 MHz | 1.592 ns | 2.206 ns | 57.0 MS/s | 44.3 MS/s |
| ics55_h7ch | 400 MHz | 0.796 ns | 1.103 ns | 101.8 MS/s | 79.2 MS/s |

These are *placeholders*. The point of the table is that pipeline speed is
linear in GBW and inverse in stage count — it is the only one of the five
whose `fs_max` is set by an analog block the framework does not own. Report
`binding_constraint = "opamp_gbw"` so the provenance is visible.

### 4.3 Behavioural
`behavioral_pipeline.py`: per-stage residue with finite gain (`1/A0`), gain
error, comparator offset inside the redundancy window, then a digital
correction adder. Reuse `spectral`/`dnl_inl`. New keys: `arch, stages,
bits_per_stage, max_dnl_lsb, max_inl_lsb, sndr_db, enob, sfdr_dbc,
residue_swing_ok`.

### 4.4 Spec
`"pipeline"` + block `pipeline: {bits_per_stage: 1.5|2.5|3.5, redundancy:
true, ota: "scalar"}`.

### 4.5 Analog / layout
- OTA: **E2**. No leaf, no bench, no profile. Highest single-effort item in
  this study.
- MDAC: binary cap array + sampling switches (caps and TGs exist) + OTA +
  two-phase non-overlapping clock (does not exist anywhere in the repo).
- `dac-generator` has **no OTA and no MDAC**; its CDAC content is the 12-bit
  SAR's segmented 9+3 array — useful as a *cap-array* precedent, not as a
  stage.

### 4.6 Prior art
| repo | license | what it is | use |
|---|---|---|---|
| `michaelk99/sg13g2_pipeadc_3b` | Apache-2.0 | 3-bit pipeline, IHP SG13G2; xschem schematics + testbenches, gm/Id design scripts, Verilog encoder, MATLAB system model | the best open pipeline **schematic** reference. **No layout files**; stage/MDAC/OTA are inside `xschem/` and `python/` (gm/Id), not top-level |
| `michaelk99/SKY130_CT-DSM` | Apache-2.0 | CT ΔΣ modulator, sky130 | shares the integrator/OTA design style |

### 4.7 Verdict
L1 yes, but only after E2's *scalar* form exists; without it the model invents
a GBW. Silicon: last of the five to attempt — it needs the OTA, an MDAC, and
a two-phase clock, i.e. three new leaves.

Deep dive: [`arch_study/pipeline.md`](arch_study/pipeline.md) — stage-variant
choice, A0/cap-matching/sampling-switch budgets, per-PDK sizing, the L1
contract with calibration, benches and go/no-go.

---

## 5. Sigma-Delta (extension, not addition)

### 5.1 Fit
`sdm` already runs end to end: `evaluate_sdm` (`feasibility.py:392`),
`behavioral_sdm.py` (DT CIFB, OSR sweep), two passing points (OSR64
SNDR 70.10 dB / 76.12 dBFS, OSR128 86.46 dB / 92.48 dBFS). Anything "new"
here is an extension, and the extensions are exactly the three things the
README already lists as missing (`gen/README.md:237-238`): the decimator, the
circuit behind `integrator_gbw_mhz`, and PVT.

### 5.2 What is modelled vs not
| item | today | gap |
|---|---|---|
| loop | DT CIFB, order 1–4, 1-bit quantizer | no CT variant, no multibit quantizer |
| quantizer | ideal | no comparator noise (fine for 1-bit) |
| integrator | scalar leak `1/A` from 60 dB | no GBW, no settling, no SC noise |
| caps | none | no kT/C, no mismatch (an SC integrator has both) |
| decimator | ideal brick wall | **the** gap |
| DAC feedback | ideal | no mismatch, no jitter |

### 5.3 Model additions (in priority order)
1. **Decimator** (**E5**): report achieved alias rejection and passband
   droop for a chosen (CIC order, halfband taps) instead of ideal SNDR.
2. **SC integrator**: kT/C per integrator (reuse the SAR expression with
   OSR), settling from GBW, finite gain from the same `opamp` block as
   pipeline — share **E2**.
3. **Feedback DAC mismatch**: for multibit only; Pelgrom on the DAC unit,
   same as the SAR CDAC.

`sdm.loading_db` (9.0) is already pinned to a measured L1 point but the block
source is still `assumption` — leave the provenance honest.

### 5.4 Reuse
- `dac-generator`'s `audiodac_dsmod` is a **digital** 1st/2nd-order CIFF
  1-bit modulator (`docs/delta_sigma.md`). It is a legitimate digital
  noise-shaping reference and a bitstream generator, but it is not an analog
  loop; do not present it as the SDM circuit.
- The MPW-8 SAR **specifies** a boxcar FIR (OSR 1/4/16/64/256,
  `dac-generator/README.md:36`) but ships as a sealed 432×403 µm GDS plus a
  behavioural stub (`macro/adc_top.v`); the CDAC, comparator and filter
  source are not in the repo. Prior art for behaviour, not code — and sinc¹
  only.
- `michaelk99/SKY130_CT-DSM` `dsSim.py` has a `cascaded` (sinc²) decimator
  that is explicitly incomplete — the closest open starting point.

### 5.5 Verdict
Cheapest extension, highest existing evidence. Do the decimator first: it is
the difference between "76 dBFS with an ideal filter" and a number anyone can
defend.

Deep dive: [`arch_study/sdm.md`](arch_study/sdm.md) — the 9 dB arithmetic, OSR
requirement table, the corrected CIC⁴ + halfband decimator (alias-rejection and
tap tables), kT/C and settling defects, DT-vs-CT, the L1 contract, benches,
and per-PDK go/no-go.

---

## 6. Dual-Slope Integrating ADC

### 6.1 Fit
Structurally the twin of `ramp`: one integrating/hold cap, one comparator,
one counter, `f_clk`-driven. `evaluate_ramp` (`feasibility.py:272`) can be
cloned almost line for line — **but its error model must not be cloned.**
Single-slope's DNL comes from the ramp step; dual-slope is *inherently
monotonic*, so resistor mismatch and comparator offset produce gain/offset
error, not DNL. Reusing `ramp.linearity_lsb_dnl` would model the wrong thing.

Correct error budget:

```
offset error   <- comparator offset + op-amp offset (+ input bias current)
gain error     <- R ratio, comparator delay, clock period
INL            <- op-amp finite gain, dielectric absorption, charge injection
DNL            <- ~0 by construction (monotonic); report as a bound
noise          <- clock jitter * slope, kT/C on the integrator
```

### 6.2 Speed — the decisive number
Two phases of `2^N` clocks (integrate + de-integrate), plus auto-zero and
overhead:

```
fs = f_clk / (2^(N+1) + overhead),  overhead = 4
```

| PDK | f_clk (ramp) | 8 b | 10 b | 12 b | 16 b |
|---|---|---|---|---|---|
| gf180mcu_fd_7t5v0 | 25 MHz | 48.4 kS/s | 12.2 kS/s | 3.1 kS/s | 0.2 kS/s |
| gf180mcu_as_7t3v3 | 150 MHz | 290.7 kS/s | 73.1 kS/s | 18.3 kS/s | 1.1 kS/s |
| sky130A_hd | 300 MHz | 581.4 kS/s | 146.2 kS/s | 36.6 kS/s | 2.3 kS/s |
| ihp_sg13g2 | 400 MHz | 775.2 kS/s | 194.9 kS/s | 48.8 kS/s | 3.1 kS/s |
| ics55_h7ch | 479 MHz | 928.3 kS/s | 233.4 kS/s | 58.4 kS/s | 3.7 kS/s |

**The current 1 MS/s, 8-bit example target is unreachable on every
characterized PDK** — it needs ≥ 512 MHz and the fastest profile is 479 MHz.
Any dual-slope spec in this repo must start at ≤ 500 kS/s (8 b) or accept
12 b at tens of kS/s. That is not a framework limitation; it is the
architecture.

### 6.3 Behavioural
`behavioral_dualslope.py`: integrate for `k = round(2^N * vin/Vref)` clocks,
de-integrate at `Vref/R`, count; inject offset, finite op-amp gain, DA,
comparator delay, clock jitter. Reuse `behavioral_ramp.convert_scan` shape.
Report `arch, nbits, fs_max, offset_lsb, gain_error_pct, inl_lsb,
dnl_bound_lsb, sndr_db`.

### 6.4 Spec / profile
`"dual_slope"` + block `dual_slope: {phases: 2, auto_zero: true,
vref: 1.2}`. Profile block: `{clk_max_mhz, opamp_gain_db,
comparator_delay_ns, dielectric_absorption_pct, source}`. `clk_max_mhz` can
default from `ramp.clk_max_mhz` (already characterized per PDK).

### 6.5 Analog / layout
- Precision resistor: **E1**, and `dac-generator/analog/string`/`r2r` is the
  only resistor work with a simulated DNL in either repo (unit 1 kΩ,
  350 Ω/sq; pre-layout 0.133 LSB at tt/25 with an ideal follower; r2r worst
  corner 0.145 LSB and ~1 µs settling; no PEX either way).
- Integrator OTA + reset switch: **E2**.
- Analog input mux / auto-zero: new, small.
No layout work exists for a dual-slope front end in the searched repos
(confirmed by the deeper layout sweep in the deep dive: 27 dual-slope repos,
none with a schematic, let alone a DRC/LVS layout; the only real multislope
hardware is a KiCad PCB). The reusable corpus is the open SAR layouts
(`lmm2024/sar-adc-8b-open` Apache-2.0, `Shadow515/sar-adc-sky130`,
`ADC-TEAM2020/avsdadc_3v3`), which supply the comparator/switch/cap-array
patterns but not the resistor or integrator composition.

### 6.6 Verdict
Do this **first**. It is the cheapest L1 in the study (a clone of `ramp`,
~120 lines), it needs only one new profile block, and it immediately produces
a hard, defensible negative on the 1 MS/s target. It also gives E1 a consumer
that does not need an OTA to be *modelled* (resistor ratio is scalar).

Deep dive: [`arch_study/dual_slope.md`](arch_study/dual_slope.md) — what
cancels (R, C, clock) vs what does not (A0, DA, delay/offset), the 2^(N+1)
speed table, the missing `R·C` integrator sizing, the GitHub layout search,
the L1 contract, benches, and per-PDK go/no-go.

---

## 7. Time-Interleaved ADC

### 7.1 Fit
TI is not a new conversion mechanism; it is `N` copies of an existing one plus
a clock and a mismatch budget. That makes the L1 seam attractive and the
silicon seam the worst of the five.

```
fs_total = N * fs_sub
fs_max   = min(N * fs_sub_max, skew_limit, mismatch_limit)
skew SNR = -20*log10(2*pi*fin*sigma_t)
offset spur ~ 20*log10(sigma_os / (Vfs/2))     (per channel, at fs/N)
gain spur   ~ 20*log10(sigma_gain)
```

| sigma_t | 10 MHz | 100 MHz | 500 MHz | 1 GHz |
|---|---|---|---|---|
| 0.1 ps | 104.0 dB | 84.0 dB | 70.1 dB | 64.1 dB |
| 0.5 ps | 90.1 dB | 70.1 dB | 56.1 dB | 50.1 dB |
| 1 ps | 84.0 dB | 64.0 dB | 50.1 dB | 44.1 dB |
| 5 ps | 70.1 dB | 50.1 dB | 36.1 dB | 30.1 dB |

| mismatch | spur (Vfs 1.2 diff) |
|---|---|
| offset 0.5 mV | −61.6 dBc |
| offset 1 mV | −55.6 dBc |
| offset 2 mV | −49.6 dBc |
| gain 0.1 % | −60.0 dBc |
| gain 0.2 % | −54.0 dBc |
| gain 0.5 % | −46.0 dBc |

These two tables use the *per-channel deviation* convention (`δ`, `ε` relative
to nominal). The deep dive uses the *channel-difference* convention (`±δ/2`,
`±ε/2`, which is what an N=2 mux actually produces) and is therefore 6 dB
lower for the same number. Use the deep dive for sign-off.

**The verdict is rate-dependent, and this is the useful finding.**
At the rates this repo actually signs off today (≈1 MS/s SAR on ics55), TI is
*easy*: 1 ps of skew at 500 kHz is 110 dB, so skew is irrelevant and
offset/gain bind. ics55's measured SAR ceiling with the TG is 701 kS/s, so
`N = 2` reaches 1.4 MS/s. But a **bootstrapped switch** (60 Ω, already assumed
in `sar8_ics55.json`) gives 311 MS/s for one leaf, so TI is *not* the cheapest
way to 1 MS/s — the bootstrap is. At 500 MS/s+ TI becomes the hardest of the
five, because 10-bit needs < 0.3 ps and neither sibling repo can generate it.

### 7.2 Model
`evaluate_ti(spec, profile)`:
1. call the existing evaluator for `spec.ti.sub_arch` with
   `fs_hz = fs_target/N` (reuse, do not duplicate);
2. compose the limits above;
3. return the same report keys, with `fs_max = N * sub_fs_max` and
   `binding_constraint` set to `"ti_skew"` / `"ti_offset"` when those bind.

New profile block `ti: {skew_fs_rms, offset_sigma_uv, gain_sigma_pct,
trim_dac_bits, source}`. All assumption; ics55 can inherit
`comparator.strongarm.sigma_uv` for the offset term.

### 7.3 Behavioural
Direct port target: `SJTU-YONGFU-RESEARCH-GRP/ti-adc-model` (CC-BY-4.0,
M=4 default, 10 b, 1 GS/s muxed, static *and* time-varying mismatch,
optional per-channel gain/offset calibration, Python golden model + Verilog-A
+ Spectre/ngspice benches, same INL/DNL/FFT metrics). Its Python side
(`src/ti_adc/mismatch.py`, `analysis.py`) is the model to reimplement in
stdlib (no numpy in this repo).

### 7.4 Analog / layout
- N instances: **E3**.
- Clock phases: **E6**. `adpll_gen` reality: no N-phase output (`il_dco.v:33`
  exposes `OUT`, `MID_OUT` only), `multiphase_dco` is
  `implementation: missing` (`architecture_catalog.json:274`), the phase
  selector has one sim tap at 60 ps and unwired silicon taps
  (`PHSEL_SPEC.md:43`), the DCDL is a single `CLK_OUT` with model steps
  120/60/15 ps and a physical LSB of "~hundreds of ps"
  (`flow/custom/sky130/CUSTOM_SPEC.md:21`), and there is **no jitter or
  phase-noise signoff** (`JITTER_PHASE_NOISE.md:9`).
- Trim DAC: a per-channel offset DAC could reuse the `string` leaf (**E1**),
  but it is a new block. Neither repo has a fast or multibit calibration DAC;
  the closest are an 8 b R-2R at ~1 µs settling (pre-layout) and RC-filtered
  PWM/ΔΣ bitstreams (`pwmdac`, `audiodac`) that are DC-only.
- The existing README caveat "no PLL/jitter signoff" (`gen/README.md:217`) is
  therefore not conservatism — it is the donor repo's own status.

### 7.5 Verdict
L1 yes, and it is a cheap composition, but it is **not** the right answer at
1 MS/s: the ics55 TG failure that motivates `N = 2` is removed by a bootstrap
switch at a fraction of the cost. Keep the wrapper because it answers the
"what does 2× cost in spurs" question; schedule silicon only after E3 + E6,
and only for a target ≥ 10 MS/s. A GS/s TI is out of scope for this toolchain
today.

Deep dive: [`arch_study/ti.md`](arch_study/ti.md) — the 1 MS/s vs 1 GS/s
mismatch tables (skew is irrelevant at 1 MS/s), the offset/gain sign-off
requirements, the `adpll_gen` clock gap, the GitHub layout search (no TI IC
layout), the L1 wrapper contract, benches, and per-PDK go/no-go.

---

## 8. Suggested sequence

Ordered by information gained per unit of work, not by architecture rank:

| step | deliverable | why first |
|---|---|---|
| 1 | `dual_slope` L1 + behavioural | cheapest, one profile block, decisive speed negative |
| 2 | `flash` L1 + behavioural | one formula, reuses measured comparator sigma, explains the bit ceiling |
| 3 | `ti` L1 wrapping the SAR evaluator | the mismatch algebra is standard; **but note the ics55 bootstrap is cheaper than N=2 at 1 MS/s** |
| 4 | `pipeline` L1 with scalar OTA | forces the `opamp` block into the profiles, which SDM and dual-slope also want |
| 5 | SDM decimator + SC integrator terms | closes the one architecture that already reports numbers |
| 6 | E1 resistor leaf (`string` port) | first shared circuit; unblocks flash/dual-slope/TI trim |
| 7 | E2 OTA leaf | the single largest missing circuit; nothing to copy |
| 8 | E3 multi-instance + E6 clock model | only meaningful once a circuit exists |

Steps 1–5 are model-only and do not touch `gen/analog/`; they are safe to do
while the ics55 L3 story is still `scaffold`.

---

## 9. Verification plan (what would count as evidence)

The repo's existing ladder is the right one; apply it per architecture.

| level | flash | pipeline | sdm | dual-slope | TI |
|---|---|---|---|---|---|
| L1 | 3σ-DNL gate vs measured `sigma_uv`; status downgrades on assumption | GBW/gain gate; `binding_constraint="opamp_gbw"` | achieved alias rejection with real decimator | `fs_max` vs `clk_max_mhz`; offset/gain/INL all reported | sub-ADC model composed; skew/offset/gain spurs reported |
| L2 | ladder DC sweep, 2^N taps, measured DNL | stage residue transfer, gain error vs A0 | SC integrator settling + kT/C | integrate/de-integrate ramp, measured INL | N instances + phase skew measured |
| L3 | comparator array DRC/LVS/PEX, kickback | MDAC + OTA DRC/LVS/PEX | loop PVT | integrator PVT, DA | per-channel skew distribution, temp/voltage drift |

Each new arch must set `status` through the existing ladder
(`PASS_FEASIBLE` / `PASS_FEASIBLE_ASSUMPTION` / `FAIL_*`), and any number
whose profile `source != "measured"` must downgrade via `_provenance`
(`feasibility.py:620`). No new status names.

---

## 10. Open questions

1. **Target level:** is the goal an L1 menu (all nine architectures model and
   report), or real silicon per architecture? The first is weeks; the second
   is dominated by E2 (OTA) and is a separate program.
2. **Reference PDK for new architectures:** ics55 for continuity with the L3
   work (but `analog.status = scaffold`), or sky130/gf180 where leaves are
   `supported` and the comparator sigma is measured (sky130 only)?
3. **SDM flavour:** keep discrete-time CIFB (already modelled) or add CT?
   CT is what `SKY130_CT-DSM` does and what audio/biosignal users expect, but
   it needs an OTA and a different noise model.
4. **TI target rate:** MS/s (feasible with a simple delay line, and it fixes
   the ics55 1 MS/s failure) or GS/s (needs IP neither sibling repo has)?
5. **Calibration:** `spec.calibration` is still four booleans
   (`spec.schema.json:61`) with no DAC behind them. Do flash/TI calibration
   become modelled (a residual-error term) before any trim DAC exists?

---

## Appendix A — GitHub sweep: what was checked, what it is good for

Searched with GitHub repo search and code search for: `flash adc`, `pipeline
adc`, `time interleaved adc`, `dual slope adc`, `mdac`, `ota`, `sky130 adc`,
`gf180 adc`, `cic decimator`, `analog layout generator`. Licenses read from
each repo's `LICENSE`. "Layout" means GDS/mag with a DRC/LVS claim, not just
schematics.

| repo | license | content | layout | use here |
|---|---|---|---|---|
| `michaelk99/sg13g2_pipeadc_3b` | Apache-2.0 | 3-bit pipeline, IHP, xschem + gm/Id + Verilog | no | pipeline schematic reference |
| `michaelk99/SKY130_CT-DSM` | Apache-2.0 | CT ΔΣ, sky130, full system-level Python | no (schematics) | CT-SDM + decimator study |
| `SJTU-YONGFU-RESEARCH-GRP/ti-adc-model` | CC-BY-4.0 | M=4, 10 b, 1 GS/s, static/time-varying mismatch, optional cal, Python + Verilog-A + Spectre/ngspice | n/a (model) | TI behavioural port |
| `w32agobot/SKY130_SAR-ADC` | Apache-2.0 | 12 b async non-binary SAR, boxcar FIR OSR 1..256 | yes (analog macro) | TI sub-ADC; decimator prior art |
| `chipfoundry/sky130_ef_ip__adc3v_12bit` | Apache-2.0 | 12 b SAR IP | yes | TI sub-ADC |
| `UAH-IC-Design-Team/sky130-10-bit-SAR-ADC` | Apache-2.0 | 10 b SAR | yes | TI sub-ADC |
| `Purdue-IEEE-SMC/sky130_si_ip__saradc_16bit` | Apache-2.0 | 16 b SAR (Chipalooza) | partial | reference only |
| `lmm2024/sar-adc-8b-open` | **Apache-2.0** | 8 b 10 MS/s SAR, IHP SG13G2, `core18` sign-off, GDS/OAS + Magic full-RC PEX, ENOB 7.96 | **yes (GDS + PEX)** | **reusable L3 flow + leaves (best)** |
| `LewisLiuLiuLiu/open-analog-sar-8b` | no LICENSE (inspect only) | 8 b 10 MS/s SAR, IHP, layout-as-code KLayout PCells, DRC 0/LVS, 604-parasitic PEX | **yes (GDS/OAS + PEX)** | flow reference; not copyable |
| `Shadow515/sar-adc-sky130` | none | 8 b SAR, sky130, Magic `.mag`, DRC 0/LVS, 256-unit common-centroid MIM CDAC | **yes (mag)** | comparator + cap-array patterns |
| `ADC-TEAM2020/avsdadc_3v3` | Apache-2.0 | 8 b SAR, sky130, 20 Magic `.mag` cells, 2020 | **yes (mag)** | reusable sky130 cells |
| `saul-castillo/MatchMaker` | MIT | GF180 common-centroid placement/route → GDS → Magic DRC | **yes (generator)** | matched resistor/cap layout |
| `iic-jku/gf180mcu-jku-atbs-adc` | Solderpad 2.1 | digital core of a JKU ATBS ADC | digital only | encoder/timing reference |
| `2AMLogic/gf180-sar-adc` | unknown | 10 b SAR on gf180 | unknown | PDK cross-check |
| `patrickschulz/openPCells` | **GPL-3.0** (copyleft) | KLayout Lua PCells: MOS, caps, guard rings, standard cells | n/a (generator) | device primitives (E1/E2) |
| `AyarLabs/ACG` | BSD-3 | BAG2 plugin for custom layout generators | n/a (generator) | permissive alternative |
| `tapeout/analog-generators` | check | BAG scripts (OTA/OPAMP/DAC templates) | n/a (Cadence) | topology names, not portable |
| `themperek/spicebind` `examples/adc/flash_adc8.v` | BSD-3 | 8 b flash, Verilog-AMS bind | no | flash encoder shape |
| `muhammadaldacher/Modeling-of-4-bit-Flash-ADC...` | none | ideal 4 b flash | no | teaching only |

Not found in this sweep: any open **flash** array layout with DRC/LVS, any
**dual-slope** schematic or layout (27 dual-slope repos; the top "design" repo
is a README + PDF + LICENSE, and the only real multislope hardware is a KiCad
**PCB**), any **TI** IC layout (`AK1194/...Virtuoso` is one file; the SJTU
repo is a behavioural model), any **MDAC/OTA** layout usable on these PDKs,
any fast or multibit **calibration DAC** (only a DC 8 b R-2R and RC-filtered
bitstreams), and any N-phase (<1 ps) clock generator. The only sign-off-clean,
permissively licensed layout corpus is the SAR set above; the standalone
resistor layout (`dac-generator/analog/r2r`) has a GDS but DRC is open
(7 density markers), and `analog/string` is pre-layout SPICE only. Those gaps
are not search failures — they match what `adpll_gen` and `dac-generator`
report about their own unfinished items.

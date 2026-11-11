# Deep dive 3 — Sigma-Delta (ΔΣ) ADC

Companion to `../ARCH_STUDY.md`. Sigma-Delta only: what the existing loop model
actually computes, where the OSR/decimator/integrator numbers come from, the
circuit behind `integrator_gbw_mhz`, the L1 contract, the L3 bench plan, and a
per-PDK go/no-go. Study only; no code.

This is the one architecture that already passes end to end. The job here is
therefore not "how would we add it" but "which of its reported numbers are
real". Three are: the closed-form SQNR, the 9 dB loading term, and the static
transfer. Three are not: the sampling cap, the integrator, and the decimator.
This file separates them.

---

## 1. What exists, and what the 9 dB means

| piece | location | state |
|---|---|---|
| feasibility | `feasibility.py:392` `evaluate_sdm` | closed-form NTF bound, OSR gate, settling gate |
| behavioural | `verify/behavioral_sdm.py` | DT CIFB loop actually run; FFT of the bitstream |
| example | `spec/examples/sdm8_sky130.json` | 8 b, 10 kS/s, OSR 64, vfs 1.8 V |
| profile | every PDK `sdm` block | order 2, 1-bit, OSR 64, `loading_db = 9.0`, GBW scalar |

Closed form (`feasibility.py:412`):

```
SQNR = 6.02·q + 1.76 − 10log10(pi^(2L)/(2L+1)) + 10(2L+1)log10(OSR) − loading_db
```

`loading_db = 9.0` is not a fudge: it is the measured gap between that formula
and the loop simulation at 0.5 FS. The arithmetic closes:

| OSR | closed form, no loading | − 9 dB | reported L1 | implied loading |
|---|---|---|---|---|
| 64 | 85.19 dB | 76.19 dBFS | 76.12 dBFS | 9.07 dB |
| 128 | 100.24 dB | 91.24 dBFS | 92.48 dBFS | **7.76 dB** |

So the profile note is right that 9 dB is flat over OSR 16–64, and the model is
**1.2 dB conservative at OSR 128**. That is the size of every modelling error
in this section: not orders of magnitude, and always err on the safe side.
Keep the number, extend the note.

One provenance wrinkle: the 9 dB *value* is measured, but the `sdm` block is
still `"source": "assumption"` in every profile, so `_provenance`
(`feasibility.py:620`) downgrades every SDM result to
`PASS_FEASIBLE_ASSUMPTION`. That is correct as long as `integrator_gbw_mhz` is
a guess (§4.2); it should not be "fixed" by flipping the flag.

The useful consequence is that the *loop's own* resolution is a function of
OSR only, not of the requested N:

| OSR | loop SQNR | ENOB (no loading) | ENOB after 9 dB |
|---|---|---|---|
| 16 | 55.1 dB | 8.9 b | 7.4 b |
| 32 | 70.1 dB | 11.4 b | 9.9 b |
| 64 | 85.2 dB | 13.9 b | **12.4 b** |
| 128 | 100.2 dB | 16.4 b | 14.9 b |
| 256 | 115.3 dB | 18.9 b | 17.4 b |

Every claim below has to be read against that column. An "8 b at OSR 64" spec
is asking the loop for 4.4 bits of margin; a "12 b at OSR 64" spec is asking
for 0.4 bits. The second is at the edge and the cap must be sized for it.

---

## 2. OSR is the knob, and the gate already uses it

`osr_required` is computed from the requested N with +6 dB design margin and
the same 9 dB loading (`feasibility.py:421`):

| N | 6 | 8 | 10 | 12 | 14 | 16 |
|---|---|---|---|---|---|---|
| OSR required | 14.5 | **25.2** | 43.8 | **76.3** | 132.8 | 231.2 |

with `osr_max = 256` in every profile. That gate is self-consistent: 12 b at
OSR 64 is rejected and OSR 128 accepted, which matches the loop column above.
The only wrinkle is that `osr_required` rounds up to an integer OSR, so 8 b at
OSR 32 passes with ~11 dB of margin over the bare 8 b requirement instead of 6.

The OSR is also the *only* knob that trades resolution for rate. Since
`f_mod = OSR·fs`, the rate ceiling is `fs_max(OSR) = clock_ceiling/OSR` (or the
settling limit, §4.2). The whole architecture therefore lives on one curve per
PDK, and the framework should report that curve rather than a single point.

---

## 3. The decimator — the real gap, and it is smaller than it looks

The L1 SNDR is measured by FFT-ing the bitstream and integrating noise over
`0..fs_out/2` (`behavioral_sdm.py:95`). That is an ideal brick-wall decimator.
A real decimator must suppress every band that folds into that passband, and
the required suppression is the *ideal* in-band SQNR plus margin (10 dB is the
usual choice):

| OSR | ideal SQNR | required stopband |
|---|---|---|
| 16 | 55.1 dB | 65.1 dB |
| 32 | 70.1 dB | 80.1 dB |
| 64 | 85.2 dB | 95.2 dB |
| 128 | 100.2 dB | 110.2 dB |
| 256 | 115.3 dB | 125.3 dB |

### 3.1 First, correct a strawman

A **single-stage** CIC that decimates all the way to `fs_out` cannot work, and
the reason is structural: its passband edge and its first alias edge are the
same frequency. At `f = fs/(2·R)` the CIC magnitude is `(2/pi)^K`, i.e.
−11.8 dB for K = 3 — simultaneously a −11.8 dB passband droop and only
−11.8 dB of alias rejection. No filter can have zero-width transition. Any
table that quotes "CIC³ = 12 dB stopband" next to "12 dB droop" is describing
this configuration. It is not how a decimator is built and it should not be
used to argue that the decimator is hopeless.

### 3.2 The correct split: coarse CIC, then halfbands

The standard structure is a CIC that decimates by `R1 << R`, followed by a
cascade of halfband stages that decimate the rest. The CIC's output Nyquist
(`fs/(2R1)`) is then much wider than the final passband (`fs/(2R)`), so the
aliases it must reject sit near its *nulls* at `k·fs/R1`, where the attenuation
is large. The sharp filtering is pushed to the lower-rate halfbands, where taps
are cheap.

Worst-case CIC attenuation at the alias edge (the max over `k = 1..R1-1` of
`|H(k·fs/R1 ± fs/(2R))|`), dB:

| OSR | required | CIC³, R1=4 | **CIC⁴, R1=4** | CIC³, R1=2 | single-stage CIC³ |
|---|---|---|---|---|---|
| 16 | 65.1 | −49.3 (fail) | **−65.7** | −60.5 (fail) | −11.8 |
| 32 | 80.1 | −68.3 (fail) | **−91.1** | −78.6 (fail) | −11.8 |
| 64 | 95.2 | −87.0 (fail) | **−115.9** | −96.6 | −11.8 |
| 128 | 110.2 | −105.3 (fail) | **−140.4** | −114.7 | −11.8 |
| 256 | 125.3 | −123.5 (fail) | **−164.7** | −132.7 | −11.8 |

**CIC⁴ with R1 = 4 meets the alias requirement at every OSR from 16 to 256**
(OSR 16 has only 0.6 dB of margin over the +10 dB target and is the one
marginal point; from OSR 32 up the margin is 11–39 dB). CIC³/R1=4 fails at
every OSR; CIC³/R1=2 only works at OSR ≥ 64. That is the whole decimator-order
decision, and it is one extra accumulator stage.

Passband droop of CIC⁴/R1=4 at the final Nyquist:

| OSR | 16 | 32 | 64 | 128 | 256 |
|---|---|---|---|---|---|
| droop | −0.84 dB | −0.21 dB | −0.05 dB | −0.01 dB | −0.00 dB |

So a compensation FIR is only needed at OSR 16; from 32 up it is optional. This
is the opposite of the single-stage picture and it is the point: **the CIC
droop is a non-problem once the decimation is split.**

### 3.3 Halfband cascade cost

After CIC⁴/R1=4, `R/4` halfbands finish the job. The last one is the only hard
one — earlier stages have a transition band that is a large fraction of their
Nyquist. Kaiser estimate (`N ≈ (A−8)/(2.285·Δω)`, 10 % guard band,
`f_pass = 0.45·fs_out`), OSR 64, R = 64:

| stage input | Δf/fs_in | taps @ 95 dB | MAC rate |
|---|---|---|---|
| 250 kHz | 0.444 | 14 | 3.5 MMAC/s |
| 125 kHz | 0.388 | 16 | 2.0 MMAC/s |
| 62.5 kHz | 0.275 | 23 | 1.4 MMAC/s |
| 31.25 kHz | 0.050 | **122** | 3.8 MMAC/s |
| total | | **175 taps** | **10.8 MMAC/s** |

At OSR 64 the last stage needs ~122 taps for 95 dB; at OSR 256 it needs ~163.
The total is ~175–220 taps and ~11 MMAC/s at `fs = 1 MS/s`, which is ~30×
inside sky130's 300 MHz digital ceiling and two orders inside ics55's. Word
growth in the CIC is `K·log2(R1·M) = 8` bits.
**The decimator is cheap; it is simply absent.**

The design decision the framework must expose is the **guard band**: a
brick-wall edge at exactly `fs_out/2` leaves no transition, so the passband
must be specified slightly inside Nyquist (here 90 %). That is a spec input,
not a hidden constant.

### 3.4 What "ideal decimator" overstates

Almost nothing, *if* the decimator is designed as above. The residual is
(a) passband droop, −0.05 dB at OSR 64, and (b) whatever margin is left over
from the CIC⁴ stopband. The honest L1 number is therefore not "10 dB worse";
it is "the same SNDR, plus a stated decimator (CIC order, R1, halfbands,
guard band) and its measured alias rejection". The gap is a missing block, not
a missing 10 dB.

What the ideal number *does* hide is the DNL story: `dnl_msb_sigma_lsb = 0.0`
is correct for a 1-bit stream (a decimated bitstream is monotonic by
construction) but becomes a real number for a multibit quantizer or DAC, where
element mismatch is unshaped. Say so in the report instead of returning 0.0
unconditionally.

---

## 4. The integrator — the model is a scalar, the circuit is an SC integrator

### 4.1 Sampling capacitor: right form, wrong target

`c_ktc = 12kT·2^(2N)/(Vfs²·OSR)` (`feasibility.py:429`). The OSR in the
denominator is correct: a sampled kT/C noise of `2kT/C` (differential) is
spread over `fs_mod/2`, so the in-band fraction is `1/OSR` and
`2kT/(C·OSR)` is the in-band thermal power. Compared with a thermal-noise
sizing to the *requested* resolution, the formula is only **2× optimistic**
(the constants: 12 vs `16·10^(1.76/10)`).

The defect is the target, not the algebra. The model sizes the cap to the
requested N, but reports the *loop's* ENOB, which is N-independent. At OSR 64
the loop claims 12.4 b (76.1 dBFS) with zero thermal noise in the simulation;
to keep that number, the sampled thermal noise must sit ~10 dB below it, i.e.
the cap must be sized for ~86 dB, not for N. Numbers at OSR 64, `Vfs = 1.8 V`,
sky130:

| N | cap today | thermal ENOB at that cap | cap for the loop floor | gap |
|---|---|---|---|---|
| 8 | 0.016 fF → layout 2.67 fF | 11.2 b (fine) | 106 fF | 40× |
| 10 | 0.25 fF → layout 2.67 fF | 11.2 b (fine) | 106 fF | 40× |
| 12 | 4.02 fF | **11.5 b vs 12.4 b claimed** | 106 fF | **26×** |
| 14 | 64.3 fF | 13.5 b | 106 fF | 1.6× |

Read the 12 b row: the model reports 12.4 b but its own 4 fF cap supports
11.5 b from thermal noise alone (the combined quantisation + thermal number is
worse, ~11.3 b). That is the concrete bug — not the formula, the missing
`enob_thermal` term. The 106 fF column is the cap that leaves the reported
number intact (thermal ~10 dB below it). Fix: size to
`min(requested N, loop ENOB(OSR))`, floor at the layout minimum, and report
`c_ktc`, `c_layout`, `enob_thermal` so the two can never disagree.

The `c_parasitic_est_ff/OSR` term (20 fF/64 ≈ 0.3 fF at sky130) is small and
should stay; the layout floor is 2.67 fF (sky130 moscap, 0.63 µm² × 4.243) and
1.33 fF (ics55 moscap, 0.12 µm² × 11.06). Those floors are why low-N numbers
look healthy even though the kT/C term is tiny.

### 4.2 Settling: no β, and the clock ceiling is per-PDK

`t_int = ln(2^(q+order+1))/(2π·GBW)` (`feasibility.py:446`) with the budget
`0.5/f_mod`. The expression has no feedback factor: an SC integrator settles
with `τ = 1/(2π·GBW·β)`, and `β = C_s/(C_s+C_i) ≈ 0.5` here, so the model is
**2× optimistic on every row**. The GBW is also a scalar: the real
`g_m/C_L` falls as the sampling cap grows, which is exactly the direction the
cap sizing above pushes it.

With `β = 0.5`, `fs_max = min(digital_ceiling, 1/(2·t_int))/OSR`:

| PDK | OSR 16 | OSR 32 | OSR 64 | OSR 128 | binds at OSR 64 |
|---|---|---|---|---|---|
| sky130 | 7.1 MS/s | 3.5 | 1.8 | 0.9 | integrator GBW |
| ics55 | 14.2 MS/s | 7.1 | 3.5 | 1.8 | integrator GBW |
| ihp | 7.1 MS/s | 3.5 | 1.8 | 0.9 | integrator GBW |
| gf180 AS | 3.5 MS/s | 1.8 | 0.9 | 0.4 | integrator GBW |
| gf180 FD | 1.6 MS/s | 0.8 | 0.4 | 0.2 | **digital ceiling (25 MHz)** |

At OSR ≥ 16 the integrator GBW binds on four of five PDKs — and it is an
assumption in every profile. That is the same "no OTA" problem as pipeline
(deep dive 2, §5.1): `sdm.integrator_gbw_mhz` is a placeholder, not a
measurement. The example spec (`fs = 10 kHz`, OSR 64 → `f_mod = 640 kHz`) is
nowhere near any of these limits, so today's passing point is safe; a 1 MS/s
SDM is not.

### 4.3 Finite gain

`integrator_gain_db = 60` (profile default, and `behavioral_sdm.py` uses it as
the per-sample leak `1/A`). For a 2nd-order 1-bit loop at OSR 64 this is
adequate: 60 dB = 1000 ≥ OSR, and the simulation *includes* the leak, so the
9 dB loading already contains it. Do not add a second gain term. For a higher
order or a multibit quantizer, revisit; the requirement rises with order.

### 4.4 Clock jitter

Not modelled, and second-order at these rates. Input-sampling jitter gives
`SNR = −20log10(2π·f_in·σ_t)`; at `f_in = fs/(2·OSR) = 7.8 kHz` (1 MS/s,
OSR 64) and `σ_t = 1 ps`, that is 146 dB. At 10 kS/s it is more than 180 dB.
Add a `clock.jitter_fs_rms` field for completeness, but it must not gate the
SDM at any spec this repo would write. (Contrast TI, where the same 1 ps is
the difference between 50 dB and 80 dB.)

---

## 5. DT vs CT

The model is DT CIFB, and that is the right default:

| | DT (current) | CT |
|---|---|---|
| input sampling | SC, kT/C, needs a switch | RC, no sampling switch, implicit anti-alias |
| coefficient | cap ratios (`A_C`) | `1/(RC)` — resistor and cap spread, PVT |
| jitter | sampled-edge only, second-order | DAC pulse width, first-order in-band |
| reuse | all open prior art is DT or system-level CT Python | `michaelk99/SKY130_CT-DSM` is CT but schematic-only |
| generator fit | cap array + OTA + switch, all existing leaves | needs a precision RC and a tunable CT DAC |

CT's selling point — no anti-alias filter — matters at high `f_in`; at
`fs ≤ 1 MS/s` with bounded input bandwidth it does not. Stay DT. If CT is ever
added, it is a second loop *structure*, not a coefficient change, and it brings
the first-order jitter term in §4.4 into play.

---

## 6. What the L1 model must do

### 6.1 `evaluate_sdm` changes

Keep the closed form and the loading term. Add:

| change | why |
|---|---|
| `binding_constraint ∈ {osr, cap_matching, integrator_settling, clock_ceiling, decimator}` | today it is the max of the *cap* constraints only (`:497`), which is never the real limit |
| `fs_max = min(clock_ceiling, 1/(2·t_settle))/OSR`, always, `t_settle = t_int/β` | `:511` echoes `fs_target` on PASS and ignores GBW/settling on FAIL |
| `t_int` with `β` and `opamp.gbw_mhz` | `:446` is 2× optimistic and load-independent |
| `enob_thermal`, `c_layout_ff`, `c_ktc_ff` | makes the §4.1 disagreement impossible to hide |
| `decimator` block in the report | `{cic_order, cic_r1, halfbands, guard_band, alias_db, droop_db, taps}` |
| `dnl_msb_sigma_lsb` only for `quantizer_bits > 1` | 0.0 is correct for 1-bit, wrong as a silent default |

The cap sizing should use `n_eff = min(N, loop ENOB(OSR))` in the kT/C term.
That single change moves the 12 b cap from 4 fF to ~106 fF and makes the
reported ENOB self-consistent; it does not move the 8 b point because the
layout floor binds there.

### 6.2 Spec / profile

```jsonc
"sdm": {
  "order": 2,
  "integrator_gain_db": 60.0,
  "decimator": { "cic_order": 4, "cic_r1": 4, "guard_band": 0.9 }
}
```

Profile: replace the scalar with the shared `opamp` block (E2, same as
pipeline):

```jsonc
"opamp": { "gain_db": 60.0, "gbw_mhz": 200.0, "noise_uv_rms": 100.0,
           "swing_v": 1.2, "source": "assumption" }
```

`sdm.integrator_gbw_mhz` stays as a backward-compatible alias.

---

## 7. Behavioural model changes

`behavioral_sdm.py` already runs the loop, so the additions are small:

- **Real decimator**: replace `inband_sndr`'s brick wall with the CIC⁴ +
  halfband chain from §3.3 on the same bitstream; report `sndr_ideal_db`,
  `sndr_decimated_db`, `alias_db`, `droop_db`. This is the only change that
  alters a number people will quote.
- **Input-referred thermal noise**: add `kT/C` samples at the input (two
  independent caps), so the DC transfer and the spectrum see the floor the cap
  actually sets. Then `enob_thermal` is measured, not estimated.
- **Mismatch**: for 1-bit, none. For `quantizer_bits > 1`, inject the DAC unit
  mismatch (reuse the SAR Pelgrom helper) and report `dnl_msb_sigma_lsb`.
- **Jitter**: one sample-clock jitter parameter; keep it off by default.

The static transfer (`dc_transfer`, `:113`) is already honest about its
0.25 LSB DNL floor at 4096 samples/point. Keep that caveat.

---

## 8. L3 plan

### 8.1 Leaves

| leaf | content | donor |
|---|---|---|
| `sc_integrator` | sampling + integrating caps, TG/bootstrap, OTA, reset | caps/TG exist; OTA does not (E2) |
| `comparator_1bit` | StrongARM latch, no offset trim needed | `gen/analog` comparator family exists |
| `decimator` | CIC⁴ + 4 halfbands, RTL, Yosys/LibreLane | new; `dac-generator` FIR is spec-only |
| `clk_2ph` | non-overlapping two-phase, break-before-make | new (digital) |

The decimator is standard RTL and is the cheapest new digital block in the
study. It does not need an analog bench.

### 8.2 Benches, in order

| # | bench | measures | note |
|---|---|---|---|
| 1 | OTA AC + settling | A0, GBW, PM at the real `C_L` | gate for §4.2; shared with pipeline |
| 2 | SC integrator | kT/C floor, charge injection, droop | 1-integrator transient + noise |
| 3 | loop transient + FFT | SNDR vs OSR, stability at 0.5/0.9 FS | replace the Python loop |
| 4 | decimator RTL | alias rejection, passband ripple, word width | digital; can run before the loop |
| 5 | PVT sweep | SNDR, offset, `f_mod` over corners | the missing PVT leg from `gen/README.md:238` |

### 8.3 Layout notes

- Input caps: unit cells, common-centroid, dummies; the *absolute* value matters
  (kT/C), the ratio does not — unlike the SAR.
- Keep the first integrator's sampling caps and OTA input pair close; the
  summing node is the sensitive node.
- Separate analog/digital supplies and a guard ring around the OTA; the
  decimator is a large switching load next to it.
- Route the 1-bit feedback as a differential pair to preserve the "no DAC
  mismatch" property — a single-ended return would reintroduce it.

---

## 9. Prior art

| repo | license | content | use |
|---|---|---|---|
| `michaelk99/SKY130_CT-DSM` | Apache-2.0 | CT ΔΣ, sky130; system-level Python with `Decimator` (`cascaded` = sinc², **explicitly incomplete**) | the closest decimator start; CT comparison |
| `dac-generator` `audiodac_dsmod` | in-repo | digital 1st/2nd-order CIFF 1-bit modulator | bitstream source, **not** an analog loop |
| MPW-8 SAR (`dac-generator/README.md:36`) | in-repo docs | boxcar FIR OSR 1/4/16/64/256, sinc¹ | prior art only; too little stopband |
| `w32agobot/SKY130_SAR-ADC` | Apache-2.0 | async SAR + boxcar FIR OSR 1..256 | FIR structure reference |

No open repo was found with a complete, synthesized CIC + halfband decimator
sized for a ΔΣ ADC. CIC⁴ + halfband is new work, but it is textbook and §3.3
sizes it.

---

## 10. Go/no-go

| PDK | credible N | OSR | fs_max | why | blocker |
|---|---|---|---|---|---|
| **sky130** | **10–12** | 44–76 | 1.8 MS/s @64 | loop ENOB 12.4 b; CIC⁴ closes; caps 106 fF | OTA leaf; GBW is assumption |
| ics55 | 10–12 | 44–76 | 3.5 MS/s @64 | 400 MHz GBW assumption is the best of the five | OTA, `analog.status = scaffold` |
| ihp | 10–12 | 44–76 | 1.8 MS/s @64 | same loop; mim layout floor 4.8 fF | OTA; mim/min density assumed |
| gf180 AS | 10 | 44 | 0.9 MS/s @64 | 100 MHz GBW assumption | OTA |
| gf180 FD | 8–10 | 25–44 | 0.4 MS/s @64 | **25 MHz digital ceiling** is the limit | OTA; low `clk_max` |

**Recommendation:** SDM is the first architecture to make production-grade at
L1. Do the decimator model first (it changes a quoted number and is ~150 lines
of Python + RTL), then the `opamp` block and `enob_thermal`. Do **not** chase
12 b/OSR 128: the loop gives 14.9 b after loading, so it is reachable, but the
integrator GBW and the 100+ fF caps are unmeasured and it is not the cheapest
credible point.

**Strategic:** at the repo's 10 kS/s example the SDM is unconstrained by every
PDK and is the right default oversampled architecture. At 1 MS/s it is limited
by an assumed GBW on four of five PDKs, so the honest statement is "1 MS/s is
reachable *if* the OTA meets the assumed GBW" — the same caveat as pipeline.

---

## 11. Risks, ranked

1. **Integrator GBW is an unmeasured scalar** — it binds at OSR ≥ 16 on four of
   five PDKs and the model does not report it as binding.
2. **`enob_thermal` is missing** — the 12 b point claims 0.9 b more than its own
   cap supports.
3. **Decimator is absent, not mis-sized** — cheap to fix, but until it exists
   every SNDR is an upper bound.
4. **Cap sizing targets N, not the loop** — understates the cap 26× at 12 b.
5. **`fs_max` is not a ceiling** — it echoes the target on PASS, so headroom is
   invisible.
6. **No PVT** — the one leg of the roadmap that the SDM shares with every other
   architecture and that no profile currently exercises.
7. **No dither** — undithered 1-bit loops are record-length sensitive below
   ~64k samples (already documented in `gen/README.md:141`); it is a bench
   artefact, not a circuit property.

---

## 12. Effort

- L1 + behavioural: ~200 lines (decimator model + `enob_thermal` + `binding`
  and `fs_max` fixes), plus the `opamp` block and the `sdm.decimator` schema.
- RTL decimator: ~300 lines Verilog + one LibreLane config + one bench.
- L3: SC integrator leaf (OTA + caps + switches), 1-bit comparator, two-phase
  clock, five benches. The OTA is the same missing leaf as pipeline — build it
  once.

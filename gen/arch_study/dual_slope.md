# Deep dive 4 — Dual-Slope Integrating ADC

Companion to `../ARCH_STUDY.md`. Dual-slope only: what cancels and what does
not, the speed ceiling, the integrator sizing that the framework currently
does not model, the GitHub layout search (done before writing this file), the
L1 contract, the L3 plan, and a per-PDK go/no-go. Study only; no code.

Dual-slope is the cheapest architecture to *model* and the hardest to get a
real layout for: the electrical error budget is small and well understood, but
**no open IC-level dual-slope layout exists** (see §5). That asymmetry drives
the verdict.

---

## 1. Topology and why it is cheap

Two phases per conversion, one integrator, one comparator, one counter:

```
run-up     : integrate Vin/R for exactly 2^N clocks        (T1 = 2^N / f_clk)
run-down   : de-integrate Vref/R until the comparator trips (T2 = k / f_clk)
result     : k = round(T2/T1 * 2^N) = round(2^N * Vin/Vref)
```

The same resistor, the same capacitor and the same clock are used in both
phases, so `R·C` and the clock period cancel in the ratio. That ratiometric
cancellation is the whole point: a dual-slope does not need a precision
resistor or a precision capacitor, only a stable *ratio in time*. It is also
why the transfer is monotonic by construction — each output code is one clock
period of run-down, so there is no code-width mechanism to make DNL.

Structurally it is the twin of `ramp`: one hold/integrate cap, one comparator,
one counter, `f_clk`-driven. `evaluate_ramp` (`feasibility.py:272`) is the
right seam to clone. **Its error model is not**: single-slope's DNL comes from
the ramp step, and dual-slope has none. Copying `dnl_lin + dnl_cmp` would model
the wrong thing twice (§2.3).

---

## 2. The error budget

### 2.1 What cancels

| term | why it cancels |
|---|---|
| absolute `R` | same resistor in both phases: `Vin·T1/R = Vref·T2/R` |
| absolute `C` | same capacitor: the charge cancels in the same equation |
| clock period | both phases are counted with the same clock; the ratio is period-free |
| clock frequency drift | first-order common-mode; only the *ratio* T2/T1 is measured |
| supply (to first order) | both phases scale together if the reference is ratiometric |

This is stronger than the ARCH_STUDY's §6.1 sketch, which lists "R ratio" as a
gain-error source. That is only true for a **two-resistor** variant (Vin
through R1, Vref through R2), where `gain = R2/R1` and the pair must match.
The default one-resistor/muxed-input variant has no `R` ratio at all. The
model should say which variant it assumes; the difference is a factor of
`σ_R/R` in gain error.

### 2.2 What does not cancel

| term | mechanism | requirement |
|---|---|---|
| op-amp finite gain `A0` | virtual ground is not zero; charge-transfer error `≈ 1/A0` | `A0 ≥ 2^(N+1)` for 0.5 LSB |
| dielectric absorption (DA) | charge released slowly between phases; memory of the previous conversion | `DA < 0.5/2^N` |
| comparator offset | constant time error → **offset**, not DNL; removable by auto-zero | `< 0.5 LSB` after auto-zero |
| comparator delay `t_d` | adds a fixed time to T2 → offset; its slope dependence → INL | `t_d·f_clk < ~1 code`, variation `< 0.1 code` |
| comparator noise | trip-point jitter → **noise**, not DNL | `< 0.5 LSB` rms |
| kT/C on the integrator | sampled noise, integrated over T1 | `C ≥ 12kT·2^(2N)/Vfs²` |
| resistor nonlinearity | voltage coefficient + self-heating → INL | ppm-level; the high-N limit |
| input bias current | `I_b·R` → offset; drifts with temperature | `< 0.5 LSB` |

The first two are the real high-resolution limits. DA is the reason bench
multimeters use special integrating capacitors; a moscap/MIM bank on a CMOS
PDK has DA in the 0.01–0.1 % range, which is already at the 12 b edge and
hopeless at 16 b without compensation. **DA is unmeasured in every profile.**

### 2.3 The corrected budget

`A0`, `kT/C` and the DA/offset requirements at `Vfs = 1.8 V`:

| N | 8 | 10 | 12 | 14 | 16 |
|---|---|---|---|---|---|
| `A0` for 0.5 LSB | 54.2 dB | 66.2 dB | 78.2 dB | 90.3 dB | 102.3 dB |
| kT/C cap | 1.0 fF | 16.1 fF | 257 fF | 4.1 pF | 65.9 pF |
| DA for 0.5 LSB | 0.195 % | 0.049 % | **0.0122 %** | 0.0031 % | 0.00076 % |
| offset budget (0.5 LSB) | 3.52 mV | 0.88 mV | **0.22 mV** | 55 µV | 13.7 µV |

Against the profile numbers: sky130's comparator offset is **500.4 µV**, which
is 0.07 LSB at 8 b but **1.1 LSB at 12 b** — so 12 b requires auto-zero or
calibration, and 16 b requires both plus a better comparator. ics55's
**1200 µV** is an assumption and is 0.68 LSB at 10 b, already marginal.

Two corrections to the ARCH_STUDY sketch:
- `DNL ≈ 0` is right, but report it as a **bound**, not as `0.0`. The residual
  is the comparator-delay *variation* with input slope and the clock-jitter
  contribution, both ≪ 1 LSB at 8–12 b.
- Comparator noise belongs in **SNR**, not DNL. The ramp evaluator's
  `dnl_cmp` (`feasibility.py:306`) must not be cloned.

---

## 3. Speed — the decisive number

```
codes = 2^(N+1) + overhead,   overhead = 4   (auto-zero, reset, latch, handshake)
fs    = f_clk / codes
```

`f_clk` is the ramp backend's already-characterized `clk_max_mhz`:

| PDK | f_clk | 8 b | 10 b | 12 b | 16 b |
|---|---|---|---|---|---|
| gf180 FD | 25 MHz | 48.4 kS/s | 12.2 kS/s | 3.1 kS/s | 0.2 kS/s |
| gf180 AS | 150 MHz | 290.7 kS/s | 73.1 kS/s | 18.3 kS/s | 1.1 kS/s |
| sky130 | 300 MHz | 581.4 kS/s | 146.2 kS/s | 36.6 kS/s | 2.3 kS/s |
| ihp | 400 MHz | 775.2 kS/s | 194.9 kS/s | 48.8 kS/s | 3.1 kS/s |
| ics55 | 479 MHz | **928.3 kS/s** | 233.4 kS/s | 58.4 kS/s | 3.7 kS/s |

**The repo's 1 MS/s / 8 b target is unreachable on every PDK** — it needs
≥ 512 MHz and the fastest profile is 479 MHz. The closest point is ics55 at
928 kS/s. This is not a framework limitation; it is `2^(N+1)` clocks per
sample. Any dual-slope spec in this repo must start at ≤ 900 kS/s (8 b) or
accept tens of kS/s at 12 b.

---

## 4. Integrator sizing — the part the framework is missing

The kT/C floor (257 fF at 12 b) is *not* how the integrator is sized. The
integration time sets the `R·C` product from the output swing:

```
Vint,max = Vin,max · T1 / (R·C)      ==>   R·C = Vin,max · T1 / Vint,max
```

with `Vint,max ≈ 1.0 V` and `Vin,max = 1.8 V`. At ics55:

| N | T1 = 2^N/f_clk | R·C | R at C=100 pF | R at C=1 nF |
|---|---|---|---|---|
| 8 | 0.53 µs | 0.96 µs | 9.6 kΩ | 0.96 kΩ |
| 10 | 2.14 µs | 3.85 µs | 38.5 kΩ | 3.85 kΩ |
| 12 | 8.55 µs | 15.4 µs | 154 kΩ | 15.4 kΩ |
| 16 | 137 µs | 246 µs | 2.46 MΩ | 246 kΩ |

Cap area for those two choices, sky130 moscap (4.243 fF/µm²) / ics55
(11.06 fF/µm²):

| C | sky130 | ics55 | ihp mim (1.2) | gf180 mim (1.0) |
|---|---|---|---|---|
| 100 pF | 0.024 mm² | 0.009 mm² | 0.083 mm² | 0.100 mm² |
| 1 nF | 0.236 mm² | 0.090 mm² | 0.833 mm² | 1.000 mm² |

So the design point is a **100 pF–1 nF integrator cap and a 10 kΩ–2.5 MΩ
resistor**, not the 257 fF kT/C minimum. The framework currently computes only
the kT/C floor (as the `ramp` clamp does) and would report a 257 fF cap that
cannot hold the integration. This is the single most important missing term.

The area optimum is a trade: bigger `C` buys lower kT/C and lower `R`, but
costs cap area; bigger `R` costs resistor area and adds thermal noise
(`4kTR·BW`) and leakage sensitivity. At 8–12 b a 100 pF / 10–154 kΩ point is
reasonable; at 16 b the DA limit dominates before the area does.

If the cap is allowed **off-chip** (bench-multimeter style), the area argument
disappears and only leakage/DA of the package pin remain. The repo targets
integrated macros, so treat off-chip as a spec option, not the default.

---

## 5. Layout prior art on GitHub — the actual search

Searched 2026-11-09 with the authenticated GitHub API (repo search, code
search) plus `gh_grep`. The result is the reason this architecture has a
different risk profile from flash/pipeline.

### 5.1 Dual-slope: nothing at IC level

| query | repositories | what they actually contain |
|---|---|---|
| `dual-slope ADC` / `dual slope adc` | 27 | MCU timers, 8051/STM32 demos, VHDL/Verilog controllers, HTML simulators |
| top "design" hit `mumaroffice/Analogue-to-Digital-Converter` | Apache-2.0 | **3 files**: `README.md`, `dsadc.pdf`, `LICENSE`. No netlist, no layout |
| `slope ADC` | 36 | PCB/discrete (KiCad), FPGA, Verilog; the highest-starred is `Rerouter/Multislope-ADC-PCB` (KiCad **PCB**, not CMOS) |
| `Wilkinson ADC` | 0 | — |
| `ramp ADC` | 18 | counter-ramp Verilog / FPGA demos; one CMOS image-sensor repo with a 2-step ramp ADC (no dual-slope) |

**No repository was found with a dual-slope schematic, let alone a DRC/LVS
layout.** The only real multislope hardware is a through-hole KiCad board.
This is the weakest layout prior-art area of all five architectures — worse
than flash, where at least a teaching schematic exists.

### 5.2 What exists and is reusable: the SAR layout corpus

Several open repos now carry *complete, sign-off* SAR layouts. They are not
dual-slope, but they contain exactly the leaves a dual-slope front end needs:
a StrongARM comparator, bootstrapped/TG switches, common-centroid cap arrays,
guard rings, and a digital macro hardened with LibreLane.

| repo | license | PDK | what is in it | use for dual-slope |
|---|---|---|---|---|
| `lmm2024/sar-adc-8b-open` | **Apache-2.0** | IHP SG13G2 | 8 b 10 MS/s, `core18` sign-off, GDS + OAS, Magic full-RC PEX, ENOB 7.96, PDN/supply-return fix writeup | **best reusable L3 reference**: comparator, bootstrap, cap array, mixed-signal PEX flow |
| `LewisLiuLiuLiu/open-analog-sar-8b` | **no LICENSE file** (all rights reserved) | IHP SG13G2 | 8 b 10 MS/s, layout-as-code KLayout PCells, 322×183 µm core, 1.6×1.6 mm chip, DRC 0 / LVS match, 604-parasitic PEX | inspect-only; the most complete *flow* documentation |
| `Shadow515/sar-adc-sky130` | no license file | sky130 | 8 b, DRC 0 / LVS match, Magic `.mag`, 256-unit common-centroid MIM CDAC (~2 pF), 11T StrongARM mirror-symmetric | sky130 comparator/cap-array **layout pattern**; inspect-only |
| `ADC-TEAM2020/avsdadc_3v3` | **Apache-2.0** | sky130 | 20 Magic `.mag` files (comparator, clock divider, logic), 2020, "needs optimization" | reusable sky130 `.mag` cells |
| `niztg/Sky130-SAR-ADC` | none | sky130 | 10 b 1 MS/s, xschem + ngspice, bootstrapped-switch n-well bug writeup | **no layout** — but the bootstrap bug is the one to read before using a TG |
| `yohanesstef/UNIC-CASS-2024` | none | sky130 | Magic `.mag` for bootstrap switch, cap array | pattern reference |

Two hard facts from this corpus:
1. **The sky130 comparator and cap-array layouts are mature enough to copy
   structurally** (mirror-symmetric StrongARM, common-centroid MIM, dummies,
   guard ring).
2. **Nobody has published a dual-slope-specific layout** — the resistor and
   the integrator composition are ours to write. The resistor is the gap, and
   the in-repo `dac-generator` work is the only starting point: `analog/string`
   is pre-layout SPICE only (255 units, DNL 0.133 LSB at tt/25, ideal follower,
   no GDS), and `analog/r2r` has a GDS but DRC is open (7 density markers) and
   settling is ~1 µs. Neither is sign-off clean.

### 5.3 Layout generators

| repo | license | what it does | fit |
|---|---|---|---|
| `patrickschulz/openPCells` | GPL-3.0 | Lua PCells → GDS; MOS, caps, guard rings, metal grids, standard cells | strong generator, but **GPL** — a problem for an Apache-2.0 flow |
| `AyarLabs/ACG` | BSD-3 | BAG2 plugin for custom layout generators | permissive; BAG2 dependency |
| `saul-castillo/MatchMaker` | MIT | GF180 common-centroid placement → route → GDS → Magic DRC | permissive; directly useful for the resistor/cap matching |
| `tapeout/analog-generators` | none | BAG scripts for OTA/OPAMP | topology names only |

For a dual-slope the only matched structure that matters is the **two-resistor
pair** (if the two-resistor variant is chosen) and the integrator cap. That is
well inside MatchMaker's or openPCells' capability; no full ADC generator is
needed.

### 5.4 Consequence

The layout plan is **not** "copy a dual-slope layout" (there is none). It is
"compose the front end from SAR-proven leaves (comparator, switches, guard
ring), write the one structure nobody has published (the precision resistor
pair + integrator), and reuse the Apache-2.0 sign-off flow from
`lmm2024/sar-adc-8b-open`." That is more work than pipeline's "copy the MDAC
idea", and less than flash's "write the whole ladder".

---

## 6. What the L1 model must do

### 6.1 `evaluate_dual_slope(spec, profile, ...)`

Clone `evaluate_ramp` for the seam, replace the error model:

```
n, vfs, fs_target
codes      = 2**(n+1) + overhead_clocks            # NOT 2**n
f_clk      = profile.ramp.clk_max_mhz (already characterized)
fs_max     = f_clk / codes
t1         = 2**n / f_clk
r_times_c  = vfs * t1 / v_int_max                  # the missing term
lsb_v      = vfs / 2**n
a0_req_db  = 6.02*(n+1)
da_req_pct = 50.0 / 2**n
offset_uv  = comparator.sigma_uv + opamp.offset_uv + ib*r
delay_lsb  = t_d * f_clk
```

Gates and `binding_constraint`:

| condition | status / binding |
|---|---|
| `fs_max < fs_target` | `FAIL_RATE`, binding `clock` |
| `opamp.gain_db < a0_req_db` | `FAIL_OPAMP_GAIN`, binding `opamp_gain` |
| `da_pct > da_req_pct` | `FAIL_DA`, binding `dielectric_absorption` |
| `offset_uv > 0.5*lsb_uv` and no auto-zero | `FAIL_OFFSET`, binding `offset` |
| `c_integ < r_times_c` | `FAIL_INTEGRATOR`, binding `rc_product` |
| any profile field assumed | `PASS_FEASIBLE_ASSUMPTION` |

Report keys: `fs_max`, `f_clk_hz`, `codes`, `t1_ns`, `rc_us`, `r_ohm`,
`c_integ_pf`, `area_cap_um2`, `a0_req_db`, `da_req_pct`, `offset_lsb`,
`gain_error_pct`, `inl_lsb`, `dnl_bound_lsb`, `sndr_db`. Do **not** emit
`dnl_msb_sigma_lsb` from resistor mismatch; emit `dnl_bound_lsb` from delay
variation and jitter.

### 6.2 Spec / profile

```jsonc
"dual_slope": {
  "variant": "one_resistor",     // one_resistor | two_resistor
  "phases": 2,
  "auto_zero": true,
  "vref": 1.2,
  "v_int_max": 1.0,
  "overhead_clocks": 4
}
```

Profile block (new; `clk_max_mhz` defaults from `ramp`):

```jsonc
"dual_slope": {
  "opamp_gain_db": 80.0,
  "opamp_offset_uv": 100.0,
  "comparator_delay_ns": 0.327,
  "dielectric_absorption_pct": 0.02,
  "ib_na": 1.0,
  "source": "assumption"
}
```

`dielectric_absorption_pct` is the decisive unmeasured number and must be
flagged as such, not silently defaulted to zero.

---

## 7. Behavioural model

`behavioral_dualslope.py` (shape from `behavioral_ramp`):

- integrate for `2^N` clocks, de-integrate and count until the comparator
  trips; add `overhead` cycles;
- inject comparator offset/delay, finite `A0` (charge-transfer error), DA as a
  two-time-constant memory of the previous sample, resistor nonlinearity,
  clock jitter, kT/C;
- ramp scan → `INL`, `offset`, `gain_error`, `missing_codes` (should be 0);
  sine → `SNDR`, `SFDR`;
- report `dnl_bound_lsb`, not a fabricated DNL histogram: a monotonic ADC has
  no code-width mechanism, and the histogram will merely show the ramp step.

Reuse `behavioral_ramp.convert_scan`, `dnl_inl`, `spectral` if present.

---

## 8. L3 plan

### 8.1 Leaves

| leaf | content | donor / status |
|---|---|---|
| precision resistor pair | 10 kΩ–2.5 MΩ, low voltage coefficient, matched pair + dummies | `dac-generator/analog/string` (pre-layout) and `r2r` (GDS, DRC open) — **the gap** |
| `integrator_ota` | A0 80–100 dB, low offset, low 1/f, ~1 V output swing | **none** (E2, shared with pipeline/SDM) |
| `comparator_1bit` | StrongARM latch + auto-zero | `gen/analog/macro_gen.py:173` `topo_cmp_strongarm`; layout pattern in the SAR repos |
| `analog_mux` / auto-zero | TG or dummy-compensated switch | TG leaf exists; mux new, small |
| `counter` | 2^N+1-bit synchronous counter, f_clk | digital, existing flow |
| `integrator_cap` | 100 pF–1 nF moscap/MIM bank | cap leaves exist; array layout pattern from the SAR CDAC repos |

### 8.2 Benches, in order

| # | bench | measures | note |
|---|---|---|---|
| 1 | integrator linearity | charge transfer vs Vin, DA memory, reset | the one architecture-specific bench |
| 2 | comparator + auto-zero | offset before/after, delay vs overdrive | drives the offset/INL split |
| 3 | resistor pair | ratio, voltage coefficient, drift | only for the two-resistor variant |
| 4 | closed-loop ramp | INL/DNL/offset/gain over codes | monotonicity check |
| 5 | PVT | `fs_max`, offset, DA over corners | no profile exercises this today |

### 8.3 Layout notes

- Resistor: unit cells in a matched pair, same orientation, dummies on both
  ends, guard ring; **do not** use a 255-unit string where two resistors are
  enough. The dual-slope's advantage is that absolute value does not matter,
  so prioritise matching and voltage-coefficient symmetry over sheet rho.
- Integrator cap: common-centroid unit bank, bottom-plate to the virtual
  ground, top-plate parasitics kept off the summing node.
- Comparator: mirror-symmetric StrongARM, separate analog supply, guard ring;
  the layout pattern in `Shadow515/sar-adc-sky130` and
  `lmm2024/sar-adc-8b-open` is directly applicable.
- Digital counter: keep it away from the integrator; the summing node and the
  comparator input are the sensitive nodes.
- The switching mux is the charge-injection source; dummy switches on both
  sides and break-before-make.

---

## 9. Prior art (layout-first)

| repo | license | layout | dual-slope relevance |
|---|---|---|---|
| `lmm2024/sar-adc-8b-open` | Apache-2.0 | GDS/OAS + PEX | **reuse the sign-off flow and leaf layouts** |
| `LewisLiuLiuLiu/open-analog-sar-8b` | none (inspect only) | GDS/OAS + PEX | best documented flow; cannot copy |
| `Shadow515/sar-adc-sky130` | none (inspect only) | Magic `.mag`, DRC 0/LVS | comparator + MIM array patterns |
| `ADC-TEAM2020/avsdadc_3v3` | Apache-2.0 | Magic `.mag` | reusable sky130 cells |
| `niztg/Sky130-SAR-ADC` | none | **no layout** | bootstrap n-well bug writeup |
| `mumaroffice/Analogue-to-Digital-Converter` | Apache-2.0 | **none** | a PDF, not a design |
| `Rerouter/Multislope-ADC-PCB` | none | KiCad PCB | discrete reference only |
| `dac-generator/analog/string`, `r2r` | in-repo | string none; r2r GDS, DRC open | the only resistor starting point |

No open dual-slope IC layout, no open multislope CMOS layout, no Wilkinson
layout. State this in the report rather than implying a reusable donor.

---

## 10. Go/no-go

| PDK | credible N | fs_max | why | blocker |
|---|---|---|---|---|
| **ics55** | **8** | **928 kS/s** | fastest clock; 1 MS/s is 7 % out of reach | resistor + OTA + DA unmeasured; `analog.status = scaffold` |
| ihp | 8 | 775 kS/s | 400 MHz | resistor + OTA |
| sky130 | 8 | 581 kS/s | 300 MHz; best layout corpus | resistor + OTA |
| gf180 AS | 8 | 291 kS/s | 150 MHz; r2r/string resistor story | resistor DRC open; OTA |
| gf180 FD | 6–8 | 48 kS/s | 25 MHz | resistor + OTA; low clock |

12 b is reachable at 18–58 kS/s but requires DA ≤ 0.012 %, auto-zero, and
`A0 ≥ 78 dB`; none of those is measured. 16 b is out of scope.

**Verdict:** do the L1 model (it is a ~120-line clone and produces the hard
negative on 1 MS/s), but do **not** schedule the L3 layout as an extension of
the SAR work. It needs a resistor leaf that does not exist and an OTA that does
not exist, and there is no dual-slope layout to copy. The architecture's real
value in this repo is as the *counter-example*: it shows exactly why the
1 MS/s target is a SAR/flash problem, not a general one.

---

## 11. Risks, ranked

1. **No dual-slope layout anywhere** — the resistor + integrator composition
   is new work, not reuse.
2. **DA unmeasured** — decides 12 b; a 0.1 % part fails by 8×.
3. **OTA absent** (E2) — same leaf as pipeline/SDM; without it `A0` is a guess.
4. **Integrator `R·C` not modelled** — the current kT/C-only sizing reports a
   257 fF cap that cannot hold the integration; the real cap is 100 pF–1 nF.
5. **`evaluate_ramp` error model is the wrong clone** — `dnl_cmp` and
   `dnl_lin` must be replaced, not reused.
6. **1 MS/s is structurally unreachable** — 512 MHz needed, 479 MHz available.
7. **Resistor DRC open** in the only in-repo GDS (`r2r`, 7 density markers).

---

## 12. Effort

- L1 + behavioural: ~180 lines (clone `evaluate_ramp`, replace the budget, add
  `rc_us`/`da_req`/`offset_lsb`, one behavioural file).
- L3: resistor pair leaf + OTA leaf + mux/auto-zero + integrator cap bank +
  five benches. The OTA is shared; the resistor is the architecture-specific
  item. This is a separate program, and it starts from a weaker layout base
  than any other architecture in this study.

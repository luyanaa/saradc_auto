# Deep dive 2 — Pipeline ADC

Companion to `../ARCH_STUDY.md`. Pipeline only: stage topology, the error
budget that actually binds, sizing and speed per PDK, the L1 contract, the L3
bench/layout plan, and a go/no-go. Study only; no code.

---

## 1. Topology choice

| variant | stages for 8 b | comparators/stage | MDAC gain | op-amps | verdict |
|---|---|---|---|---|---|
| 1.5 b/stage | 7 | 2 | 2 | 7 | **default**; fastest at fixed GBW, smallest gain error |
| 2.5 b/stage | 4 | 6 | 4 | 4 | alternative; fewer OTAs, reuses the flash sub-ADC, needs 2× GBW and +6 dB gain |
| 3.5 b/stage | 3 | 14 | 8 | 3 | too much sub-ADC for a generator |

Both credible variants use **digital redundancy + shift-add correction**, and
that single fact changes the framework's default intuition:

> **Comparator offset is not the binding constraint in a pipeline.**
> Redundancy absorbs it. The binding constraints are the OTA's finite gain,
> the MDAC capacitor ratio, and — at high N — the sampling switch.

1.5 b/stage is the default because at fixed GBW it is faster: `β = C_f/C_tot =
1/G`, so 2.5 b/stage halves β (2 → 4 gain), which doubles `τ` and needs +6 dB
of DC gain. Its advantage is fewer op-amps (4 vs 7 for 8 b) and a sub-ADC that
is the flash comparator from the previous deep dive.

2.5 b/stage is worth keeping as a spec option because it is the only pipeline
variant that reuses an existing leaf family (flash comparators), and because
OTA count — not cap area — dominates the area/power budget.

MDAC style: flip-around or charge-redistribution, `C_s = (G-1)·C_f`,
bottom-plate sampling. Differential, because the cap matching budget is
already tight.

---

## 2. Error budget

### 2.1 OTA finite DC gain (systematic)

With `β = C_f/C_tot = 1/G`, output error `≈ V_res/(β·A0)` and
`V_res,max = Vfs/2`, require `< 0.5 LSB = 0.5·Vfs/2^N`:

```
A0 >= 2^N / beta                 ==>  A0_dB >= 6.02·N + 6.02·log2(G)
```

| N | 1.5 b (β=0.5) | 2.5 b (β=0.25) |
|---|---|---|
| 8 | **54.2 dB** | 60.2 dB |
| 10 | **66.2 dB** | 72.2 dB |
| 12 | **78.2 dB** | 84.2 dB |

These are the classic numbers. They are *not* in any profile today.

### 2.2 MDAC capacitor ratio (random)

Gain error is not corrected by redundancy, and there is no gain calibration in
the framework. With `σ_gain ≈ √2·σ_Cu/Cu` and `3σ_gain <= 0.5 LSB`:

```
sigma_Cu/Cu <= 1 / (3·sqrt(2)·2^N)      ==>  sigma_DNL = 1/6 LSB at the gate
```

| N | 8 | 10 | 12 |
|---|---|---|---|
| required σ_Cu/Cu | 0.092 % | 0.023 % | 0.0058 % |

Compare with the SAR evaluator's requirement at the same N
(`sigma_target = 0.5/2^((N-1)/2)`): 4.42 % at 8 b, 2.21 % at 10 b. A pipeline
needs **48× better unit matching at 8 b and 95× at 10 b** — because the SAR's
binary array averages mismatch over 2^k units while a 2C/1C MDAC ratio does
not, and because the SAR's DNL gate is 1σ where this one is 3σ.

Consequence: the pipeline unit cap is 10× (sky130) to 2200× (ics55) the
*area* of the SAR unit cap at 8 b — see §3.

### 2.3 kT/C and OTA noise

`C_tot >= 12kT·2^(2N)/Vfs²` is trivially met: at 8 b it is 1.0 fF (sky130),
while matching demands 55 fF of first-stage sampling cap. Mismatch dominates
kT/C by ~50× at every N. OTA input-referred noise is likewise not binding at
8–12 b; report it but do not let it size the caps.

### 2.4 Sampling switch

The sampling network is sized by the *MDAC caps*, not by the SAR's CDAC, and
`t_sample = ln(2^(N+1))·Ron·C_sample,diff`. This is a once-per-cycle term, not
per stage. On ics55 it is decisive — the measured TG is **74.5 kΩ** at
mid-rail, and a 5.9 pF MDAC gives **5.5 µs** per sample (0.2 kS/s). The
assumed bootstrap (60 Ω) gives ~4.4 ns. **A pipeline on ics55 must use a
bootstrapped switch; the measured TG cannot drive it.**

---

## 3. Sizing and speed per PDK

Default cap per PDK (`sky130` moscap, `ics55` moscap, `ihp` mim, `gf180`
mim), 1.5 b/stage, GBW from `sdm.integrator_gbw_mhz` (all assumption) as a
placeholder, `t_cmp = 0.3 ns`:

| PDK | N | A0 req | σ_Cu req | C_unit | C_stage (1 side) | kT/C | fs (OTA) | t_sample | fs (switch) |
|---|---|---|---|---|---|---|---|---|---|
| sky130 | 8 | 54.2 dB | 0.092 % | 27.4 fF | 54.8 fF | 1.0 fF | 57.0 MS/s | 1.06 ns | 942 MS/s |
| sky130 | 10 | 66.2 dB | 0.023 % | 438 fF | 877 fF | 16 fF | 44.3 MS/s | 20.8 ns | 48 MS/s |
| sky130 | 12 | 78.2 dB | 0.006 % | 7.0 pF | 14.0 pF | 257 fF | 36.3 MS/s | 392 ns | 2.5 MS/s |
| ics55 | 8 | 54.2 dB | 0.092 % | 2.93 pF | 5.87 pF | 2.3 fF | 101.8 MS/s | 5.46 µs | **0.2 kS/s** |
| ics55 | 10 | 66.2 dB | 0.023 % | 47 pF | 94 pF | 36 fF | 79.2 MS/s | 107 µs | 9 S/s |
| ihp | 8 | 54.2 dB | 0.092 % | 8.8 fF | 17.7 fF | 2.3 fF | 57.0 MS/s | 0.04 ns | 23 GS/s |
| ihp | 10 | 66.2 dB | 0.023 % | 142 fF | 283 fF | 36 fF | 44.3 MS/s | 0.86 ns | 1.2 GS/s |
| gf180 AS | 8 | 54.2 dB | 0.092 % | 18.9 fF | 37.7 fF | 0.3 fF | 30.3 MS/s | 0.07 ns | 14 GS/s |
| gf180 AS | 10 | 66.2 dB | 0.023 % | 302 fF | 604 fF | 4.8 fF | 23.6 MS/s | 1.38 ns | 724 MS/s |
| gf180 FD | 8 | 54.2 dB | 0.092 % | 10.6 fF | 21.2 fF | 0.1 fF | 18.7 MS/s | 0.03 ns | 31 GS/s |

(12 b rows for ihp/gf180 omitted; all are ≥ 2.3 pF units and switch-limited
in the same way as sky130.)

### 3.1 The binding constraint moves with N

| N | what binds | why |
|---|---|---|
| 8 | **cap matching** | 10–27 fF units on sky130/ihp/gf180; on ics55 2.9 pF because `A_C = 1.5 %·µm` (assumed) is 6.4× worse than sky130 |
| 10 | **OTA GBW** | 44 MS/s vs 48 MS/s switch on sky130 — they are within 10 % of each other |
| 12 | **sampling switch** | 14 pF first-stage cap × 1.55 kΩ TG → 392 ns |

The framework should report all four and set `binding_constraint` to the
winner, exactly as the SAR does. It should not hardcode "OTA".

### 3.2 The ics55 2.9 pF problem is not obviously an artefact

The SAR evaluator's ics55 unit cap is 1.327 fF (layout floor, 0.12 µm²) and
its σ at that area is `1.5/√0.12 = 4.33 %`, just under the SAR requirement of
4.42 %. In other words, **`A_C = 1.5 %·µm` is the value that makes the
existing 8-bit SAR barely work**, so it is at least self-consistent. If it is
real, an 8-bit pipeline on ics55 needs a 2.9 pF unit — not buildable at speed.
Measuring ics55 moscap mismatch is therefore the same first experiment as
measuring comparator σ (both are missing Pelgrom coefficients).

### 3.3 GBW is a placeholder, and the model must say so

`sdm.integrator_gbw_mhz` is a scalar: the real GBW is `g_m/C_L`, so the
ics55 5.9 pF stage would be far slower than the 101.8 MS/s in the table. The
evaluator must (a) read a dedicated `opamp.gbw_mhz`, (b) print
`binding_constraint = "opamp_gbw"` whenever it wins, and (c) never claim
`PASS` on an assumed GBW without the assumption downgrade.

---

## 4. What the pipeline L1 model must do

### 4.1 `evaluate_pipeline(spec, profile, ...)`

```
n, vfs, fs_target, bps          # bps = 1.5 (default) | 2.5 | 3.5
G      = 2**floor(bps)          # 2 / 4 / 8
S      = n - 1                  # 1.5 b/stage; ceil(n/2) for 2.5
beta   = 1/G
ota    = profile.opamp          # E2; fallback sdm.integrator_gbw_mhz
a0_req = 2**n / beta
tau    = 1/(2*pi*ota.gbw_mhz*1e6*beta)
t_set  = (floor(bps)+1)*ln2*tau          # 2·ln2 for 1.5 b
fs_ota = 1 / (S*(t_set + cmp.delay_ns*1e-9))
sigma_cu_req = 1/(3*sqrt(2)*2**n)
cu     = max(mismatch_area, ktc, layout) * density
t_samp = ln(2**(n+1)) * sw.ron_ohm * 2*(G+1)*cu * 1e-15
fs_max = 1 / max(S*(t_set+t_cmp), t_samp)
```

Gates and `binding_constraint`:

| condition | status / binding |
|---|---|
| `ota.gain_db < 20log10(a0_req)` | `FAIL_OPAMP_GAIN`, binding `ota_gain` |
| `fs_ota < fs_target` | `FAIL_SETTLING`, binding `ota_gbw` |
| `1/t_samp < fs_target` | `FAIL_SETTLING`, binding `switch_settling` |
| cap from mismatch | binding `cap_matching` |
| any profile field assumed | `PASS_FEASIBLE_ASSUMPTION` |

Report keys: `cu_ff` = unit, `c_tot_ff` = `(G+1)·cu` (first stage, one side),
`area_cap_um2` = `c_tot/density`, `dnl_msb_sigma_lsb` = `√2·σ_Cu/Cu·2^(N-1)`
(= 1/6 at the sizing point), `fs_max` as above. Extra: `stages`, `a0_req_db`,
`sigma_cu_req_pct`, `cal_residual_lsb`.

### 4.2 Calibration (already in the spec, not in any model)

`spec.calibration.gain` is a boolean (`spec.schema.json:61`). The pipeline is
the architecture that needs it. Minimal model: `cal_residual_lsb` (default
`0.5` when `gain: true`, else uncalibrated), then
`sigma_cu_req = cal_residual_lsb/(3·2^(N-1))`. Each calibration bit buys 4×
cap area. Without this term the model will reject 10 b/12 b on every PDK,
which is the correct *uncalibrated* answer but not the only design point.

### 4.3 Behavioural (`gen/verify/behavioral_pipeline.py`)

- `S` stages, each: sample → 2/6/14 thresholds → DAC level → residue
  `G·(Vin - Vdac)` with finite gain `1/(βA0)`, cap gain error, switch
  thermal noise; comparator offset drawn inside the redundancy window.
- Digital correction: shift-and-add with 1 redundant bit.
- Ramp → DNL/INL/missing codes; sine → SNDR/SFDR. Reuse
  `behavioral_sar.static_scan`, `dnl_inl`, `spectral`.
- Extra metrics: `residue_swing_ok` (flag clipping), `gain_error_pct`,
  `bubble_rate` for 2.5 b sub-ADCs.
- Pipeline is *not* monotonic once gain error exceeds the redundancy range;
  `missing_codes` is a real output, not a formality.

### 4.4 Spec / profile

```jsonc
"pipeline": {
  "bits_per_stage": 1.5,      // 1.5 | 2.5 | 3.5
  "stages": "auto",
  "redundancy_bits": 1,
  "cal_residual_lsb": 0.5
}
```

Profile: add a shared `opamp` block (E2) rather than a pipeline-only one:

```jsonc
"opamp": {
  "gain_db": 60.0, "gbw_mhz": 200.0,
  "noise_uv_rms": 100.0, "swing_v": 1.2,
  "source": "assumption"
}
```

`sdm.integrator_gbw_mhz` stays for backward compatibility and maps onto this
block when it arrives.

---

## 5. L3 plan

### 5.1 Leaves — three, none of which exist

| leaf | content | donor |
|---|---|---|
| `ota` | folded-cascode or two-stage, A0 60–80 dB, GBW 200–400 MHz, full swing | **none** in either repo |
| `mdac` | 2C/1C (or 4C/1C) caps + TG + OTA + reset | caps/TG exist as leaves; the composition does not |
| `clk_2ph` | non-overlapping two-phase clock, break-before-make | none (digital; Yosys) |

### 5.2 Benches, in order

| # | bench | measures | note |
|---|---|---|---|
| 1 | OTA AC | A0, GBW, phase margin vs load cap | the one missing analog measurement |
| 2 | OTA settling | closed-loop step to 0.5 LSB at `β` | must include the real `C_L` |
| 3 | MDAC DC transfer | gain error, residue swing, clipping | drives `σ_Cu` |
| 4 | two-phase clock | non-overlap, charge-injection symmetry | digital + transient |
| 5 | stage cascade | 2–3 stages, DNL/INL with correction | system check |

Bench 1 is the gate for everything else. Without it, every pipeline number is
a placeholder.

### 5.3 Layout notes

- MDAC caps: common-centroid, unit cells, dummies; **ratio 2:1 or 4:1 by unit
  count**, not by drawn size.
- OTA: symmetric floorplan, matched input pair, separate analog supply, guard
  ring; keep the feedback caps close to the summing node.
- Sampling switches: TG or bootstrap with dummy switches; charge injection
  mismatch between the two sides is a gain/offset error.
- Inter-stage routing: the residue path is sensitive; keep it short and
  symmetric, and do not route it over the OTA.

---

## 6. Prior art

| repo | license | content | use |
|---|---|---|---|
| `michaelk99/sg13g2_pipeadc_3b` | Apache-2.0 | 3-bit pipeline, IHP SG13G2; xschem schematics + testbenches, **gm/Id sizing scripts**, Verilog encoder, MATLAB model; **no layout** | the best open stage/OTA reference; its 3-bit stage is the 3.5 b/stage variant |
| `ridvanumaz/2AC_Folded-Cascode-OTA-with-SKY130-PDK` | check | folded-cascode OTA on sky130 | OTA topology candidate |
| `tdextrous/sky130_td_ip__opamp_hp` | check | high-performance op-amp hard IP, sky130 | OTA reference / possible hard macro |
| `tapeout/analog-generators` | check | BAG OTA/OPAMP templates | topology names only (Cadence/BAG, not KLayout) |

No open pipeline **layout** with DRC/LVS was found, and no MDAC generator.

---

## 7. Go/no-go

| PDK | credible N | why | blocker |
|---|---|---|---|
| **sky130** | **8 uncalibrated, 10 calibrated** | 27 fF unit at 8 b, 57 MS/s OTA placeholder; full leaf machinery | OTA leaf absent |
| gf180 AS/FD | 8 | 11–19 fF units; resistor/switch story is the best in dac-generator | OTA absent; comparator/TG assumptions |
| ihp | 8 | 8.8 fF units (mim) | OTA absent |
| ics55 | 8 **only with bootstrap + calibration** | 2.9 pF unit from assumed `A_C = 1.5`; TG 74.5 kΩ makes the sampling network 0.2 kS/s | OTA, bootstrap, mismeasured `A_C`; `analog.status = scaffold` |

**Recommendation:** 8 b on sky130 is the first credible point, and it is the
only one that does not need gain calibration. 10 b is reachable *if* the
calibration term is modelled; 12 b is out of scope for this generator (7 pF
unit + 392 ns sampling switch).

**Strategic:** pipeline beats SAR only above ~10 MS/s. At the repo's 1 MS/s
target a 7-OTA pipeline is strictly more expensive than one comparator array;
the architecture earns its place when the spec moves to ≥ 10 MS/s, which is
the same threshold as flash. If both are added, pipeline is the one that
scales in resolution and flash the one that scales in rate.

---

## 8. Risks, ranked

1. **No OTA anywhere** — the profile block is a placeholder until bench 1.
2. **`A_C` unknown on ics55** — decides whether 8 b is 27 fF or 2.9 pF.
3. **GBW is load-dependent** — the scalar model overstates speed on large caps.
4. **No calibration model** — 10 b/12 b will look impossible when they are
   only expensive.
5. **Two-phase clock** — no leaf, no bench, and it is easy to get wrong
   (non-overlap, charge injection).
6. **Digital correction** — modelled only; no digital handoff exists yet.

---

## 9. Effort

- L1 + behavioural: ~250 lines (one evaluator with the stage loop, one
  behavioural file), plus the `opamp` profile block and schema enum.
- L3: OTA leaf (new device/bench/DRC — the largest item in this study), MDAC
  composition, two-phase clock, five benches. This is a separate program, not
  an extension of the current one.

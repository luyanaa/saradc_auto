# Deep dive 1 — Flash ADC

Companion to `../ARCH_STUDY.md` (cross-architecture study). This file is
Flash only: topology choice, error budget, the L1 model contract, the L3
bench/layout plan, and a per-PDK go/no-go. Study only; no code.

Numbers use the checked-in profiles and the same `measured` vs `assumption`
flags the generator already honours. The one thing that decides this
architecture is unmeasured on **every** PDK — see §2.

---

## 1. Topology choice

| option | comparators (8 b) | extra blocks | verdict |
|---|---|---|---|
| full flash | 255 | ladder + encoder | **chosen** |
| interpolating ×2/×4 | 255 latches, 64/127 preamps | interpolation resistors | defer; only helps input cap/kickback, which do not bind here |
| two-step (4+4) | 2 × 15 | **residue amplifier (OTA)** + DAC | reject: it reintroduces the OTA that flash otherwise avoids |
| folding | ~2^(N/2+1) | folding amps + coarse flash | reject: more analog design, worse for a generator |

Full flash, direct comparator per tap, popcount encoder. At 6–8 bit the
latch count is 63–255, which is affordable in area; the problem is never
count, it is matching and kickback.

**One correction to the usual "flash needs no op-amp":** it needs a
*reference buffer*. 255 comparators kick charge into the ladder on every
clock; the divider must be held by something with low output impedance.
That is an op-amp, but a DC one — a source follower or a 5-transistor OTA is
enough, unlike the pipeline/dual-slope integrator. Keep it out of the L1
model, but count it in the leaf list.

---

## 2. The offset budget — and why the input is not trustworthy

DNL from adjacent comparator offsets (independent, add in quadrature):

```
sigma_DNL / LSB = sqrt(2) * sigma_os / LSB
gate: 3 * sigma_DNL <= dnl_target          (3 sigma over process)
==>   sigma_os <= dnl_target * Vfs / (2^N * 3 * sqrt(2))     [0.118 LSB at 0.5 LSB target]
```

| PDK | Vfs | `sigma_uv` in profile | 6 b budget | 7 b | 8 b | 9 b |
|---|---|---|---|---|---|---|
| sky130A_hd | 1.8 | 500.4 | 3315 | 1657 | **829** | 414 |
| ics55_h7ch | 1.2 | 1200 | **2210** | 1105 | 552 | 276 |
| ihp_sg13g2 | 1.2 | 800 | 2210 | **1105** | 552 | 276 |
| gf180mcu_as_7t3v3 | 3.3 | 1500 | 6077 | 3038 | **1519** | 760 |
| gf180mcu_fd_7t5v0 | 5.0 | 2000 | 9207 | 4604 | **2302** | 1151 |

(µV; bold = passes. `dnl_target = 0.5 LSB`.)

### 2.1 Provenance defect — read this before trusting the table

No profile contains a measured **random** offset distribution:

- **sky130** `sigma_uv: 500.4` is marked `source: measured`, but the same
  block's note says *"systematic offset only; mismatch was not measurable,
  so this is not an offset distribution"* (`sky130A_hd.json`, comparator
  `measurement.note`). `_provenance` (`feasibility.py:620`) only checks the
  word `measured`, so a flash evaluator would report `PASS_FEASIBLE` on a
  number that is not σ.
- **ics55** `sigma_uv: 1200` carries `sigma_source: assumption`; the
  measured 78.12 µV is the *systematic* offset at `Wn=1, Wp=4, L=0.06`.
  Xyce 7.10 `.noise` returns zeros and the mismatch hooks in the flattened
  deck are baked to 0, so ics55 cannot produce σ at all today.
- **ihp / gf180** are `assumption` for every comparator backend.

Consequences:
1. Flash is `PASS_FEASIBLE_ASSUMPTION` on **every** PDK, including sky130 —
   the evaluator must not upgrade on `source: measured` alone.
2. The first L3 experiment for flash is not a faster comparator; it is a
   **Pelgrom mismatch Monte Carlo** to replace `sigma_uv` with a real
   distribution. sky130 can do it with ngspice (the same MC machinery
   `measure.py` already uses for moscap); ics55 cannot until the vendor
   mismatch cards are usable under Xyce.
3. A factor-2 error in σ costs a factor-4 in comparator area — see §2.2.
   The bit verdict is more sensitive to this number than to anything else in
   the design.

### 2.2 Comparator area scaling

`sigma ∝ 1/sqrt(WL)`, so `area_scale = (sigma_have / budget)^2`:

| PDK | N | budget | area scale | input cap | verdict |
|---|---|---|---|---|---|
| ics55 | 6 | 2210 | 1.00 | 3.5 fF | ok |
| ics55 | 7 | 1105 | 1.18 | 4.1 fF | ok |
| ics55 | 8 | 552 | **4.72** | 16.5 fF | needs 4.7× input devices |
| sky130 | 8 | 829 | 1.00 | 3.5 fF | ok |
| sky130 | 9 | 414 | 1.46 | 5.1 fF | plausible if σ is real |
| ihp | 8 | 552 | 2.10 | 7.3 fF | needs 2.1× area |
| gf180 AS | 8 | 1519 | 1.00 | 3.5 fF | exactly at the edge (1500 vs 1519) |
| gf180 FD | 8 | 2302 | 1.00 | 3.5 fF | ok |

Baseline: `C_in ≈ 3.5 fF` at the existing geometry (`(Wn+Wp)·L ≈ 0.3 µm²`
ics55, 0.63 µm² sky130, `2/3·Cox·WL` plus overlap). It scales linearly with
area, which is why the ics55 8-bit case also costs 4.7× input capacitance.

Trimming is **not** the answer: `spec.comparator.trim_bits` exists but a
per-comparator trim DAC (255 of them) is more area than the extra input
device, and a global reference trim does not fix random offset. Spend the
area, not the DAC.

---

## 3. Timing — never the binding constraint here

```
R_tap,max  = 2^(N-2) * Ru          (Thevenin at mid-string)
t_ladder   = ln(2^(N+1)) * R_tap * C_tap
t_cmp      = delay * sqrt(area_scale)      (bigger device = slower)
fs_max     = 1 / (t_ladder + t_cmp + t_enc)
```

with `Ru = 1 kΩ` (the `dac-generator/analog/string` unit), `C_tap = C_in`,
`t_enc = 0.2 ns` placeholder:

| PDK | 6 b | 7 b | 8 b |
|---|---|---|---|
| ics55 | 1.25 GS/s | 776 MS/s | 133 MS/s |
| sky130 | 1.75 GS/s | 1.08 GS/s | 589 MS/s |
| ihp | 1.15 GS/s | 819 MS/s | 270 MS/s |
| gf180 AS | 786 MS/s | 617 MS/s | 417 MS/s |
| gf180 FD | 680 MS/s | 549 MS/s | 385 MS/s |

Even the worst cell (ics55 8 b, 4.7× comparator area, 255 taps) is 133 MS/s.
At the repo's current targets (≤ 1 MS/s) **flash is 100–1000× faster than
needed**, so `fs_max` is decorative and `binding_constraint` will always be
`comparator_offset`. That is the correct output, and it is also the honest
strategic note: at MS/s a 6-bit SAR is smaller and cheaper than a 6-bit
flash. Flash only earns its place at ≥ 100 MS/s.

Power is likewise irrelevant here: the 8-bit ladder is 255 kΩ → 7 µA at
1.8 V (13 µW), and 255 comparators at 1 MS/s are microwatts.

---

## 4. What the flash L1 model must do

### 4.1 `evaluate_flash(spec, profile, headroom, temperature)`

```
n, vfs, fs_target        = spec
cmp                      = profile.comparator[backend]
res                      = profile.resistors[flash.ru]        # E1
sigma_os                 = cmp.sigma_uv
budget                   = dnl_target * vfs / (2^n * 3*sqrt(2))
area_scale               = max(1, (sigma_os/budget)^2)
c_in                     = cmp.input_cap_ff * area_scale
r_tap                    = 2^(n-2) * res.ru_ohm
t_ladder                 = ln(2^(n+1)) * r_tap * c_in * 1e-15
t_cmp                    = cmp.delay_ns * sqrt(area_scale)
t_enc                    = profile.flash.encoder_delay_ns
fs_max                   = 1e9 / (t_ladder + t_cmp + t_enc)
```

Returned keys (must satisfy `cmd_report`, `run.py:174`):

| key | flash value |
|---|---|
| `status` | `FAIL_OFFSET` if budget < sigma_os **and** area_scale > cap (say 8); else `PASS_FEASIBLE*` |
| `cu_ff` | `c_in` (the load one tap drives) |
| `c_tot_ff` | `(2^n - 1) * c_in` |
| `area_cap_um2` | `2^n * res.unit_area_um2` (ladder) — comparator area reported separately |
| `fs_max` | as above |
| `dnl_msb_sigma_lsb` | `sqrt(2) * sigma_os / LSB` (this *is* the DNL, unlike the SAR's MSB-step formula) |
| `binding_constraint` | `comparator_offset` / `ladder_settling` / `comparator_delay` / `encoder_delay` |
| `area_scale`, `n_comparators` | extra, for the human renderer |

Design decision to record: cap `area_scale` at a practical maximum (e.g. 8×)
and fail above it. Otherwise an 8-bit ics55 flash "passes" by asserting a
33 fF input device and a 40× area, which no one would build.

### 4.2 Behavioural (`gen/verify/behavioral_flash.py`)

- Draw `2^n - 1` offsets `N(0, sigma_os)`; optional preamp stage with gain
  `A_p` that divides latch offset by `A_p` and adds its own.
- Ramp and sine: threshold each comparator, popcount the thermometer word,
  optionally inject bubble errors from offset ordering.
- Reuse `behavioral_sar.static_scan`, `dnl_inl`, `spectral` unchanged.
- Extra metric worth printing: `bubble_rate` and `missing_codes` (popcount is
  monotonic, so missing codes should be 0 — a good sanity check on the model).

### 4.3 Spec / profile additions

```jsonc
// spec
"flash": {
  "encoder": "popcount",      // or "priority"
  "interpolate": 1,           // 1|2|4
  "preamps": false,
  "ru_ohm": 1000
}

// profile (per PDK)
"flash": {
  "encoder_delay_ns": 0.2,
  "ladder_unit_area_um2": 11.4,
  "source": "assumption"
},
"resistors": [
  { "name": "poly_unit", "rsh_ohm_sq": 350, "unit_w_um": 2.0,
    "unit_l_um": 5.714, "ru_ohm": 1000, "ac_pct_um": 0.5,
    "source": "assumption" }
]
```

`comparator.<backend>` needs two new fields, `input_cap_ff` and
`area_um2`, or the area-scaling model is unanchored.

---

## 5. L3 plan — benches and layout

### 5.1 Benches, in order

| # | bench | measures | why first |
|---|---|---|---|
| 1 | comparator mismatch MC | `sigma_os` distribution | the only input that decides the bit count; nothing else matters until it exists |
| 2 | ladder DC sweep | ladder-only DNL/INL, tap routing | separates resistor error from comparator error (dac-generator proved the method: pre-layout DNL 0.133 LSB at 8 b, tt/25) |
| 3 | two-comparator kickback | tap disturbance vs a quiet neighbour | flash's real failure mode; cheap to build |
| 4 | 6-bit array transient | bubble count, sparkle codes, encoder delay | system-level check at the target rate |

Bench 1 is only possible where mismatch models work: **sky130 / gf180**
(ngspice), not ics55 (Xyce `.noise` zeros; hooks at 0). Bench 2–4 work on any
PDK with a deck.

### 5.2 Layout notes

- Ladder: unit resistors abutted, dummies at both ends, **Kelvin taps**, and
  tap metal of matched length/width. `dac-generator/analog/string` (pre-layout
  SPICE, ideal follower, tt/25 only, no GDS) found that the *switch tree and
  tap routing*, not the resistor units, set its 0.133 LSB DNL — a flash has no
  TG tree, but its tap routing plays the same role.
- Comparator array: rows of identical cells, all the same orientation,
  dummies at the row ends, shared bias, guard ring, separate analog/digital
  supplies, and input routes of matched length from the ladder.
- Reference buffer next to the ladder, not at the far end.
- Rough area: 255 comparators at ~10×20 µm pitch ≈ 0.05 mm², ladder ~0.03 mm²
  (255 × 11.4 µm²), so ~0.1 mm² for 8 bit — dominated by routing, not devices.

---

## 6. Per-PDK go/no-go

| PDK | best credible N | why | blocker |
|---|---|---|---|
| **sky130** | **8** | full leaf machinery (`layout_array.py`), measured cap/TG/comparator delay, ngspice mismatch MC available | σ is a systematic number today; run bench 1 |
| gf180 AS | 8 (edge) | only PDK with a simulated resistor-string result (dac-generator `string`, 3.3 V, pre-layout DNL 0.133 LSB, tt/25, ideal follower) | comparator/cap/TG all assumption in this repo |
| ihp | 7 | — | assumption-only; no reason to prefer it |
| ics55 | **6** | measured delay, deterministic offset 78 µV | `analog.status = scaffold` blocks leaf emission; **no mismatch measurement possible** |
| gf180 FD | 8 | high Vfs helps | 5 V devices, no measured analog |

**Recommendation:** sky130 is the first silicon target (8 bit, 255
comparators is a lot but the leaves exist); ics55 6 bit is the second, after
`analog.status` and mismatch. 8-bit ics55 is a no-go until σ exists — the
4.7× area and 16.5 fF input cap are buildable, the evidence is not.

---

## 7. Risks, ranked

1. **`sigma_os` provenance** — decides everything, measured nowhere. Mitigate
   with bench 1 before any layout.
2. **Kickback** — unquantified; can corrupt the ladder more than offset does.
   Mitigate with bench 3 and, if needed, one preamp per comparator (which
   changes the area and offset model).
3. **Tap routing resistance** — adds to `R_tap` and is layout-dependent;
   include it in the DNL budget, not just in settling.
4. **Reference buffer** — an op-amp after all, but a DC one; keep it small.
5. **Strategic** — at ≤ 1 MS/s flash is the wrong architecture (a 6-bit SAR
   is smaller). Flash is justified only at ≥ 100 MS/s, where this generator
   has no other architecture yet.

---

## 8. Effort

- L1 + behavioural: ~200 lines (one evaluator, one behavioural file), plus
  schema enum and two profile fields. Same order as `ramp`.
- L3: bench 1 is a modification of the existing comparator MC path
  (`measure.py`/`char.py`); benches 2–4 are new but small. Layout is new:
  ladder array + comparator array + encoder, and the encoder is the only
  digital block (Yosys, as in the sibling repos).

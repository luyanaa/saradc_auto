#!/usr/bin/env python3
"""L1 behavioral pipelined ADC: stage cascade, redundancy, residue clipping.

A pipelined converter is a cascade of low-resolution stages.  Each stage
resolves a few bits, subtracts the result with a DAC and amplifies the
residue by G for the next stage:

    d_i   = sub-ADC(v_i)                      d_i in [-G/2, G/2]
    r_i   = G*(v_i - d_i/G)*(1 - eps)        residue, |r_i| <= 1
    v_i+1 = r_i

Redundancy is what makes the topology work: the sub-ADC decision is allowed
to be wrong near a boundary because the next stage's range covers the
displaced residue.  So comparator offset is *not* the binding constraint --
the residue amplifier's finite gain (eps) and the sampling network are.  This
model makes that visible: offset is absorbed, gain error is not, and when the
residue leaves [-1,1] the model says so instead of silently wrapping.

The default is 1.5 b/stage (G=2, 2 comparators, 1 raw bit + redundancy), with
S = N-1 stages.  2.5 b/stage (G=4) uses the same weights and is supported, but
the checked-in examples are all 1.5 b.

No dependencies outside the standard library.
"""

from __future__ import annotations

import json
import math
import os
import random


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------

def stage(v, G, os_hi, os_lo, gain_eps, ktc, rng):
    """One G-way stage.  Returns (decision, residue).

    v is the stage input normalised so full scale is +-1.  The sub-ADC
    decision is the nearest integer to G*v, clipped to [-G/2, G/2]; the two
    drawn offsets shift the decision edges into the redundancy window.
    """
    lo = -(G // 2)
    hi = G // 2
    d = int(math.floor(G * (v + 0.5 * (os_hi - os_lo)) + 0.5))
    d = max(lo, min(hi, d))
    r = G * v - d
    r *= (1.0 - gain_eps)
    if ktc:
        r += rng.gauss(0.0, ktc)
    return d, r


def convert(v, nbits, stages, G, weights, offsets, gain_eps, ktc, rng):
    code = 1 << (nbits - 1)
    r = v
    clipped = 0
    for i in range(stages):
        d, r = stage(r, G, offsets[2 * i], offsets[2 * i + 1],
                     gain_eps, ktc, rng)
        code += d * weights[i]
        if abs(r) > 1.0:
            clipped += 1
    return int(round(min(max(code, 0), (1 << nbits) - 1))), clipped


def convert_scan(nbits, stages, G, weights, offsets, gain_eps, ktc, rng,
                 points_per_code=64, guard=2):
    n_codes = 1 << nbits
    counts = [0] * n_codes
    clipped = 0
    total_points = n_codes * points_per_code
    for k in range(-guard * points_per_code, total_points + guard * points_per_code):
        v = 2.0 * (k + 0.5) / total_points - 1.0
        code, c = convert(v, nbits, stages, G, weights, offsets,
                          gain_eps, ktc, rng)
        counts[code] += 1
        if -1.0 <= v <= 1.0:
            clipped += c
    return counts, clipped


def dnl_inl(counts, points_per_code, interior_only=True):
    dnl = [c / float(points_per_code) - 1.0 for c in counts]
    inl, acc = [], 0.0
    for d in dnl:
        acc += d
        inl.append(acc)
    missing = counts.count(0)
    if interior_only and len(dnl) > 2:
        dnl = dnl[1:-1]
        inl = inl[1:-1]
    return dnl, inl, missing


# --------------------------------------------------------------------------
# fft / dynamic performance (same bench as behavioral_sar)
# --------------------------------------------------------------------------

try:
    from spectral import fft, spectral
except ImportError:  # standalone: python3 gen/verify/behavioral_x.py
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from spectral import fft, spectral

# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run(spec, sigma_cu_rel=0.0, trials=10, seed=1, n_samples=1024,
        bin_index=31, points_per_code=64):
    nbits = int(spec["resolution"])
    pl = spec.get("pipeline", {})
    cal = spec.get("calibration", {})
    bps = float(pl.get("bits_per_stage", 1.5))
    if abs(bps - 1.5) > 1e-9:
        # The cascade, redundancy weights and residue window are derived for
        # the 1.5 b (G=2) stage.  Running 2.5/3.5 b here produced >100 %
        # residue clipping and 40-60 LSB DNL, i.e. silent garbage; those
        # stage counts exist only in evaluate_pipeline (feasibility).
        raise SystemExit(
            f"behavioral_pipeline: bits_per_stage={bps} is not modelled "
            f"(only 1.5 b/stage); 2.5/3.5 b is feasibility-only.")
    stage_bits = int(math.floor(bps))
    G = 1 << stage_bits
    beta = 1.0 / G
    stages = (nbits - 1) if stage_bits == 1 else int(math.ceil(nbits / 2.0))
    stages = int(pl.get("stages", "auto")) if pl.get("stages", "auto") != "auto" else stages

    a0_db = float(pl.get("a0_db", 60.0))
    A0 = 10.0 ** (a0_db / 20.0)
    gain_eps = 1.0 / (beta * A0)
    cal_residual = float(pl.get("cal_residual_lsb", 0.5))
    if cal.get("gain"):
        gain_eps = cal_residual / float(1 << nbits)
    comparator_offset_lsb = float(pl.get("comparator_offset_lsb", 0.5))
    sigma_os = comparator_offset_lsb / float(1 << nbits)
    ktc_lsb = float(pl.get("ktc_lsb", 0.0))
    ktc = ktc_lsb / float(1 << nbits)

    # digital weights: v = sum d_i/G^(i+1), so
    # code = 2^(N-1) + sum d_i * 2^(N-1) / G^(i+1)
    weights = [2.0 ** (nbits - 1 - stage_bits * (i + 1)) for i in range(stages)]

    worst_dnl = worst_inl = 0.0
    missing = 0
    clipped_total = 0
    dynamic = None

    for t in range(trials):
        rng = random.Random(seed + t)
        offsets = [rng.gauss(0.0, sigma_os) for _ in range(2 * stages)]
        counts, clipped = convert_scan(nbits, stages, G, weights, offsets,
                                       gain_eps, ktc, rng, points_per_code)
        dnl, inl, miss = dnl_inl(counts, points_per_code)
        worst_dnl = max(worst_dnl, max(abs(d) for d in dnl))
        worst_inl = max(worst_inl, max(abs(i) for i in inl))
        missing = max(missing, miss)
        clipped_total += clipped

        if t == 0:
            codes = []
            for m in range(n_samples):
                v = 0.9 * math.sin(2.0 * math.pi * bin_index * m / n_samples)
                code, _ = convert(v, nbits, stages, G, weights, offsets,
                                  gain_eps, ktc, rng)
                codes.append(code)
            dynamic = spectral(codes, nbits)

    total_points = trials * ((1 << nbits) + 4) * points_per_code
    return {
        "arch": "pipeline",
        "nbits": nbits,
        "bits_per_stage": bps,
        "stages": stages,
        "stage_gain": G,
        "beta": beta,
        "a0_db": a0_db,
        "gain_error_pct": gain_eps * 100.0,
        "calibrated": bool(cal.get("gain")),
        "comparator_offset_lsb": comparator_offset_lsb,
        "trials": trials,
        "points_per_code": points_per_code,
        "max_dnl_lsb": worst_dnl,
        "max_inl_lsb": worst_inl,
        "missing_codes": missing,
        "residue_clip_rate": clipped_total / float(max(total_points, 1)),
        "sndr_db": dynamic["sndr_db"] if dynamic else None,
        "snr_db": dynamic["snr_db"] if dynamic else None,
        "sinad_db": dynamic["sinad_db"] if dynamic else None,
        "thd_db": dynamic["thd_db"] if dynamic else None,
        "enob": dynamic["enob"] if dynamic else None,
        "sfdr_dbc": dynamic["sfdr_dbc"] if dynamic else None,
    }


def human(r):
    return "\n".join([
        f"behavioral pipeline {r['nbits']}-bit, {r['stages']} x "
        f"{r['bits_per_stage']:.1f} b (G={r['stage_gain']}, "
        f"beta={r['beta']:.2f}), A0={r['a0_db']:.0f} dB, "
        f"gain error {r['gain_error_pct']:.4f}% "
        f"({'calibrated' if r['calibrated'] else 'uncalibrated'}), "
        f"{r['trials']} trials",
        f"  static   max|DNL|={r['max_dnl_lsb']:.3f} LSB  "
        f"max|INL|={r['max_inl_lsb']:.3f} LSB  "
        f"missing codes={r['missing_codes']}",
        f"           residue clipping {r['residue_clip_rate']*100:.3f}% of "
        f"stage evaluations (redundancy absorbs offset, not gain error)",
        f"  dynamic  SNDR={r['sndr_db']:.2f} dB  "
        f"ENOB={r['enob']:.2f} bit  SFDR={r['sfdr_dbc']:.1f} dBc  THD={r['thd_db']:.1f} dB  SNR={r['snr_db']:.1f} dB",
    ])


def main(argv):
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--samples", type=int, default=1024)
    ap.add_argument("--bin", type=int, default=31)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(os.path.expanduser(args.spec), encoding="utf-8") as fh:
        spec = json.load(fh)
    result = run(spec, 0.0, args.trials, args.seed, args.samples, args.bin)
    print(json.dumps(result, indent=2) if args.json else human(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))

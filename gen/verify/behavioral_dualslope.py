#!/usr/bin/env python3
"""L1 behavioral dual-slope integrating ADC.

Two phases, one integrator, one comparator, one counter:

    run-up      integrate Vin/R for exactly 2^N clocks   (T1 = 2^N / f_clk)
    run-down    de-integrate Vref/R until the comparator trips
    code        number of run-down clocks

R, C and the clock cancel in the ratio, so the converter is monotonic by
construction: there is no code-width (DNL) mechanism at all.  The errors that
*do* exist are different ones, and this model makes each of them a named term:

    A0          finite integrator gain leaves a charge-transfer residual
    DA          dielectric absorption remembers the previous peak
    offset      comparator / op-amp input offset (auto-zero reduces it)
    delay       comparator delay, in clocks (a code offset)
    jitter      clock-period noise changes T1/T2
    kT/C        integrator-cap thermal noise

Because a histogram on a monotonic converter only shows the ramp step, the
model reports `dnl_bound_lsb` (delay + jitter) rather than pretending to
measure DNL.  The ramp scan is used for INL/gain/offset/missing codes; the
sine scan is used for SNDR/SFDR, where DA shows up as a memory term.

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

def convert(x, nbits, a0_db, da_frac, offset_lsb, delay_clocks,
            jitter_rms, ktc_lsb, prev_peak, rng):
    """One conversion of input x in [0,1) referenced to Vref.

    x is the run-up fraction; the ideal run-down count is x*2^N.  Returns
    (code, peak) where peak is the run-up peak in codes, kept for the DA
    memory of the next sample.
    """
    n = 1 << nbits
    peak = x * n
    gain = 1.0 - 10.0 ** (-a0_db / 20.0)          # finite-A0 charge residual
    jitter = 1.0 + (rng.gauss(0.0, jitter_rms) if jitter_rms else 0.0)
    da_term = da_frac * prev_peak                  # previous peak remembered
    noise = rng.gauss(0.0, ktc_lsb) if ktc_lsb else 0.0
    code = peak * gain * jitter + da_term + offset_lsb + delay_clocks + noise
    code = int(min(max(round(code), 0), n - 1))
    return code, peak


def convert_scan(nbits, a0_db, da_frac, offset_lsb, delay_clocks,
                 jitter_rms, ktc_lsb, rng, points_per_code=64, guard=2):
    n_codes = 1 << nbits
    counts = [0] * n_codes
    total_points = n_codes * points_per_code
    prev_peak = 0.0
    for k in range(-guard * points_per_code, total_points + guard * points_per_code):
        x = (k + 0.5) / total_points
        prev_peak = x * n_codes
        code, _ = convert(x, nbits, a0_db, da_frac, offset_lsb,
                          delay_clocks, jitter_rms, ktc_lsb, prev_peak, rng)
        counts[code] += 1
    return counts


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

def fft(values):
    n = len(values)
    a = [complex(v) for v in values]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        ang = -2.0 * math.pi / length
        wl = complex(math.cos(ang), math.sin(ang))
        half = length >> 1
        for start in range(0, n, length):
            w = 1.0 + 0.0j
            for k in range(start, start + half):
                u = a[k]
                v = a[k + half] * w
                a[k] = u + v
                a[k + half] = u - v
                w *= wl
        length <<= 1
    return a


def spectral(samples, nbits):
    n = len(samples)
    code_fs = float(1 << nbits)
    mean = sum(samples) / n
    spectrum = fft([(s - mean) / code_fs for s in samples])
    power = [abs(v) ** 2 for v in spectrum[: n // 2]]
    fundamental = max(range(1, n // 2), key=lambda k: power[k])
    sig_bins = {fundamental + d for d in (-1, 0, 1) if 1 <= fundamental + d < n // 2}
    harm_bins = set()
    for h in range(2, 6):
        for d in (-1, 0, 1):
            hb = fundamental * h + d
            if 1 <= hb < n // 2 and hb not in sig_bins:
                harm_bins.add(hb)
    sig = sum(power[k] for k in sig_bins)
    harm = sum(power[k] for k in harm_bins)
    excluded = sig_bins | harm_bins | {0}
    noise = sum(power[k] for k in range(1, n // 2) if k not in excluded)
    if sig <= 0 or noise <= 0:
        return {"sndr_db": None, "enob": None, "fundamental_bin": fundamental}
    sndr = 10.0 * math.log10(sig / noise)
    return {"sndr_db": sndr, "enob": (sndr - 1.76) / 6.02,
            "fundamental_bin": fundamental,
            "harmonic_power_db": 10.0 * math.log10(harm / sig) if harm > 0 else None}


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run(spec, sigma_cu_rel=0.0, trials=10, seed=1, n_samples=1024,
        bin_index=31, points_per_code=64):
    """sigma_cu_rel is accepted for the common driver signature; a dual-slope
    integrator's R*C is a ratio in both phases, so unit-cap mismatch does not
    enter the transfer."""
    nbits = int(spec["resolution"])
    ds = spec.get("dual_slope", {})
    a0_db = float(ds.get("a0_db", 80.0))
    da_frac = float(ds.get("da_pct", 0.02)) / 100.0
    offset_lsb = float(ds.get("offset_lsb", 0.0))
    delay_clocks = float(ds.get("delay_clocks", 0.0))
    jitter_rms = float(ds.get("jitter_rms", 0.0))
    ktc_lsb = float(ds.get("ktc_lsb", 0.0))
    f_clk_mhz = float(ds.get("f_clk_mhz", 479.0))

    worst_dnl = worst_inl = 0.0
    missing = 0
    dynamic = None

    for t in range(trials):
        rng = random.Random(seed + t)
        counts = convert_scan(nbits, a0_db, da_frac, offset_lsb, delay_clocks,
                              jitter_rms, ktc_lsb, rng, points_per_code)
        dnl, inl, miss = dnl_inl(counts, points_per_code)
        worst_dnl = max(worst_dnl, max(abs(d) for d in dnl))
        worst_inl = max(worst_inl, max(abs(i) for i in inl))
        missing = max(missing, miss)

        if t == 0:
            codes = []
            prev_peak = 0.0
            for m in range(n_samples):
                x = 0.5 + 0.49 * math.sin(
                    2.0 * math.pi * bin_index * m / n_samples)
                code, prev_peak = convert(
                    x, nbits, a0_db, da_frac, offset_lsb, delay_clocks,
                    jitter_rms, ktc_lsb, prev_peak, rng)
                codes.append(code)
            dynamic = spectral(codes, nbits)

    # a monotonic converter has no code-width mechanism: the DNL is a bound
    # set by the timing terms, not a histogram result
    delay_variation_clocks = float(ds.get("delay_variation_ns", 0.0)) * f_clk_mhz * 1e-3
    dnl_bound = delay_variation_clocks + jitter_rms * (1 << nbits)
    return {
        "arch": "dual_slope",
        "nbits": nbits,
        "a0_db": a0_db,
        "da_pct": da_frac * 100.0,
        "offset_lsb": offset_lsb,
        "delay_clocks": delay_clocks,
        "trials": trials,
        "points_per_code": points_per_code,
        "max_dnl_lsb": worst_dnl,
        "max_inl_lsb": worst_inl,
        "dnl_bound_lsb": dnl_bound,
        "missing_codes": missing,
        "sndr_db": dynamic["sndr_db"] if dynamic else None,
        "enob": dynamic["enob"] if dynamic else None,
        "sfdr_dbc": dynamic["harmonic_power_db"] if dynamic else None,
    }


def human(r):
    return "\n".join([
        f"behavioral dual-slope {r['nbits']}-bit, A0={r['a0_db']:.0f} dB, "
        f"DA={r['da_pct']:.4f}%, {r['trials']} trials, "
        f"{r['points_per_code']} points/code",
        f"  static   max|DNL|={r['max_dnl_lsb']:.3f} LSB  "
        f"max|INL|={r['max_inl_lsb']:.3f} LSB  "
        f"missing codes={r['missing_codes']}",
        f"           DNL is a bound: {r['dnl_bound_lsb']:.3f} LSB "
        f"(delay variation + jitter), not a histogram",
        f"  dynamic  SNDR={r['sndr_db']:.2f} dB  "
        f"ENOB={r['enob']:.2f} bit  worst harmonic={r['sfdr_dbc']:.1f} dBc",
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

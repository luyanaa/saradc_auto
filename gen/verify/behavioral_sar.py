#!/usr/bin/env python3
"""L1 behavioral SAR model: static DNL/INL and dynamic ENOB.

This is the portable reference model, not a transistor simulation. It models

  * binary cap weights with per-unit capacitor mismatch (Pelgrom)
  * comparator input-referred offset
  * comparator input-referred noise

so that a spec can be rejected before any layout exists, and so that the same
model can be compared against the taped-out sg13g2 chip's known weights.

No dependencies outside the standard library.
"""

from __future__ import annotations

import json
import math
import os
import random
import sys


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------

def make_weights(nbits, sigma_cu_rel, rng):
    """Binary weights in unit-capacitor units, with mismatch on each cell."""
    weights = []
    for bit in range(nbits):
        nominal = 1 << bit
        # the cell is 2^bit units in parallel: absolute errors add in
        # quadrature, so the cell's relative sigma is sigma_unit/sqrt(2^bit)
        sigma = sigma_cu_rel / math.sqrt(nominal)
        weights.append(nominal * (1.0 + rng.gauss(0.0, sigma)))
    return weights


def convert(x, weights, total, offset_lsb, sigma_lsb, rng):
    """One SAR conversion. x, offset and sigma are normalised to full scale."""
    code = 0
    trial_sum = 0.0
    for bit in range(len(weights) - 1, -1, -1):
        trial = trial_sum + weights[bit]
        threshold = trial / total + offset_lsb
        sample = x + (rng.gauss(0.0, sigma_lsb) if sigma_lsb else 0.0)
        if sample >= threshold:
            code |= 1 << bit
            trial_sum = trial
    return code


def static_scan(nbits, weights, total, offset_lsb, sigma_lsb, rng, points_per_code=64):
    """Fine input ramp, histogram of output codes.

    One point per code cannot see DNL: every decision keeps a half-LSB margin
    and small weight errors never flip it. A fine ramp measures the actual
    code bin widths instead.
    """
    n_codes = 1 << nbits
    counts = [0] * n_codes
    total_points = n_codes * points_per_code
    for k in range(total_points):
        x = (k + 0.5) / total_points
        counts[convert(x, weights, total, offset_lsb, sigma_lsb, rng)] += 1
    return counts


def dnl_inl(counts, points_per_code):
    """Code-density DNL/INL; the ideal bin holds points_per_code samples."""
    dnl = [c / float(points_per_code) - 1.0 for c in counts]
    inl = []
    acc = 0.0
    for d in dnl:
        acc += d
        inl.append(acc)
    return dnl, inl, counts.count(0)


# --------------------------------------------------------------------------
# fft / dynamic performance
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
    """Coherent-sine SNDR/ENOB. samples must have a power-of-two length."""
    n = len(samples)
    mean = sum(samples) / n
    spectrum = fft([s - mean for s in samples])
    power = [abs(v) ** 2 for v in spectrum[: n // 2]]

    fundamental = max(range(1, n // 2), key=lambda k: power[k])
    sig_bins = set()
    for d in (-1, 0, 1):
        if 1 <= fundamental + d < n // 2:
            sig_bins.add(fundamental + d)

    # first five harmonics (skip DC and the fundamental cluster)
    harm_bins = set()
    for h in range(2, 6):
        hb = fundamental * h
        for d in (-1, 0, 1):
            if 1 <= hb + d < n // 2 and (hb + d) not in sig_bins:
                harm_bins.add(hb + d)

    sig = sum(power[k] for k in sig_bins)
    harm = sum(power[k] for k in harm_bins)
    excluded = sig_bins | harm_bins | {0}
    noise = sum(power[k] for k in range(1, n // 2) if k not in excluded)

    if sig <= 0 or noise <= 0:
        return {"sndr_db": None, "enob": None, "fundamental_bin": fundamental}

    sndr = 10.0 * math.log10(sig / noise)
    return {
        "sndr_db": sndr,
        "enob": (sndr - 1.76) / 6.02,
        "fundamental_bin": fundamental,
        "harmonic_power_db": 10.0 * math.log10(harm / sig) if harm > 0 else None,
    }


def dynamic_scan(nbits, weights, total, offset_lsb, sigma_lsb, rng, n_samples=1024, bin_index=31):
    codes = []
    for m in range(n_samples):
        x = 0.5 + 0.49 * math.sin(2.0 * math.pi * bin_index * m / n_samples)
        codes.append(convert(x, weights, total, offset_lsb, sigma_lsb, rng))
    return codes


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run(spec, sigma_cu_rel, trials=10, seed=1, n_samples=1024, bin_index=31,
        points_per_code=64):
    nbits = int(spec["resolution"])
    offset_lsb = float(spec.get("comparator_offset_lsb", 0.0)) / (1 << nbits)
    sigma_lsb = float(spec.get("comparator_noise_lsb", 0.0)) / (1 << nbits)

    worst_dnl = 0.0
    worst_inl = 0.0
    missing = 0
    dynamic = None

    for t in range(trials):
        rng = random.Random(seed + t)
        weights = make_weights(nbits, sigma_cu_rel, rng)
        # the array holds 2^N units; only 2^N-1 are switched, one is the
        # fixed dummy, so the reference is the nominal 2^N, not sum(weights)
        total = float(1 << nbits)

        counts = static_scan(nbits, weights, total, offset_lsb, sigma_lsb, rng,
                             points_per_code)
        dnl, inl, miss = dnl_inl(counts, points_per_code)
        worst_dnl = max(worst_dnl, max(abs(d) for d in dnl))
        worst_inl = max(worst_inl, max(abs(i) for i in inl))
        missing = max(missing, miss)

        if t == 0:
            samples = dynamic_scan(nbits, weights, total, offset_lsb, sigma_lsb,
                                   rng, n_samples, bin_index)
            dynamic = spectral(samples, nbits)

    return {
        "nbits": nbits,
        "sigma_cu_rel": sigma_cu_rel,
        "trials": trials,
        "max_dnl_lsb": worst_dnl,
        "max_inl_lsb": worst_inl,
        "missing_codes": missing,
        "sndr_db": dynamic["sndr_db"] if dynamic else None,
        "enob": dynamic["enob"] if dynamic else None,
        "sfdr_dbc": dynamic["harmonic_power_db"] if dynamic else None,
    }


def human(r):
    return "\n".join([
        f"behavioral {r['nbits']}-bit, sigma_Cu/Cu={r['sigma_cu_rel']*100:.2f}%, "
        f"{r['trials']} mismatch trials",
        f"  static   max|DNL|={r['max_dnl_lsb']:.3f} LSB  "
        f"max|INL|={r['max_inl_lsb']:.3f} LSB  missing codes={r['missing_codes']}",
        f"  dynamic  SNDR={r['sndr_db']:.2f} dB  "
        f"ENOB={r['enob']:.2f} bit  worst harmonic={r['sfdr_dbc']:.1f} dBc",
    ])


def main(argv):
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--sigma-cu", type=float, default=None,
                    help="relative unit-cap mismatch (default: from --from-feasibility)")
    ap.add_argument("--from-feasibility", help="feasibility JSON report to take sigma_Cu from")
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--samples", type=int, default=1024)
    ap.add_argument("--bin", type=int, default=31)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(os.path.expanduser(args.spec), encoding="utf-8") as fh:
        spec = json.load(fh)

    sigma = args.sigma_cu
    if sigma is None and args.from_feasibility:
        with open(os.path.expanduser(args.from_feasibility), encoding="utf-8") as fh:
            sigma = json.load(fh)["sigma_cu_rel"]
    if sigma is None:
        sigma = 0.0

    result = run(spec, sigma, args.trials, args.seed, args.samples, args.bin)
    print(json.dumps(result, indent=2) if args.json else human(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

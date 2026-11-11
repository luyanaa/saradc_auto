#!/usr/bin/env python3
"""L1 behavioral single-slope ramp ADC: DNL/INL and ENOB.

The conversion is one comparison per clock against a monotonic ramp:

    code = min{ k : V_ramp(k) >= V_in + offset + noise }

So the code transfer *is* the ramp's step error sequence.  That is different
from the SAR model, where DNL comes from capacitor mismatch: here a unit-cap
mismatch is only a gain error, and the DNL is the ramp generator's own
step-to-step linearity.  Modelling it that way is the point -- it makes the
ramp linearity requirement visible as a number instead of a hand wave.

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

def make_ramp(nbits, linearity_lsb, rng):
    """Ramp step heights in LSB, each perturbed by the generator's DNL.

    linearity_lsb is the 1-sigma step error.  The steps are normalised back
    to 2^N total so a linearity error does not also become a gain error:
    a real ramp is trimmed at full scale, and only the step-to-step variation
    is what a code-density test sees.
    """
    steps = [1.0 + rng.gauss(0.0, linearity_lsb) for _ in range(1 << nbits)]
    total = sum(steps)
    return [s * (1 << nbits) / total for s in steps]


def convert(x, cum, offset_lsb, sigma_lsb, rng):
    """First ramp step at or above the sampled input.  x in [0,1)."""
    target = x * (1 << (len(cum).bit_length() - 1))
    # bisect on the cumulative ramp
    lo, hi = 0, len(cum)
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] >= target:
            hi = mid
        else:
            lo = mid + 1
    return min(lo, len(cum) - 1)


def convert_scan(nbits, steps, offset_lsb, sigma_lsb, rng, points_per_code=64,
                 guard=2):
    """Uniform input sweep with a few code widths of over-range at each end.

    The two end codes are open on one side and are only fully populated when
    the sweep goes past full scale; dnl_inl drops them.
    """
    cum, acc = [], 0.0
    for s in steps:
        acc += s
        cum.append(acc)
    n_codes = 1 << nbits
    counts = [0] * n_codes
    total_points = n_codes * points_per_code
    for k in range(-guard * points_per_code, total_points + guard * points_per_code):
        x = (k + 0.5) / total_points + offset_lsb / n_codes
        if sigma_lsb:
            x += rng.gauss(0.0, sigma_lsb) / n_codes
        counts[convert(x, cum, 0.0, 0.0, rng)] += 1
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

try:
    from spectral import fft, spectral
except ImportError:  # standalone: python3 gen/verify/behavioral_x.py
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from spectral import fft, spectral

# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run(spec, sigma_cu_rel=0.0, trials=10, seed=1, n_samples=1024, bin_index=31,
        points_per_code=512):
    """sigma_cu_rel is accepted for a common driver signature; a single hold
    capacitor's mismatch is a gain error and does not enter DNL."""
    nbits = int(spec["resolution"])
    ramp = spec.get("ramp", {})
    linearity = float(ramp.get("linearity_lsb_dnl", 0.25))
    offset_lsb = float(spec.get("comparator_offset_lsb", 0.0))
    sigma_lsb = float(spec.get("comparator_noise_lsb", 0.0))

    worst_dnl = worst_inl = 0.0
    missing = 0
    dynamic = None

    for t in range(trials):
        rng = random.Random(seed + t)
        steps = make_ramp(nbits, linearity, rng)
        counts = convert_scan(nbits, steps, offset_lsb, sigma_lsb, rng,
                              points_per_code)
        dnl, inl, miss = dnl_inl(counts, points_per_code)
        worst_dnl = max(worst_dnl, max(abs(d) for d in dnl))
        worst_inl = max(worst_inl, max(abs(i) for i in inl))
        missing = max(missing, miss)

        if t == 0:
            cum, acc = [], 0.0
            for s in steps:
                acc += s
                cum.append(acc)
            codes = []
            for m in range(n_samples):
                x = 0.5 + 0.49 * math.sin(2 * math.pi * bin_index * m / n_samples)
                if sigma_lsb:
                    x += rng.gauss(0.0, sigma_lsb) / (1 << nbits)
                x += offset_lsb / (1 << nbits)
                codes.append(convert(x, cum, 0.0, 0.0, rng))
            dynamic = spectral(codes)

    return {
        "arch": "ramp",
        "nbits": nbits,
        "ramp_linearity_lsb": linearity,
        "trials": trials,
        "points_per_code": points_per_code,
        "dnl_floor_lsb": 1.0 / math.sqrt(points_per_code),
        "max_dnl_lsb": worst_dnl,
        "max_inl_lsb": worst_inl,
        "missing_codes": missing,
        "sndr_db": dynamic["sndr_db"] if dynamic else None,
        "snr_db": dynamic["snr_db"] if dynamic else None,
        "sinad_db": dynamic["sinad_db"] if dynamic else None,
        "thd_db": dynamic["thd_db"] if dynamic else None,
        "enob": dynamic["enob"] if dynamic else None,
        "sfdr_dbc": dynamic["sfdr_dbc"] if dynamic else None,
    }


def human(r):
    return "\n".join([
        f"behavioral ramp {r['nbits']}-bit, ramp step sigma "
        f"{r['ramp_linearity_lsb']:.3f} LSB, {r['trials']} trials, "
        f"{r['points_per_code']} points/code",
        f"  static   max|DNL|={r['max_dnl_lsb']:.3f} LSB  "
        f"max|INL|={r['max_inl_lsb']:.3f} LSB  missing codes={r['missing_codes']}",
        f"           histogram floor {r['dnl_floor_lsb']:.3f} LSB",
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

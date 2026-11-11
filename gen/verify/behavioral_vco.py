#!/usr/bin/env python3
"""L1 behavioral VCO/counter ADC: DNL/INL and ENOB.

Vin sets the oscillator frequency; a counter counts cycles over a fixed
window and the count is the code:

    f(x)   = f_min + (f_max - f_min) * y(x)      x = Vin / V_FS
    y(x)   = x + 4 k x (1 - x)                   k = curvature
    code   = round(2^N * y(x) + jitter)

There is no sampling capacitor and no comparator, so kT/C and comparator
noise do not appear.  What does appear is the part a feasibility formula
cannot see: the count quantization's own DNL statistics and whatever the
tuning curvature does to the code widths.  Both are measured here by running
the conversion.

The count is dithered (uniform fractional delay on the window edge) because
without dither a deterministic ramp produces a strictly periodic bin-width
pattern and the DNL numbers are an artifact of the sampling grid, not of the
converter.

No dependencies outside the standard library.
"""

from __future__ import annotations

import json
import math
import os
import random


def tuning(x, curvature):
    """Normalised V->f transfer, y(0)=0, y(1)=1, max deviation k at x=0.5."""
    return x + 4.0 * curvature * x * (1.0 - x)


def convert(x, nbits, curvature, jitter_rel, rng):
    # No clamp here: the sweep deliberately over-ranges so the end codes are
    # fully populated; only the output code is clipped.
    count = tuning(x, curvature) * (1 << nbits)
    if jitter_rel:
        count += rng.gauss(0.0, abs(count) * jitter_rel)
    count += rng.random()          # dither the window edge
    return min((1 << nbits) - 1, max(0, int(count)))


def static_scan(nbits, curvature, jitter_rel, rng, points_per_code=512, guard=2):
    n_codes = 1 << nbits
    counts = [0] * n_codes
    total = n_codes * points_per_code
    for k in range(-guard * points_per_code, total + guard * points_per_code):
        counts[convert((k + 0.5) / total, nbits, curvature, jitter_rel, rng)] += 1
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


try:
    from spectral import fft, spectral
except ImportError:  # standalone: python3 gen/verify/behavioral_x.py
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from spectral import fft, spectral

def run(spec, sigma_cu_rel=0.0, trials=5, seed=1, n_samples=1024, bin_index=31,
        points_per_code=512, feas=None):
    """feas is the feasibility report, used only for t_window_ns (jitter)."""
    nbits = int(spec["resolution"])
    vco = spec.get("vco", {})
    curvature = float(vco.get("curvature_pct", 2.0)) / 100.0
    tj_fs = vco.get("period_jitter_fs_rms")
    t_window_ns = float(vco.get("t_window_ns")
                        or (feas or {}).get("t_window_ns") or 0.0)
    jitter_rel = (float(tj_fs) * 1e-6 / t_window_ns) if (tj_fs and t_window_ns) else 0.0

    worst_dnl = worst_inl = 0.0
    missing = 0
    dynamic = None
    for t in range(trials):
        rng = random.Random(seed + t)
        counts = static_scan(nbits, curvature, jitter_rel, rng, points_per_code)
        dnl, inl, miss = dnl_inl(counts, points_per_code)
        worst_dnl = max(worst_dnl, max(abs(d) for d in dnl))
        worst_inl = max(worst_inl, max(abs(i) for i in inl))
        missing = max(missing, miss)
        if t == 0:
            codes = []
            for m in range(n_samples):
                x = 0.5 + 0.49 * math.sin(2 * math.pi * bin_index * m / n_samples)
                codes.append(convert(x, nbits, curvature, jitter_rel, rng))
            dynamic = spectral(codes)

    return {
        "arch": "vco",
        "nbits": nbits,
        "curvature_pct": curvature * 100.0,
        "period_jitter_fs_rms": tj_fs,
        "jitter_rel": jitter_rel,
        "t_window_ns": t_window_ns,
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
    jit = (f"jitter {r['period_jitter_fs_rms']} fs rms "
           f"({r['jitter_rel']*1e3:.4f} per-mille of the window)")
    if r["period_jitter_fs_rms"] is None:
        jit = "jitter not measured for this PDK"
    return "\n".join([
        f"behavioral vco {r['nbits']}-bit, curvature {r['curvature_pct']:.1f}%, "
        f"{jit}, {r['trials']} trials, {r['points_per_code']} points/code",
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
    ap.add_argument("--from-feasibility")
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--samples", type=int, default=1024)
    ap.add_argument("--bin", type=int, default=31)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(os.path.expanduser(args.spec), encoding="utf-8") as fh:
        spec = json.load(fh)
    feas = None
    if args.from_feasibility:
        with open(os.path.expanduser(args.from_feasibility), encoding="utf-8") as fh:
            feas = json.load(fh)
    result = run(spec, 0.0, args.trials, args.seed, args.samples, args.bin,
                 feas=feas)
    print(json.dumps(result, indent=2) if args.json else human(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))

#!/usr/bin/env python3
"""Regression tests for behavioral_sar.py.

    python3 gen/verify/behavioral_sar_test.py

The decisive case is common-scale invariance: multiplying every capacitor --
switched units AND the terminating dummy -- by the same factor must be a
physically transparent transformation (it models a process/voltage common-mode
density shift such as the MOSCAP C(V) drift README records at ~1.8x).  The
transfer denominator is the actual array total, so INL and missing codes must
not move.  The old code used the nominal 2^N denominator and reported false
INL/missing-code defects under common scaling.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import behavioral_sar as bs  # noqa: E402


def _scan(weights, dummy, nbits, total, seed=7):
    rng = random.Random(seed)
    counts = bs.static_scan(nbits, weights, total, 0.0, 0.0, rng)
    dnl, inl, miss = bs.dnl_inl(counts, 64)
    return max(abs(d) for d in dnl), max(abs(i) for i in inl), miss


def test_common_scale_invariance():
    """Scaling every cap (signal + dummy) by a common factor is a no-op."""
    nbits, sigma = 8, 0.005
    rng = random.Random(1)
    w, d = bs.make_weights(nbits, sigma, rng)

    # baseline at the drawn total
    total0 = sum(w) + d
    dnl0, inl0, miss0 = _scan(w, d, nbits, total0)

    for scale in (0.9, 1.01, 1.5):
        ws = [x * scale for x in w]
        ds = d * scale
        totals = sum(ws) + ds          # denominator tracks the drawn array
        dnl, inl, miss = _scan(ws, ds, nbits, totals)
        assert abs(inl - inl0) < 0.05, \
            f"common scale {scale}: INL moved {inl0:.3f}->{inl:.3f} LSB"
        assert miss == miss0, \
            f"common scale {scale}: missing {miss0}->{miss}"
        print(f"  scale {scale:5.2f}: INL {inl0:.3f}->{inl:.3f}  "
              f"missing {miss0}->{miss}  PASS")


def test_denominator_uses_drawn_total():
    """The broken form used the nominal 2^N: reproduces the false defect."""
    nbits, sigma = 8, 0.005
    rng = random.Random(1)
    w, d = bs.make_weights(nbits, sigma, rng)
    ws = [x * 1.01 for x in w]
    ds = d * 1.01

    broken_total = float(1 << nbits)           # OLD bug
    fixed_total = sum(ws) + ds                 # NEW
    _, inl_broken, miss_broken = _scan(ws, ds, nbits, broken_total)
    _, inl_fixed, miss_fixed = _scan(ws, ds, nbits, fixed_total)
    assert inl_broken > 1.0, "expected the nominal-2^N bug to show >1 LSB INL"
    assert inl_fixed < 0.2 and miss_fixed == 0, \
        "drawn-total denominator must not report the false defect"
    print(f"  nominal 2^N denom: INL {inl_broken:.3f} missing {miss_broken}  "
          f"(the bug)")
    print(f"  drawn total denom: INL {inl_fixed:.3f} missing {miss_fixed}  PASS")


def test_arch_key_present():
    spec = {"resolution": 8, "input": {"vfs": 1.8}, "vdd": 1.8}
    r = bs.run(spec, 0.0, trials=1, seed=1, n_samples=256)
    assert r["arch"] == "sar", "evaluate_sar must report arch like the others"
    assert "ktc_lsb" in r
    print(f"  arch='{r['arch']}'  ktc_lsb={r['ktc_lsb']:.4f}  PASS")


def main():
    print("behavioral_sar regression:")
    test_common_scale_invariance()
    test_denominator_uses_drawn_total()
    test_arch_key_present()
    print("all PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

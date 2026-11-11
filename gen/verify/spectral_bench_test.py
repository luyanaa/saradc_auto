#!/usr/bin/env python3
"""Cross-check the shared `spectral.py` against the bench reference.

`gen/verify/spectral.py` is the dependency-free coherent-FFT metric used by
every L1 behavioral model.  `sim/report/make_sim_report.py:analyze_sine` is the
transistor-bench reference (numpy sine-fit for SINAD, per-harmonic dBc, worst
spur for SFDR).  They must agree on the same record or one of them is wrong.

This test drives both on a synthetic coherent sine + known impairments and
asserts the key metrics land inside a tolerance.  numpy is required for the
bench side; the test skips itself cleanly when it is absent so the stdlib
self-test in spectral.py is never gated on it.

    python3 gen/verify/spectral_bench_test.py
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spectral import spectral  # noqa: E402


def sine_record(n, bin_index, h3_dbc=None, noise_lsb=None, seed=1):
    import random
    rng = random.Random(seed)
    out = []
    for m in range(n):
        v = 0.5 + 0.49 * math.sin(2.0 * math.pi * bin_index * m / n)
        if h3_dbc is not None:
            v += 0.49 * (10.0 ** (h3_dbc / 20.0)) * \
                math.sin(2.0 * math.pi * 3 * bin_index * m / n + 0.4)
        if noise_lsb:
            v += rng.gauss(0.0, noise_lsb)
        out.append(v)
    return out


def bench_spectral(samples, fs, fin, cycles):
    """numpy sine-fit SINAD/SFDR in the style of make_sim_report.analyze_sine."""
    import numpy as np
    x = np.asarray(samples, float)
    n = len(x)
    t = np.arange(n) / fs
    # 4-param sine fit (freq known from the coherent record)
    a = np.column_stack([np.sin(2 * np.pi * fin * t),
                         np.cos(2 * np.pi * fin * t), np.ones(n)])
    coef, *_ = np.linalg.lstsq(a, x, rcond=None)
    y = a @ coef
    amp = math.hypot(coef[0], coef[1])
    err = math.sqrt(np.mean((x - y) ** 2))
    sinad = 20 * math.log10(amp / math.sqrt(2) / err)
    spec = np.abs(np.fft.rfft(x - x.mean())) ** 2
    p_fund = spec[cycles]
    spur = spec.copy()
    spur[[0, cycles]] = 0
    sfdr = 10 * math.log10(p_fund / spur.max())
    return {"sinad": sinad, "enob": (sinad - 1.76) / 6.02, "sfdr": sfdr}


def main():
    try:
        import numpy  # noqa: F401
    except ImportError:
        print("numpy not available; bench cross-check skipped "
              "(spectral.py self-test still covers the shared module)")
        return 0

    n, b = 4096, 31
    fs = 1.0
    fin = b * fs / n
    ok = True

    cases = [
        # a clean coherent sine only reaches the float-noise floor; both
        # solvers land at a different arbitrary huge dB, so only require a
        # lower bound, not agreement
        ("clean coherent", sine_record(n, b), {"sinad_min": 90.0}),
        ("-40dBc h3", sine_record(n, b, h3_dbc=-40.0),
         {"sfdr": 40.0, "sfdr_tol": 2.0}),
        ("noisy", sine_record(n, b, noise_lsb=0.002), {}),
    ]
    for name, rec, expect in cases:
        shared = spectral(rec)
        bench = bench_spectral(rec, fs, fin, b)
        d_sinad = abs(shared["sinad_db"] - bench["sinad"])
        d_sfdr = abs(shared["sfdr_db"] - bench["sfdr"])
        d_enob = abs(shared["enob"] - bench["enob"])
        line = (f"{name:16s}  SINAD shared={shared['sinad_db']:.2f} "
                f"bench={bench['sinad']:.2f} (d={d_sinad:.2f})  "
                f"SFDR shared={shared['sfdr_db']:.1f} bench={bench['sfdr']:.1f} "
                f"(d={d_sfdr:.1f})  ENOB d={d_enob:.3f}")
        good = d_sinad < 1.0 and d_enob < 0.2
        if "sinad_min" in expect:
            # float-noise floor: just require both to be "very high"
            good = (shared["sinad_db"] > expect["sinad_min"]
                    and bench["sinad"] > expect["sinad_min"])
        if "sfdr" in expect:
            good &= d_sfdr < expect.get("sfdr_tol", 2.0)
        print(line + f"  -> {'PASS' if good else 'FAIL'}")
        ok &= good
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

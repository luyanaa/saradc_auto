#!/usr/bin/env python3
"""Shared FFT spectral metrics for every L1 behavioral model.

The metric definitions follow the IEEE ADC terminology used on the bench:
the SINAD/SNDR denominator includes harmonics; the SNR denominator does not.

    sig_bins   fundamental +- sig_leak          (the carrier)
    harm_bins  first n_harm harmonics +- sig_leak, minus any sig_bins
    spur       the single largest non-DC, non-sig_bins bin (harmonic or not)

    SNR    = sig / sum(all non-DC bins not in sig_bins and not in harm_bins)
    SINAD  = sig / sum(all non-DC bins not in sig_bins)   (harmonics count)
    THD    = harm / sig                                  (reported, negative)
    SFDR   = sig / worst_spur                            (reported positive dBc)
    ENOB   = (SINAD - 1.76) / 6.02

A model may also report `sndr_db` for backward compatibility; it aliases
SINAD.  `harmonic_power_db` aliases THD (it is the summed harmonic power,
not the worst single harmonic -- the old labels said "worst harmonic" and
"SFDR" for this same number, which was wrong).

No dependencies outside the standard library.
"""

from __future__ import annotations

import math


def fft(values):
    """In-place radix-2 DIT FFT; `values` length must be a power of two."""
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


def _db(ratio):
    return 10.0 * math.log10(ratio) if ratio > 0 else None


def spectral(samples, nbits=None, n_harm=5, sig_leak=1):
    """Full-Nyquist spectral metrics on a coherent record of `samples`.

    `samples` is any sequence of code/voltage values; DC is removed first.
    `nbits` is accepted for call-site uniformity but does not change the
    math.  Returns a dict; metric values are None where undefined.
    """
    n = len(samples)
    mean = sum(samples) / n
    spectrum = fft([s - mean for s in samples])
    power = [abs(v) ** 2 for v in spectrum[: n // 2]]

    fundamental = max(range(1, n // 2), key=lambda k: power[k])
    sig_bins = {fundamental + d for d in range(-sig_leak, sig_leak + 1)
                if 1 <= fundamental + d < n // 2}

    harm_bins = set()
    for h in range(2, n_harm + 1):
        hb = fundamental * h
        for d in range(-sig_leak, sig_leak + 1):
            k = hb + d
            if 1 <= k < n // 2 and k not in sig_bins:
                harm_bins.add(k)

    sig = sum(power[k] for k in sig_bins)
    harm = sum(power[k] for k in harm_bins)

    # worst spur = largest non-DC bin outside the signal lobe
    spur_bin = max((k for k in range(1, n // 2) if k not in sig_bins),
                   key=lambda k: power[k], default=None)
    spur = power[spur_bin] if spur_bin is not None else 0.0

    noise_noh = sum(power[k] for k in range(1, n // 2)
                    if k not in sig_bins and k not in harm_bins)
    noise_full = noise_noh + harm

    snr = _db(sig / noise_noh) if sig > 0 and noise_noh > 0 else None
    sinad = _db(sig / noise_full) if sig > 0 and noise_full > 0 else None
    thd = _db(harm / sig) if sig > 0 and harm > 0 else None
    sfdr = _db(sig / spur) if sig > 0 and spur > 0 else None

    return {
        "fundamental_bin": fundamental,
        "sig_power": sig,
        "snr_db": snr,
        "sinad_db": sinad,
        "sndr_db": sinad,                    # back-compat alias
        "thd_db": thd,
        "harmonic_power_db": thd,            # back-compat alias
        "sfdr_db": sfdr,
        "sfdr_dbc": sfdr,
        "spur_bin": spur_bin,
        "spur_fs": (spur_bin / float(n)) if spur_bin is not None else None,
        "enob": (sinad - 1.76) / 6.02 if sinad is not None else None,
    }


# --------------------------------------------------------------------------
# self-test: coherent sine + known impairment vs expected dB
# --------------------------------------------------------------------------

def _sine(n, bin_index, amp=0.49, h3_dbc=None, noise=None, seed=1):
    import random
    rng = random.Random(seed)
    out = []
    for m in range(n):
        v = 0.5 + amp * math.sin(2.0 * math.pi * bin_index * m / n)
        if h3_dbc is not None:
            v += amp * (10.0 ** (h3_dbc / 20.0)) * \
                math.sin(2.0 * math.pi * 3 * bin_index * m / n + 0.7)
        if noise:
            v += rng.gauss(0.0, noise)
        out.append(v)
    return out


def _selftest():
    n = 4096
    b = 31
    ok = True

    pure = spectral(_sine(n, b))
    # a clean sine limited only by float noise: SINAD and SNR both very high
    t = pure["sinad_db"] is not None and pure["sinad_db"] > 90
    print(f"pure sine   SINAD={pure['sinad_db']:.1f} SNR={pure['snr_db']:.1f} "
          f"SFDR={pure['sfdr_db']:.1f} -> {'PASS' if t else 'FAIL'}")
    ok &= t

    d3 = spectral(_sine(n, b, h3_dbc=-40.0))
    t = (d3["thd_db"] is not None and abs(d3["thd_db"] - (-40.0)) < 1.0
         and d3["sfdr_db"] is not None and abs(d3["sfdr_db"] - 40.0) < 1.0
         and d3["sinad_db"] < d3["snr_db"])
    print(f"-40dBc h3   THD={d3['thd_db']:.1f} SFDR={d3['sfdr_db']:.1f} "
          f"SINAD={d3['sinad_db']:.1f}<SNR={d3['snr_db']:.1f} "
          f"-> {'PASS' if t else 'FAIL'}")
    ok &= t

    noisy = spectral(_sine(n, b, noise=0.002))
    t = (noisy["snr_db"] is not None and 40 < noisy["snr_db"] < 60
         and abs(noisy["sinad_db"] - noisy["snr_db"]) < 0.5)
    print(f"white noise SNR={noisy['snr_db']:.1f} SINAD={noisy['sinad_db']:.1f} "
          f"-> {'PASS' if t else 'FAIL'}")
    ok &= t

    return ok


if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)

#!/usr/bin/env python3
"""L1 behavioral flash ADC: comparator offset, thermometer, popcount.

A flash converter is a string of comparators, one per code boundary.  The
thing that decides its DNL is *not* capacitor matching (there is no CDAC): it
is the random input-referred offset of the comparators, because adjacent
comparators' offsets move adjacent code edges by an independent amount.

    code = number of comparators whose threshold is below the input

with each threshold perturbed by a drawn offset.  That makes DNL visible as a
histogram, and it also makes the bubble mechanism visible: when offsets
reorder two comparators the thermometer word stops being contiguous, and a
plain popcount silently absorbs the bubble while a priority encoder does not.

This model is a behavioural one, not a transistor netlist.  The offset sigma
it uses is an input; on every checked-in PDK it is still an assumption (see
GAPS.md G-FL5), so a passing run here is not evidence of silicon DNL.

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

def draw_offsets(nbits, sigma_os_lsb, preamp_gain, rng):
    """One input-referred offset per comparator, in LSB of the input.

    A preamp with gain A divides the latch offset by A but adds its own, so
    the two contributions add in quadrature:
        sigma_total^2 = (sigma_latch/A)^2 + sigma_preamp^2.
    With no preamp this is just the latch sigma.
    """
    n_cmp = (1 << nbits) - 1
    if preamp_gain and preamp_gain > 1.0:
        latch = sigma_os_lsb / preamp_gain
        preamp = sigma_os_lsb * math.sqrt(max(0.0, 1.0 - 1.0 / preamp_gain ** 2))
        return [rng.gauss(0.0, latch) + rng.gauss(0.0, preamp)
                for _ in range(n_cmp)]
    return [rng.gauss(0.0, sigma_os_lsb) for _ in range(n_cmp)]


def thermometer(x, offsets, nbits):
    """Comparator word for input x in [0,1) full scale.

    Comparator k (k=1..2^N-1) has nominal threshold k/2^N.  An offset `off`
    in LSB moves that threshold by off/2^N, so the comparison is
    x >= k/2^N - off/2^N.  The return value is (raw bits, contiguous?).
    """
    n = 1 << nbits
    bits = 0
    for k, off in enumerate(offsets, start=1):
        if x >= (k - off) / n:
            bits |= 1 << (k - 1)
    # contiguity: the set bits must be 0..m-1 for some m
    m = bits
    contiguous = ((m + 1) & m) == 0
    return bits, contiguous


def encode(bits, nbits, encoder):
    """Popcount absorbs a bubble; a priority encoder does not.

    A bubble is a non-contiguous thermometer word: comparator k+1 tripped
    before k.  A plain popcount still returns the count of set bits, so it
    silently hides the bubble; a priority encoder returns the highest set
    bit and therefore a different (wrong) code.  This is the behavioural
    difference the `flash.encoder` spec field selects.
    """
    if encoder == "priority":
        code = bits.bit_length()          # index of the highest set bit + 1
    else:
        code = bits.bit_count()
    return min(code, (1 << nbits) - 1)


def convert(x, offsets, nbits, sigma_noise_lsb, rng, encoder="popcount"):
    if sigma_noise_lsb:
        x += rng.gauss(0.0, sigma_noise_lsb) / (1 << nbits)
    bits, contiguous = thermometer(x, offsets, nbits)
    return encode(bits, nbits, encoder), contiguous


def convert_scan(nbits, offsets, sigma_noise_lsb, rng, points_per_code=64,
                 guard=2, encoder="popcount"):
    n_codes = 1 << nbits
    counts = [0] * n_codes
    bubbles = 0
    total_points = n_codes * points_per_code
    for k in range(-guard * points_per_code, total_points + guard * points_per_code):
        x = (k + 0.5) / total_points
        code, contiguous = convert(x, offsets, nbits, sigma_noise_lsb, rng,
                                   encoder)
        if code == n_codes - 1 and x < 0:
            code = 0
        counts[code] += 1
        if not contiguous:
            bubbles += 1
    return counts, bubbles


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
    """sigma_cu_rel is accepted for the common driver signature; a flash has
    no CDAC, so unit-cap mismatch does not enter its DNL.

    sigma_os_lsb defaults to spec['flash']['sigma_os_lsb']; when it is absent
    the model runs at 0.5 LSB, which is the offset level a flash needs to be
    usable at all.  It is an input, not a PDK result.
    """
    nbits = int(spec["resolution"])
    fl = spec.get("flash", {})
    sigma_os_lsb = float(fl.get("sigma_os_lsb", 0.5))
    preamp_gain = float(fl.get("preamp_gain", 1.0))
    encoder = str(fl.get("encoder", "popcount"))
    sigma_noise_lsb = float(spec.get("comparator_noise_lsb", 0.0))
    dnl_target = float(spec.get("dnl_target_lsb", 0.5))

    worst_dnl = worst_inl = 0.0
    missing = 0
    bubbles = 0
    dynamic = None

    for t in range(trials):
        rng = random.Random(seed + t)
        offsets = draw_offsets(nbits, sigma_os_lsb, preamp_gain, rng)
        counts, bub = convert_scan(nbits, offsets, sigma_noise_lsb, rng,
                                   points_per_code, encoder=encoder)
        dnl, inl, miss = dnl_inl(counts, points_per_code)
        worst_dnl = max(worst_dnl, max(abs(d) for d in dnl))
        worst_inl = max(worst_inl, max(abs(i) for i in inl))
        missing = max(missing, miss)
        bubbles += bub

        if t == 0:
            amplified = []
            for m in range(n_samples):
                x = 0.5 + 0.49 * math.sin(
                    2.0 * math.pi * bin_index * m / n_samples)
                code, _ = convert(x, offsets, nbits, sigma_noise_lsb, rng,
                                  encoder)
                amplified.append(code)
            dynamic = spectral(amplified, nbits)

    # 3-sigma offset budget for the adjacent-comparator DNL term
    dnl_pred = math.sqrt(2.0) * 3.0 * sigma_os_lsb
    return {
        "arch": "flash",
        "nbits": nbits,
        "sigma_os_lsb": sigma_os_lsb,
        "preamp_gain": preamp_gain,
        "encoder": encoder,
        "dnl_target_lsb": dnl_target,
        "trials": trials,
        "points_per_code": points_per_code,
        "max_dnl_lsb": worst_dnl,
        "max_inl_lsb": worst_inl,
        "dnl_predicted_3sigma_lsb": dnl_pred,
        "missing_codes": missing,
        "bubble_rate": bubbles / float(trials * ((1 << nbits) * points_per_code)),
        "sndr_db": dynamic["sndr_db"] if dynamic else None,
        "enob": dynamic["enob"] if dynamic else None,
        "sfdr_dbc": dynamic["harmonic_power_db"] if dynamic else None,
    }


def human(r):
    return "\n".join([
        f"behavioral flash {r['nbits']}-bit, sigma_os={r['sigma_os_lsb']:.3f} "
        f"LSB (from spec, not a PDK result), preamp x{r['preamp_gain']:.1f}, "
        f"encoder {r['encoder']}, {r['trials']} trials",
        f"  static   max|DNL|={r['max_dnl_lsb']:.3f} LSB  "
        f"max|INL|={r['max_inl_lsb']:.3f} LSB  "
        f"missing codes={r['missing_codes']}  "
        f"bubbles={r['bubble_rate']*100:.2f}%",
        f"           adjacent-offset prediction (3 sigma) "
        f"{r['dnl_predicted_3sigma_lsb']:.3f} LSB vs target "
        f"{r['dnl_target_lsb']:.2f}",
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

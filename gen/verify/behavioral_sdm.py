#!/usr/bin/env python3
"""L1 behavioral delta-sigma ADC: SNDR/ENOB vs OSR, plus a DC transfer.

This actually runs the loop instead of quoting the closed-form formula, so
the numbers include what the formula leaves out: finite integrator gain
(state leakage), quantizer loading and the 1-bit stability ceiling.

Structure: CIFB, unity signal transfer function folded into the first
integrator, 1-bit quantizer (output +-1):

    u1[n+1] = u1 + (x - y)          order 1 uses this alone
    u2[n+1] = u2 + (u1 - y)         y = sign(u2)   (order 2)

Every integrator also leaks by 1/A per sample, with A the finite DC gain from
the spec (`sdm.integrator_gain_db`, default 60 dB).  A 1-bit loop has no
comparator offset problem (it is a DC shift) and its comparator noise is
shaped like any other in-band noise, so neither is a separate input.

The decimator does not exist in this repo, so the measurement is the ideal
one: FFT the bitstream and integrate the noise power over 0..fs_out/2.  That
is an upper bound on the real decimator, and it is labelled as such.

No dependencies outside the standard library.
"""

from __future__ import annotations

import json
import math
import os
import random


# --------------------------------------------------------------------------
# modulator
# --------------------------------------------------------------------------

def modulate(xs, order=2, gain_db=60.0):
    """Run the loop over a sequence of inputs in [-1, 1]; returns +-1 output."""
    leak = 0.0 if not gain_db else 10.0 ** (-gain_db / 20.0)
    out = []
    if order <= 1:
        u1 = 0.0
        for x in xs:
            y = 1.0 if u1 >= 0.0 else -1.0
            out.append(y)
            u1 = (1.0 - leak) * u1 + (x - y)
            u1 = max(-8.0, min(8.0, u1))
        return out

    u1 = u2 = 0.0
    for x in xs:
        y = 1.0 if u2 >= 0.0 else -1.0
        out.append(y)
        u1 = (1.0 - leak) * u1 + (x - y)
        u2 = (1.0 - leak) * u2 + (u1 - y)
        u1 = max(-16.0, min(16.0, u1))
        u2 = max(-16.0, min(16.0, u2))
    return out


# --------------------------------------------------------------------------
# fft and in-band measurement
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


def inband_sndr(bits, osr, bin_index):
    """Ideal decimation: signal bin vs all noise below fs_out/2."""
    n = len(bits)
    mean = sum(bits) / n
    power = [abs(v) ** 2 for v in fft([b - mean for b in bits])[: n // 2]]
    edge = max(2, n // (2 * osr))
    sig_bins = {bin_index + d for d in (-1, 0, 1) if 1 <= bin_index + d < edge}
    sig = sum(power[k] for k in sig_bins)
    noise = sum(power[k] for k in range(1, edge) if k not in sig_bins)
    if sig <= 0 or noise <= 0:
        return None
    return 10.0 * math.log10(sig / noise)


# --------------------------------------------------------------------------
# dc transfer -> dnl / inl
# --------------------------------------------------------------------------

def dc_transfer(nbits, order, osr, gain_db, points_per_code, settle, rng):
    """Average the bitstream at each dc input; the loop is the 'decimator'."""
    n_codes = 1 << nbits
    total = n_codes * points_per_code
    codes = []
    for k in range(total):
        x = -1.0 + 2.0 * (k + 0.5) / total
        run = modulate([x] * settle, order, gain_db)
        mean = sum(run[settle // 2:]) / (settle - settle // 2)
        codes.append((x, mean))
    return codes


def dnl_inl_from_transfer(codes, nbits, interior_only=True):
    """Bin the averaged transfer and measure code widths.

    The ideal bin is 2/2^N wide in input units; a real bin is where the
    averaged output crosses each code boundary.  The end bins are open on
    one side (their width is not measurable from a finite sweep), so they
    are excluded from the summary unless interior_only is False.
    """
    n_codes = 1 << nbits
    step = 2.0 / n_codes
    # invert: for each boundary value, find the input where mean crosses it
    bounds = []
    idx = 0
    vals = [(x, m) for x, m in codes]
    for c in range(1, n_codes):
        # first sample whose averaged output exceeds the boundary level
        level = -1.0 + step * c
        j = idx
        while j < len(vals) and vals[j][1] < level:
            j += 1
        x = vals[min(j, len(vals) - 1)][0] if j > 0 else vals[0][0]
        bounds.append(x)
        idx = j
    widths = [b - a for a, b in zip([-1.0] + bounds[:-1], bounds)]
    dnl = [w / step - 1.0 for w in widths]
    inl, acc = [], 0.0
    for d in dnl:
        acc += d
        inl.append(acc)
    missing = sum(1 for d in dnl if d <= -1.0)
    if interior_only and len(dnl) > 2:
        dnl = dnl[1:-1]
        inl = inl[1:-1]
    return dnl, inl, missing



# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run(spec, sigma_cu_rel=0.0, trials=1, seed=1, n_samples=65536, bin_index=None,
        points_per_code=1):
    nbits = int(spec["resolution"])
    sdm = spec.get("sdm", {})
    order = int(sdm.get("order", 2))
    gain_db = float(sdm.get("integrator_gain_db", 60.0))
    osr_list = sdm.get("osr_sweep") or [16, 32, 64, 128]
    rng = random.Random(seed)

    # ---- dynamic: SNDR vs OSR ------------------------------------------
    # A 1-bit loop overloads above ~0.55 FS, so the measurement amplitude is
    # fixed at 0.5 and every number is also given referenced to full scale
    # (the feasibility bound is a full-scale SQNR).
    amp = 0.5
    sweep = []
    for osr in osr_list:
        n = n_samples
        b = bin_index or max(3, int(round(0.11 * n / osr)))
        xs = [amp * math.sin(2 * math.pi * b * m / n) for m in range(n)]
        bits = modulate(xs, order, gain_db)
        sndr = inband_sndr(bits, osr, b)
        sweep.append({"osr": osr, "sndr_db": sndr,
                      "sndr_dbfs": (sndr + 20.0 * math.log10(1.0 / amp))
                                   if sndr else None,
                      "enob": (sndr - 1.76) / 6.02 if sndr else None})

    # ---- static: dc transfer at the selected OSR ------------------------
    osr = int(spec.get("logic", {}).get("osr") or sdm.get("default_osr", 64))
    settle = max(4096, 64 * osr)
    codes = dc_transfer(nbits, order, osr, gain_db, points_per_code, settle, rng)
    dnl, inl, missing = dnl_inl_from_transfer(codes, nbits)
    lsb_u = 2.0 / (1 << nbits)
    dc_res = max(abs(m - x) for x, m in codes) / lsb_u
    sel = next((s for s in sweep if s["osr"] == osr), sweep[-1])

    return {
        "arch": "sdm",
        "nbits": nbits,
        "order": order,
        "integrator_gain_db": gain_db,
        "osr": osr,
        "sndr_vs_osr": sweep,
        "sndr_db": sel["sndr_db"],
        "sndr_dbfs": sel["sndr_dbfs"],
        "enob": sel["enob"],
        "dc_settle": settle,
        "dc_resolution_lsb": dc_res,
        "max_dnl_lsb": max(abs(d) for d in dnl) if dnl else None,
        "max_inl_lsb": max(abs(i) for i in inl) if inl else None,
        "missing_codes": missing,
    }


def human(r):
    lines = [
        f"behavioral sdm {r['nbits']}-bit, order {r['order']}, "
        f"integrator gain {r['integrator_gain_db']:.0f} dB, "
        f"ideal decimator",
        f"  SNDR vs OSR (measured on the bitstream at 0.5 FS, noise "
        f"integrated 0..fs_out/2):",
    ]
    for s in r["sndr_vs_osr"]:
        lines.append(f"    OSR {s['osr']:4d}  SNDR={s['sndr_db']:6.2f} dB  "
                     f"({s['sndr_dbfs']:6.2f} dBFS)  ENOB={s['enob']:5.2f} bit")
    lines.append(
        f"  static   at OSR {r['osr']}, {r['dc_settle']} samples/point: "
        f"max|DNL|={r['max_dnl_lsb']:.2f} LSB  "
        f"max|INL|={r['max_inl_lsb']:.2f} LSB  missing codes={r['missing_codes']}")
    lines.append(
        f"           measurement floor {r['dc_resolution_lsb']:.2f} LSB "
        f"(a decimated 1-bit stream, not a cap-array DNL)")
    return "\n".join(lines)


def main(argv):
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--samples", type=int, default=65536)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(os.path.expanduser(args.spec), encoding="utf-8") as fh:
        spec = json.load(fh)
    result = run(spec, 0.0, 1, 1, args.samples)
    print(json.dumps(result, indent=2) if args.json else human(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))

#!/usr/bin/env python3
"""L1 behavioral time-interleaved ADC: N channels, mux, mismatch spurs.

A time-interleaved converter is N copies of a sub-ADC, each sampling at fs/N
and the outputs are multiplexed back to fs:

    channel c  samples at  t = (m*N + c)/fs + skew_c
    gain_c     = 1 + eps_c/2
    offset_c   = delta_c

The interesting error is not inside any channel: it is the *mismatch between
channels*.  Offset mismatch puts spurs at k*fs/N; gain and timing (skew)
mismatch put spurs at fin +- k*fs/N.  This model instantiates the channels,
muxes them and FFTs the fs-rate stream, so the spurs are measured rather than
quoted.

The sub-ADC is modelled as an N-bit quantiser plus the per-channel offset and
gain; that is enough to expose the interleaving spurs, which is the point.  A
full per-channel SAR model would be the same mux with a slower inner loop.

No dependencies outside the standard library.
"""

from __future__ import annotations

import json
import math
import os
import random


# --------------------------------------------------------------------------
# fft / spectral measurement
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


def spectral_ti(codes, nbits, channels):
    """SNDR/SFDR on the fs-rate stream, excluding DC and the signal lobe."""
    n = len(codes)
    code_fs = float(1 << nbits)
    mean = sum(codes) / n
    spectrum = fft([(c - mean) / code_fs for c in codes])
    power = [abs(v) ** 2 for v in spectrum[: n // 2]]
    fundamental = max(range(1, n // 2), key=lambda k: power[k])
    sig_bins = {fundamental + d for d in (-1, 0, 1) if 1 <= fundamental + d < n // 2}
    sig = sum(power[k] for k in sig_bins)
    # worst non-signal bin is the SFDR spur; harmonics are just other spurs
    spur_bin = max((k for k in range(1, n // 2) if k not in sig_bins),
                   key=lambda k: power[k], default=1)
    spur = power[spur_bin]
    noise = sum(power[k] for k in range(1, n // 2)
                if k not in sig_bins and k != spur_bin)
    if sig <= 0 or noise <= 0:
        return {"sndr_db": None, "enob": None, "fundamental_bin": fundamental,
                "sfdr_db": None, "spur_bin": spur_bin, "spur_dbc": None}
    sndr = 10.0 * math.log10(sig / noise)
    sfdr = 10.0 * math.log10(sig / spur) if spur > 0 else None
    return {"sndr_db": sndr, "enob": (sndr - 1.76) / 6.02,
            "fundamental_bin": fundamental, "sfdr_db": sfdr,
            "spur_bin": spur_bin, "spur_fs": spur_bin / float(n),
            "spur_dbc": -sfdr if sfdr is not None else None}


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run(spec, sigma_cu_rel=0.0, trials=10, seed=1, n_samples=1024,
        bin_index=31, points_per_code=1):
    nbits = int(spec["resolution"])
    ti = spec.get("ti", {})
    cal = spec.get("calibration", {})
    channels = int(ti.get("channels", 2))
    vfs = float(spec["input"]["vfs"])
    fs = float(spec["fs_hz"])
    lsb = vfs / float(1 << nbits)
    amp = 0.49 * vfs
    fin = bin_index * fs / float(n_samples)

    delta_uv = float(ti.get("offset_mismatch_uv", 0.0))
    gain_pct = float(ti.get("gain_mismatch_pct", 0.0))
    skew_fs = float(ti.get("skew_fs_rms", 0.0))
    if cal.get("offset"):
        delta_uv = float(ti.get("offset_residual_uv", delta_uv * 0.1))
    if cal.get("gain"):
        gain_pct = float(ti.get("gain_residual_pct", gain_pct * 0.1))

    worst_sfdr = None
    worst_spur = None
    last = None
    for t in range(trials):
        rng = random.Random(seed + t)
        offsets = [rng.gauss(0.0, delta_uv * 1e-6) for _ in range(channels)]
        gains = [1.0 + rng.gauss(0.0, gain_pct / 100.0) for _ in range(channels)]
        skews = [rng.gauss(0.0, skew_fs * 1e-15) for _ in range(channels)]
        codes = []
        for m in range(n_samples):
            c = m % channels
            t_s = m / fs + skews[c]
            x = amp * math.sin(2.0 * math.pi * fin * t_s)
            x = x * gains[c] + offsets[c]
            code = int(round((x + vfs / 2.0) / lsb))
            codes.append(min(max(code, 0), (1 << nbits) - 1))
        res = spectral_ti(codes, nbits, channels)
        if res["sfdr_db"] is not None:
            worst_sfdr = res["sfdr_db"] if worst_sfdr is None else min(worst_sfdr, res["sfdr_db"])
            worst_spur = res["spur_bin"] if worst_spur is None else worst_spur
        last = res

    # analytic interleaving spurs for comparison
    def spur(x):
        return -300.0 if x <= 0 else 20.0 * math.log10(x)

    spur_off = spur(delta_uv * 1e-6 / (2.0 * amp))
    spur_gain = spur((gain_pct / 100.0) / 2.0)
    spur_skew = spur(2.0 * math.pi * fin * skew_fs * 1e-15)
    return {
        "arch": "ti",
        "nbits": nbits,
        "channels": channels,
        "fin_hz": fin,
        "trials": trials,
        "offset_mismatch_uv": delta_uv,
        "gain_mismatch_pct": gain_pct,
        "skew_fs_rms": skew_fs,
        "sndr_db": last["sndr_db"] if last else None,
        "enob": last["enob"] if last else None,
        "sfdr_db": worst_sfdr,
        "worst_spur_bin": worst_spur,
        "worst_spur_fs": (worst_spur / float(n_samples)) if worst_spur else None,
        "spur_offset_dbc": spur_off,
        "spur_gain_dbc": spur_gain,
        "spur_skew_dbc": spur_skew,
        "predicted_worst_dbc": min(spur_off, spur_gain, spur_skew),
    }


def human(r):
    return "\n".join([
        f"behavioral TI {r['nbits']}-bit, {r['channels']} channels, "
        f"fin={r['fin_hz']/1e3:.2f} kHz, {r['trials']} trials",
        f"  mismatch offset {r['offset_mismatch_uv']:.1f} uV, gain "
        f"{r['gain_mismatch_pct']:.4f}%, skew {r['skew_fs_rms']:.1f} fs rms",
        f"  spurs    measured SFDR={r['sfdr_db']:.1f} dB at "
        f"{r['worst_spur_fs']*100:.2f}*fs; predicted offset/gain/skew = "
        f"{r['spur_offset_dbc']:.1f}/{r['spur_gain_dbc']:.1f}/"
        f"{r['spur_skew_dbc']:.1f} dBc",
        f"  dynamic  SNDR={r['sndr_db']:.2f} dB  ENOB={r['enob']:.2f} bit",
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

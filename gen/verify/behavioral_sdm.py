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

Two measurements are reported.  `sndr_ideal_db` is the brick-wall bound:
FFT the bitstream and integrate the noise over 0..fs_out/2.  `sndr_decimated_db`
runs the actual CIC⁴/R1 decimator from `sdm.decimator` (default order 4,
R1=4), follows it with an ideal halfband to fs_out, and measures the same
band.  The difference is the CIC's droop plus any alias that folds in, so the
report shows whether the decimator is a real constraint instead of assuming
it away.  `thermal_noise_lsb` optionally injects input-referred kT/C noise.

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

try:
    from spectral import fft, spectral
except ImportError:  # standalone: python3 gen/verify/behavioral_x.py
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from spectral import fft, spectral


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


def cic_magnitude(f, f_mod, r1, order):
    """|H(f)| of an R1-decimating CIC with `order` integrator/comb pairs."""
    x = math.pi * f / f_mod
    den = r1 * math.sin(x)
    ratio = 1.0 if abs(den) < 1e-15 else math.sin(r1 * x) / den
    return abs(ratio) ** order


def cic_decimate(bits, r1, order):
    """Run-length CIC as cascaded boxcars, then decimate by r1.

    Writing the integrator/comb form literally integrates `order` times at
    the full rate, so the intermediate values grow like n^order (n^4.5 at
    n=65536, order 5).  The combs then subtract numbers of order 1e22, and in
    float64 that difference leaves a residual larger than the signal: at
    R1=2 the decimated SNDR collapsed to a few dB.  Cascaded boxcars have
    the identical magnitude response |sin(pi f R)/ (R sin(pi f))|^order, but
    the state stays bounded by the boxcar length, so there is no cancellation.
    """
    y = list(bits)
    for _ in range(order):
        acc, z = 0.0, []
        for i, v in enumerate(y):
            acc += v
            if i >= r1:
                acc -= y[i - r1]
            z.append(acc / r1)
        y = z
    return y[r1 - 1::r1]


def inband_sndr_stream(samples, edge_bins):
    """Peak-signal / in-band-noise SNDR of an arbitrary-rate stream."""
    n = len(samples)
    edge = max(2, min(edge_bins, n // 2))
    mean = sum(samples) / n
    power = [abs(v) ** 2 for v in fft([s - mean for s in samples])[: n // 2]]
    fundamental = max(range(1, edge), key=lambda k: power[k])
    sig_bins = {fundamental + d for d in (-1, 0, 1) if 1 <= fundamental + d < edge}
    sig = sum(power[k] for k in sig_bins)
    noise = sum(power[k] for k in range(1, edge) if k not in sig_bins)
    if sig <= 0 or noise <= 0:
        return None
    return 10.0 * math.log10(sig / noise)


def decimator_metrics(spec, osr):
    """CIC size, alias rejection and droop for the configured decimator."""
    dec = (spec.get("sdm") or {}).get("decimator") or {}
    order = int(dec.get("cic_order", 4))
    r1 = int(dec.get("cic_r1", 4))
    guard = float(dec.get("guard_band", 0.9))
    r1 = max(1, min(r1, osr))
    f_mod, f_out = float(osr), 1.0
    worst = 0.0
    if r1 > 1:
        for k in range(1, r1):
            for edge in (-f_out / 2.0, f_out / 2.0):
                worst = max(worst, cic_magnitude(k * f_mod / r1 + edge,
                                                 f_mod, r1, order))
    alias_db = -20.0 * math.log10(max(worst, 1e-15))
    droop_db = 20.0 * math.log10(max(
        cic_magnitude(guard * f_out / 2.0, f_mod, r1, order), 1e-15))
    return {"cic_order": order, "cic_r1": r1, "guard_band": guard,
            "alias_db": alias_db, "droop_db": droop_db}


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
    qbits = int(sdm.get("quantizer_bits", 1))
    if order > 2:
        # modulate() only implements the 1st- and 2nd-order CIFB loops; for
        # order 3/4 it used to fall through to the 2nd-order branch and print
        # the requested order next to a 2nd-order SNDR.  Fail closed instead.
        raise SystemExit(
            f"behavioral_sdm: order {order} is not modelled (the DT loop is "
            f"order 1-2); order 3-4 is feasibility-only.")
    gain_db = float(sdm.get("integrator_gain_db", 60.0))
    osr_list = sdm.get("osr_sweep") or [16, 32, 64, 128]
    rng = random.Random(seed)

    # ---- dynamic: SNDR vs OSR ------------------------------------------
    # A 1-bit loop overloads above ~0.55 FS, so the measurement amplitude is
    # fixed at 0.5 and every number is also given referenced to full scale
    # (the feasibility bound is a full-scale SQNR).
    amp = 0.5
    thermal_lsb = float(sdm.get("thermal_noise_lsb", 0.0))
    sweep = []
    for osr in osr_list:
        n = n_samples
        b = bin_index or max(3, int(round(0.11 * n / osr)))
        xs = []
        for m in range(n):
            x = amp * math.sin(2 * math.pi * b * m / n)
            if thermal_lsb:
                x += rng.gauss(0.0, thermal_lsb)
            xs.append(x)
        bits = modulate(xs, order, gain_db)
        sndr = inband_sndr(bits, osr, b)
        dm = decimator_metrics(spec, osr)
        dec_bits = cic_decimate(bits, dm["cic_r1"], dm["cic_order"])
        edge_bins = len(dec_bits) * dm["cic_r1"] // (2 * osr)
        sndr_dec = inband_sndr_stream(dec_bits, edge_bins)
        sweep.append({"osr": osr, "sndr_db": sndr,
                      "sndr_decimated_db": sndr_dec,
                      "alias_db": dm["alias_db"], "droop_db": dm["droop_db"],
                      "cic_order": dm["cic_order"], "cic_r1": dm["cic_r1"],
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
        "quantizer_bits": qbits,
        "integrator_gain_db": gain_db,
        "osr": osr,
        "sndr_vs_osr": sweep,
        "sndr_db": sel["sndr_db"],
        "sndr_ideal_db": sel["sndr_db"],
        "sndr_decimated_db": sel["sndr_decimated_db"],
        "decimator": {k: sel[k] for k in
                      ("cic_order", "cic_r1", "alias_db", "droop_db")},
        "thermal_noise_lsb": thermal_lsb,
        "sndr_dbfs": sel["sndr_dbfs"],
        "enob": sel["enob"],
        "dc_settle": settle,
        "dc_resolution_lsb": dc_res,
        "max_dnl_lsb": max(abs(d) for d in dnl) if dnl else None,
        "max_inl_lsb": max(abs(i) for i in inl) if inl else None,
        "missing_codes": missing,
    }


def human(r):
    d = r["decimator"]
    lines = [
        f"behavioral sdm {r['nbits']}-bit, order {r['order']}, "
        f"integrator gain {r['integrator_gain_db']:.0f} dB, "
        f"CIC{d['cic_order']}/R1={d['cic_r1']} decimator",
        f"  SNDR vs OSR (measured on the bitstream at 0.5 FS, noise "
        f"integrated 0..fs_out/2):",
    ]
    for s in r["sndr_vs_osr"]:
        dec = s.get("sndr_decimated_db")
        dec_txt = f"  decimated={dec:6.2f} dB" if dec is not None else ""
        lines.append(f"    OSR {s['osr']:4d}  ideal={s['sndr_db']:6.2f} dB"
                     f"{dec_txt}  ({s['sndr_dbfs']:6.2f} dBFS)  "
                     f"ENOB={s['enob']:5.2f} bit")
    lines.append(
        f"  decimator at OSR {r['osr']}: alias rejection "
        f"{d['alias_db']:.1f} dB, droop {d['droop_db']:.2f} dB")
    lines.append(
        f"  static   at OSR {r['osr']}, {r['dc_settle']} samples/point: "
        f"max|DNL|={r['max_dnl_lsb']:.2f} LSB  "
        f"max|INL|={r['max_inl_lsb']:.2f} LSB  missing codes={r['missing_codes']}")
    lines.append(
        f"           measurement floor {r['dc_resolution_lsb']:.2f} LSB "
        f"(a decimated 1-bit stream, not a cap-array DNL)")
    if r.get("quantizer_bits", 1) > 1:
        lines.append(
            f"  caveat   the loop model is 1-bit; quantizer_bits="
            f"{r['quantizer_bits']} is a feasibility-only DAC-mismatch path")
    if r.get("thermal_noise_lsb"):
        lines.append(f"  thermal  input-referred {r['thermal_noise_lsb']:.6f} "
                     f"LSB injected before the loop")
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

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
    """Binary weights in unit-capacitor units, with mismatch on each cell.

    Returns ``(weights, dummy)`` where ``weights`` are the nbits switched
    capacitors and ``dummy`` is the fixed terminating unit.  The dummy is a
    real capacitor too: it shares the array's common-mode error and its own
    unit mismatch, and it is part of the physical charge budget, so it is drawn
    here rather than assumed ideal.
    """
    weights = []
    for bit in range(nbits):
        nominal = 1 << bit
        # the cell is 2^bit units in parallel: absolute errors add in
        # quadrature, so the cell's relative sigma is sigma_unit/sqrt(2^bit)
        sigma = sigma_cu_rel / math.sqrt(nominal)
        weights.append(nominal * (1.0 + rng.gauss(0.0, sigma)))
    # the terminating dummy is a single unit capacitor
    dummy = 1.0 * (1.0 + rng.gauss(0.0, sigma_cu_rel))
    return weights, dummy


def convert(x, weights, total, offset_lsb, sigma_lsb, rng, cv=None):
    """One SAR conversion. x, offset and sigma are normalised to full scale.

    ``cv`` (Bug3): an optional per-unit-cap voltage coefficient -- the
    fractional capacitance change when a bit's bottom plate sits at VREFH
    instead of VSS.  The MOSCAP backend swings up to ~1.8x over bias
    (cv≈0.8); a MOM cap is flat (cv≈0).  The bit's effective weight then
    depends on the code being built, which is exactly the input-dependent
    weight error the taped-out chip showed as -8%..+7% bit-weight spread.
    """
    code = 0
    # At each bit decision the trial plate sits at VREFH, so the cap under
    # test carries its high-bias value; every bit already kept at VREFH is
    # also at high bias.  The threshold the comparator resolves is the DAC
    # level *at that moment* -- decided bits + trial bit at high bias,
    # undecided bits at low bias -- over the array total biased the same
    # way.  With cv>0 the weights are no longer code-independent: the same
    # physical bit contributes differently depending on where the search
    # has parked the already-decided plates.  That is Bug3: with a MOSCAP
    # cv of ~0.8 the weight set drifts with the input; with a MOM cap
    # (cv~0) it does not.
    set_sum = 0.0   # nominal (low-bias) sum of bits decided at VREFH
    for bit in range(len(weights) - 1, -1, -1):
        w_hi = weights[bit] * (1.0 + (cv or 0.0))
        # DAC level during this trial: decided bits + trial bit at VREFH
        trial_dac = set_sum * (1.0 + (cv or 0.0)) + w_hi
        # array total biased the same way: decided + trial swollen
        eff_total = total + (cv or 0.0) * (set_sum + weights[bit])
        threshold = trial_dac / eff_total + offset_lsb
        sample = x + (rng.gauss(0.0, sigma_lsb) if sigma_lsb else 0.0)
        if sample >= threshold:
            code |= 1 << bit
            set_sum += weights[bit]
    return code


def static_scan(nbits, weights, total, offset_lsb, sigma_lsb, rng, points_per_code=64, cv=None):
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
        counts[convert(x, weights, total, offset_lsb, sigma_lsb, rng, cv=cv)] += 1
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

try:
    from spectral import fft, spectral
except ImportError:  # standalone: python3 gen/verify/behavioral_x.py
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from spectral import fft, spectral

def dynamic_scan(nbits, weights, total, offset_lsb, sigma_lsb, rng,
                 n_samples=1024, bin_index=31, ktc_lsb=0.0, cv=None):
    codes = []
    for m in range(n_samples):
        x = 0.5 + 0.49 * math.sin(2.0 * math.pi * bin_index * m / n_samples)
        if ktc_lsb:
            # per-sample input-referred kT/C sampling noise (one draw each
            # conversion, not per bit decision); x is normalised to FS so the
            # LSB-sized sigma is divided by 2^N
            x += rng.gauss(0.0, ktc_lsb / float(1 << nbits))
        codes.append(convert(x, weights, total, offset_lsb, sigma_lsb, rng, cv=cv))
    return codes


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

K_B = 1.380649e-23
T_DEFAULT = 300.0


def _ktc_lsb(spec, nbits, temperature=T_DEFAULT):
    """Input-referred kT/C sampling noise in LSB.

    feasibility.py sizes C_tot against 12 kT 2^(2N)/vfs^2 so that the
    Nyquist-band SNR meets the ideal quantisation limit; the per-sample
    input-referred variance for a C_tot array sampled once is
        sigma_v^2 = kT / C_tot
    normalised to LSB = vfs/2^N.  The behavioral model needs the same term or
    its dynamic SINAD claims noise the real sampling switch cannot deliver.
    """
    vfs = float(spec.get("input", {}).get("vfs", spec.get("vdd", 1.0)))
    cu_ff = spec.get("cdac", {}).get("cu_ff", "auto")
    # the array total: 2^N unit caps.  When cu_ff is "auto" the spec does not
    # pin it, so fall back to the feasibility floor (the binding constraint).
    if isinstance(cu_ff, (int, float)):
        c_tot_f = (1 << nbits) * float(cu_ff) * 1e-15
    else:
        c_tot_f = 12.0 * K_B * temperature * (2.0 ** (2 * nbits)) / (vfs ** 2)
    if c_tot_f <= 0:
        return 0.0
    lsb_v = vfs / (1 << nbits)
    return math.sqrt(K_B * temperature / c_tot_f) / lsb_v


def run(spec, sigma_cu_rel, trials=10, seed=1, n_samples=1024, bin_index=31,
        points_per_code=64, cv=None):
    nbits = int(spec["resolution"])
    offset_lsb = float(spec.get("comparator_offset_lsb", 0.0)) / (1 << nbits)
    sigma_lsb = float(spec.get("comparator_noise_lsb", 0.0)) / (1 << nbits)
    # dynamic scan uses a 0.49-FS sine; ENOB is quoted vs full scale, so SINAD
    # is corrected by the amplitude shortfall (a ~0.03 bit pessimism otherwise)
    amp_corr_db = -20.0 * math.log10(0.49 / 0.5)
    # kT/C sampling noise the real array adds on top of quantisation.  It is a
    # per-SAMPLE offset on the input value, not per-comparison noise -- folding
    # it into sigma_lsb would wrongly inject it into every one of the N bit
    # decisions (which cannot be undone).  Apply it to the input sine instead.
    ktc_lsb = _ktc_lsb(spec, nbits)

    worst_dnl = 0.0
    worst_inl = 0.0
    missing = 0
    dynamic = None

    for t in range(trials):
        rng = random.Random(seed + t)
        weights, dummy = make_weights(nbits, sigma_cu_rel, rng)
        # the charge budget is the ACTUAL array total -- every switched unit
        # plus the drawn dummy.  Using the nominal 2^N denominator here was a
        # real bug: a common-scale cap error (e.g. MOSCAP C(V) density drift,
        # which moves every unit together by ~1.8x over bias) then showed up
        # as a false INL/missing-code defect.  Scaling all units by the same
        # factor is physically transparent; only the drawn sum is.
        total = sum(weights) + dummy

        counts = static_scan(nbits, weights, total, offset_lsb, sigma_lsb, rng,
                             points_per_code, cv=cv)
        dnl, inl, miss = dnl_inl(counts, points_per_code)
        worst_dnl = max(worst_dnl, max(abs(d) for d in dnl))
        worst_inl = max(worst_inl, max(abs(i) for i in inl))
        missing = max(missing, miss)

        if t == 0:
            samples = dynamic_scan(nbits, weights, total, offset_lsb,
                                   sigma_lsb, rng, n_samples, bin_index,
                                   ktc_lsb=ktc_lsb, cv=cv)
            dynamic = spectral(samples, nbits)
            if dynamic["sinad_db"] is not None:
                # SINAD measured on a 0.49-FS tone -> SINAD at full scale
                dynamic["sinad_db"] += amp_corr_db
                dynamic["sndr_db"] = dynamic["sinad_db"]
                dynamic["enob"] = (dynamic["sinad_db"] - 1.76) / 6.02

    return {
        "arch": "sar",
        "nbits": nbits,
        "sigma_cu_rel": sigma_cu_rel,
        "trials": trials,
        "max_dnl_lsb": worst_dnl,
        "max_inl_lsb": worst_inl,
        "missing_codes": missing,
        "ktc_lsb": ktc_lsb,
        "cv": cv,
        "sndr_db": dynamic["sndr_db"] if dynamic else None,
        "snr_db": dynamic["snr_db"] if dynamic else None,
        "sinad_db": dynamic["sinad_db"] if dynamic else None,
        "thd_db": dynamic["thd_db"] if dynamic else None,
        "enob": dynamic["enob"] if dynamic else None,
        "sfdr_dbc": dynamic["sfdr_dbc"] if dynamic else None,
    }


def human(r):
    return "\n".join([
        f"behavioral {r['nbits']}-bit, sigma_Cu/Cu={r['sigma_cu_rel']*100:.2f}%, "
        f"{r['trials']} mismatch trials",
        f"  static   max|DNL|={r['max_dnl_lsb']:.3f} LSB  "
        f"max|INL|={r['max_inl_lsb']:.3f} LSB  missing codes={r['missing_codes']}",
        f"  dynamic  SNDR={r['sndr_db']:.2f} dB  "
        f"ENOB={r['enob']:.2f} bit  SFDR={r['sfdr_dbc']:.1f} dBc  THD={r['thd_db']:.1f} dB  SNR={r['snr_db']:.1f} dB",
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
    ap.add_argument("--cv", type=float, default=None,
                    help="unit-cap voltage coefficient: fractional C change at "
                    "VREFH vs VSS (MOSCAP ~0.8, MOM ~0.0).  Drives the Bug3 "
                    "input-dependent weight error.")
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

    result = run(spec, sigma, args.trials, args.seed, args.samples, args.bin,
                 cv=args.cv)
    print(json.dumps(result, indent=2) if args.json else human(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

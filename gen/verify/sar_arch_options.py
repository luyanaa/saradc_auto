#!/usr/bin/env python3
"""Architecture options for the SAR CDAC, evaluated in the code domain.

Three options the taped-out chip did not have, each with a measurable model:

  switching="conventional" | "monotonic"
      Plate-switching activity per conversion and the common-mode walk.
      Conventional drives every bit's differential trial up and back on a
      clear; monotonic (Vcm-based, Liu et al.) starts both plates at Vcm and
      moves exactly one plate per decision, so the reference charge per
      conversion drops and the common mode walks in one direction.

  split/bridge
      A sub-DAC behind a series bridge capacitor.  The bridge ratio error
      ``eps`` scales every sub-DAC weight, which is a real, code-domain INL
      mechanism: the error steps at the bridge boundary.

  calibration
      Per-bit digital gain trims computed from the measured weights, then the
      same static scan re-run.  Shows how much of the mismatch INL is
      recoverable without changing the analog array.

These are behavioural models, not transistor simulations; they exist so an
architecture can be compared before layout, and they are checked by
``sar_arch_options_test.py``.
"""

from __future__ import annotations

import json
import math
import random
import sys


# --------------------------------------------------------------------------
# switching activity
# --------------------------------------------------------------------------

def _events(nbits, code, scheme):
    """Return (switch_events, common_mode_walk_units) for one conversion.

    Units are "one plate moved by one reference step".  A differential
    conventional trial moves both plates (+w on one side, -w on the other),
    so it is two events; a clear moves them back (+2).  Monotonic moves one
    plate per decision.
    """
    if scheme == "conventional":
        events = 0
        for bit in range(nbits - 1, -1, -1):
            events += 2                      # differential trial, both plates
            if not (code >> bit) & 1:
                events += 2                  # cleared: drive back
        return events, 0
    if scheme == "monotonic":
        # One plate per decision; the moved side is the one whose decision
        # went high, so the common mode walks toward that rail and never
        # returns.
        signed = 0
        for bit in range(nbits - 1, -1, -1):
            signed += 1 if (code >> bit) & 1 else -1
        return nbits, abs(signed)
    raise ValueError(scheme)


def switching_activity(nbits, scheme):
    n_codes = 1 << nbits
    total_events = 0
    max_walk = 0
    for code in range(n_codes):
        e, w = _events(nbits, code, scheme)
        total_events += e
        max_walk = max(max_walk, w)
    return {"scheme": scheme, "nbits": nbits,
            "avg_switch_events": total_events / float(n_codes),
            "max_cm_walk_steps": max_walk,
            "note": "events = one plate moved one reference step; "
                    "cm walk = |sum of +-1 decisions|, the number of steps the "
                    "common mode moves off Vcm"}


# --------------------------------------------------------------------------
# split / bridge sub-DAC
# --------------------------------------------------------------------------

def bridge_weights(nbits, split_bit, eps, sigma_cu_rel=0.0, rng=None):
    """Weights with ``split_bit`` sub-DAC bits behind a bridge of ratio 1+eps.

    Bits 0..split_bit-1 form the sub-DAC and are attenuated by the bridge;
    a fractional ratio error eps multiplies all of them together, so the
    sub-DAC gain is wrong by eps while the main DAC is exact.
    """
    rng = rng or random.Random(0)
    weights = []
    for bit in range(nbits):
        nominal = float(1 << bit)
        sigma = (sigma_cu_rel / math.sqrt(nominal)) if sigma_cu_rel else 0.0
        w = nominal * (1.0 + (rng.gauss(0.0, sigma) if sigma else 0.0))
        if bit < split_bit:
            w *= (1.0 + eps)
        weights.append(w)
    return weights


def static_inl(nbits, weights, total=None, points_per_code=64,
               offset_lsb=0.0, sigma_lsb=0.0, seed=0):
    """Reuse the main behavioural model's static scan for a weight set."""
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from behavioral_sar import static_scan, dnl_inl

    rng = random.Random(seed)
    total = total if total is not None else sum(weights)
    counts = static_scan(nbits, weights, total, offset_lsb, sigma_lsb, rng,
                         points_per_code)
    # The ramp in static_scan runs over [0, 1) at (k+0.5)/N, so the top code
    # (all bits set, threshold = 1.0) is never reached.  Dropping that known
    # unreachable bin is what the model's own test does; otherwise every
    # result is dominated by a false 1-LSB DNL at the top of the range.
    counts = counts[:-1]
    dnl, inl, missing = dnl_inl(counts, points_per_code)
    # INL is reported against a best-fit straight line, the standard
    # definition.  Cumulative DNL from bin 0 also contains the gain error
    # (the first bin is a hair wider than the rest), which is a linear term
    # and not nonlinearity; leaving it in made every weight set look like a
    # 1-LSB INL defect.
    n = len(inl)
    if n >= 2:
        xs = list(range(n))
        mx = sum(xs) / n
        my = sum(inl) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, inl))
        slope = sxy / sxx if sxx else 0.0
        inl = [y - (my + slope * (x - mx)) for x, y in zip(xs, inl)]
    return {"max_dnl_lsb": max(abs(d) for d in dnl),
            "max_inl_lsb": max(abs(i) for i in inl),
            "missing_codes": missing,
            "note": "best-fit INL; top code excluded (unreachable by the [0,1) ramp)"}


# --------------------------------------------------------------------------
# digital calibration
# --------------------------------------------------------------------------

def calibrate(weights, nominal=None):
    """Per-bit trims that restore the nominal binary weights exactly.

    In hardware the trims are measured (code-density or bit-weight tests);
    here the measured weights are known, so the optimum is trim_i =
    nominal_i / measured_i.  Trims are clipped to +-25% like a real trim DAC.
    """
    nbits = len(weights)
    nominal = nominal or [float(1 << i) for i in range(nbits)]
    trims = []
    for w, nom in zip(weights, nominal):
        t = nom / w if w else 1.0
        trims.append(min(1.25, max(0.75, t)))
    return trims


def apply_trims(weights, trims):
    return [w * t for w, t in zip(weights, trims)]


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--nbits", type=int, default=8)
    ap.add_argument("--sigma-cu", type=float, default=0.01)
    ap.add_argument("--split-bit", type=int, default=4)
    ap.add_argument("--bridge-eps", type=float, default=0.005)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    out = {"nbits": args.nbits}
    out["switching"] = [switching_activity(args.nbits, s)
                        for s in ("conventional", "monotonic")]

    rng = random.Random(args.seed)
    ideal = bridge_weights(args.nbits, args.split_bit, 0.0, 0.0, rng)
    out["bridge"] = []
    for eps in (0.0, args.bridge_eps, 2 * args.bridge_eps):
        w = bridge_weights(args.nbits, args.split_bit, eps, 0.0, rng)
        r = static_inl(args.nbits, w, sum(ideal))
        r["eps"] = eps
        out["bridge"].append(r)

    w = bridge_weights(args.nbits, args.split_bit, args.bridge_eps,
                       args.sigma_cu, random.Random(args.seed))
    # Analog trim = switchable trim capacitors in parallel with each cell, so
    # the physical weight (and therefore the drawn array total) really does
    # change; each run uses its own sum as the denominator.
    out["calibration"] = {
        "uncal": static_inl(args.nbits, w),
        "cal": static_inl(args.nbits, apply_trims(w, calibrate(w))),
        "trims": calibrate(w),
    }

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"SAR architecture options, {args.nbits} bit")
        print("  switching:")
        for s in out["switching"]:
            print(f"    {s['scheme']:13s} avg events/conv {s['avg_switch_events']:6.2f}"
                  f"  max CM walk {s['max_cm_walk_steps']:2d} steps")
        print("  bridge ratio error (sub-DAC bits 0..%d):" % (args.split_bit - 1))
        for r in out["bridge"]:
            print(f"    eps={r['eps']*100:5.2f}%  max|DNL|={r['max_dnl_lsb']:.3f}"
                  f"  max|INL|={r['max_inl_lsb']:.3f} LSB")
        c = out["calibration"]
        print("  analog trim (bridge eps + %.1f%% unit mismatch):"
              % (args.sigma_cu * 100))
        print(f"    uncal  max|DNL|={c['uncal']['max_dnl_lsb']:.3f}"
              f"  max|INL|={c['uncal']['max_inl_lsb']:.3f} LSB")
        print(f"    trim   max|DNL|={c['cal']['max_dnl_lsb']:.3f}"
              f"  max|INL|={c['cal']['max_inl_lsb']:.3f} LSB"
              f"  (trims {min(c['trims']):.3f}..{max(c['trims']):.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

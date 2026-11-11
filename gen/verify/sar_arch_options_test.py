#!/usr/bin/env python3
"""Regression checks for gen/verify/sar_arch_options.py.

Each check is a property the model must satisfy for the option to be real:
switching activity must drop, bridge ratio error must be visible as INL, and
trimming must recover the ideal transfer.
"""

import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sar_arch_options as o


def check(name, cond, detail):
    print(f"  {name:44s} {detail}  {'PASS' if cond else 'FAIL'}")
    return bool(cond)


def main():
    ok = True
    nbits = 8
    print("SAR architecture option regression:")

    conv = o.switching_activity(nbits, "conventional")
    mono = o.switching_activity(nbits, "monotonic")
    ok &= check("monotonic is one plate per decision",
                abs(mono["avg_switch_events"] - nbits) < 1e-9,
                f"{mono['avg_switch_events']:.2f} events/conv")
    ok &= check("conventional is >2 events per bit",
                conv["avg_switch_events"] > 2 * nbits,
                f"{conv['avg_switch_events']:.2f} events/conv")
    ok &= check("monotonic trades CM walk for fewer events",
                mono["max_cm_walk_steps"] == nbits and conv["max_cm_walk_steps"] == 0,
                f"walk {conv['max_cm_walk_steps']} -> {mono['max_cm_walk_steps']} steps")

    ideal = o.static_inl(nbits, o.bridge_weights(nbits, 4, 0.0))
    ok &= check("ideal binary array is clean", ideal["max_inl_lsb"] < 0.02,
                f"INL {ideal['max_inl_lsb']:.4f} LSB")

    inl = [o.static_inl(nbits, o.bridge_weights(nbits, 4, eps))["max_inl_lsb"]
           for eps in (0.0, 0.005, 0.01)]
    ok &= check("bridge ratio error raises INL monotonically",
                inl[0] < inl[1] < inl[2],
                "  ".join(f"{v:.4f}" for v in inl))
    ok &= check("0.5% bridge error is a sub-LSB but visible step",
                0.02 < inl[1] < 0.2, f"{inl[1]:.4f} LSB")

    w = o.bridge_weights(nbits, 4, 0.005, 0.01, random.Random(7))
    uncal = o.static_inl(nbits, w)
    trims = o.calibrate(w)
    cal = o.static_inl(nbits, o.apply_trims(w, trims))
    ok &= check("trim recovers >=5x of the mismatched INL",
                cal["max_inl_lsb"] * 5.0 <= uncal["max_inl_lsb"],
                f"{uncal['max_inl_lsb']:.4f} -> {cal['max_inl_lsb']:.4f} LSB")
    ok &= check("trims stay inside the +-25% trim range",
                all(0.75 <= t <= 1.25 for t in trims),
                f"{min(trims):.3f}..{max(trims):.3f}")

    print("all PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

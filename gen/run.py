#!/usr/bin/env python3
"""Single entry point for the ADC generator (M0).

  run.py feasibility --spec S --pdk P     one spec on one PDK
  run.py matrix --spec S                  one spec on every PDK profile
  run.py behavioral --spec S [--feas F]   L1 model chosen by spec["arch"]
  run.py report [--out FILE]              markdown matrix of all specs
  run.py check                            profile inputs that do not exist
                                          (generated decks under outputs are
                                          reported, not failed)
"""

from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "verify"))

import feasibility  # noqa: E402
import behavioral_sar  # noqa: E402
import behavioral_ramp  # noqa: E402
import behavioral_sdm  # noqa: E402
import behavioral_vco  # noqa: E402

BEHAVIORAL = {
    "sar": behavioral_sar,
    "ramp": behavioral_ramp,
    "sdm": behavioral_sdm,
    "vco": behavioral_vco,
}

PDK_DIR = os.path.join(HERE, "pdk")
SPEC_DIR = os.path.join(HERE, "spec", "examples")
OUTPUT_DIR = os.path.join(HERE, "outputs")


def is_build_artifact(path):
    """True for generated files under gen/outputs (not PDK inputs)."""
    real = os.path.realpath(feasibility.expand(path))
    return real.startswith(os.path.realpath(OUTPUT_DIR) + os.sep)


def profiles():
    return [os.path.join(PDK_DIR, n)
            for n in sorted(os.listdir(PDK_DIR)) if n.endswith(".json")]


def specs():
    return [os.path.join(SPEC_DIR, n)
            for n in sorted(os.listdir(SPEC_DIR)) if n.endswith(".json")]


def load(path):
    with open(feasibility.expand(path), encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------

def resolve_pdk(value):
    path = feasibility.expand(value)
    if os.path.exists(path):
        return path
    candidate = os.path.join(PDK_DIR, value + ".json")
    if os.path.exists(candidate):
        return candidate
    raise SystemExit(f"unknown pdk '{value}'; profiles: "
                     + ", ".join(os.path.basename(p)[:-5] for p in profiles()))


def cmd_feasibility(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="run.py feasibility")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--pdk", required=True)
    ap.add_argument("--headroom", type=float, default=feasibility.DEFAULT_HEADROOM)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = feasibility.evaluate(load(args.spec), load(resolve_pdk(args.pdk)),
                                  args.headroom)
    print(feasibility.dump(report) if args.json else feasibility.human(report))
    return 0 if report["status"].startswith("PASS") else 1


def cmd_matrix(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="run.py matrix")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    spec = load(args.spec)
    rows = []
    for path in profiles():
        try:
            rows.append(feasibility.evaluate(spec, load(path)))
        except KeyError as exc:
            rows.append({"pdk": os.path.basename(path)[:-5],
                         "status": "FAIL_UNCHARACTERIZED", "error": str(exc)})
    print(feasibility.dump(rows) if args.json else feasibility.matrix_table(rows))
    return 0 if any(r["status"].startswith("PASS") for r in rows) else 1


def cmd_behavioral(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="run.py behavioral")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--sigma-cu", type=float)
    ap.add_argument("--from-feasibility")
    ap.add_argument("--osr", type=int, help="override logic.osr (sdm)")
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--samples", type=int, help="FFT length (default: 65536 sdm, 1024 otherwise)")
    ap.add_argument("--bin", type=int, default=31)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    spec = load(args.spec)
    arch = str(spec.get("arch", "sar")).lower()
    backend = BEHAVIORAL.get(arch)
    if backend is None:
        raise SystemExit(f"no behavioral model for arch '{arch}'; "
                         f"known: {', '.join(BEHAVIORAL)}")
    if args.osr is not None:
        spec.setdefault("logic", {})["osr"] = args.osr
    samples = args.samples or (65536 if arch == "sdm" else 1024)

    feas = None
    if args.from_feasibility:
        feas = load(args.from_feasibility)
    sigma = args.sigma_cu
    if sigma is None and feas is not None:
        sigma = feas.get("sigma_cu_rel")

    if arch == "vco":
        result = backend.run(spec, sigma or 0.0, args.trials, args.seed,
                             samples, args.bin, feas=feas)
    elif arch == "sdm":
        result = backend.run(spec, sigma or 0.0, 1, args.seed, samples)
    else:
        result = backend.run(spec, sigma or 0.0, args.trials, args.seed,
                             samples, args.bin)
    print(json.dumps(result, indent=2) if args.json else backend.human(result))
    return 0


def cmd_report(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="run.py report")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    header = ("| spec | pdk | arch | target | status | cap fF | Ctot fF | binding | "
              "fs_max | DNL(1s) | area um2 |\n"
              "|---|---|---|---|---|---|---|---|---|---|---|")
    lines = ["# ADC generator feasibility matrix", "", header]
    for spec_path in specs():
        spec = load(spec_path)
        name = os.path.basename(spec_path)[:-5]
        for pdk_path in profiles():
            pdk = os.path.basename(pdk_path)[:-5]
            try:
                r = feasibility.evaluate(spec, load(pdk_path))
            except KeyError as exc:
                lines.append(f"| {name} | {pdk} | {spec.get('arch', 'sar')} | | "
                             f"FAIL_UNCHARACTERIZED | | | | | | {exc} |")
                continue
            lines.append(
                f"| {name} | {pdk} | {r.get('arch', 'sar')} | "
                f"{r['fs_target']/1e3:.0f} kS/s | {r['status']} | "
                f"{r['cu_ff']:.3f} | {r['c_tot_ff']:.1f} | {r['binding_constraint']} | "
                f"{r['fs_max']/1e3:.0f} kS/s | {r['dnl_msb_sigma_lsb']:.2f} | "
                f"{r['area_cap_um2']:.0f} |"
            )
    text = "\n".join(lines) + "\n"
    if args.out:
        with open(feasibility.expand(args.out), "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_check(_argv):
    bad = 0
    for path in profiles():
        prof = load(path)
        fields = [(f"stdcell.{k}", prof["stdcell"][k]) for k in ("lib", "lef", "tech_lef")]
        for key in ("ngspice_deck", "xyce_deck"):
            if prof.get("sim", {}).get(key):
                fields.append((f"sim.{key}", prof["sim"][key]))
        if prof.get("signoff", {}).get("magicrc"):
            fields.append(("signoff.magicrc", prof["signoff"]["magicrc"]))

        missing, artifacts = [], []
        for name, value in fields:
            if os.path.exists(feasibility.expand(value)):
                continue
            # a deck this repo generates (gen/outputs) is not a PDK input;
            # `make deck-ics55` is what produces it
            (artifacts if is_build_artifact(value) else missing).append((name, value))

        if missing:
            bad += len(missing)
            print(f"{prof['id']}: {len(missing)} missing")
            for name, value in missing:
                print(f"    {name}: {feasibility.expand(value)}")
        elif artifacts:
            print(f"{prof['id']}: ok ({len(artifacts)} generated artifact(s) "
                  f"absent; run the matching deck target)")
        else:
            print(f"{prof['id']}: ok")
    return 0 if bad == 0 else 1


def cmd_analog(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="run.py analog")
    ap.add_argument("--pdk", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--nbits", type=int, default=8)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)

    sys.path.insert(0, os.path.join(HERE, "analog"))
    import macro_gen

    profile = macro_gen.load_profile(args.pdk)
    rows = []
    for topo in ("cap_unit", "switch_tg", "switch_bootstrap",
                 "cmp_strongarm", "cdac_binary"):
        name = "SARADC_" + topo.upper()
        try:
            m = macro_gen.generate(profile, topo, name,
                                   os.path.join(args.out, topo),
                                   {"nbits": args.nbits})
            rows.append((topo, m["status"], m.get("unit_caps", ""), m.get("n_devices", "")))
        except SystemExit as exc:
            print(exc)
            return 1

    width = max(len(r[0]) for r in rows)
    for topo, status, caps, devs in rows:
        extra = f"  caps={caps}" if caps else f"  devices={devs}" if devs else ""
        print(f"{topo:{width}s}  {status}{extra}")

    if args.smoke:
        import smoke
        manifests = {t: os.path.join(args.out, t) for t, _, _, _ in rows}
        results = smoke.run(manifests, profile["vdd"],
                            devices=profile["devices"])
        print()
        for r in results:
            print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['topology']:16s} {r['check']}")
        return 0 if all(r["pass"] for r in results) else 1
    return 0


COMMANDS = {
    "feasibility": cmd_feasibility,
    "matrix": cmd_matrix,
    "behavioral": cmd_behavioral,
    "report": cmd_report,
    "analog": cmd_analog,
    "check": cmd_check,
}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print(__doc__.strip())
        return 2 if not argv else 1
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

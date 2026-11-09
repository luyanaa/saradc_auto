#!/usr/bin/env python3
"""Build a self-contained PDK model deck from a PDK's corner files.

Why this exists: sky130's ``sky130.lib.spice`` uses nested relative
``.include`` paths that only resolve from inside the PDK tree, and the
flattened device files reference mismatch parameters in a scope ngspice does
not populate.  Flattening once, in order, into a single file fixes both:

  * every include is resolved relative to the file that contains it;
  * ``.param`` lines and the ``.model`` that uses them end up in one scope;
  * parameters that the models reference but no corner file defines (the LOD
    ``*_diff`` terms, which are zero in the nominal case) are declared once,
    explicitly, as 0 instead of failing the substitution.

The output is a run artifact.  It is not checked in and not a PDK copy.
"""

from __future__ import annotations

import os
import re
import sys

import hspice_expr

INCLUDE = re.compile(r'^\s*\.include\s+"?([^"\s]+)"?', re.I)
PARAM_DEF = re.compile(r"^\s*\.param\s+(?:([A-Za-z_]\w*)\s*=\s*([^\s]+))", re.I)
PARAM_ALL = re.compile(r"^\s*\.param\s+(.+)$", re.I)
BRACED = re.compile(r"\{([^}]*)\}")
IDENT = re.compile(r"\bsky130_fd_pr__[A-Za-z0-9_]+")


def expand(path):
    return os.path.expandvars(os.path.expanduser(path))

def _read(path):
    with open(expand(path), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _resolve(ref, base):
    ref = ref.strip().strip('"')
    if not os.path.isabs(ref):
        ref = os.path.normpath(os.path.join(base, ref))
    return ref


def collect(path, lines, wanted=None, depth=0, seen=None):
    """Recursively inline a spice file, optionally filtering includes."""
    if depth > 12:
        raise SystemExit(f"include recursion too deep at {path}")
    seen = seen or set()
    real = os.path.realpath(expand(path))
    if real in seen:
        return
    seen.add(real)
    base = os.path.dirname(expand(path))
    for raw in _read(path).splitlines():
        inc = INCLUDE.match(raw)
        if inc:
            sub = _resolve(inc.group(1), base)
            if wanted and not any(w in os.path.basename(sub) for w in wanted):
                continue
            collect(sub, lines, wanted, depth + 1, seen)
            continue
        if re.match(r"^\s*\.(lib|endl)\b", raw, re.I):
            continue
        lines.append(raw)


def declared_params(text):
    names = set()
    for line in text.splitlines():
        for chunk in PARAM_ALL.findall(line):
            for part in chunk.split():
                if "=" in part:
                    names.add(part.split("=", 1)[0].strip())
    return names


def referenced_params(text):
    names = set()
    for expr in BRACED.findall(text):
        names.update(IDENT.findall(expr))
    return names


def build(corner_index, wanted, out_path, temperature=27.0):
    lines = [f"* flattened PDK deck, corner index {os.path.basename(corner_index)}",
             f".options tnom={temperature:g} temp={temperature:g}"]
    collect(corner_index, lines, wanted=wanted)

    text = "\n".join(lines)
    defined = declared_params(text)
    missing = sorted(referenced_params(text) - defined)

    header = [".param mc_mm_switch=0", ".param mc_pr_switch=0"]
    header += [f".param {name}=0" for name in missing]

    os.makedirs(os.path.dirname(os.path.abspath(expand(out_path))), exist_ok=True)
    with open(expand(out_path), "w", encoding="utf-8") as fh:
        fh.write("\n".join(header) + "\n")
        fh.write(text + "\n")
    return {"path": out_path, "declared_zero": missing,
            "bytes": os.path.getsize(expand(out_path)),
            "includes_flattened": len(text.splitlines())}


# --------------------------------------------------------------------------
# ICsprout 55 nm -> flat Xyce deck
# --------------------------------------------------------------------------
#
# The vendor deck is HSpice-only: model_wrapper1_hsp.lib holds the corner
# parameters as a .LIB section, and each mos/<vt>.mdl holds a *binned* BSIM4
# model (20 .model blocks with lmin/lmax/wmin/wmax brackets).  Xyce cannot
# consume the wrapper/include shape, so this flattens it the same way the
# sky130 deck is flattened, with three ics55-specific steps that were found
# the hard way:
#
#   1. keep only the requested .LIB section (its .PARAM lines are the corner
#      deltas, already 0 for tt) and drop every .LIB/.ENDL line;
#   2. keep exactly ONE bin per device -- the one whose lmin/lmax/wmin/wmax
#      brackets the requested geometry -- and rename it to the bare device
#      name (nm1p2_svt_lp, pm1p2_svt_lp, ...).  Loading all bins makes the
#      model choice ambiguous in Xyce;
#   3. declare the statistical parameters the bin expressions can reference
#      (sigma_mis_a..d) as 0.  Without them the .param substitution fails
#      before the first device is parsed.
#
# The result runs in Xyce 7.10 (BSIM4 level 54).  Spot check with a bare
# device: w/l in metres, nf=1, explicit sa/sb/as/ad/ps/pd (the bin's
# saref/sbref defaults do not fill them in).
ICS55_MOS = {
    "nm1p2_hvt_lp": "nhvt.mdl",
    "pm1p2_hvt_lp": "phvt.mdl",
    "nm1p2_svt_lp": "nsvt.mdl",
    "pm1p2_svt_lp": "psvt.mdl",
    "nm1p2_lvt_lp": "nlvt.mdl",
    "pm1p2_lvt_lp": "plvt.mdl",
    "nnat1p2_lp": "nnat1p2_lp.mdl",
}
MODEL_LINE = re.compile(r"^\.model\s+(\S+)\s+(\S+)\s*$", re.I)
BOUND = re.compile(r"\b(lmin|lmax|wmin|wmax)\s*=\s*([0-9.+\-eE]+)")


def _lib_section(path, corner):
    out, inside = [], False
    for raw in _read(path).splitlines():
        s = raw.strip()
        if re.match(r"^\.lib\b", s, re.I):
            parts = s.split()
            inside = len(parts) > 1 and parts[1].lower() == corner.lower()
            continue
        if re.match(r"^\.endl\b", s, re.I):
            if inside:
                break
            continue
        if inside:
            out.append(raw)
    if not out:
        raise SystemExit(f"{path}: no .LIB {corner} section")
    return out


def _model_blocks(path):
    lines = _read(path).splitlines()
    heads = [i for i, ln in enumerate(lines) if MODEL_LINE.match(ln)]
    blocks = []
    for k, start in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        name, kind = MODEL_LINE.match(lines[start]).groups()
        bounds = {}
        # lmin/lmax/wmin/wmax sit near the top of a binned block but past
        # 30 lines in the single-model svt/lvt files; scan the whole block.
        for ln in lines[start:end]:
            for key, val in BOUND.findall(ln):
                bounds.setdefault(key, float(val))
        blocks.append({"name": name, "kind": kind, "bounds": bounds,
                       "lines": lines[start:end]})
    return blocks


def build_ics55_xyce(pdk_root, out_path, devices, corner="tt", w_um=1.0,
                     l_um=0.06):
    root = expand(pdk_root)
    wrapper = os.path.join(root, "libs.tech/spice/foundry/hspice/"
                                 "model_wrapper1_hsp.lib")
    if not os.path.isfile(wrapper):
        raise SystemExit(f"not an ICsprout 55 PDK: {wrapper} missing")

    w_m, l_m = w_um * 1e-6, l_um * 1e-6
    picks, model_lines, baked, resolved = [], [], 0, 0
    for device in devices:
        mdl = ICS55_MOS.get(device)
        if mdl is None:
            raise SystemExit(f"unknown ics55 device '{device}'; known: "
                             + ", ".join(sorted(ICS55_MOS)))
        path = os.path.join(root, "libs.tech/spice/foundry/hspice/mos", mdl)
        chosen = None
        for block in _model_blocks(path):
            b = block["bounds"]
            if not b:
                continue
            if (b["lmin"] <= l_m <= b["lmax"]
                    and b["wmin"] <= w_m <= b["wmax"]):
                chosen = block
                break
        if chosen is None:
            raise SystemExit(f"{path}: no bin brackets L={l_um} um "
                             f"W={w_um} um")
        b = chosen["bounds"]
        if "evalbin" in _read(path) or "selectp" in _read(path):
            # single card interpolated over a W/L grid: resolve the vendor's
            # select()/evalbin() macros to numbers at this geometry
            model_lines.append("\n".join(
                hspice_expr.resolve_card(path, device, chosen["kind"],
                                         l_m, w_m,
                                         sigma={"sigma_mis_a": 0.0,
                                                "sigma_mis_b": 0.0,
                                                "sigma_mis_c": 0.0,
                                                "sigma_mis_d": 0.0})))
            resolved += 1
            picks.append({"device": device, "bin": chosen["name"],
                          "lmin": b["lmin"], "lmax": b["lmax"],
                          "wmin": b["wmin"], "wmax": b["wmax"],
                          "interpolated": True})
            continue
        # Xyce cannot expand the HSpice-style corner deltas inside a quoted
        # expression, so bake every identifier that carries the device name
        # (d*_<device> deltas and the vendor's <device>_*_retgt hooks) to 0,
        # exactly as the tt corner defines them.
        devid = re.compile(r"\b[A-Za-z0-9_]*" + re.escape(device)
                           + r"[A-Za-z0-9_]*\b")
        body = [devid.sub("0", ln) for ln in chosen["lines"]]
        # the vendor file has a `+ + 2.6E-9` typo in lvth0; HSpice and Xyce
        # both read it as unary plus, but collapse it so the deck is clean
        body = [re.sub(r"\+\s+\+", "+", ln) for ln in body]
        baked += sum(1 for a, c in zip(chosen["lines"], body) if a != c)
        body[0] = f".model {device} {chosen['kind']}"
        model_lines.append("\n".join(body))
        picks.append({"device": device, "bin": chosen["name"],
                      "lmin": b["lmin"], "lmax": b["lmax"],
                      "wmin": b["wmin"], "wmax": b["wmax"]})

    header = [
        f"* ICsprout 55 nm flattened deck for Xyce, .LIB {corner}",
        "* Vendor models are evaluation-licensed; do not redistribute.",
        ".param sigma_mis_a=0 sigma_mis_b=0 sigma_mis_c=0 sigma_mis_d=0",
        "* prelayout is an HSpice subckt argument in the vendor wrapper (0 =",
        "* pre-layout, contact-to-poly edge cap excluded); Xyce needs it global.",
        ".param prelayout=0",
    ]
    text = "\n".join(header + _lib_section(wrapper, corner)
                     + ["", *model_lines]) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(expand(out_path))),
                exist_ok=True)
    with open(expand(out_path), "w", encoding="utf-8") as fh:
        fh.write(text)
    return {"path": out_path, "corner": corner, "devices": picks,
            "baked_lines": baked, "resolved_cards": resolved,
            "bytes": os.path.getsize(expand(out_path))}


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corner-index", help="e.g. .../libs.tech/ngspice/corners/tt.spice")
    ap.add_argument("--wanted", nargs="+",
                    help="substrings of include basenames to keep")
    ap.add_argument("--out", required=True)
    ap.add_argument("--temperature", type=float, default=27.0)
    ap.add_argument("--ics55-xyce", action="store_true",
                    help="build a flat Xyce deck from the ICsprout 55 nm "
                         "model_wrapper1_hsp.lib + mos/*.mdl instead")
    ap.add_argument("--pdk-root",
                    default="/grid/icsprout55-pdk",
                    help="ICsprout 55 PDK root (with --ics55-xyce)")
    ap.add_argument("--devices", nargs="+",
                    default=["nm1p2_svt_lp", "pm1p2_svt_lp"],
                    help="device model names to extract (with --ics55-xyce)")
    ap.add_argument("--corner", default="tt",
                    help=".LIB section in the wrapper (with --ics55-xyce)")
    ap.add_argument("--w-um", type=float, default=1.0)
    ap.add_argument("--l-um", type=float, default=0.06)
    args = ap.parse_args(argv)

    if args.ics55_xyce:
        info = build_ics55_xyce(args.pdk_root, args.out, args.devices,
                                args.corner, args.w_um, args.l_um)
        print(f"{info['path']}: {info['bytes']} bytes, "
              f"corner {info['corner']}, {info['baked_lines']} baked delta "
              f"lines, {info['resolved_cards']} interpolated card(s) resolved")
        for d in info["devices"]:
            print(f"  {d['device']}: bin {d['bin']} "
                  f"(L {d['lmin']*1e9:.1f}..{d['lmax']*1e9:.1f} nm, "
                  f"W {d['wmin']*1e6:.2f}..{d['wmax']*1e6:.2f} um) "
                  f"-> .model {d['device']}"
                  + ("  [geometry-locked]" if d.get("interpolated") else ""))
        return 0

    if not args.corner_index or not args.wanted:
        ap.error("--corner-index and --wanted are required unless --ics55-xyce")
    info = build(args.corner_index, args.wanted, args.out, args.temperature)
    print(f"{info['path']}: {info['bytes']} bytes, "
          f"{info['includes_flattened']} lines, "
          f"{len(info['declared_zero'])} undefined params set to 0")
    for name in info["declared_zero"]:
        print(f"  zeroed {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

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


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corner-index", required=True,
                    help="e.g. .../libs.tech/ngspice/corners/tt.spice")
    ap.add_argument("--wanted", nargs="+", required=True,
                    help="substrings of include basenames to keep")
    ap.add_argument("--out", required=True)
    ap.add_argument("--temperature", type=float, default=27.0)
    args = ap.parse_args(argv)

    info = build(args.corner_index, args.wanted, args.out, args.temperature)
    print(f"{info['path']}: {info['bytes']} bytes, "
          f"{info['includes_flattened']} lines, "
          f"{len(info['declared_zero'])} undefined params set to 0")
    for name in info["declared_zero"]:
        print(f"  zeroed {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Pick a KLayout binary that is new enough for the PDK rule decks.

The PDK decks are not version tolerant.  The installed /usr/bin/klayout here
is 0.30.0, and on it:

  * gf180mcu DRC dies in the deck framework
    ("undefined method 'evaluate_nets'", or "Heap lost" in deep mode);
  * ihp-sg13g2 LVS dies with "undefined local variable or method
    'flag_missing_ports'".

Both run on 0.30.12.  A deck crash looks like a layout problem if nobody
checks the version, so every caller goes through this helper and gets a clear
error instead of a Ruby traceback.

Resolution order:
    1. explicit argument
    2. $KLAYOUT
    3. "klayout" on PATH
    4. any klayout under /nix/store (newest first)

Minimum: 0.30.9 (the gf180 README's floor).
"""

from __future__ import annotations

import glob
import os
import re
import subprocess

MIN = (0, 30, 9)


def version(binary: str) -> tuple[int, int, int] | None:
    try:
        proc = subprocess.run([binary, "-v"], capture_output=True, text=True,
                              timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", proc.stdout + proc.stderr)
    return tuple(int(x) for x in m.groups()) if m else None


def find(explicit: str | None = None) -> str:
    cands: list[str] = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("KLAYOUT"):
        cands.append(os.environ["KLAYOUT"])
    cands.append("klayout")
    cands.extend(sorted(glob.glob("/nix/store/*-klayout-*/bin/klayout"), reverse=True))

    seen = set()
    tried = []
    for cand in cands:
        if cand in seen:
            continue
        seen.add(cand)
        v = version(cand)
        if v is None:
            continue
        tried.append(f"{cand}={'.'.join(map(str, v))}")
        if v >= MIN:
            return cand
    raise RuntimeError(
        "no KLayout >= %d.%d.%d found (tried: %s). Set KLAYOUT=/path/to/klayout."
        % (*MIN, ", ".join(tried) or "nothing")
    )


if __name__ == "__main__":
    print(find())

# Pinned toolchain

The digital back end must produce the same numbers on a re-run. That means the
EDA binaries have to be identified by more than a name: `librelane` on `PATH`
used to resolve to whatever `nix profile` last installed, and `nix-collect-garbage`
could remove the closure under it. This file records the pin and how to rebuild
it.

## What is pinned

LibreLane is pinned from the local checkout `/opt/librelane`, whose own
`flake.lock` fixes the rest of the stack:

| input | pin |
| --- | --- |
| librelane | 3.0.17 (`/opt/librelane`, git tag `3.0.17`, commit `64ef80802d3d`) |
| nix-eda | 6.11.0 (rev `8f990fb77529`) |
| nixpkgs | rev `b3aad468604d` |

Tool versions that the pin pulls in, as resolved by the LibreLane wrapper:

| tool | version |
| --- | --- |
| klayout | 0.30.7 |
| openroad | 2026-02-17 |
| yosys | 0.62 (with sby/eqy/slang/ghdl plugins) |
| magic | 8.3.623 |
| netgen | 1.5.316 |
| verilator | 5.044 |
| iverilog | s20250103-60-gdb82380ce |
| opensta | from the nix-eda pin |
| python | 3.13.9 |
| ciel | 2.4.0 |

`backend/toolchain.sh --check` prints this table from the installed wrapper, so
the table above is a record, not the source of truth.

## How it is installed

```sh
nix build 'path:/opt/librelane#librelane' \
  --out-link backend/.toolchain/librelane-3.0.17
```

The out-link is a registered Nix GC root (`nix-store -q --roots
backend/.toolchain/librelane-3.0.17`), so `nix-collect-garbage -d` cannot delete
the closure, and no flake resolution or network access happens at run time.
`backend/.toolchain/` is machine-local and git-ignored.

`backend/run.sh` sources `backend/toolchain.sh` and executes
`$LIBRELANE_BIN` (`backend/.toolchain/librelane-3.0.17/bin/librelane`). The
wrapper prepends its own `openroad`, `yosys`, `klayout`, `netgen`, `magic` and
`verilator` to `PATH`, so pinning the wrapper pins the flow. Override with
`LIBRELANE_BIN=/path/to/librelane` if a comparison build is needed.

## Manual sign-off runs

Anything run by hand against a hardened design (DRC, LVS, GDS inspection) must
use the same KLayout as the flow. `toolchain.sh` exports `KLAYOUT_BIN` for this:

```sh
. backend/toolchain.sh
"$KLAYOUT_BIN" -b -zz -r <deck> -rd input=<gds>
```

The system `klayout` in `/usr/bin` is a different build (0.30.0) and must not be
used for sign-off evidence.

## Tools not covered by the pin

These resolve from the system and are *not* part of the Nix pin:

- `sta` — `/usr/bin/sta`, OpenSTA 2.0.17 (Debian package).
- `iverilog` — `/usr/bin/iverilog` (Debian package).

They are only used for RTL simulation/lint (`make -C rtl sim lint`), not for the
hardening metrics. If a hardening step ever picks them up, pin them too.

## Configuration migration for 3.0.17

`backend/gen_configs.py` emits the configs; one key changed between 3.0.14 and
3.0.17:

| 3.0.14 | 3.0.17 | note |
| --- | --- | --- |
| `KLAYOUT_DRC_OPTIONS` | `KLAYOUT_DRC_DEFINES` | `Dict[str, str]`, passed to the deck as `-rd key=value`; every value is stringified, so `no_density: true` became `no_density=True`.  The generated configs now spell `no_density: "true"` |

All 28 generated configs load clean under 3.0.17 (`Verilator.Lint` smoke run per
distinct key set, no deprecation warnings left).

## Previous environment

Before this pin the flow ran LibreLane 3.0.14 from nixpkgs `26.11pre1105041`
(klayout 0.30.12, openroad 26Q3, yosys 0.70, magic 8.3.685, netgen 1.5.325,
verilator 5.052). That closure is still installed in the `nix profile` and kept
as a GC root at `backend/.toolchain/librelane-3.0.14` for comparison, but run
results produced with it are not comparable with pinned runs: every run in
`backend/RESULTS.md` has to be repeated under the pin before its numbers can be
quoted again.

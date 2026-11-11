# Digital back end — seven ADC cores x four PDKs

The seven non-SAR architectures have a synthesizable digital core each
(`rtl/<arch>/`).  This directory hardens every core on every PDK with
[LibreLane](https://librelane.readthedocs.io) and is the only place that knows
about PDKs.  Nothing here touches the analog macros: these are the digital
halves, harden-only, no pads.

```
gen_configs.py     ARCHS x PDKS -> configs/<pdk>/<arch>.yaml   (28 files)
pdkroot.sh         compose pdkroot/<pdk>/ = PDK tree + local overlay
pdk_overlay/       the only files we replace or add
run.sh             one job: run.sh <pdk> <arch> [run-tag]
sweep.sh           run a queue of jobs in one lane: sweep.sh jobs.txt [logdir]
toolchain.sh       pinned LibreLane/tool versions (`make env`), see TOOLCHAIN.md
```

```sh
make -C backend env          # print the pinned toolchain in use
make -C backend configs      # regenerate the YAMLs
make -C backend pdkroot      # compose the PDK roots
make -C backend check        # YAMLs current, roots present
make -C backend ihp-sg13g2-ramp
make -C backend run PDK=sky130A ARCH=flash TAG=try2

# a whole matrix in two lanes (each job already fans out over every core)
printf '%s\n' "sky130A flash" "sky130A pipeline" > /tmp/laneB.txt
pjf=backend/sweep.sh; $pjf /tmp/laneB.txt /tmp/opencode/sweep &
```

`sweep.sh` writes one TSV row per finished job into `<logdir>/<jobs>-summary.tsv`
(die area, all DRC/LVS counters, worst setup/hold slack), so the matrix in
[`RESULTS.md`](RESULTS.md) is filled from machine output rather than from notes.
A third column turns the run directory into `runs/<arch>-<tag>`, which is what
you want when re-running a whole lane against a changed config.

Runs land in `configs/<pdk>/runs/<tag>/`, each with `final/metrics.json`, the
report directories of every step, and `final/{gds,def,spice,sdc,lib}`.

## Why a composed PDK root

LibreLane reads exactly two Tcl files per PDK:

```
{pdk_root}/{pdk}/libs.tech/librelane/config.tcl
{pdk_root}/{pdk}/libs.tech/librelane/{scl}/config.tcl
```

When the installed copy of one of those is wrong, or when a standard-cell
library ships outside the PDK tree, the temptation is to edit the PDK.  We do
not: `pdkroot.sh` builds `pdkroot/<pdk>/` as a directory of symlinks in which
every file resolves to the PDK except the ones `pdk_overlay/<pdk>/` provides.
`pdkroot/` is git-ignored and rebuildable, and `pdk_overlay/` is small enough to
review.

`--manual-pdk` is always required: these trees are not Ciel-enabled and, for
gf180mcu and ihp, their installed hashes differ from the ones LibreLane pins.

An overlay entry may also be named `<vendor file>.patch`.  `pdkroot.sh` then
copies the vendor file into `pdkroot/` and applies the unified diff to the copy,
so a one-line vendor defect is a reviewable diff rather than a vendored copy of
a multi-hundred-kilobyte file.  The only user today is the ics55 standard-cell
CDL (see [`ICS55_SIGNOFF.md`](ICS55_SIGNOFF.md)).

## PDKs

| dir / `--pdk` | SCL | supply | clock | notes |
|---|---|---|---|---|
| `ihp-sg13g2` | `sg13g2_stdcell` | `VDD`/`VSS` | 10 ns | 130 nm, 1.2 V. Vendor `librelane` config restored by the overlay; the Ciel cache shipped a rebuilt one with `PDN_RAIL_WIDTH 1.0` instead of 0.44, which made every Metal1 rail 0.56 µm too wide and produced ~660 Metal1 spacing violations plus shorts. |
| `sky130A` | `sky130_fd_sc_hd` | `VPWR`/`VGND` | 20 ns | 180 nm, 1.8 V. Vendor config is OpenLane 2 format (`libs.tech/openlane`); LibreLane migrates it. |
| `gf180mcuD` | `gf180mcu_as_sc_mcu7t3v3` | `VDD`/`VSS` | 20 ns | 180 nm, **3.3 V AS** 7-track cells on the **gf180mcuD** process. `gf180mcuA/B/C` are deprecated and cannot produce a valid signoff deck, so D is the only variant used here. The AS library ships outside the PDK tree (`/grid/gf180mcu/...`); the overlay links it into `libs.ref` so the D magic/KLayout/netgen tech applies. |
| `ics55` | `ics55_LLSC_H7CH` | `VDD`/`VSS` | 10 ns (`sdm` 20, `ti` 11) | 55 nm. PDK id is `ics55`; the vendor LVS/DRC plugin must be on `PYTHONPATH` (done by `run.sh`). Sign-off is KLayout-only and needs five local repairs (a real LEF/DEF layer map, `LAYERS_RC`/`VIAS_R`, `EXTRA_EXCLUDED_CELLS`, `DECAP_CELLS: []`, and an eight-circuit CDL patch); LVS is fatal (`ERROR_ON_LVS_ERROR: true`) and all seven cores close it. All of it, with the measurements behind it, is in [`ICS55_SIGNOFF.md`](ICS55_SIGNOFF.md). |

Power nets in the YAML must equal the SCL cells' PG pins, not the PDK's legacy
`VDD_PIN`/`GND_PIN` (`ihp` sets those to `VPWR`/`VGND` while the cells use
`VDD`/`VSS`).

## What "done" means here

A (PDK, arch) cell of the matrix is done when one run reports, from a GDS that
this run streamed out itself:

- `route__drc_errors == 0`, antenna 0;
- `magic__drc_error__count == 0` and `klayout__drc_error__count == 0`;
- `design__lvs_error__count == 0`;
- `design__setup__ws >= 0` and `design__hold__ws >= 0` on every signoff corner.

For `ics55` the second bullet is replaced by the library floor recorded in
[`ICS55_SIGNOFF.md`](ICS55_SIGNOFF.md): magic does not run at all, and the
KLayout count is at most that floor, which is entirely standard-cell and
abutted-pad geometry rather than routed geometry.

Reusing an older run's reports is not evidence.  The matrix and the numbers
actually achieved are in [`RESULTS.md`](RESULTS.md).

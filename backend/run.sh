#!/usr/bin/env bash
# Run one LibreLane hardening job: backend/run.sh <pdk> <arch> [run-tag]
#
# Every PDK is consumed through backend/pdkroot, a composed symlink root that
# exists so a broken vendor config can be overridden without editing the PDK
# tree (see pdkroot.sh).  --manual-pdk is required because these trees are not
# Ciel-enabled and/or their hashes differ from LibreLane's pinned hashes.
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <pdk> <arch> [run-tag]" >&2
  exit 2
fi

pdk=$1
arch=$2
tag=${3:-$arch}

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root="$here/pdkroot"
cfg="$here/configs/$pdk/$arch.yaml"

# Pinned toolchain (see TOOLCHAIN.md): LIBRELANE_BIN, KLAYOUT_BIN, PATH.
. "$here/toolchain.sh"

[ -f "$cfg" ] || { echo "no config $cfg (run gen_configs.py)" >&2; exit 1; }
[ -d "$root/$pdk" ] || { echo "no composed PDK root $root/$pdk (run pdkroot.sh)" >&2; exit 1; }

scl=""
case "$pdk" in
  ihp-sg13g2)         scl=sg13g2_stdcell ;;
  sky130A)            scl=sky130_fd_sc_hd ;;
  gf180mcuD)          scl=gf180mcu_as_sc_mcu7t3v3 ;;
  ics55)
    # The vendor ships a LibreLane plugin for the ics55 rule decks.
    scl=ics55_LLSC_H7CH
    export PYTHONPATH="/grid/icsprout55-pdk/libs.tech/librelane${PYTHONPATH:+:$PYTHONPATH}"
    ;;
  *) echo "unknown pdk $pdk" >&2; exit 2 ;;
esac

echo "librelane $pdk/$arch (scl=$scl, tag=$tag)"
exec "$LIBRELANE_BIN" \
  --manual-pdk \
  --pdk-root "$root" \
  --pdk "$pdk" \
  --scl "$scl" \
  --run-tag "$tag" \
  "$cfg"

#!/usr/bin/env bash
# run_contention.sh -- runner for the two contention benches.
#
# The .sp templates carry {temp}/{corner}/{PDK}/{CELLS}/{supply} placeholders
# because the IHP deck paths moved when the environment migrated to ciel.
# This script substitutes them and runs ngspice with the PDK's own
# .spiceinit so the Verilog-A (PSP 103) OSDI models resolve.
#
#   src/run_contention.sh comp            # OAI+NAND shared-driver probe
#   src/run_contention.sh cmpcs2cmp       # input-port back-drive probe
#   CORNER=mos_ff TEMP=125 src/run_contention.sh comp
#
set -euo pipefail
cd "$(dirname "$0")/.."

WHICH="${1:?usage: run_contention.sh comp|cmpcs2cmp}"
case "$WHICH" in
  comp|cmpcs2cmp) ;;
  *) echo "unknown bench '$WHICH' (want comp or cmpcs2cmp)" >&2; exit 2 ;;
esac

PDK_ROOT="${PDK_ROOT:-$HOME/.ciel/ciel/ihp-sg13/versions/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008}"
PDK="${PDK:-ihp-sg13g2}"
PDKDIR="$PDK_ROOT/$PDK"
CORNER="${CORNER:-mos_tt}"
TEMP="${TEMP:-25}"
SUPPLY="${SUPPLY:-1.2}"
CELLS="$PWD/cells"
OUT="${OUT:-/tmp/opencode/contention_$WHICH}"

[ -f "$PDKDIR/libs.tech/ngspice/.spiceinit" ] || {
  echo "PDK tree not found at $PDKDIR -- set PDK_ROOT/PDK" >&2; exit 1; }

mkdir -p "$OUT"
# cornerMOSlv.lib section names are the full corner ids (mos_tt, mos_ff, ...).
# trtol=7 (the flow setting): trtol=1 collapses the timestep in the
# contention region -- the sim burns 120 s reaching only ~88 ns.
sed -e "s|{temp}|$TEMP|g" \
    -e "s|{corner}|$CORNER|g" \
    -e "s|{PDK}|$PDKDIR|g" \
    -e "s|{CELLS}|$CELLS|g" \
    -e "s|{supply}|$SUPPLY|g" \
    -e "s|trtol=1|trtol=7|" \
    "src/${WHICH}_contention_tb.sp" > "$OUT/${WHICH}_tb.sp"

echo "deck: $OUT/${WHICH}_tb.sp  corner=$CORNER temp=$TEMP"
PDK_ROOT="$PDK_ROOT" PDK="$PDK" \
SPICEINIT="$PDKDIR/libs.tech/ngspice/.spiceinit" \
  ngspice -b "$OUT/${WHICH}_tb.sp" 2>&1 | tee "$OUT/${WHICH}.log" \
  | grep -iE 'error|meas|t_regen|i_peak|v_min|v_max|dip|oshot|fail|halt' || true

echo "--- .meas results ---"
grep -E '^\s*[a-z_]+\s*=' "$OUT/${WHICH}.log" | head -20 || true

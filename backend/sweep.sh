#!/usr/bin/env bash
# Run a queue of hardening jobs sequentially in one lane.
#
#   backend/sweep.sh jobs.txt [logdir]
#
# jobs.txt is one "<pdk> <arch>" per line, '#' comments allowed.  Each job is a
# plain backend/run.sh invocation inside its own run tag, so lanes never touch
# the same directory.  Two lanes on an 8-core box is about the practical limit:
# every LibreLane job already fans out across all cores.
#
# One TSV line per finished job goes to <logdir>/summary.tsv, so several lanes
# can append to the same file.
set -uo pipefail

jobs=${1:?usage: sweep.sh <jobs-file> [logdir]}
logdir=${2:-/tmp/opencode/sweep}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$logdir"
tag=$(basename "$jobs" .txt)

while read -r pdk arch jobtag rest; do
  case "${pdk:-}" in ''|'#'*) continue ;; esac
  [ -n "${arch:-}" ] || continue
  # A tag column makes the run directory <arch>-<tag>, so one tag value can be
  # used across a whole lane without two architectures sharing runs/<tag>.
  tag_name=$arch
  [ -n "${jobtag:-}" ] && tag_name="$arch-$jobtag"
  log="$logdir/$pdk-$arch-$tag_name.log"
  echo "[$(date +%H:%M:%S)] start $pdk/$arch"
  "$here/run.sh" "$pdk" "$arch" "$tag_name" >"$log" 2>&1
  rc=$?
  m="$here/configs/$pdk/runs/$tag_name/final/metrics.json"
  if [ $rc -eq 0 ] && [ -f "$m" ]; then
    row=$(python3 - "$m" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
keys = ["route__drc_errors", "magic__drc_error__count",
        "magic__illegal_overlap__count", "klayout__drc_error__count",
        "design__lvs_error__count", "antenna__violating__nets",
        "timing__setup__wns", "timing__hold__wns",
        "design__instance__area", "design__instance__utilization"]
neg = [k for k in d if ("setup__wns" in k or "hold__wns" in k) and d[k] < 0]
print("\t".join(str(d.get(k, "MISSING")) for k in keys),
      "\t" + ("ALL_POS" if not neg else "NEG:" + ",".join(neg)))
EOF
)
    printf '%s\t%s\tOK\t%s\n' "$pdk" "$arch" "$row" >>"$logdir/$tag-summary.tsv"
    echo "[$(date +%H:%M:%S)] done  $pdk/$arch"
  else
    printf '%s\t%s\tFAIL(rc=%s)\tsee %s\n' "$pdk" "$arch" "$rc" "$log" \
      >>"$logdir/$tag-summary.tsv"
    echo "[$(date +%H:%M:%S)] FAIL  $pdk/$arch rc=$rc"
  fi
done <"$jobs"

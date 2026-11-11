#!/usr/bin/env bash
# Pinned EDA toolchain for the digital back end.  See TOOLCHAIN.md.
#
# Pinning the LibreLane wrapper pins the whole flow: the wrapper prepends its
# own openroad / yosys / klayout / netgen / magic / verilator to PATH.  The pin
# is a Nix GC root under backend/.toolchain/, so `nix-collect-garbage` cannot
# remove it and no flake resolution happens at run time.
#
#   source backend/toolchain.sh      # sets LIBRELANE_BIN, KLAYOUT_BIN, PATH
#   backend/toolchain.sh --check     # print the versions in use

_tc_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TOOLCHAIN_LINK=${TOOLCHAIN_LINK:-$_tc_here/.toolchain/librelane-3.0.17}

if [ -z "${LIBRELANE_BIN:-}" ]; then
  if [ -x "$TOOLCHAIN_LINK/bin/librelane" ]; then
    LIBRELANE_BIN=$TOOLCHAIN_LINK/bin/librelane
  else
    cat >&2 <<EOF
toolchain.sh: pinned LibreLane is missing: $TOOLCHAIN_LINK
  rebuild it with:
    nix build 'path:/opt/librelane#librelane' --out-link $TOOLCHAIN_LINK
EOF
    return 1 2>/dev/null || exit 1
  fi
fi
export LIBRELANE_BIN
case ":$PATH:" in
  *":$(dirname "$LIBRELANE_BIN"):"*) ;;
  *) PATH=$(dirname "$LIBRELANE_BIN"):$PATH ;;
esac
export PATH

# The wrapper hard-codes the store paths it puts on PATH; reuse them so manual
# sign-off runs (klayout -b -r ...) use the same binaries as the flow.
if [ -z "${KLAYOUT_BIN:-}" ]; then
  _tc_klayout=$(grep -oE "/nix/store/[a-z0-9]+-klayout-[^/']*/bin" "$LIBRELANE_BIN" 2>/dev/null | head -1 || true)
  [ -n "$_tc_klayout" ] && KLAYOUT_BIN=$_tc_klayout/klayout && export KLAYOUT_BIN
  _tc_klayout=
fi

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:---check}" in
    --check)
      echo "pin           : $TOOLCHAIN_LINK"
      echo "librelane     : $("$LIBRELANE_BIN" --version 2>/dev/null | head -1)"
      echo "klayout       : ${KLAYOUT_BIN:-not-found} ($("${KLAYOUT_BIN:-true}" -v 2>/dev/null | head -1))"
      echo "tools on the wrapper PATH:"
      grep -oE "/nix/store/[a-z0-9]+-[^/']*/bin" "$LIBRELANE_BIN" 2>/dev/null |
        sed 's|/nix/store/[a-z0-9]*-||; s|/bin$||' | sort -u | sed 's/^/  /'
      echo "ambient PATH tools (not pinned by toolchain.sh):"
      for t in sta iverilog; do
        printf '  %-10s %s\n' "$t" "$(command -v "$t" 2>/dev/null || echo MISSING)"
      done
      ;;
    *)
      echo "usage: $0 [--check]" >&2
      exit 2
      ;;
  esac
fi

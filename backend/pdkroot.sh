#!/usr/bin/env bash
# Compose a writable PDK root from a read-only PDK tree plus a local overlay.
#
# LibreLane wants {pdk_root}/{pdk}/libs.tech/librelane/config.tcl and
# {pdk_root}/{pdk}/libs.tech/librelane/{scl}/config.tcl.  When the vendor copy
# of one of those files is wrong we must not edit the PDK tree, so this script
# builds a directory of symlinks in which every file resolves to the PDK except
# the files that backend/pdk_overlay/{pdk}/ provides.
#
# An overlay entry may also be "<vendor file>.patch": the vendor file is then
# copied into pdkroot and the unified diff applied to the copy.  That keeps a
# one-line vendor defect reviewable instead of vendoring a whole multi-hundred
# kilobyte file.
#
# Usage: backend/pdkroot.sh [pdk ...]      (default: ihp-sg13g2)
# Output: backend/pdkroot/{pdk}/  (git-ignored, rebuildable)
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
overlay_root="$here/pdk_overlay"
dest_root="$here/pdkroot"
mkdir -p "$dest_root"

# Source trees, one per PDK, from the local Ciel cache or a vendor checkout.
#
# gf180mcu: the AS 3.3 V standard-cell library is built on the gf180mcuD
# process.  A/B/C are deprecated and cannot produce a valid signoff deck, so D
# is the only variant that is ever composed here.
declare -A SRC=(
  [ihp-sg13g2]="$HOME/.ciel/ihp-sg13g2"
  [sky130A]="$HOME/.ciel/ciel/sky130/versions/8afc8346a57fe1ab7934ba5a6056ea8b43078e71/sky130A"
  [gf180mcuD]="$HOME/.ciel/ciel/gf180mcu/versions/792ef67037d74c8c51811eab3ce23cf793491296/gf180mcuD"
  [ics55]="/grid/icsprout55-pdk"
)

compose() { # compose <src> <dest> <overlay>
  local src=$1 dest=$2 ovl=$3 entry name
  mkdir -p "$dest"
  for entry in "$src"/*; do
    name=$(basename "$entry")
    if [ -f "$entry" ] && [ -f "$ovl/$name.patch" ]; then
      # Patching mode: keep the vendor bytes and apply only the diff.
      cp "$entry" "$dest/$name"
      patch --quiet --no-backup-if-mismatch "$dest/$name" < "$ovl/$name.patch"
      echo "  patched $dest/$name"
    elif [ -d "$ovl/$name" ]; then
      if [ ! -d "$entry" ]; then
        echo "overlay has directory $name but $src/$name is not a directory" >&2
        exit 1
      fi
      compose "$entry" "$dest/$name" "$ovl/$name"
    elif [ -e "$ovl/$name" ]; then
      ln -sfn "$(readlink -f "$ovl/$name")" "$dest/$name"
    else
      ln -sfn "$entry" "$dest/$name"
    fi
  done
  # Overlay entries that do not exist upstream at all (new files, e.g. a
  # standard-cell library that ships outside the PDK tree).  Link them rather
  # than copy so the overlay stays a pointer.
  if [ -d "$ovl" ]; then
    for entry in "$ovl"/*; do
      [ -e "$entry" ] || continue
      name=$(basename "$entry")
      case "$name" in *.patch) continue ;; esac
      [ -e "$src/$name" ] && continue
      ln -sfn "$(readlink -f "$entry")" "$dest/$name"
    done
  fi
}

pdks=("$@")
[ ${#pdks[@]} -eq 0 ] && pdks=(ihp-sg13g2)

for pdk in "${pdks[@]}"; do
  src=${SRC[$pdk]:-}
  if [ -z "$src" ] || [ ! -d "$src" ]; then
    echo "no source tree for PDK '$pdk' (set SRC[$pdk] in this script)" >&2
    exit 1
  fi
  ovl="$overlay_root/$pdk"
  # A PDK may need no overlay at all; then the composed root is a pure mirror.
  [ -d "$ovl" ] || ovl=""
  dest="$dest_root/$pdk"
  rm -rf "$dest"
  compose "$(readlink -f "$src")" "$dest" "$ovl"
  echo "composed $dest"
  # Show which files the overlay actually replaced.
  if [ -n "$ovl" ]; then
    (cd "$ovl" && find . -type f) | while read -r f; do
      case "$f" in
        *.patch)
          printf '  overlaid %s -> %s\n' "${f%.patch} (patched)" "$(readlink -f "$dest/${f%.patch}")" ;;
        *)
          printf '  overlaid %s -> %s\n' "$f" "$(readlink -f "$dest/$f")" ;;
      esac
    done
  fi
done

# Hierarchical post-layout extraction of the assembled SAR ADC.
#
# Bug10: the old flow extracted the *flattened* chip.  KLayout's 2.5D engine
# never finished (16 h+) and Magic's flat extractor over-reported supply
# capacitance by ~100x because it walked every standard-cell rail as one giant
# node.  Neither is used here.
#
# This script extracts cell-by-cell ("extract do local") and lets ext2spice
# emit one .subckt per cell with "hierarchy on".  Every distinct cell is
# extracted exactly once, so the 17 k-instance digital sea costs the same as a
# few hundred gates; the analog CDAC/switch/comparator hierarchy keeps its own
# device-level parasitics; only the top-level interconnect is new work.
#
# Environment (set by signoff/Makefile):
#   TOP  top cell name
#   GDS  input stream file
#   OUT  output SPICE path
if {![info exists env(TOP)] || ![info exists env(GDS)] || ![info exists env(OUT)]} {
    puts stderr "pex.tcl: TOP, GDS and OUT must be set in the environment"
    exit 1
}

set top $env(TOP)
set gds $env(GDS)
set out $env(OUT)

puts "\[pex\] reading $gds"
gds read $gds
load $top
select top cell

# Extract every cell in the hierarchy separately; do not flatten children into
# the parent (that is exactly the flat-chip failure this replaces).
extract do local on
extract all

# Keep everything: cthresh/rthresh 0 means no parasitic is dropped as small.
ext2spice hierarchy on
ext2spice cthresh 0
ext2spice rthresh 0
ext2spice format ngspice
ext2spice subcircuit top on

puts "\[pex\] writing $out"
ext2spice -o $out

puts "\[pex\] done"
quit

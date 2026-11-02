"""Copy one cell (with its hierarchy) out of a multi-cell GDS library.

    SRC=lib.gds CELL=my_cell OUT=one.gds klayout -b -r gen/analog/extract_cell.py

PDK libraries ship hundreds of cells in one GDS.  DRC/LVS/PEX on the whole
library is slow and noisy, so the flow probe pulls a single known-good cell
out first.  Environment variables are used instead of -rd because KLayout's
Python bindings do not expose -rd values reliably.

Fails loudly if the cell is not present.
"""

import os

import pya

src = os.environ.get("SRC", "")
cell_name = os.environ.get("CELL", "")
out = os.environ.get("OUT", "")

if not (src and cell_name and out):
    raise SystemExit("needs SRC, CELL and OUT in the environment")

ly = pya.Layout()
ly.read(src)
cell = ly.cell(cell_name)
if cell is None:
    raise SystemExit(f"cell {cell_name!r} not found in {src}")

dst = pya.Layout()
dst.dbu = ly.dbu
new_top = dst.create_cell(cell_name)
new_top.copy_tree(cell)
dst.write(out)

top = dst.top_cell()
print(f"wrote {out}  cell={top.name}  bbox={top.bbox().to_s()}  "
      f"cells={len(list(dst.each_cell()))}")

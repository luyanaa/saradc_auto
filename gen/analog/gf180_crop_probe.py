import pya
ly=pya.Layout(); ly.read("inv_1.gds")
src=ly.top_cell()
box=pya.Box(-50,200,1750,1700)          # NMOS half, below the nwell
dst=pya.Layout(); dst.dbu=ly.dbu
top=dst.create_cell("gf_nmos")
for li in ly.layer_indexes():
    reg=pya.Region(src.begin_shapes_rec(li)) & pya.Region(box)
    if not reg.is_empty():
        top.shapes(dst.layer(ly.get_info(li))).insert(reg)
dst.write("gf_nmos.gds")
print("wrote gf_nmos.gds bbox", top.bbox().to_s())

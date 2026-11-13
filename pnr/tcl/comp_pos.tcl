# Orientation a cell placed at height $y has to carry so that it is not mirrored
# against the rest of its row.
#
# Rows are built from $corey upwards in $row steps and alternate.  The DEF ROW
# declarations in this design carry the *inverse* of that alternation, though:
# ROW_23_ANALOG_2 (y=159.12) declares MX while 62 of the 67 logic cells the
# placer put in that band are R0, and every row is inverted in the same way
# (checked by grouping the pre-route DEF components by their y and taking the
# majority orientation per row).  pos_stdcell_box has always followed the
# placer's convention (R0 on the first row it fills, then alternating) and its
# cells are the ones with no DRC cluster, so mirror the placer here: counted
# from $corey, odd rows are R0 and even rows are MX.
#
# A cell mirrored against its neighbours butts its NWell / pSD strip against
# their N+ diffusion, which is the NW.*/pSD.*/NBL.*/Cnt.* block of the DRC
# report and, in KLayout deep LVS, promotes the flipped master's devices to the
# top level.  LOCKED instances are never re-oriented by the legalizer, so this
# has to be right at placement time.
proc row_orient {y} {
  global row
  global corey
  if {[expr {round(($y-$corey)/$row)} % 2] == 0} {
    return "MX"
  } else {
    return "R0"
  }
}

proc filter_by_regex {pattern lst} {
    set result {}
    foreach item $lst {
        if {[regexp $pattern [$item getName]]} {
            lappend result $item
        }
    }
    return $result
}

proc pos_stdcell_comp {x y path} {
  global track
  global row
  global metal1_py
  global saradc_fill_nopower
  global ::block
  
  set vp_cmp_objs [filter_by_regex [list ${path}.vp_cmp\..*] [$::block getInsts]]
  set vn_cmp_objs [filter_by_regex [list ${path}.vn_cmp\..*] [$::block getInsts]]
  set vp_n2p_objs [filter_by_regex [list ${path}.n2p\..*] [$::block getInsts]]
  set vn_p2n_objs [filter_by_regex [list ${path}.p2n\.*] [$::block getInsts]]
  set vp_buf_objs [filter_by_regex [list ${path}.buf_p.*] [$::block getInsts]]
  set vn_buf_objs [filter_by_regex [list ${path}.buf_n.*] [$::block getInsts]]
  
  set vp_cmp_objs [list {*}$vp_cmp_objs {*}$vp_n2p_objs {*}$vp_buf_objs]
  set vn_cmp_objs [list {*}$vn_cmp_objs {*}$vn_p2n_objs {*}$vn_buf_objs]
  
  set n [llength $vp_cmp_objs]
  set tapx $x
  set upx $x
  set dwx $x
  set upy [expr $y+$row]
  set dwy [expr $y]
  for {set i 0} {$i < $n} {incr i} {
    set up_obj [lindex $vp_cmp_objs $i]
    set dw_obj [lindex $vn_cmp_objs $i]
    set up_inst [$up_obj getName]
    set dw_inst [$dw_obj getName]
    set up_sizex [::ord::dbu_to_microns [[$up_obj getMaster] getWidth]]
    set dw_sizex [::ord::dbu_to_microns [[$dw_obj getMaster] getWidth]]

    # The n-side pair sits in the row at $dwy and the p-side pair one row up;
    # those two rows have opposite orientations, so the pair stays mirror
    # symmetric about the shared row edge.  Each cell carries the orientation
    # the placer uses for its own row (see row_orient).
    if {[[$::block findInst $up_inst] isPlaced] == 0} {
      place_inst -name $up_inst -location "$upx $upy" -orientation [row_orient $upy] -status LOCKED
    }
    if {[[$::block findInst $dw_inst] isPlaced] == 0} {
      place_inst -name $dw_inst -location "$dwx $dwy" -orientation [row_orient $dwy] -status LOCKED
    }
    set upx [expr $upx + $up_sizex]
    set dwx [expr $dwx + $dw_sizex]
    if {[expr $upx+$up_sizex-$tapx] > 40 } {
      place_inst -name tapcell_up_inst_$i -cell sg13g2f_TAPCELL -location "$upx $upy" -orientation [row_orient $upy] -status LOCKED
      place_inst -name tapcell_dw_inst_$i -cell sg13g2f_TAPCELL -location "$dwx $dwy" -orientation [row_orient $dwy] -status LOCKED
      set upx [expr $upx + [::ord::dbu_to_microns [[[$::block findInst tapcell_up_inst_$i] getBBox] getDX]]]
      set dwx [expr $dwx + [::ord::dbu_to_microns [[[$::block findInst tapcell_dw_inst_$i] getBBox] getDX]]]
      set tapx $upx
    }
  }
  place_inst -name tapcell_up_inst_$n -cell sg13g2f_TAPCELL -location "$upx $upy" -orientation [row_orient $upy] -status LOCKED
  place_inst -name tapcell_dw_inst_$n -cell sg13g2f_TAPCELL -location "$dwx $dwy" -orientation [row_orient $dwy] -status LOCKED
  
  set compx [expr $upx - $x]
  set compy [expr 2*$row]
  return "$compx $compy"
}

proc pos_stdcell_box {x y width path} {
  global row
  # A very simple and dummy stdcell placement
  set stdcell_objs [filter_by_regex [list ${path}\..*] [$::block getInsts]]
  set curx 0.0
  set cury 0.0
  set currot "R0"
  foreach obj $stdcell_objs {
    set inst [$obj getName]
    set sizex [::ord::dbu_to_microns [[$obj getMaster] getWidth]]
    set nextx [expr $curx+$sizex]
    if {$nextx > $width} {
      if {$currot == "R0"} {
        set currot "MX"
      } else {
        set currot "R0"
      }
      set curx 0
      set cury [expr $cury+$row]
      set nextx [expr $sizex]
    }
    if {[[$::block findInst $inst] isPlaced] == 0} {
      place_inst -name $inst -location "[expr $x+$curx] [expr $y+$cury]" -orientation $currot -status PLACED
    }
    set curx $nextx
  }
}

proc route_vouts_comp {ya1 ya2 path} {
  global track
  global row
  global metal1
  global metal2
  global metal2_w
  global metal3
  global ::block

  setAddStripeMode -orthogonal_only false
  setAddStripeMode -stacked_via_top_layer $metal3 -stacked_via_bottom_layer $metal1

  set vp_cmp_objs [filter_by_regex [list ${path}.vp_cmp\..*] [$::block getInsts]]
  set vn_cmp_objs [filter_by_regex [list ${path}.vn_cmp\..*] [$::block getInsts]]

  set n [llength $vp_cmp_objs]

  for {set i 0} {$i < $n} {incr i} {
    set up_obj [lindex $vp_cmp_objs $i]
    set dw_obj [lindex $vn_cmp_objs $i]
    set up_a2_obj [$up_obj findITerm a2]
    set dw_a2_obj [$dw_obj findITerm a2]
    set up_net [[$up_a2_obj getNet] getName]
    set dw_net [[$dw_a2_obj getNet] getName]

    set up_ipin_shapes [$up_a2_obj getGeometries]
    lassign [lindex $up_ipin_shapes 0] up_ipin_layer_obj up_ipin_obj
    foreach up_ipin_layer_shape $up_ipin_shapes {
      lassign $up_ipin_layer_shape up_ipin_layer up_ipin_shape
      set dx [$up_ipin_shape dx]
      set dy [$up_ipin_shape dy]
      set dya [$up_ipin_obj dy]
      if {$dy > $dya && [$up_ipin_layer getName] == $metal1} {
        set up_ipin_layer_obj $up_ipin_layer
        set up_ipin_obj $up_ipin_shape
      }
    }
    set x1 [expr [::ord::dbu_to_microns [$up_ipin_obj xCenter]] - $metal2_w]
    set x2 [expr $x1 + 2*$metal2_w]
    set y1 [::ord::dbu_to_microns [$up_ipin_obj yMin]]
    set y2 $ya2
    set area [list $x1 $y1 $x2 $y2]
    addStripe -nets $up_net -layer $metal2 -direction vertical \
      -width [expr $x2 - $x1] -spacing 0.0 -set_to_set_distance 1.0 \
      -start_from left -start_offset 0 -area $area

    set dw_ipin_shapes [$dw_a2_obj getGeometries]
    lassign [lindex $dw_ipin_shapes 0] dw_ipin_layer_obj dw_ipin_obj
    foreach dw_ipin_layer_shape $dw_ipin_shapes {
      lassign $dw_ipin_layer_shape dw_ipin_layer dw_ipin_shape
      set dx [$dw_ipin_shape dx]
      set dy [$dw_ipin_shape dy]
      set dya [$dw_ipin_obj dy]
      if {$dy > $dya && [$dw_ipin_layer getName] == $metal1} {
        set dw_ipin_layer_obj $dw_ipin_layer
        set dw_ipin_obj $dw_ipin_shape
      }
    }
    set x1 [expr [::ord::dbu_to_microns [$dw_ipin_obj xCenter]] - $metal2_w]
    set x2 [expr $x1 + 2*$metal2_w]
    set y1 $ya1
    set y2 [::ord::dbu_to_microns [$dw_ipin_obj yMax]]
    set area [list $x1 $y1 $x2 $y2]
    addStripe -nets $dw_net -layer $metal2 -direction vertical \
      -width [expr $x2 - $x1] -spacing 0.0 -set_to_set_distance 1.0 \
      -start_from left -start_offset 0 -area $area
  }
  setAddStripeMode -reset
}


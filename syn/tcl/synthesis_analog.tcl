##############################################################################
## Preset global variables and attributes
##############################################################################
set TOP $env(TOP)
set SYN_DIR $env(SYN_DIR)
set SYN_SRC $env(SYN_SRC)
set LOGS $env(SYN_DIR)/logs
set REPORTS $env(SYN_DIR)/reports
set OUTPUTS $env(SYN_DIR)/outputs
set ROOT_DIR $env(ROOT_DIR)

set SYN_EFF high
set MAP_EFF high
set INC_EFF high

if {![file exists $OUTPUTS]} {
  puts "Creating directory $OUTPUTS"
  file mkdir $OUTPUTS
}
if {![file exists $REPORTS]} {
  puts "Creating directory $REPORTS"
  file mkdir $REPORTS
}

yosys -import

###############################################################
## Library setup
###############################################################
source $env(ROOT_DIR)/cells/settings.tcl

# Prepend to all strings the -liberty
set LIBSLIB ""
foreach i $LIBS {
    append LIBSLIB "-liberty $i "
}
puts "LIBSLIB = $LIBSLIB"

# Get only the DFF library, which is the standard cell library
# TODO: Not intelligent. We assume is the first one
set LIBFF [lindex $LIBS 0]
puts "LIBFF = $LIBFF"
#dfflibmap -liberty $LIBFF
#dfflibmap -liberty /opt/OpenLane/share/pdk/sky130A/libs.ref/sky130_fd_sc_hs/lib/sky130_fd_sc_hs__tt_025C_1v80.lib
#exit

################################################################
## Verilog read
################################################################
# CAP_KIND=MOM flips SARADC's default to the metal fringe cap.  yosys
# cannot take a string chparam, so the switch is a preprocessor define.
if {[info exists ::env(CAP_KIND)] && $::env(CAP_KIND) eq "MOM"} {
  puts "CAP_KIND=MOM: SARADC_CAP_MOM selected"
  verilog_defines -DSARADC_CAP_MOM
}
# SW_KIND=BOOT flips the CDAC pass devices from the inverter TG to the
# bootstrapped switch leaf.
if {[info exists ::env(SW_KIND)] && $::env(SW_KIND) eq "BOOT"} {
  puts "SW_KIND=BOOT: SARADC_SW_BOOT selected"
  puts "WARNING: SW_KIND=BOOT has a SPICE model and RTL abstraction but no"
  puts "WARNING: physical leaf (GDS/LEF) yet -- the netlist will not place."
  verilog_defines -DSARADC_SW_BOOT
}
# CMP_KIND=STRONGARM swaps the OAI211 latch for the dynamic comparator.
if {[info exists ::env(CMP_KIND)] && $::env(CMP_KIND) eq "STRONGARM"} {
  puts "CMP_KIND=STRONGARM: SARADC_CMP_STRONGARM selected"
  puts "WARNING: SARADC_CMP_STRONGARM has a SPICE model and RTL abstraction"
  puts "WARNING: but no physical leaf (GDS/LEF) yet."
  verilog_defines -DSARADC_CMP_STRONGARM
}
# SEQ_KIND=ASYNC swaps the clocked sequencer for the self-timed one.  This is
# an RTL-level change, so it elaborates and places -- but the handshake's
# delays are behavioural, so timing is not sign-off quality.
if {[info exists ::env(SEQ_KIND)] && $::env(SEQ_KIND) eq "ASYNC"} {
  puts "SEQ_KIND=ASYNC: sar_logic_async selected"
  puts "WARNING: async handshake uses behavioural delays; not sign-off timed."
  verilog_defines -DSARADC_SEQ_ASYNC
}

foreach src $SYN_SRC {
  read_verilog $src
}

read_verilog $env(ROOT_DIR)/cells/sg13g2f_blackboxes.v

hierarchy -check -top $::env(TOP)

####################################################################
## Load Design
####################################################################
procs
opt
fsm
opt
memory
opt
flatten
synth -run coarse 
#techmap -map $env(TECH_DIR)/verilog/yosys_techmap.v
techmap
#dffunmap -ce-only
opt

hierarchy -check

####################################################################
## Constraints Setup
####################################################################
# read_sdc $SYN_DIR/tcl/${TOP}.sdc.tcl

####################################################################################################
## Synthesizing to gates
####################################################################################################
dfflibmap -liberty $LIBFF
dfflegalize
set cmd "abc $LIBSLIB"
eval $cmd

splitnets

###################################################
## TIES
###################################################
#hilomap -hicell it18_to01_0 HI -locell it18_to01_0 LO # TODO: This command fails
hilomap

######################################################################################################
## Outputs (verilog, SDC, config, etc.)
######################################################################################################
clean -purge
#write_sdc ${OUTPUTS}/${TOP}.sdc # TODO: This command fails
write_verilog -renameprefix ins -noexpr -noattr -nohex ${OUTPUTS}/${TOP}_net.v


ROOT_DIR=$(abspath ..)
include $(ROOT_DIR)/settings.mk
SYN_DIR=$(abspath .)

export SYN_DIR
export SYN_SRC
export TOP
export DIGTOP
export SYN_DIG_SRC
export DIG_SYNC_SRC
export DIG_ASYNC_SRC
export ROOT_DIR
export TECH
export CAP_KIND
export SW_KIND
export CMP_KIND
export SEQ_KIND

SYN_ANA_NET=$(SYN_DIR)/outputs/$(TOP)_net.v
SYN_NET=$(SYN_DIR)/outputs/$(DIGTOP)_net.v
DIG_SYNC_NET=$(SYN_DIR)/outputs/sar_logic_wreset_net.v
DIG_ASYNC_NET=$(SYN_DIR)/outputs/sar_logic_async_net.v
export SYN_ANA_NET

all: $(SYN_ANA_NET) $(DIG_SYNC_NET) $(DIG_ASYNC_NET)

$(SYN_ANA_NET): $(SYN_DIR)/tcl/synthesis_analog.tcl $(SYN_SRC) $(ROOT_DIR)/cells/sar_logic_wreset_blackbox.v $(ROOT_DIR)/cells/sar_logic_async_blackbox.v
	yosys -h tcl $(SYN_DIR)/tcl/synthesis_analog.tcl | tee -a yosys_analog.log

# One netlist per sequencer.  DIGTOP/SYN_DIG_SRC are overridden per recipe so
# the same synthesis.tcl emits each block; P&R links both.
$(DIG_SYNC_NET): $(SYN_DIR)/tcl/synthesis.tcl $(DIG_SYNC_SRC)
	DIGTOP=sar_logic_wreset SYN_DIG_SRC="$(DIG_SYNC_SRC)" yosys -h tcl $(SYN_DIR)/tcl/synthesis.tcl | tee -a yosys.log

$(DIG_ASYNC_NET): $(SYN_DIR)/tcl/synthesis.tcl $(DIG_ASYNC_SRC)
	DIGTOP=sar_logic_async SYN_DIG_SRC="$(DIG_ASYNC_SRC)" yosys -h tcl $(SYN_DIR)/tcl/synthesis.tcl | tee -a yosys.log

$(SYN_NET): $(SYN_DIR)/tcl/synthesis.tcl $(SYN_DIG_SRC)
	yosys -h tcl $(SYN_DIR)/tcl/synthesis.tcl | tee -a yosys.log

# Generating only the analog netlist (for subcircuits)
gen: $(SYN_ANA_NET) $(SYN_DIR)/tcl/netlist.tcl
	openroad -exit $(SYN_DIR)/tcl/netlist.tcl | tee -a openroad.log

manual: $(SYN_DIR)/tcl/synthesis.tcl $(SYN_SRC)
	yosys -h tcl

clean:
	rm -rfv *.log* *.cmd* *_intermediate.v *_net.v *.sdf fv reports outputs *.tstamp

.PHONY: clean


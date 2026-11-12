ROOT_DIR=$(abspath ../)
include $(ROOT_DIR)/settings.mk
PNR_DIR=$(abspath .)
SYN_DIR=$(abspath ../syn)
RTL_DIR=$(abspath ../rtl)

export SYN_DIR
export PNR_DIR
export SYN_SRC
export RTL_DIR
export TOP
export ROOT_DIR
export X
export Y
export TECH
export PDK_ROOT

SYN_NET=$(SYN_DIR)/outputs/$(TOP)_net.v
export SYN_NET
# Both sequencer netlists: SARADC instantiates `digital` and `digital_async`
# and muxes them, so the linker needs both module definitions.
SYN_DIG_SYNC_NET=$(SYN_DIR)/outputs/sar_logic_wreset_net.v
SYN_DIG_ASYNC_NET=$(SYN_DIR)/outputs/sar_logic_async_net.v
export SYN_DIG_SYNC_NET
export SYN_DIG_ASYNC_NET

# OR_CMD exists so the same targets can be run under gdb (see
# /tmp/opencode/openroad_gdb.sh) without editing the flow: the pinned
# OpenROAD aborts in CTS without printing anything useful.
OR_CMD ?= openroad -exit

all: $(PNR_DIR)/outputs/$(TOP).def

$(PNR_DIR)/outputs/$(TOP).def: $(SYN_NET) $(SYN_DIG_SYNC_NET) $(SYN_DIG_ASYNC_NET) $(PNR_DIR)/tcl/pnr.tcl
	$(OR_CMD) $(PNR_DIR)/tcl/pnr.tcl | tee -a openroad.log

manual: $(SYN_NET) $(SYN_DIG_SYNC_NET) $(SYN_DIG_ASYNC_NET) $(PNR_DIR)/tcl/pnr.tcl
	openroad -gui $(PNR_DIR)/tcl/pnr.tcl

debug: $(SYN_NET) $(SYN_DIG_SYNC_NET) $(SYN_DIG_ASYNC_NET) $(PNR_DIR)/tcl/pnr.tcl
	gdb --args openroad -gui $(PNR_DIR)/tcl/pnr.tcl

clean:
	rm -vrf fv/ outputs reports *.size *.log DesignLib

.PHONY: all manual clean


# RTL family build/simulation.  Technology independent; no PDK path here.
ROOT := $(abspath ..)
IVERILOG ?= iverilog
VVP ?= vvp
VERILATOR ?= verilator

ARCHS := ramp flash pipeline dual_slope vco sdm ti
BUILD := $(CURDIR)/build

.PHONY: all lint sim $(addprefix sim-,$(ARCHS)) clean
all: lint sim

lint:
	@mkdir -p $(BUILD)
	$(IVERILOG) -g2005 -Wall -I common -tnull common/*.v analog_models/*.v
	@for a in $(ARCHS); do \
	  if [ -f $$a/filelist.f ]; then \
	    echo "lint $$a"; \
	    $(IVERILOG) -g2005 -Wall -I common -tnull -f $$a/filelist.f || exit 1; \
	  fi; \
	done

sim: $(addprefix sim-,$(ARCHS))

# Static pattern rule: a plain `sim-%:` pattern rule is not applied to phony
# targets, which is why this must be static.
$(addprefix sim-,$(ARCHS)): sim-%:
	@test -f $*/filelist.f || { echo "no $*/filelist.f"; exit 1; }
	@mkdir -p $(BUILD)
	$(IVERILOG) -g2005 -I common -o $(BUILD)/$*.vvp -s $*_tb -f $*/filelist.f
	$(VVP) $(BUILD)/$*.vvp

clean:
	rm -rf $(BUILD)

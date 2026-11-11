# Digital back end: seven ADC cores x four PDKs, executed by LibreLane.
#
#   make configs    regenerate backend/configs/<pdk>/<arch>.yaml
#   make pdkroot    compose backend/pdkroot (symlink root + local overlay)
#   make env        print the pinned LibreLane/tool versions in use
#   make run PDK=ihp-sg13g2 ARCH=ramp
#   make ihp-ramp   same, with the PDK/arch spelled out
#   make check      configs are current; PDK roots exist

HERE := $(CURDIR)
PDKS := ihp-sg13g2 sky130A gf180mcuD ics55
ARCHS := ramp flash pipeline dual_slope vco sdm ti
LIBRELANE ?= $(HERE)/.toolchain/librelane-3.0.17/bin/librelane

.PHONY: all configs pdkroot env check clean-runs $(addprefix run-,$(ARCHS)) \
        $(foreach p,$(PDKS),$(addprefix $(p)-,$(ARCHS)))

all: check

configs:
	python3 $(HERE)/gen_configs.py

pdkroot:
	$(HERE)/pdkroot.sh $(PDKS)

env:
	@$(HERE)/toolchain.sh --check

check:
	python3 $(HERE)/gen_configs.py --check
	@for p in $(PDKS); do \
	  test -e $(HERE)/pdkroot/$$p || { echo "missing composed PDK root: $$p (make pdkroot)"; exit 1; }; \
	done
	@echo "backend configs and PDK roots OK"

run:
	@test -n "$(PDK)" -a -n "$(ARCH)" || { echo "usage: make run PDK=<pdk> ARCH=<arch> [TAG=<tag>]"; exit 2; }
	$(HERE)/run.sh $(PDK) $(ARCH) $(or $(TAG),$(ARCH))

# One target per (pdk, arch).
define pdk_arch
$(1)-$(2):
	$(HERE)/run.sh $(1) $(2) $(2)
endef
$(foreach p,$(PDKS),$(foreach a,$(ARCHS),$(eval $(call pdk_arch,$(p),$(a)))))

clean-runs:
	rm -rf $(HERE)/configs/*/runs

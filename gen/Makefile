ROOT := $(abspath ..)
PY   ?= python3
SPEC ?= $(ROOT)/gen/spec/examples/sar8_sky130.json
PDK  ?= sky130A_hd
OUT  ?= $(ROOT)/gen/outputs
TRIALS ?= 20

.PHONY: all check matrix feas behavioral deck deck-ics55 measure char analog layout pdk-flow report clean

all: check matrix report

check:
	cd $(ROOT) && $(PY) gen/run.py check

matrix:
	cd $(ROOT) && $(PY) gen/run.py matrix --spec $(SPEC)

feas:
	mkdir -p $(OUT)
	cd $(ROOT) && $(PY) gen/run.py feasibility --spec $(SPEC) --pdk $(PDK) --json > $(OUT)/feas.json
	cd $(ROOT) && $(PY) gen/run.py feasibility --spec $(SPEC) --pdk $(PDK)

behavioral: feas
	cd $(ROOT) && $(PY) gen/run.py behavioral --spec $(SPEC) \
		--from-feasibility $(OUT)/feas.json --trials $(TRIALS)

SKY130_VERSION ?= 8afc8346a57fe1ab7934ba5a6056ea8b43078e71
SKY130_ROOT ?= $(HOME)/.ciel/ciel/sky130/versions/$(SKY130_VERSION)
SKY130_NGSPICE ?= $(HOME)/.ciel/ciel/sky130/versions/$(SKY130_VERSION)/sky130A/libs.tech/ngspice
DECK ?= $(OUT)/deck/sky130_tt.spice
MC ?= 0

deck:
	mkdir -p $(OUT)/deck
	cd $(ROOT) && $(PY) gen/analog/deck.py \
		--corner-index $(SKY130_NGSPICE)/corners/tt.spice \
		--wanted nfet_01v8__tt nfet_01v8__mismatch pfet_01v8__tt pfet_01v8__mismatch \
		--out $(DECK)

# ICsprout 55 nm deck for Xyce.  The svt/lvt cards are geometry-interpolated,
# so this is built for one (W, L); see gen/analog/PDK_FLOW.md.
ICS55_ROOT ?= /grid/icsprout55-pdk
ICS55_DEVICES ?= nm1p2_svt_lp pm1p2_svt_lp
ICS55_DECK ?= $(OUT)/deck/ics55_tt.spice
ICS55_W ?= 1
ICS55_L ?= 0.06

deck-ics55:
	mkdir -p $(OUT)/deck
	cd $(ROOT) && $(PY) gen/analog/deck.py --ics55-xyce \
		--pdk-root $(ICS55_ROOT) --devices $(ICS55_DEVICES) \
		--w-um $(ICS55_W) --l-um $(ICS55_L) --out $(ICS55_DECK)

measure: deck
	cd $(ROOT) && $(PY) gen/analog/measure.py --pdk $(PDK) --deck $(DECK) \
		--mc-runs $(MC) --json --out $(OUT)/measure.json

char: deck
	cd $(ROOT) && $(PY) gen/analog/char.py --pdk $(PDK) --deck $(DECK) \
		--json --out $(OUT)/char.json

analog:
	cd $(ROOT) && $(PY) gen/run.py analog --pdk $(PDK) --out $(OUT)/analog --smoke

# Shipped-cell DRC/LVS/PEX for the non-sky130 PDKs (sky130 is covered by
# layout).  Needs KLayout >= 0.30.9; klayout_bin.py finds one or fails clearly.
pdk-flow:
	mkdir -p $(OUT)/pdk_flow
	cd $(ROOT) && $(PY) gen/analog/pdk_flow.py --pdk all --out $(OUT)/pdk_flow

# Layout proof chain: KLayout DRC (feol+beol forced on), Magic extract,
# netgen LVS, then Magic PEX and an ngspice check that the extracted
# parasitics are additive against the schematic C(V).  Fails closed.
layout: deck
	mkdir -p $(OUT)/layout
	cd $(ROOT) && $(PY) gen/analog/layout_check.py --pdk $(PDK) \
		--pdk-root $(SKY130_ROOT) --deck $(DECK) --out $(OUT)/layout

report:
	mkdir -p $(OUT)
	cd $(ROOT) && $(PY) gen/run.py report --out $(OUT)/matrix.md

clean:
	rm -rf $(OUT)

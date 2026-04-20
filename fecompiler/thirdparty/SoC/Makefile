ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
SIM_BIN := $(ROOT)/build/soc_top
TEST ?= add
TEST_SRC := $(ROOT)/tests/programs/$(TEST).c
TEST_OUT := $(ROOT)/tests/out
IMAGE := $(TEST_OUT)/$(TEST).soc.bin
MAX_CYCLES ?= 2000000

.PHONY: sim test run run-prebuilt filelists clean

sim:
	$(ROOT)/scripts/build_soc_sim.sh $(SIM_BIN)

test:
	$(ROOT)/scripts/build_test.sh --src $(TEST_SRC) --name $(TEST) --out_dir $(TEST_OUT)

run: sim test
	$(ROOT)/scripts/run_soc_sim.sh --sim-bin $(SIM_BIN) --image $(IMAGE) --max-cycles $(MAX_CYCLES)

run-prebuilt: sim
	$(ROOT)/scripts/run_soc_sim.sh --sim-bin $(SIM_BIN) --image $(ROOT)/tests/out/min2.soc.bin --max-cycles $(MAX_CYCLES)

filelists:
	$(ROOT)/scripts/gen_filelists.sh

clean:
	rm -rf $(ROOT)/build

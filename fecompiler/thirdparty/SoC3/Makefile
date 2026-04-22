ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
SIM_BIN := $(ROOT)/build/soc_top
TEST ?= add
TEST_SRC := $(ROOT)/tests/programs/$(TEST).c
TEST_OUT := $(ROOT)/tests/out
TEST_LOG_DIR := $(TEST_OUT)/logs
IMAGE := $(TEST_OUT)/$(TEST).soc.bin
MAX_CYCLES ?= 2000000
TEST_SRCS := $(wildcard $(ROOT)/tests/programs/*.c)
TEST_NAMES := $(basename $(notdir $(TEST_SRCS)))

.PHONY: sim test test-all run run-prebuilt run-all run-all-prebuilt list-tests filelists clean

sim:
	$(ROOT)/scripts/build_soc_sim.sh $(SIM_BIN)

test:
	$(ROOT)/scripts/build_test.sh --src $(TEST_SRC) --name $(TEST) --out_dir $(TEST_OUT)

test-all:
	@set -e; \
	for t in $(TEST_NAMES); do \
		echo "[test-all] building $$t"; \
		$(ROOT)/scripts/build_test.sh --name "$$t" --out_dir "$(TEST_OUT)"; \
	done

run: sim test
	$(ROOT)/scripts/run_soc_sim.sh --sim-bin $(SIM_BIN) --image $(IMAGE) --max-cycles $(MAX_CYCLES)

run-prebuilt: sim
	$(ROOT)/scripts/run_soc_sim.sh --sim-bin $(SIM_BIN) --image $(ROOT)/tests/out/min2.soc.bin --max-cycles $(MAX_CYCLES)

run-all: sim test-all
	@set -e; \
	mkdir -p "$(TEST_LOG_DIR)"; \
	for img in "$(TEST_OUT)"/*.soc.bin; do \
		name="$$(basename "$$img" .soc.bin)"; \
		log="$(TEST_LOG_DIR)/$$name.log"; \
		echo "[run-all] $$name -> $$log"; \
		$(ROOT)/scripts/run_soc_sim.sh --sim-bin "$(SIM_BIN)" --image "$$img" --max-cycles "$(MAX_CYCLES)" > "$$log" 2>&1; \
	done

run-all-prebuilt: sim
	@set -e; \
	mkdir -p "$(TEST_LOG_DIR)"; \
	for img in "$(TEST_OUT)"/*.soc.bin; do \
		name="$$(basename "$$img" .soc.bin)"; \
		log="$(TEST_LOG_DIR)/$$name.log"; \
		echo "[run-all-prebuilt] $$name -> $$log"; \
		$(ROOT)/scripts/run_soc_sim.sh --sim-bin "$(SIM_BIN)" --image "$$img" --max-cycles "$(MAX_CYCLES)" > "$$log" 2>&1; \
	done

list-tests:
	@printf "%s\n" $(TEST_NAMES)

filelists:
	$(ROOT)/scripts/gen_filelists.sh

clean:
	rm -rf $(ROOT)/build

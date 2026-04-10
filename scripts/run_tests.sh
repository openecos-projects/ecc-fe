#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python3 -m unittest tests.test_workspace_flow -v
bazel test //:workspace_flow_test

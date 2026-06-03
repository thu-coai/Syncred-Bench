#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${SYNCRED_DATA_DIR:-/path/to/SynCred_600}"
OUTPUT_DIR="${SYNCRED_OUTPUT_DIR:-/path/to/results/mllm}"

python "${SCRIPT_DIR}/evaluate_mllm.py" \
  --data-dir "${DATA_DIR}" \
  --output-dir "${OUTPUT_DIR}" \
  "$@"

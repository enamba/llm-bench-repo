#!/usr/bin/env bash
# run_quality_12gb.sh - the full unattended 12 GB chain used for the published results:
#   1) context loss (recall) -> 2) recall at 8k for every model -> 3) quality prompts (llama-completion, see note)
#   -> 4) developer tasks -> 5) size limit (do 12B+ models even load?)
#
# Usage:  nohup ./run_quality_12gb.sh > chain.out 2>&1 &
# Resume after Termux/Android kills the run (the steps that finished are not repeated):
#   FROM=1   everything (default)
#   FROM=15  only the remaining 8B 16k cuts (4k, 2k), then steps 2-5
#   FROM=2   start at step 2 (recall at 8k for the ladder)
#   FROM=3   start at step 3 (quality prompts)
#   FROM=4   start at step 4 (dev tasks)
#   FROM=5   only step 5 (fit test for 12B / 14B / 20B)
#
# NOTE step 3: run_bench.sh quality uses llama-completion, which ignores --reasoning off at af6b5243d. Its answers are
# valid only for the non-thinking Qwen3-4B-Instruct-2507 models. For the thinking models (Qwen3-1.7B, Qwen3.5-4B,
# Qwen3-8B) the published quality scores come from ./run_quality_nothink.sh (llama-server, thinking really off).
# Qwen3-8B-Q5_K_M was removed from the ladder: Android killed Termux every time it loaded on the 12 GB phone.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
export PHONE="${PHONE:-fold8-12gb}" THREADS="${THREADS:-6}" CHARGER="${CHARGER:-yes}" LLAMA_ARG_REASONING=off
export OUTDIR="${OUTDIR:-$HERE/results/raw/$PHONE}"
mkdir -p "$OUTDIR"
L="$OUTDIR/quality_chain_$(date +%Y%m%d_%H%M).log"
LADDER="Qwen3-1.7B-Q4_K_M.gguf Qwen3-1.7B-Q8_0.gguf Qwen3-4B-Instruct-2507-Q4_0.gguf Qwen3-4B-Instruct-2507-Q4_K_M.gguf Qwen3-4B-Instruct-2507-Q8_0.gguf Qwen3.5-4B-Q4_K_M.gguf Qwen3-8B-Q4_K_M.gguf"
step() { echo "=== $(date '+%F %T') START $*" | tee -a "$L"; "$@" 2>&1 | tee -a "$L"; echo "=== $(date '+%F %T') END rc=${PIPESTATUS[0]} $*" | tee -a "$L"; }

FROM=${FROM:-1}
# 1) Context loss: same 16k document, cut to 8k / 4k / 2k
[ "$FROM" -le 1 ] && FUNC_GRID="16k:16384:f16 16k:8192:f16 16k:4096:f16 16k:2048:f16" step ./run_bench.sh func Qwen3-4B-Instruct-2507-Q4_K_M.gguf
[ "$FROM" -le 1 ] && FUNC_SKIP_AFTER_OOM=0 FUNC_GRID="8k:8192:f16 16k:8192:f16 16k:4096:f16 16k:2048:f16" step ./run_bench.sh func Qwen3-8B-Q4_K_M.gguf
[ "$FROM" -eq 15 ] && FUNC_GRID="16k:4096:f16 16k:2048:f16" step ./run_bench.sh func Qwen3-8B-Q4_K_M.gguf
# 2) Recall at 8k for every model on the ladder
{ [ "$FROM" -le 2 ] || [ "$FROM" -eq 15 ]; } && FUNC_GRID="8k:8192:f16" step ./run_bench.sh func $LADDER
# 3) Quality prompts P01-P10 (llama-completion; valid for the Instruct-2507 models only, see NOTE above)
{ [ "$FROM" -le 3 ] || [ "$FROM" -eq 15 ]; } && step ./run_bench.sh quality $LADDER
# 4) Developer tasks (auto-graded; regrade off-device with dev_bench.py regrade), 8k context
{ [ "$FROM" -le 4 ] || [ "$FROM" -eq 15 ]; } && DEV_MODELS="$(for m in $LADDER; do printf '%s:8192 ' "$m"; done)" step ./run_bench.sh dev
# 5) Size limit (last, may get killed by the system): do the bigger models even load?
step ./run_bench.sh fit gemma-3-12b-it-Q4_K_M.gguf Qwen3-14B-Q4_K_M.gguf gpt-oss-20b-Q4_K_M.gguf

# Package everything (Downloads on Termux after termux-setup-storage, otherwise next to the results)
DEST="$HOME/storage/downloads"; [ -d "$DEST" ] || DEST="$(dirname "$OUTDIR")"
tar czf "$DEST/${PHONE}-quality_$(date +%Y%m%d_%H%M).tgz" -C "$(dirname "$OUTDIR")" "$(basename "$OUTDIR")" && echo "PACKAGE READY in $DEST" | tee -a "$L"
command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock
exit 0

#!/usr/bin/env bash
# Re-run P01-P10 on the 12 GB Fold for the thinking models, with thinking
# actually turned off. Writes to quality_nothink_* so the first quality run
# is not overwritten.
#
# Why this does not use llama-completion --reasoning off:
#   In llama.cpp af6b5243d, llama-completion records --reasoning off /
#   LLAMA_ARG_REASONING=off into default_template_kwargs, but the path that
#   builds the real prompt (tools/completion/completion.cpp) never copies
#   those kwargs or enable_thinking into common_chat_templates_inputs.
#   enable_thinking defaults to true, so the model still opens <think>.
#   llama-server does apply --reasoning off (tools/server/server-context.cpp).
#
# Mechanism (all three, in this commit):
#   1. llama-server --reasoning off  (also --chat-template-kwargs
#      '{"enable_thinking":false}'). Both Qwen3 and Qwen3.5 jinja templates
#      then prefill an empty think block:
#        <|im_start|>assistant\n<think>\n\n</think>\n\n
#   2. Each request also sends chat_template_kwargs.enable_thinking=false.
#   3. Fallback: append /no_think to the user message (Qwen soft switch).
#      The bundled jinja does not parse /no_think; the weights may still
#      honour it. Disable with NO_THINK_SUFFIX=0 if you want the original
#      prompt text only.
#
# Usage (from the repo folder):
#   FROM=1 nohup ./run_quality_nothink.sh > quality_nothink.out 2>&1 &
#   FROM is the 1-based model index: 1 = Qwen3-1.7B-Q4_K_M, 2 = Qwen3-1.7B-Q8_0,
#   3 = Qwen3.5-4B-Q4_K_M, 4 = Qwen3-8B-Q4_K_M. Resume after a Termux death.
#   --smoke  checks parsing and the think-flag grader; does not load a model.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/quality_nothink.py"
PROMPTS="${PROMPTS:-$HERE/quality_prompts.md}"

if [ "${1:-}" = "--smoke" ]; then
  echo "smoke: bash parse + quality_nothink.py selftest (no model)"
  command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
  python3 "$PY" selftest || exit 1
  python3 "$PY" extract --prompts "$PROMPTS"
  echo "--- wrapped P03 (last 3 lines) ---"
  python3 "$PY" extract --prompts "$PROMPTS" --id P03 | tail -3
  echo "--- FROM demo (FROM=${FROM:-1}; models 1-4, 8B is 4) ---"
  LADDER=(Qwen3-1.7B-Q4_K_M.gguf Qwen3-1.7B-Q8_0.gguf Qwen3.5-4B-Q4_K_M.gguf Qwen3-8B-Q4_K_M.gguf)
  FROM=${FROM:-1}
  i=0
  for m in "${LADDER[@]}"; do
    i=$((i+1))
    if [ "$i" -lt "$FROM" ]; then echo "skip  $i $m"; else echo "run   $i $m"; fi
  done
  echo "SMOKE OK"
  exit 0
fi

# ---- unattended phone run ----
cd "$HERE" || exit 1
PY="$HERE/quality_nothink.py"
PROMPTS="${PROMPTS:-$HERE/quality_prompts.md}"

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
export PHONE="${PHONE:-fold8-12gb}"
export THREADS="${THREADS:-6}"
export CHARGER="${CHARGER:-yes}"
export LLAMA_ARG_REASONING=off
export GGML_KLEIDIAI_SME=0
BACKEND="${BACKEND:-cpu}"
_BD="build-$BACKEND"; [ "$BACKEND" = cpu ] && _BD=build-nosme
BIN="${BIN:-${LLAMA_DIR:-$HOME/llama.cpp}/$_BD/bin}"
SERVER="${SERVER:-$BIN/llama-server}"
MODELS_DIR="${MODELS_DIR:-$HERE/models}"
OUTDIR="${OUTDIR:-$HERE/results/raw/$PHONE}"
PORT="${PORT:-8090}"
LOAD_TIMEOUT="${LOAD_TIMEOUT:-900}"
ASK_TIMEOUT="${ASK_TIMEOUT:-14400}"
N_PREDICT="${N_PREDICT:-2048}"
N_CTX="${N_CTX:-8192}"
COOL_TEMP="${COOL_TEMP:-34}"; COOL_MIN="${COOL_MIN:-60}"; COOL_MAX="${COOL_MAX:-600}"
LOG_INTERVAL="${LOG_INTERVAL:-60}"
PROMPT_SLEEP="${PROMPT_SLEEP:-10}"
NO_THINK_SUFFIX="${NO_THINK_SUFFIX:-1}"
LOGGER="$HERE/log_thermal.sh"
mkdir -p "$OUTDIR"

FROM=${FROM:-1}
LADDER=(Qwen3-1.7B-Q4_K_M.gguf Qwen3-1.7B-Q8_0.gguf Qwen3.5-4B-Q4_K_M.gguf Qwen3-8B-Q4_K_M.gguf)

[ -x "$SERVER" ] || { echo "Cannot find $SERVER (set BIN=...)" >&2; exit 1; }
[ -f "$PY" ] || { echo "Missing $PY" >&2; exit 1; }
[ -f "$PROMPTS" ] || { echo "Missing $PROMPTS" >&2; exit 1; }
command -v python3 >/dev/null || { echo "Missing python3 (pkg install python)" >&2; exit 1; }
command -v curl >/dev/null || { echo "Missing curl (pkg install curl)" >&2; exit 1; }

RUN_ID="$(date +%Y%m%d_%H%M%S)"
STAMP="$(date +%Y%m%d_%H%M)"
QDIR="$OUTDIR/quality_nothink_${RUN_ID}"
PERF="$OUTDIR/quality_nothink_perf_${RUN_ID}.csv"
FLAGS="$OUTDIR/quality_nothink_flags_${RUN_ID}.csv"
PROG="$OUTDIR/quality_nothink_progress_${RUN_ID}.txt"
L="$OUTDIR/quality_nothink_chain_${STAMP}.log"
mkdir -p "$QDIR"
: > "$PROG"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$L" >&2; }
step_start() { echo "=== $(date '+%F %T') START $*" | tee -a "$L"; }
step_end() { echo "=== $(date '+%F %T') END rc=$1 $2" | tee -a "$L"; }

field() {
  if [ -x "$LOGGER" ]; then
    "$LOGGER" --once | cut -d, -f"$1"
  else
    echo NA
  fi
}

cooldown() {
  local t0 t now
  t0=$(date +%s)
  sleep "$COOL_MIN"
  while :; do
    t=$(field 6); now=$(date +%s)
    if [ "$t" = NA ] || awk -v a="$t" -v b="$COOL_TEMP" 'BEGIN{exit !(a<=b)}'; then break; fi
    [ $((now - t0)) -ge "$COOL_MAX" ] && { log "cooldown: limit of ${COOL_MAX}s reached (battery at ${t} C)"; break; }
    sleep 15
  done
}

LOGGER_PID=""
start_logger() {
  if [ -x "$LOGGER" ]; then
    "$LOGGER" "$LOG_INTERVAL" "$OUTDIR/thermal_quality_nothink_cpu_${RUN_ID}.csv" &
    LOGGER_PID=$!
  fi
}

SRVPID=""
stop_srv() {
  if [ -n "${SRVPID:-}" ]; then
    kill "$SRVPID" 2>/dev/null
    wait "$SRVPID" 2>/dev/null
    SRVPID=""
  fi
}

cleanup() {
  stop_srv
  [ -n "${LOGGER_PID:-}" ] && kill "$LOGGER_PID" 2>/dev/null
  command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock
}
trap cleanup EXIT INT TERM

# Describe the run so the tarball is self-contained
cat > "$QDIR/README.txt" << EOF
quality_nothink run $RUN_ID
phone=$PHONE threads=$THREADS ctx=$N_CTX n_predict=$N_PREDICT temp=0 seed=42
backend=$BACKEND FROM=$FROM NO_THINK_SUFFIX=$NO_THINK_SUFFIX
llama.cpp commit af6b5243d (llama-server --reasoning off)
Prompts: P01-P10 from quality_prompts.md, plus a trailing /no_think line when NO_THINK_SUFFIX=1.
This folder must not be mixed with quality_YYYYMMDD_* from the first quality run.
EOF

python3 "$PY" selftest > "$OUTDIR/quality_nothink_selftest_${RUN_ID}.txt" 2>&1 || {
  log "WARNING: quality_nothink.py selftest failed - see quality_nothink_selftest_${RUN_ID}.txt"
}

EXTRA=()
"$SERVER" --help 2>&1 | grep -q -- '--cache-ram' && EXTRA+=(--cache-ram 0)
"$SERVER" --help 2>&1 | grep -qE -- '(^| )-fit[ ,]|--fit ' && EXTRA+=(-fit off)

SUFFIX_FLAG=()
[ "$NO_THINK_SUFFIX" = 0 ] && SUFFIX_FLAG=(--no-suffix)

start_logger
log "quality_nothink plan: models=${#LADDER[@]} FROM=$FROM n_predict=$N_PREDICT ctx=$N_CTX suffix=$NO_THINK_SUFFIX"
log "results: $QDIR"
log "perf:    $PERF"

# Prompt ids in order
mapfile -t PIDS < <(python3 "$PY" extract --prompts "$PROMPTS" | awk '{print $1}')
[ "${#PIDS[@]}" -eq 10 ] || { log "expected 10 prompts, got ${#PIDS[@]}"; exit 1; }

overall=0
idx=0
first_run=1
for gguf in "${LADDER[@]}"; do
  idx=$((idx+1))
  if [ "$idx" -lt "$FROM" ]; then
    log "skip model $idx/$FROM (FROM=$FROM): $gguf"
    continue
  fi
  M="$MODELS_DIR/$gguf"
  mb=$(basename "$gguf" .gguf)
  step_start "quality_nothink $mb"
  echo "STARTING,$mb,$idx,$(date +%s)" >> "$PROG"

  if [ ! -f "$M" ]; then
    log "missing $M"
    echo "END,$mb,rc=4,missing" >> "$PROG"
    step_end 4 "quality_nothink $mb"
    overall=1
    continue
  fi

  if [ "$first_run" = 1 ]; then
    first_run=0
  else
    log "cooldown between models"
    cooldown
  fi

  mem=$(field 15); tmp=$(field 6)
  slog="$OUTDIR/server_quality_nothink_${mb}_c${N_CTX}_${RUN_ID}.log"
  log "quality_nothink: $mb (battery ${tmp} C, MemAvailable ${mem} MB)"
  "$SERVER" -m "$M" -c "$N_CTX" -ctk f16 -ctv f16 -fa on -np 1 -t "${THREADS%%,*}" -ngl 0 \
    -lm none --jinja --reasoning off --chat-template-kwargs '{"enable_thinking":false}' \
    "${EXTRA[@]}" --host 127.0.0.1 --port "$PORT" > "$slog" 2>&1 &
  SRVPID=$!
  t0=$(date +%s); ready=0
  while kill -0 "$SRVPID" 2>/dev/null; do
    curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && { ready=1; break; }
    [ $(( $(date +%s) - t0 )) -ge "$LOAD_TIMEOUT" ] && break
    sleep 2
  done
  load_s=$(( $(date +%s) - t0 ))
  model_rc=0
  if [ "$ready" != 1 ]; then
    log "server did not start ($mb) after ${load_s}s - see $slog"
    model_rc=4
  else
    log "server ready in ${load_s}s"
    for id in "${PIDS[@]}"; do
      prompt=$(python3 "$PY" extract --prompts "$PROMPTS" --id "$id" "${SUFFIX_FLAG[@]}")
      # extract already wraps when suffix is on; pass --no-suffix to ask to avoid a second wrap
      echo "STARTING,$mb,$id,$(date +%s)" >> "$PROG"
      log "quality_nothink: $mb $id"
      python3 "$PY" ask \
        --server "http://127.0.0.1:$PORT" \
        --prompt "$prompt" --no-suffix \
        --prompt-id "$id" --model "$mb" \
        --out-txt "$QDIR/${mb}__${id}.txt" \
        --out-json "$QDIR/${mb}__${id}.json" \
        --perf-csv "$PERF" \
        --phone "$PHONE" --backend "$BACKEND" --load-s "$load_s" \
        --temp 0 --seed 42 --n-predict "$N_PREDICT" --timeout "$ASK_TIMEOUT" \
        | tee -a "$L"
      prc=${PIPESTATUS[0]}
      echo "END,$mb,$id,rc=$prc" >> "$PROG"
      [ "$prc" = 0 ] || model_rc=$prc
      sleep "$PROMPT_SLEEP"
    done
  fi
  srv_rc=alive
  if [ -n "$SRVPID" ] && ! kill -0 "$SRVPID" 2>/dev/null; then
    wait "$SRVPID" 2>/dev/null; srv_rc=$?; SRVPID=""
  fi
  stop_srv
  echo "END,$mb,rc=$model_rc,server=$srv_rc" >> "$PROG"
  step_end "$model_rc" "quality_nothink $mb"
  [ "$model_rc" = 0 ] || overall=1
done

# Compact flag table (also already in the perf CSV)
if [ -f "$PERF" ]; then
  python3 - "$PERF" "$FLAGS" << 'PY' || true
import csv, sys
src, dst = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(src)))
with open(dst, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["model","prompt_id","flag","has_think","unclosed","final_empty","gen_tokens","gen_tps","wall_s"])
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in w.fieldnames})
print("flags:", dst)
from collections import Counter
print("by flag:", dict(Counter(r["flag"] for r in rows)))
for r in rows:
    if r["flag"] != "OK":
        print(f"  {r['model']} {r['prompt_id']} {r['flag']}")
PY
  log "flag summary written to $FLAGS"
  if grep -q 'THINK_NO_FINAL\|,EMPTY' "$FLAGS"; then
    log "WARNING: at least one answer is THINK_NO_FINAL or EMPTY - thinking may still be on"
  else
    log "no THINK_NO_FINAL/EMPTY flags in this run"
  fi
fi

# Package only this run, not the earlier quality_* tree
STAGE="$OUTDIR/package_nothink_${STAMP}"
mkdir -p "$STAGE/fold8-12gb-nothink"
cp -a "$QDIR" "$STAGE/fold8-12gb-nothink/"
for f in "$PERF" "$FLAGS" "$PROG" "$L" \
         "$OUTDIR/quality_nothink_selftest_${RUN_ID}.txt" \
         "$OUTDIR/thermal_quality_nothink_cpu_${RUN_ID}.csv"; do
  [ -e "$f" ] && cp -a "$f" "$STAGE/fold8-12gb-nothink/"
done
cp -a "$OUTDIR"/server_quality_nothink_*_"${RUN_ID}".log "$STAGE/fold8-12gb-nothink/" 2>/dev/null || true
TAR="$HOME/storage/downloads/fold8-12gb-nothink_${STAMP}.tgz"
if [ ! -d "$HOME/storage/downloads" ]; then
  TAR="$OUTDIR/fold8-12gb-nothink_${STAMP}.tgz"
  log "Downloads not mounted; writing $TAR instead"
fi
tar czf "$TAR" -C "$STAGE" fold8-12gb-nothink
rm -rf "$STAGE"
echo "PACKAGE READY: $TAR" | tee -a "$L"
echo "archive: $TAR" | tee -a "$L"
exit "$overall"

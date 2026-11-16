#!/usr/bin/env bash
# run_bench.sh - llama.cpp benchmark modes used for the Galaxy Z Fold 8 (12 GB) results (Termux or any CPU-only box)
#
# Usage:  PHONE=fold8-12gb ./run_bench.sh <mode> [models...]
# Modes:
#   info       versions, CPU, llama.cpp devices, memory (run this first)
#   speed      pp512 + tg128 (llama-bench, REPS repetitions) per model/threads/backend
#   fit        does the model load at all? (no mmap, -lm none), smallest to largest
#   ctx        context sweep with llama-bench (depth CTX_LIST) until it fails (OOM)
#   func       long-context RECALL test (func_bench.py): 10 facts hidden in a 2k..32k-token document, asked at the end;
#              recall = facts answered correctly / 10, plus prefill/decode t/s, TTFT, wall time and OOM.
#              Variables: FUNC_GRID="DOC:CTX:KV ..." (default "8k:8192:f16 16k:16384:f16 16k:8192:f16 16k:16384:q8_0";
#              CTX < DOC = document truncated from the start)  FUNC_SLEEP=20  FUNC_MMAP=0 (1 = mmap the model)
#              FUNC_SKIP_AFTER_OOM=1  PORT=8090  LOAD_TIMEOUT=900   Summary: python3 func_bench.py report "$OUTDIR"
#   dev        developer scenario (scenarios/dev_questions.json): shell/git one-liners, regex, errors, bug fixes,
#              small functions and questions about a ~4.6k-token source file; objective grading by executing the
#              answers in a sandbox (dev_bench.py). Variables: DEV_MODELS="<gguf>[:ctx[:kv]] ..."  DEV_NPREDICT=768
#              DEV_EXEC=1 (0 = do not execute model output, only extract it)
#   quality    DEPRECATED for thinking models (Qwen3 / Qwen3.5): uses llama-completion, which IGNORES --reasoning off
#              at llama.cpp af6b5243d, so the model still thinks. Use ./run_quality_nothink.sh instead.
#              Still valid for non-thinking models (Qwen3-4B-Instruct-2507), whose published scores come from it.
# Without a model list, uses every .gguf in MODELS_DIR (sorted by size).
#
# Variables (defaults):
#   PHONE=fold8-12gb (required, label only)       BACKEND=cpu (only cpu was used for the published results)
#   LLAMA_DIR=~/llama.cpp   BIN=$LLAMA_DIR/build-nosme/bin (cpu) or $LLAMA_DIR/build-$BACKEND/bin
#   MODELS_DIR=<repo>/models   OUTDIR=<repo>/results/raw/$PHONE
#   THREADS=8,6   REPS=5   NGL=(0 on cpu, 99 otherwise)   DEV= (e.g. GPUOpenCL, Vulkan0)
#   CTX_LIST="2048 4096 8192 16384 32768"
#   COOL_TEMP=34 (battery C)  COOL_MIN=60  COOL_MAX=600 (s)        LOG_INTERVAL=60
#   CHARGER=no|yes (label only)  FOLD_STATE=open|closed (label only)
set -u
MODE="${1:-}"; shift || true
[ -n "${PHONE:-}" ] || { echo "Set PHONE, e.g. PHONE=fold8-16gb $0 speed" >&2; exit 1; }
BACKEND="${BACKEND:-cpu}"
_BD="build-$BACKEND"; [ "$BACKEND" = cpu ] && _BD=build-nosme
BIN="${BIN:-${LLAMA_DIR:-$HOME/llama.cpp}/$_BD/bin}"
export GGML_KLEIDIAI_SME=0   # SME crashed the Fold 8 in Termux (harmless elsewhere)
THREADS="${THREADS:-8,6}"
REPS="${REPS:-5}"
if [ "$BACKEND" = cpu ]; then NGL="${NGL:-0}"; else NGL="${NGL:-99}"; fi
DEV="${DEV:-}"
CTX_LIST="${CTX_LIST:-2048 4096 8192 16384 32768}"
COOL_TEMP="${COOL_TEMP:-34}"; COOL_MIN="${COOL_MIN:-60}"; COOL_MAX="${COOL_MAX:-600}"
LOG_INTERVAL="${LOG_INTERVAL:-60}"
CHARGER="${CHARGER:-no}"; FOLD_STATE="${FOLD_STATE:-open}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MODELS_DIR="${MODELS_DIR:-$HERE/models}"
LOGGER="$HERE/log_thermal.sh"
PROMPTS="$HERE/quality_prompts.md"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUTDIR="${OUTDIR:-$HERE/results/raw/$PHONE}"
mkdir -p "$OUTDIR"
export PHONE TAG_FILE="$HOME/.bench_tag"
BENCH="$BIN/llama-bench"; COMPL="$BIN/llama-completion"; SERVER="$BIN/llama-server"
DEVARG=(); [ -n "$DEV" ] && DEVARG=(-dev "$DEV")
CTX_PREFIX="phone,backend,charger,fold_state,mode,run_id,ram_plus,memavail_mb_start,batt_pct_start,batt_temp_start_c,rc"

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }
tag() { echo "$*" > "$TAG_FILE"; }
field() { "$LOGGER" --once | cut -d, -f"$1"; }   # 5=pct 6=temp 7=charge_counter 15=memavail
ram_plus() { awk '/^SwapTotal:/{print ($2>0?"on("int($2/1024)"MB)":"off")}' /proc/meminfo; }

models() {
  if [ "$#" -gt 0 ]; then
    for m in "$@"; do [ -f "$m" ] && echo "$m" || echo "$MODELS_DIR/$m"; done
  else
    ls -1S -r "$MODELS_DIR"/*.gguf 2>/dev/null
  fi
}

cooldown() {
  local t0 t now
  t0=$(date +%s); tag "cooldown"
  sleep "$COOL_MIN"
  while :; do
    t=$(field 6); now=$(date +%s)
    if [ "$t" = NA ] || awk -v a="$t" -v b="$COOL_TEMP" 'BEGIN{exit !(a<=b)}'; then break; fi
    [ $((now - t0)) -ge "$COOL_MAX" ] && { log "cooldown: limit of ${COOL_MAX}s reached (battery at ${t} C)"; break; }
    sleep 15
  done
}

start_logger() {
  "$LOGGER" "$LOG_INTERVAL" "$OUTDIR/thermal_${MODE}_${BACKEND}_${RUN_ID}.csv" &
  LOGGER_PID=$!
  trap 'kill $LOGGER_PID 2>/dev/null; tag idle' EXIT INT TERM
}

# Runs llama-bench with CSV output and prefixes every row with the test context.
bench_csv() {   # bench_csv <output_file> <llama-bench args...>
  local out="$1"; shift
  local mem pct tmp rc tmpf
  mem=$(field 15); pct=$(field 5); tmp=$(field 6)
  tmpf=$(mktemp)
  "$BENCH" "$@" "${DEVARG[@]}" -o csv > "$tmpf" 2>>"$OUTDIR/stderr_${RUN_ID}.log"; rc=$?
  local pre
  pre="$PHONE,$BACKEND,$CHARGER,$FOLD_STATE,$MODE,$RUN_ID,$(ram_plus),$mem,$pct,$tmp,$rc"
  if [ ! -s "$out" ]; then
    local hdr; hdr=$(head -1 "$tmpf")
    [ -n "$hdr" ] || hdr="(no_output)"
    echo "$CTX_PREFIX,$hdr" > "$out"
  fi
  if [ "$(wc -l < "$tmpf")" -gt 1 ]; then
    tail -n +2 "$tmpf" | sed "s|^|$pre,|" >> "$out"
  else
    echo "$pre,FAILED args=[$*]" >> "$out"
  fi
  rm -f "$tmpf"
  return $rc
}

[ -x "$BENCH" ] || [ "$MODE" = info ] || { echo "Cannot find $BENCH (set BIN=...)" >&2; exit 1; }
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock

case "$MODE" in
info)
  F="$OUTDIR/info_${RUN_ID}.txt"
  {
    echo "phone=$PHONE run=$RUN_ID backend=$BACKEND"
    echo "--- getprop"; for p in ro.product.model ro.build.display.id ro.build.version.release ro.build.version.oneui ro.soc.model; do echo "$p=$(getprop $p 2>/dev/null)"; done
    echo "--- /proc/meminfo"; head -5 /proc/meminfo; grep -E '^Swap' /proc/meminfo
    echo "--- RAM Plus: $(ram_plus)"
    echo "--- cpu"; grep -m1 -i 'features' /proc/cpuinfo; nproc
    echo "--- llama.cpp"; "$BENCH" --version 2>&1 | tail -2
    echo "--- devices"; "$BENCH" --list-devices 2>&1
    echo "--- battery/thermal"; "$LOGGER" --header; "$LOGGER" --once
  } | tee "$F"
  ;;
speed)
  start_logger
  OUT="$OUTDIR/speed_${BACKEND}_${RUN_ID}.csv"
  while read -r M; do
    [ -f "$M" ] || { log "missing $M"; continue; }
    cooldown
    tag "speed $(basename "$M")"; log "speed: $(basename "$M") t=$THREADS ngl=$NGL"
    bench_csv "$OUT" -m "$M" -p 512 -n 128 -r "$REPS" -t "$THREADS" -ngl "$NGL" --delay 5 || log "rc=$? on $(basename "$M")"
  done < <(models "$@")
  log "done: $OUT"
  ;;
fit)
  start_logger
  OUT="$OUTDIR/fit_${BACKEND}_${RUN_ID}.csv"
  while read -r M; do
    [ -f "$M" ] || continue
    echo "STARTING,$(basename "$M"),$(date +%s)" >> "$OUTDIR/fit_progress_${RUN_ID}.txt"   # if Termux gets killed, the last line shows where
    tag "fit $(basename "$M")"; log "fit: $(basename "$M")"
    bench_csv "$OUT" -m "$M" -p 64 -n 16 -r 1 -t "${THREADS%%,*}" -ngl "$NGL" -lm none --no-warmup
    rc=$?; echo "END,$(basename "$M"),rc=$rc" >> "$OUTDIR/fit_progress_${RUN_ID}.txt"
    sleep 20
  done < <(models "$@")
  log "done: $OUT"
  ;;
ctx)
  start_logger
  OUT="$OUTDIR/ctx_${BACKEND}_${RUN_ID}.csv"
  while read -r M; do
    [ -f "$M" ] || continue
    for D in $CTX_LIST; do
      echo "STARTING,$(basename "$M"),d=$D" >> "$OUTDIR/ctx_progress_${RUN_ID}.txt"
      tag "ctx $(basename "$M") d=$D"; log "ctx: $(basename "$M") depth=$D (total ctx = D+512+32)"
      if ! bench_csv "$OUT" -m "$M" -p 512 -n 32 -d "$D" -r 1 -t "${THREADS%%,*}" -ngl "$NGL" -lm none -fa on; then
        log "failed at d=$D - stopping this model"; break
      fi
      sleep 20
    done
    cooldown
  done < <(models "$@")
  log "done: $OUT"
  ;;
quality)
  # DEPRECATED for thinking models: llama-completion ignores --reasoning off / LLAMA_ARG_REASONING at af6b5243d
  # (tools/completion/completion.cpp never copies enable_thinking; default is true). Use run_quality_nothink.sh.
  log "WARNING: quality mode uses llama-completion; thinking CANNOT be turned off here. Valid only for non-thinking models."
  [ -x "$COMPL" ] || { echo "Missing $COMPL" >&2; exit 1; }
  start_logger
  QDIR="$OUTDIR/quality_${RUN_ID}"; mkdir -p "$QDIR"
  PERF="$OUTDIR/quality_perf_${BACKEND}_${RUN_ID}.csv"
  echo "phone,backend,model,prompt_id,rc,load_ms,prompt_tokens,prompt_ms,ttft_ms,gen_tokens,gen_tps" > "$PERF"
  while read -r M; do
    [ -f "$M" ] || continue
    mb=$(basename "$M" .gguf)
    for id in $(grep -oE '^### P[0-9]+' "$PROMPTS" | awk '{print $2}'); do
      prompt=$(awk -v id="$id" '
        $0 ~ "^### "id"( |$)" {f=1; next}
        f && /^```text/ {inb=1; next}
        f && inb && /^```/ {exit}
        f && inb {print}' "$PROMPTS")
      tag "quality $mb $id"; log "quality: $mb $id"
      "$COMPL" -m "$M" -p "$prompt" -cnv -st --jinja --temp 0 --seed 42 -n 1024 -c 8192 \
        -t "${THREADS%%,*}" -ngl "$NGL" "${DEVARG[@]}" </dev/null \
        > "$QDIR/${mb}__${id}.txt" 2> "$QDIR/${mb}__${id}.log"; rc=$?
      L="$QDIR/${mb}__${id}.log"
      load=$(awk '/load time =/{for(i=1;i<=NF;i++) if($i=="=") {print $(i+1); exit}}' "$L")
      read -r pms ptok < <(awk '/prompt eval time =/{for(i=1;i<=NF;i++){if($i=="="&&!a){a=$(i+1)} if($i=="/"&&!b){b=$(i+1)}} print a, b; exit}' "$L")
      read -r gms gtok < <(awk '/[^t] eval time =/ && !/prompt/{for(i=1;i<=NF;i++){if($i=="="&&!a){a=$(i+1)} if($i=="/"&&!b){b=$(i+1)}} print a, b; exit}' "$L")
      ttft=$(awk -v p="${pms:-0}" -v g="${gms:-0}" -v n="${gtok:-0}" 'BEGIN{ if(n>0) printf "%.1f", p+g/n; else print "NA"}')
      tps=$(awk -v g="${gms:-0}" -v n="${gtok:-0}" 'BEGIN{ if(g>0) printf "%.2f", n/(g/1000); else print "NA"}')
      echo "$PHONE,$BACKEND,$mb,$id,$rc,${load:-NA},${ptok:-NA},${pms:-NA},$ttft,${gtok:-NA},$tps" >> "$PERF"
      sleep 10
    done
    cooldown
  done < <(models "$@")
  log "done: answers in $QDIR ; metrics in $PERF"
  ;;
func)
  # Long-context RECALL test (func_bench.py): 10 facts planted in a synthetic document of 2k..32k tokens,
  # answered as key=value lines at temp 0. One llama-server per spec DOC:CTX:KV; CTX < DOC = truncated
  # (the start of the document is dropped, like a phone that cannot hold it all).
  [ -x "$SERVER" ] || { echo "Missing $SERVER (build the llama-server target)" >&2; exit 1; }
  command -v python3 >/dev/null || { echo "Missing python3 (pkg install python)" >&2; exit 1; }
  command -v curl >/dev/null || { echo "Missing curl (pkg install curl)" >&2; exit 1; }
  FUNC_GRID="${FUNC_GRID:-8k:8192:f16 16k:16384:f16 16k:8192:f16 16k:16384:q8_0}"
  FUNC_SLEEP="${FUNC_SLEEP:-20}"; FUNC_MMAP="${FUNC_MMAP:-0}"; FUNC_SKIP_AFTER_OOM="${FUNC_SKIP_AFTER_OOM:-1}"
  PORT="${PORT:-8090}"; LOAD_TIMEOUT="${LOAD_TIMEOUT:-900}"
  LM=(-lm none); [ "$FUNC_MMAP" = 1 ] && LM=()
  FB="$HERE/func_bench.py"
  OUT="$OUTDIR/func_${BACKEND}_${RUN_ID}.csv"; RAW="$OUTDIR/func_raw_${RUN_ID}"; PROG="$OUTDIR/func_progress_${RUN_ID}.txt"
  python3 "$FB" selftest > "$OUTDIR/func_selftest_${RUN_ID}.txt" 2>&1 || { echo "func_bench.py selftest failed - see $OUTDIR/func_selftest_${RUN_ID}.txt" >&2; exit 1; }
  EXTRA=(); "$SERVER" --help 2>&1 | grep -q -- '--cache-ram' && EXTRA+=(--cache-ram 0)
  "$SERVER" --help 2>&1 | grep -qE -- '(^| )-fit[ ,]|--fit ' && EXTRA+=(-fit off)
  start_logger
  SRVPID=""
  stop_srv() { [ -n "$SRVPID" ] && kill "$SRVPID" 2>/dev/null; wait "$SRVPID" 2>/dev/null; SRVPID=""; }
  trap 'stop_srv; kill $LOGGER_PID 2>/dev/null; tag idle' EXIT INT TERM
  log "func plan (ESTIMATES from the measured Fold 8 llama-bench speeds of Qwen3-4B/8B, not measurements):"
  while read -r M; do
    for SPEC in $FUNC_GRID; do log "  $(python3 "$FB" estimate --model-name "$(basename "$M" .gguf)" --spec "$SPEC")"; done
  done < <(models "$@")
  first=1
  while read -r M; do
    [ -f "$M" ] || { log "missing $M"; continue; }
    MB=$(basename "$M" .gguf)
    [ "$first" = 1 ] || { log "cooldown between models"; cooldown; }; first=0
    declare -A OOMC=()
    for SPEC in $FUNC_GRID; do
      IFS=: read -r D C KV <<< "$SPEC"; KV="${KV:-f16}"
      COMMON_ARGS=(--phone "$PHONE" --backend "$BACKEND" --model-name "$MB" --spec "$SPEC" --out "$OUT" --run-id "$RUN_ID")
      if [ "$FUNC_SKIP_AFTER_OOM" = 1 ] && [ -n "${OOMC[$KV]:-}" ] && [ "$C" -ge "${OOMC[$KV]}" ]; then
        log "skip $MB $SPEC (n_ctx=${OOMC[$KV]} with kv=$KV already failed)"
        python3 "$FB" skip "${COMMON_ARGS[@]}" --reason "n_ctx ${OOMC[$KV]} kv=$KV already failed in this run"; continue
      fi
      mem=$(field 15); tmp=$(field 6)
      echo "STARTING,$MB,$SPEC,$(date +%s)" >> "$PROG"      # if Termux gets killed, this line has no END
      tag "func $MB $SPEC"; log "func: $MB $SPEC (battery ${tmp} C, MemAvailable ${mem} MB)"
      slog="$OUTDIR/server_func_${MB}_${D}_c${C}_${KV}_${RUN_ID}.log"
      "$SERVER" -m "$M" -c "$C" -ctk "$KV" -ctv "$KV" -fa on -np 1 -t "${THREADS%%,*}" -ngl "$NGL" "${DEVARG[@]}" \
        "${LM[@]}" "${EXTRA[@]}" --host 127.0.0.1 --port "$PORT" --chat-template-kwargs '{"enable_thinking":false}' > "$slog" 2>&1 &
      SRVPID=$!; t0=$(date +%s); ready=0
      while kill -0 "$SRVPID" 2>/dev/null; do
        curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && { ready=1; break; }
        [ $(( $(date +%s) - t0 )) -ge "$LOAD_TIMEOUT" ] && break
        sleep 2
      done
      load_s=$(( $(date +%s) - t0 ))
      if [ "$ready" = 1 ]; then
        log "server ready in ${load_s}s"
        python3 "$FB" run "${COMMON_ARGS[@]}" --server "http://127.0.0.1:$PORT" --raw-dir "$RAW" \
          --load-s "$load_s" --temp "$tmp" --mem "$mem"; rc=$?
      else
        log "server did not start ($MB c=$C kv=$KV) - recorded as failed_to_load (likely OOM); see $slog"
        python3 "$FB" oom "${COMMON_ARGS[@]}" --load-s "$load_s" --temp "$tmp" --mem "$mem" --reason "server did not start (see $(basename "$slog"))"; rc=4
      fi
      srv_rc=alive
      if [ -n "$SRVPID" ] && ! kill -0 "$SRVPID" 2>/dev/null; then wait "$SRVPID" 2>/dev/null; srv_rc=$?; SRVPID=""; fi   # 137 = killed (OOM killer)
      stop_srv
      echo "END,$MB,$SPEC,rc=$rc,server=$srv_rc" >> "$PROG"
      if [ "$rc" != 0 ]; then
        log "FAILED: $MB $SPEC rc=$rc server=$srv_rc"
        { [ -z "${OOMC[$KV]:-}" ] || [ "$C" -lt "${OOMC[$KV]}" ]; } && OOMC[$KV]=$C
      fi
      sleep "$FUNC_SLEEP"
    done
    unset OOMC
  done < <(models "$@")
  python3 "$FB" report "$OUTDIR" > "$OUTDIR/func_report.csv"
  log "done: $OUT  (all func runs on this phone: $OUTDIR/func_report.csv)"
  cat "$OUTDIR/func_report.csv"
  ;;
dev)
  [ -x "$SERVER" ] || { echo "Missing $SERVER (build the llama-server target)" >&2; exit 1; }
  command -v python3 >/dev/null || { echo "Missing python3 (pkg install python)" >&2; exit 1; }
  command -v node >/dev/null || log "WARNING: node not found (pkg install nodejs) - the JavaScript questions will be not_run"
  command -v git >/dev/null || log "WARNING: git not found - the git questions will fail"
  # best 12 GB model(s) vs best 16 GB model(s); run the SAME list on both phones - what does not fit is recorded as OOM
  DEV_MODELS="${DEV_MODELS:-Qwen3.5-4B-Q4_K_M.gguf:16384 Qwen3.5-9B-Q4_K_M.gguf:8192 Qwen3.5-9B-Q6_K.gguf:16384 Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf:8192:q8_0}"
  DEV_NPREDICT="${DEV_NPREDICT:-768}"; DEV_EXEC="${DEV_EXEC:-1}"
  PORT="${PORT:-8090}"; LOAD_TIMEOUT="${LOAD_TIMEOUT:-900}"
  NOEXEC=(); [ "$DEV_EXEC" = 0 ] && NOEXEC=(--no-exec)
  start_logger
  JOUT="$OUTDIR/dev_${BACKEND}_${RUN_ID}.jsonl"
  SRVPID=""
  stop_srv() { [ -n "$SRVPID" ] && kill "$SRVPID" 2>/dev/null; wait "$SRVPID" 2>/dev/null; SRVPID=""; }
  trap 'stop_srv; kill $LOGGER_PID 2>/dev/null; tag idle' EXIT INT TERM
  python3 "$HERE/dev_bench.py" selftest > "$OUTDIR/dev_selftest_${RUN_ID}.txt" 2>&1 || log "WARNING: grader selftest failed - see dev_selftest_${RUN_ID}.txt"
  for SPEC in $DEV_MODELS; do
    IFS=: read -r MM C KV <<< "$SPEC"; C="${C:-16384}"; KV="${KV:-f16}"
    M=$(models "$MM" | head -1); [ -f "$M" ] || { log "missing $MM - skipped"; continue; }
    MB=$(basename "$M" .gguf)
    cooldown
    slog="$OUTDIR/server_dev_${MB}_c${C}_${KV}_${RUN_ID}.log"
    echo "STARTING,$MB,c=$C,kv=$KV" >> "$OUTDIR/dev_progress_${RUN_ID}.txt"
    "$SERVER" -m "$M" -c "$C" -ctk "$KV" -ctv "$KV" -fa on -np 1 -t "${THREADS%%,*}" -ngl "$NGL" "${DEVARG[@]}" \
      -lm none --host 127.0.0.1 --port "$PORT" --chat-template-kwargs '{"enable_thinking":false}' > "$slog" 2>&1 &
    SRVPID=$!; t0=$(date +%s); ready=0
    while kill -0 "$SRVPID" 2>/dev/null; do
      curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && { ready=1; break; }
      [ $(( $(date +%s) - t0 )) -ge "$LOAD_TIMEOUT" ] && break
      sleep 3
    done
    if [ "$ready" = 1 ]; then
      tag "dev $MB c=$C"; log "dev: $MB n_ctx=$C kv=$KV (loaded in $(( $(date +%s) - t0 ))s)"
      python3 "$HERE/dev_bench.py" run --server "http://127.0.0.1:$PORT" --phone "$PHONE" --backend "$BACKEND" \
        --model-name "$MB" --n-ctx "$C" --kv "$KV" --n-predict "$DEV_NPREDICT" --out "$JOUT" "${NOEXEC[@]}" \
        || log "server crashed mid-run (likely OOM) - see $slog"
      stop_srv
    else
      log "server did not start ($MB, c=$C) - recorded as OOM; see $slog"; stop_srv
      python3 "$HERE/dev_bench.py" oom --phone "$PHONE" --backend "$BACKEND" --model-name "$MB" --n-ctx "$C" --kv "$KV" --out "$JOUT"
    fi
  done
  python3 "$HERE/dev_bench.py" summary "$JOUT" --out "${JOUT%.jsonl}.csv"
  log "done: $JOUT and ${JOUT%.jsonl}.csv  (blind grading: python3 dev_bench.py blind <jsonl from both phones>)"
  ;;
*)
  awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit 1 ;;
esac

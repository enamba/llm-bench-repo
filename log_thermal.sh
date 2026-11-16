#!/usr/bin/env bash
# log_thermal.sh - logs the phone's battery, temperatures and memory to CSV.
# Usage:
#   ./log_thermal.sh [interval_s] [file.csv]   # loops until Ctrl+C / kill
#   ./log_thermal.sh --once                    # prints ONE CSV line (no header)
#   ./log_thermal.sh --header                  # prints only the header
# Data source (automatic):
#   1) "adb" connected to the phone itself (Wireless debugging) -> dumpsys battery + dumpsys thermalservice
#   2) otherwise Termux:API (termux-battery-status) -> battery only
#   cpu_temp_c falls back to the max of /sys/class/thermal/thermal_zone*/ (type cpu*) when adb gives none
# Variables: PHONE (e.g. fold8-16gb), TAG_FILE (file holding the label of the running test), ADB_SERIAL (optional)
set -u
PHONE="${PHONE:-unknown}"
TAG_FILE="${TAG_FILE:-$HOME/.bench_tag}"
HEADER="ts_local,ts_epoch,phone,source,batt_pct,batt_temp_c,charge_counter_uah,current_ua,voltage_mv,plugged,thermal_status,cpu_temp_c,gpu_temp_c,skin_temp_c,memavail_mb,swap_used_mb,cpu_max_khz,tag"

adb_ok() {
  command -v adb >/dev/null 2>&1 || return 1
  local s; s=$(adb ${ADB_SERIAL:+-s "$ADB_SERIAL"} get-state 2>/dev/null) || return 1
  [ "$s" = "device" ]
}
ash() { adb ${ADB_SERIAL:+-s "$ADB_SERIAL"} shell "$@" 2>/dev/null | tr -d '\r'; }

meminfo() {
  local avail swt swf
  avail=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo 2>/dev/null)
  swt=$(awk '/^SwapTotal:/{print $2}' /proc/meminfo 2>/dev/null)
  swf=$(awk '/^SwapFree:/{print $2}' /proc/meminfo 2>/dev/null)
  echo "${avail:-NA},$(( (${swt:-0} - ${swf:-0}) / 1024 ))"
}

# Max CPU temperature from sysfs thermal zones (readable without root on the Fold 8)
sysfs_cpu_temp() {
  local m=-1 z t v
  for z in /sys/class/thermal/thermal_zone*; do
    t=$(cat "$z/type" 2>/dev/null) || continue
    case "$t" in *trip*) continue ;; cpu*) ;; *) continue ;; esac
    v=$(cat "$z/temp" 2>/dev/null) || continue
    [ -n "$v" ] && [ "$v" -gt "$m" ] && m=$v
  done
  [ "$m" -gt 0 ] && awk -v v="$m" 'BEGIN{printf "%.1f", v/1000}' || echo NA
}

cpu_max_khz() {
  local m=0 f v
  for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do
    v=$(cat "$f" 2>/dev/null) || continue
    [ -n "$v" ] && [ "$v" -gt "$m" ] && m=$v
  done
  [ "$m" -gt 0 ] && echo "$m" || echo NA
}

one_line() {
  local ts ep tag src pct temp cc cur volt plug tstat cput gput skint mem
  ts=$(date '+%Y-%m-%d %H:%M:%S'); ep=$(date +%s)
  tag=$(cat "$TAG_FILE" 2>/dev/null | head -1 | tr ',' ';'); tag=${tag:-idle}
  pct=NA temp=NA cc=NA cur=NA volt=NA plug=NA tstat=NA cput=NA gput=NA skint=NA
  if adb_ok; then
    src=adb
    local b t
    b=$(ash dumpsys battery)
    pct=$(echo "$b" | awk -F': ' '/^ *level:/{print $2; exit}')
    temp=$(echo "$b" | awk -F': ' '/^ *temperature:/{printf "%.1f", $2/10; exit}')
    cc=$(echo "$b" | awk -F': ' 'tolower($0) ~ /charge counter:/{print $2; exit}')
    cur=$(echo "$b" | awk -F': ' 'tolower($0) ~ /current now:/{print $2; exit}')
    volt=$(echo "$b" | awk -F': ' '/^ *voltage:/{print $2; exit}')
    plug=$(echo "$b" | awk -F': ' '/AC powered:|USB powered:|Wireless powered:/{if($2=="true")p=1} END{print (p?"yes":"no")}')
    t=$(ash dumpsys thermalservice)
    tstat=$(echo "$t" | awk -F': ' '/Thermal Status:/{print $2; exit}')
    # "Current temperatures from HAL" block: mType 0=CPU 1=GPU 2=BATTERY 3=SKIN; keeps the max of each type
    read -r cput gput skint < <(echo "$t" | awk '
      /Current temperatures from HAL/{on=1; next}
      on && /^[^ \t]/ && !/Temperature\{/{on=0}
      on && /Temperature\{/{
        v=$0; sub(/.*mValue=/,"",v); sub(/,.*/,"",v)
        ty=$0; sub(/.*mType=/,"",ty); sub(/,.*/,"",ty)
        if(!(ty in mx) || v+0>mx[ty]) mx[ty]=v+0
      }
      END{ printf "%s %s %s\n", (0 in mx?mx[0]:"NA"), (1 in mx?mx[1]:"NA"), (3 in mx?mx[3]:"NA") }')
  elif command -v termux-battery-status >/dev/null 2>&1; then
    src=termux-api
    local j; j=$(timeout 10 termux-battery-status 2>/dev/null)
    if command -v jq >/dev/null 2>&1 && [ -n "$j" ]; then
      pct=$(echo "$j" | jq -r '.percentage // "NA"')
      temp=$(echo "$j" | jq -r '.temperature // "NA"')
      cur=$(echo "$j" | jq -r '.current // "NA"')
      cc=$(echo "$j" | jq -r '.charge_counter // "NA"')
      plug=$(echo "$j" | jq -r '.plugged // "NA"')
    fi
  else
    src=none
  fi
  [ "${cput:-NA}" = NA ] && cput=$(sysfs_cpu_temp)
  mem=$(meminfo)
  echo "$ts,$ep,$PHONE,$src,${pct:-NA},${temp:-NA},${cc:-NA},${cur:-NA},${volt:-NA},${plug:-NA},${tstat:-NA},${cput:-NA},${gput:-NA},${skint:-NA},$mem,$(cpu_max_khz),$tag"
}

case "${1:-}" in
  --header) echo "$HEADER"; exit 0 ;;
  --once)   one_line; exit 0 ;;
esac

INTERVAL="${1:-60}"
OUT="${2:-./thermal_${PHONE}_$(date +%Y%m%d_%H%M%S).csv}"
mkdir -p "$(dirname "$OUT")"
[ -s "$OUT" ] || echo "$HEADER" > "$OUT"
echo "Logging every ${INTERVAL}s to $OUT (Ctrl+C to stop)" >&2
trap 'exit 0' INT TERM
while :; do
  one_line >> "$OUT"
  sleep "$INTERVAL"
done

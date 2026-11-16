#!/usr/bin/env bash
# download_models.sh - downloads the GGUF files listed in models.txt (resumable, with a byte-size check)
# Usage: bash download_models.sh ladder        the 7 benchmarked models (~19.9 GB)
#        bash download_models.sh fit           the 3 load-limit models (~27.9 GB; they do NOT load on a 12 GB phone)
#        bash download_models.sh qwen3-4b      any short name containing "qwen3-4b"
#        bash download_models.sh               everything
#        MAX_GB=6 bash download_models.sh      skip files larger than 6 GB
# Destination: $MODELS_DIR (default: <repo>/models)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${MODELS_DIR:-$HERE/models}"; mkdir -p "$DEST"
FILTER="${1:-}"; MAX_GB="${MAX_GB:-99}"
grep -v '^#' "$HERE/models.txt" | grep '|' | while IFS='|' read -r name url size group; do
  case "$FILTER" in
    "") ;;
    ladder|fit) [ "$group" = "$FILTER" ] || continue ;;
    *) [[ "$name" == *"$FILTER"* ]] || continue ;;
  esac
  awk -v s="$size" -v m="$MAX_GB" 'BEGIN{exit !(s/1e9>m)}' && { echo "skip $name ($((size/1000000)) MB > ${MAX_GB} GB)"; continue; }
  f="$DEST/$(basename "$url")"
  have=$(wc -c < "$f" 2>/dev/null | tr -d ' ' || echo 0); have=${have:-0}
  if [ "$have" = "$size" ]; then echo "ok   $name"; continue; fi
  echo "downloading $name ($((size/1000000)) MB) -> $f"
  curl -L --fail -C - --retry 5 -o "$f" "$url" || { echo "ERROR $name"; continue; }
  have=$(wc -c < "$f" | tr -d ' '); [ "$have" = "$size" ] && echo "ok   $name" || echo "WRONG SIZE $name: $have != $size"
done
df -h "$DEST" | tail -1

#!/usr/bin/env bash
# setup_termux.sh - installs the Termux packages and builds llama.cpp at the benchmark commit (CPU backend only)
# Usage: bash setup_termux.sh                      (clones into ~/llama.cpp, checks out af6b5243d, builds build-nosme)
#        LLAMA_DIR=~/src/llama.cpp LLAMA_COMMIT=<hash> bash setup_termux.sh
# Requires Termux from F-Droid or GitHub (not the old Play Store build).
set -eu
LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
LLAMA_COMMIT="${LLAMA_COMMIT:-af6b5243d}"
pkg update -y && pkg upgrade -y
pkg install -y git cmake ninja clang make curl jq python nodejs termux-api
termux-setup-storage || true   # optional: gives ~/storage/downloads for the result packages

[ -d "$LLAMA_DIR/.git" ] || git clone https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
cd "$LLAMA_DIR"
git fetch --quiet origin
git checkout --quiet "$LLAMA_COMMIT"
echo "llama.cpp commit: $(git rev-parse --short=9 HEAD)"

# build-nosme: no KleidiAI, no SME, explicit armv8.6-a+dotprod+i8mm+fp16.
# On the Fold 8, KleidiAI with SME crashed/rebooted the phone; this build was stable and faster.
cmake -B build-nosme -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_OPENMP=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_OPENSSL=OFF \
  -DGGML_NATIVE=OFF -DGGML_CPU_KLEIDIAI=OFF -DGGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm+fp16
cmake --build build-nosme -j4 --target llama-bench llama-completion llama-cli llama-server
ls -lh build-nosme/bin/llama-server build-nosme/bin/llama-bench build-nosme/bin/llama-completion
echo "Done. Next step (in the benchmark repo): bash download_models.sh ladder"

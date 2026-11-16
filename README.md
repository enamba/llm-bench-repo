# Fold 8 on-device LLM benchmark (llama.cpp, CPU only)

Scripts, test data and result summaries from a benchmark of local LLMs on a **Samsung Galaxy Z Fold 8 with 12 GB of RAM**,
running [llama.cpp](https://github.com/ggml-org/llama.cpp) in Termux on the CPU only (no GPU, no NPU).

It measures three things for 7 models (Qwen3 1.7B / 4B-Instruct-2507 / Qwen3.5-4B / Qwen3 8B, several quantizations):

| Test | What it answers | Script |
|---|---|---|
| **Recall** ("needle in a haystack") | Does the model still find 10 facts in a long document, and what is lost when the context is too small? | `run_bench.sh func` -> `func_bench.py` |
| **Quality** (P01-P10) | How good are the answers to 10 fixed prompts (reasoning, code, summary, facts, format)? Thinking really OFF. | `run_quality_nothink.sh` -> `quality_nothink.py` |
| **Developer tasks** (21) | Can it write working shell / git / regex / SQL / Python / JS? Graded by executing the answers. | `run_bench.sh dev` -> `dev_bench.py` |
| **Load limit / speed** | Which models load at all, and how fast are prompt processing and generation? | `run_bench.sh fit / ctx / speed` (llama-bench) |

Article: [MEDIUM_LINK]

**Everything here is CPU-only.** The same scripts, models and method run on any machine without a GPU
(a laptop, a mini PC, a server) - see [Running on Linux or macOS](#3b-linux-or-macos-any-cpu-only-machine).

---

## Contents

1. [Headline results](#1-headline-results)
2. [What you need](#2-what-you-need)
3. [Install and build llama.cpp (af6b5243d)](#3-install-and-build-llamacpp-at-commit-af6b5243d)
4. [Get this repository](#4-get-this-repository)
5. [Download the models](#5-download-the-models)
6. [Self-tests (no model needed)](#6-self-tests-no-model-needed)
7. [Run the benchmark, step by step](#7-run-the-benchmark-step-by-step)
8. [Monitor a running benchmark](#8-monitor-a-running-benchmark)
9. [Resume after Termux is killed (FROM)](#9-resume-after-termux-is-killed-the-from-index)
10. [Collect the results](#10-collect-the-results)
11. [Regrade off-device, summarize and plot](#11-regrade-off-device-summarize-and-plot)
12. [What each test does](#12-what-each-test-does)
13. [Troubleshooting](#13-troubleshooting)
14. [Caveats](#14-caveats)
15. [Repository layout](#15-repository-layout)

---

## 1. Headline results

Device: Galaxy Z Fold 8, 12 GB RAM (11,085 MB visible to Android, 12 GB swap / "RAM Plus" on), SoC SM8850, 8 cores.
llama.cpp `af6b5243d`, CPU backend (`build-nosme`), 6 threads, temp 0, seed 42. One device, one run per configuration.

| Model (GGUF) | Size | Recall 8k (of 10) | Dev tasks passed (of 21, regraded) | Quality mean (1-5, LLM-assisted) | Decode t/s, median (run behind the score) |
|---|---:|---:|---:|---:|---:|
| Qwen3-1.7B Q4_K_M | 1.03 GiB | 10 | 8 | 3.6 (thinking off) | 35.69 |
| Qwen3-1.7B Q8_0 | 1.71 GiB | 10 | 11 | 4.2 (thinking off) | 27.42 |
| Qwen3-4B-Instruct-2507 Q4_0 | 2.21 GiB | 10 | 15 | 4.2 | 0.22 (first run, swapping) |
| Qwen3-4B-Instruct-2507 Q4_K_M | 2.33 GiB | 10 | 13 | 4.2 | 0.23 (first run, swapping) |
| Qwen3-4B-Instruct-2507 Q8_0 | 3.99 GiB | 10 | 16 | 4.5 | 0.23 (first run, swapping) |
| Qwen3.5-4B Q4_K_M | 2.55 GiB | 10 | 17 | 4.1 (thinking off) | 12.06 |
| Qwen3-8B Q4_K_M | 4.68 GiB | 10 | 14 | 4.7 (thinking off) | 0.23 (swapping) |

- **Every model recalled 10/10 facts when the document fit in the context.** With the same 16k-token document cut to
  8k / 4k / 2k, Qwen3-4B-Instruct Q4_K_M dropped to 5 / 2 / 1 of 10 and answered `NOT_FOUND` for the rest (it never
  invented a value). Qwen3-8B at the 4k cut answered `build_rack=Porto` (expected `R-17`), the only wrong answer in the test.
- **Thinking mode was the biggest single factor in the quality scores.** With thinking on, the same models scored
  2.0-3.5 (answers cut off inside `<think>`); with thinking really off they scored 3.6-4.7.
- **Swap is what kills speed.** Qwen3-8B decoded at a median 6.81 tokens/s in the first quality run and 0.23 tokens/s in
  the thinking-off re-run, when the phone was swapping.
  `Qwen3-8B-Q5_K_M` got Termux killed every time; `gemma-3-12b-it-Q4_K_M` did not load (12B+ does not fit).
- q8_0 KV cache saved memory but made prefill 2.34x slower (4B Q4_0, 16k).

Full tables: `results/summary/` - charts: `results/charts/` - recommendations: `results/summary/decision_table.md`.

---

## 2. What you need

**Option A - Android phone (what was used):**
- Termux **from F-Droid** (or the Termux GitHub releases). The Google Play build is outdated - do not use it.
- Termux:API app from the same source (optional: battery temperature for the cooldown logic and `log_thermal.sh`).
- Free storage: ~20 GB for the 7 models (+28 GB if you also want the 3 load-limit models), plus ~2 GB for the build.
- Ideally a charger, a cool place, and a phone you do not need for a day: the full chain took ~2 days on the 12 GB phone.

**Option B - any Linux or macOS machine, CPU only:**
- git, cmake (3.14+), a C/C++ compiler (gcc/clang), ninja or make, curl, python3 (3.9+), bash 4+ (macOS: `brew install bash`).
- Optional: nodejs (JavaScript dev tasks; without it they are `not_run`), git (git dev tasks), jq.
- For the charts: `pip install matplotlib`.

---

## 3. Install and build llama.cpp at commit af6b5243d

All results use llama.cpp **`af6b5243d`** (2026-11-11). Newer commits may behave differently, especially around
`--reasoning` / `enable_thinking`.

### 3a. Termux (Android)

Open Termux and run, in this order:

```bash
pkg update -y && pkg upgrade -y
pkg install -y git
git clone https://github.com/<your-user>/fold8-llm-bench.git ~/fold8-llm-bench   # or copy the folder, see section 4
cd ~/fold8-llm-bench
bash setup_termux.sh
```

`setup_termux.sh` installs `git cmake ninja clang make curl jq python nodejs termux-api`, clones llama.cpp into
`~/llama.cpp`, checks out `af6b5243d` and builds `llama-bench llama-completion llama-cli llama-server` into
`~/llama.cpp/build-nosme/bin`. The same thing by hand:

```bash
pkg install -y git cmake ninja clang make curl jq python nodejs termux-api
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp && git checkout af6b5243d
cmake -B build-nosme -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_OPENMP=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_OPENSSL=OFF \
  -DGGML_NATIVE=OFF -DGGML_CPU_KLEIDIAI=OFF -DGGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm+fp16
cmake --build build-nosme -j4 --target llama-bench llama-completion llama-cli llama-server
ls -lh build-nosme/bin/llama-server     # must exist
```

**Why `build-nosme`:** on the Fold 8, llama.cpp's KleidiAI path with SME crashed (and once rebooted) the phone.
This build disables KleidiAI and SME and sets the ARM features explicitly; the scripts also export
`GGML_KLEIDIAI_SME=0`. With `BACKEND=cpu` (the default) the scripts look for the binaries in
`~/llama.cpp/build-nosme/bin`; override with `LLAMA_DIR=/path/to/llama.cpp` or `BIN=/path/to/bin`.

Then, once:

```bash
termux-setup-storage        # optional: lets the scripts drop result packages in ~/storage/downloads
termux-wake-lock            # the scripts also call it; keeps the CPU awake with the screen off
```

### 3b. Linux or macOS (any CPU-only machine)

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp && git checkout af6b5243d
cmake -B build-cpu -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_TESTS=OFF -DLLAMA_OPENSSL=OFF   # macOS: add -DGGML_METAL=OFF
cmake --build build-cpu -j --target llama-bench llama-completion llama-server
export BIN=~/llama.cpp/build-cpu/bin     # tell the scripts where the binaries are (they default to build-nosme)
```

The whole pipeline (`run_quality_nothink.sh`, `run_bench.sh func|dev|speed|fit`, regrade, summaries) was smoke-tested this
way on an x86_64 Linux box (CPU only, small stand-in model) before release. Everything else in this README is identical; skip the `termux-*` commands (the scripts skip them automatically when
they are missing). Battery/thermal columns will read `NA` on a desktop, which simply disables the temperature-based
cooldown wait (the fixed minimum pause still applies). Set `THREADS` to your physical core count.

---

## 4. Get this repository

```bash
git clone https://github.com/<your-user>/fold8-llm-bench.git ~/fold8-llm-bench
cd ~/fold8-llm-bench
chmod +x *.sh
```

No git on the phone? Download the zip on the phone, then (`pkg install unzip` first):
`cd ~ && unzip ~/storage/downloads/fold8-llm-bench-repo.zip && mv fold8-llm-bench-repo fold8-llm-bench`.

All commands below are run **from the repository folder**. Defaults (all overridable with environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `PHONE` | `fold8-12gb` (required by `run_bench.sh`) | label; also the results sub-folder |
| `MODELS_DIR` | `<repo>/models` | where the `.gguf` files are |
| `OUTDIR` | `<repo>/results/raw/$PHONE` | raw results (git-ignored) |
| `LLAMA_DIR` / `BIN` | `~/llama.cpp` / `$LLAMA_DIR/build-nosme/bin` | llama.cpp binaries |
| `THREADS` | `6` in the 12 GB scripts (`8,6` in `run_bench.sh`) | CPU threads |
| `PORT` | `8090` | local llama-server port (127.0.0.1 only) |

---

## 5. Download the models

```bash
bash download_models.sh ladder     # 7 models, ~19.9 GB, resumable, size-checked
bash download_models.sh fit        # optional, ~27.9 GB: gemma-3-12b, Qwen3-14B, gpt-oss-20b (load-limit test only)
ls -lh models/
```

Expected: `ok   <name>` for each file. Exact repos, file names, `hf download` commands and sizes: `models/README.md`.

---

## 6. Self-tests (no model needed)

```bash
bash -n run_bench.sh run_quality_12gb.sh run_quality_nothink.sh   # shell syntax, prints nothing if OK
bash run_quality_nothink.sh --smoke     # ends with: SMOKE OK
python3 func_bench.py selftest          # ends with: selftest OK
python3 dev_bench.py selftest           # ends with: selftest: PASSED   (see Troubleshooting if shell tasks fail)
PHONE=fold8-12gb ./run_bench.sh info    # versions, CPU features, memory, llama.cpp devices
```

`run_bench.sh info` writes `results/raw/fold8-12gb/info_<run>.txt`. Check that `llama-bench --version` shows `af6b5243d`.

---

## 7. Run the benchmark, step by step

Plug in the charger, close other apps, and start the chains with `nohup` so they survive closing the Termux window.
Durations below are what the 12 GB Fold 8 measured (swap made the larger models much slower); a desktop will be faster.

### Step 7.1 - the main chain: recall, quality (first run), dev tasks, load limit

```bash
cd ~/fold8-llm-bench
nohup ./run_quality_12gb.sh > chain.out 2>&1 &
```

What it runs, in order (each step is `./run_bench.sh <mode> ...` with these settings):

| # | Step | Command it runs | Measured on the 12 GB phone |
|---|---|---|---|
| 1 | Context loss: one 16k document, cut to 8k/4k/2k | `FUNC_GRID="16k:16384:f16 16k:8192:f16 16k:4096:f16 16k:2048:f16" run_bench.sh func Qwen3-4B-Instruct-2507-Q4_K_M.gguf`, then the 8B with `8k:8192 16k:8192 16k:4096 16k:2048` | ~40-80 min for the 4B, ~1 h for the 8B |
| 2 | Recall at 8k for all 7 models | `FUNC_GRID="8k:8192:f16" run_bench.sh func <ladder>` | ~2 h |
| 3 | Quality P01-P10, first run (**llama-completion, see note**) | `run_bench.sh quality <ladder>` | ~23 h |
| 4 | Developer tasks, 8k context | `DEV_MODELS="<each model>:8192" run_bench.sh dev` | ~11 h |
| 5 | Load limit (may get Termux killed) | `run_bench.sh fit gemma-3-12b-it-Q4_K_M.gguf Qwen3-14B-Q4_K_M.gguf gpt-oss-20b-Q4_K_M.gguf` | gemma-3-12b killed Termux while loading |

It ends with `PACKAGE READY in <folder>` (a `.tgz` of `results/raw/fold8-12gb`). If step 5 kills Termux, steps 1-4 are
already complete on disk - that is expected on a 12 GB phone.

> **Note on step 3 (deprecated path).** `run_bench.sh quality` uses `llama-completion`. At `af6b5243d`,
> llama-completion **ignores `--reasoning off` / `LLAMA_ARG_REASONING=off`** (tools/completion/completion.cpp never
> passes `enable_thinking` to the chat template, whose default is true), so Qwen3 and Qwen3.5 still think and often run out
> of tokens inside `<think>`. Its answers are only valid for the non-thinking **Qwen3-4B-Instruct-2507** models.
> For the thinking models, use step 7.2. (You can save ~20 h by running step 3 only for the Instruct models:
> `PHONE=fold8-12gb ./run_bench.sh quality Qwen3-4B-Instruct-2507-Q4_0.gguf Qwen3-4B-Instruct-2507-Q4_K_M.gguf Qwen3-4B-Instruct-2507-Q8_0.gguf`.)

### Step 7.2 - quality with thinking really off (the published scores for the thinking models)

Run it after 7.1 has finished (never two benchmarks at the same time):

```bash
FROM=1 nohup ./run_quality_nothink.sh > quality_nothink.out 2>&1 &
```

It starts `llama-server` per model with `--jinja --reasoning off --chat-template-kwargs '{"enable_thinking":false}'`,
sends `chat_template_kwargs.enable_thinking=false` in every request and appends `/no_think` to each prompt
(disable that with `NO_THINK_SUFFIX=0`). Settings: ctx 8192, n_predict 2048, temp 0, seed 42.
Models, in this order (this is the `FROM` index): 1 Qwen3-1.7B-Q4_K_M, 2 Qwen3-1.7B-Q8_0, 3 Qwen3.5-4B-Q4_K_M, 4 Qwen3-8B-Q4_K_M.

Measured: ~3 min per 1.7B model, ~25 min for Qwen3.5-4B, ~2 h 17 min for Qwen3-8B (0.23 t/s, swapping); 2 h 48 min total.
Each answer is flagged `OK`, `THINK_BUT_HAS_FINAL`, `THINK_NO_FINAL` or `EMPTY`; the published run had 40/40 `OK`.
It ends with `PACKAGE READY: <path>/fold8-12gb-nothink_<date>.tgz`.

### Step 7.3 (optional) - raw speed and context sweeps with llama-bench

```bash
PHONE=fold8-12gb THREADS=6 ./run_bench.sh speed Qwen3-4B-Instruct-2507-Q4_K_M.gguf     # pp512 + tg128, REPS=5
PHONE=fold8-12gb THREADS=6 ./run_bench.sh ctx   Qwen3-4B-Instruct-2507-Q4_0.gguf       # depth 2k..32k until OOM
PHONE=fold8-12gb THREADS=6 ./run_bench.sh fit   Qwen3-8B-Q4_K_M.gguf gemma-3-12b-it-Q4_K_M.gguf
```

Outputs: `results/raw/fold8-12gb/{speed,ctx,fit}_cpu_<run>.csv`. Each mode waits for the battery to cool to
`COOL_TEMP=34` °C between models (min `COOL_MIN=60` s, max `COOL_MAX=600` s).

### Running a single test by hand

```bash
# recall for one model and your own grid (DOC:CTX:KV; CTX < DOC truncates the start of the document)
PHONE=fold8-12gb THREADS=6 FUNC_GRID="8k:8192:f16 16k:16384:q8_0" ./run_bench.sh func Qwen3-1.7B-Q8_0.gguf
# dev tasks for one model at 8k
PHONE=fold8-12gb THREADS=6 DEV_MODELS="Qwen3.5-4B-Q4_K_M.gguf:8192" ./run_bench.sh dev
# thinking-off quality for the last two models only
FROM=3 ./run_quality_nothink.sh
```

---

## 8. Monitor a running benchmark

From a second Termux session (swipe from the left edge -> NEW SESSION) or after reopening Termux:

```bash
cd ~/fold8-llm-bench
R=results/raw/fold8-12gb
tail -f chain.out                                   # live log of step 7.1 (Ctrl+C stops watching, not the run)
grep '^===' $R/quality_chain_*.log                  # which steps started / ended (END rc=0 = finished)
tail -n 3 $R/*_progress_*.txt                       # STARTING,<model>,<spec> without an END line = running or killed
tail -f quality_nothink.out                         # live log of step 7.2: one "[Pnn] rc=0 flag=OK ..." line per prompt
grep '^===' $R/quality_nothink_chain_*.log
pgrep -af llama-server                              # is a model loaded right now?
free -m                                             # RAM and swap; heavy swap = expect ~0.2 tokens/s
grep -h 'PACKAGE READY' chain.out quality_nothink.out   # printed once a chain is completely done
```

A run is finished when its log prints `PACKAGE READY` and `pgrep -af run_` shows nothing.
If `pgrep -af run_` is empty but there is no `PACKAGE READY`, Android killed it: see the next section.

---

## 9. Resume after Termux is killed (the FROM index)

Look at the last `START` line without a matching `END` (`grep '^===' results/raw/fold8-12gb/quality_chain_*.log | tail -4`)
and restart with the matching `FROM`. Finished steps keep their files; a resumed step writes a new run id next to them.

**`run_quality_12gb.sh` (step 7.1)** - FROM is a step number:

| FROM | Runs |
|---|---|
| `1` (default) | everything: steps 1, 2, 3, 4, 5 |
| `15` | only the remaining Qwen3-8B 16k cuts (`16k:4096`, `16k:2048`), then steps 2-5 |
| `2` | steps 2 (recall at 8k for the ladder), 3, 4, 5 |
| `3` | steps 3 (quality), 4, 5 |
| `4` | steps 4 (dev tasks), 5 |
| `5` | only step 5 (load limit) |

```bash
FROM=4 nohup ./run_quality_12gb.sh > chain.out 2>&1 &
```

**`run_quality_nothink.sh` (step 7.2)** - FROM is a 1-based model index:

| FROM | First model run |
|---|---|
| `1` | Qwen3-1.7B-Q4_K_M (all 4) |
| `2` | Qwen3-1.7B-Q8_0 |
| `3` | Qwen3.5-4B-Q4_K_M |
| `4` | Qwen3-8B-Q4_K_M (last one only) |

```bash
FROM=4 nohup ./run_quality_nothink.sh > quality_nothink.out 2>&1 &
```

A model that keeps getting killed does not fit: remove it from the `LADDER=` line of the script (this is how
`Qwen3-8B-Q5_K_M` was dropped: `sed -i 's/ *Qwen3-8B-Q5_K_M.gguf//' run_quality_12gb.sh`).

---

## 10. Collect the results

Everything is under `results/raw/fold8-12gb/` (git-ignored). The chains also pack it:

- step 7.1: `fold8-12gb-quality_<date>.tgz` in `~/storage/downloads` (Termux) or in `results/raw/`
- step 7.2: `fold8-12gb-nothink_<date>.tgz` in the same place

Copy the `.tgz` files to the computer where you will analyse them (Android: Downloads folder via USB, or any file share).
On that computer, from a clone of this repository:

```bash
mkdir -p results/raw && tar xzf fold8-12gb-quality_<date>.tgz -C results/raw
tar xzf fold8-12gb-nothink_<date>.tgz -C results/raw     # -> results/raw/fold8-12gb-nothink/
```

Main raw files: `func_cpu_*.csv` + `func_raw_*/` (recall answers), `dev_cpu_*.jsonl` (dev answers),
`quality_<run>/` (first-run answers), `fold8-12gb-nothink/quality_nothink_<run>/*.txt|json` (thinking-off answers),
`quality_nothink_perf_*.csv` (speed, flags), `fit_cpu_*.csv`, `ctx_cpu_*.csv`, `thermal_*.csv`, `server_*.log`.

---

## 11. Regrade off-device, summarize and plot

Run these on Linux/macOS (needs bash, git, grep, find, sort, sed, awk, python3; nodejs for the JS tasks):

```bash
R=results/raw/fold8-12gb
S=results/summary

# a) dev tasks: re-run the official graders on the saved answers (fixes the on-phone rc=126 shell grades)
python3 dev_bench.py selftest                                   # must say: selftest: PASSED
python3 dev_bench.py regrade $R/dev_cpu_*.jsonl --out $S/dev_regrade.csv

# b) recall: one row per run (KILLED rows come from progress files with no END)
python3 func_bench.py report $R > $S/func_report.csv

# c) thinking-off quality speed + flags
cp results/raw/fold8-12gb-nothink/quality_nothink_perf_*.csv results/raw/fold8-12gb-nothink/quality_nothink_flags_*.csv $S/

# d) summary tables (func_summary, dev_pass_rates, dev_pass_matrix, quality_nothink_speed, quality_score_by_model)
python3 analysis/summarize.py all
python3 analysis/summarize.py bench --raw $R                    # llama-bench fit/ctx/speed -> llama_bench_summary.csv

# e) charts (pip install matplotlib)
python3 analysis/plot_charts.py                                 # -> results/charts_regen/*.png + .pdf
```

**Quality grades are not computed by code.** Each P01-P10 answer was given a 1-5 score with the rubric in
`quality_prompts.md` (LLM-assisted, same rubric for every model) and stored in `results/summary/quality_grades.csv`
(first run) and `quality_grades_nothink.csv` (thinking-off run); `quality_grades_merged.csv` puts both side by side, with
`score_primary` = thinking-off score where it exists, otherwise the first-run score. To grade your own run, fill in the
same CSV columns (by hand or blind, by renaming answers to random codes) and run `python3 analysis/summarize.py scores`.

The PNGs in `results/charts/` are the ones used in the article; `plot_charts.py` redraws the same data
(layout and annotations differ slightly).

---

## 12. What each test does

**Recall (`func_bench.py`).** A synthetic engineering log ("Project Kestrel", `scenarios/recall/hay_{2k..32k}.txt`)
hides the same 10 facts (`needles.json`, e.g. `staging_db_port=6431`, `build_rack=R-17`) at ~5 %, 15 % ... 95 % of the text.
The model must return all 10 as `key=value` lines, or `key=NOT_FOUND`. A spec `DOC:CTX:KV` runs a `DOC`-sized document in a
`CTX`-token window with a `KV` cache type; when CTX < DOC the START of the document is dropped, so early facts are gone.
Each fact is scored correct / not_found / wrong / missing; also prefill & decode t/s, TTFT and wall time.

**Quality (`run_quality_nothink.sh`, `quality_nothink.py`).** 10 fixed prompts (`quality_prompts.md`): reasoning
(time arithmetic, the "17 sheep" trick), code (fix/write a function), summarisation, factual (e.g. the longest river that flows
through Portugal), and strict-format output (JSON). Thinking is forced off three ways (server flag, per-request kwarg, `/no_think`)
and every answer is checked for leftover `<think>` blocks.

**Developer tasks (`dev_bench.py`).** 21 tasks in `scenarios/dev_questions.json`: shell and git one-liners executed in a
throw-away sandbox with fixture files, Termux, regexes tested against examples, error explanations (keywords), bug fixes
and small functions run against hidden tests (Python, Node), a SQLite query, and questions about a ~4.5k-token source file.
A guard blocks dangerous commands (rm, curl, sudo, absolute paths...). `DEV_EXEC=0` only extracts, never executes.

**Load limit / speed (`run_bench.sh fit|ctx|speed`, llama-bench).** `fit` tries pp64+tg16 with `-lm none` (no mmap), so a
model that cannot be held in RAM fails instead of paging; `ctx` grows the KV depth until it fails; `speed` is pp512+tg128.

---

## 13. Troubleshooting

**Thinking is not off (answers start with `<think>`, or `THINK_NO_FINAL` flags).**
- Use `run_quality_nothink.sh` (llama-server), not `run_bench.sh quality` (llama-completion ignores `--reasoning off` at af6b5243d).
- Check your build has the flag: `$BIN/llama-server --help | grep -- '--reasoning'`.
- Check the results: `grep -c ',OK,' results/raw/fold8-12gb/quality_nothink_flags_*.csv` (expect 40) and
  `grep -l '<think>' results/raw/fold8-12gb/quality_nothink_*/*.txt` (expect nothing).
- The script warns at the end: `WARNING: at least one answer is THINK_NO_FINAL or EMPTY - thinking may still be on`.

**Out of memory / swap (decode drops to ~0.2 t/s, `server did not start`, `FAILED` rows, `KILLED (Termux closed)`).**
- `free -m` before a run; close other apps; reboot the phone before long runs.
- Use a smaller quant (Q4_0 / Q4_K_M) or a smaller context (`N_CTX=4096 ./run_quality_nothink.sh`, `FUNC_GRID=...:4096:...`).
- `q8_0` KV (`...:q8_0` in `FUNC_GRID`) roughly halves the KV cache but measured 2.34x slower prefill.
- On a 12 GB phone, 8B Q4_K_M is the practical ceiling, 8B Q5_K_M got Termux killed, 12B+ did not load.

**Termux gets killed during a run.**
- Keep `termux-wake-lock` (the scripts call it) and the charger connected.
- Android settings -> Apps -> Termux -> Battery -> Unrestricted.
- Android 12+ "phantom process killer": in Developer options enable *Disable child process restrictions*
  (Android 14+), or from a computer: `adb shell "/system/bin/device_config put activity_manager max_phantom_processes 2147483647"`.
- Then resume with `FROM` (section 9). If the same model is always the one being loaded when Termux dies, it does not fit.

**`Permission denied` (rc=126) for grep/find/sort/git in the dev shell tasks on the phone.**
On the Fold 8, every shell/git grader returned `rc=126 ... Permission denied` inside the sandbox (the minimal environment
used to execute model commands), so the on-phone dev grades for those tasks are invalid. The cause was not investigated;
the fix used for the published numbers is to regrade off-device: `python3 dev_bench.py regrade <dev_cpu_*.jsonl>`
(section 11). Always run `python3 dev_bench.py selftest` first on the machine that grades: it must say `selftest: PASSED`.

**`Cannot find .../llama-server (set BIN=...)`.** Build the `llama-server` target, or point `BIN` at your build
(`export BIN=~/llama.cpp/build-cpu/bin` on Linux/macOS).

**`llama-server` aborts at start-up with `free(): invalid pointer` (Linux).** Seen once on an x86_64 box with a
non-standard clang toolchain and shared libraries; a static build fixed it: add `-DBUILD_SHARED_LIBS=OFF` to the cmake line.

**`missing <file>.gguf`.** The file name must match exactly and be in `models/` (or `MODELS_DIR`). Run `bash download_models.sh ladder`.

**macOS: `mapfile: command not found` / `declare -A` errors.** macOS ships bash 3.2: `brew install bash` and run the
scripts with `/opt/homebrew/bin/bash ./run_quality_nothink.sh` (or `/usr/local/bin/bash` on Intel Macs).

**Heat / thermal throttling.**
- The scripts wait between models until the battery is at or below `COOL_TEMP` (default 34 °C); raise `COOL_MAX` to wait longer.
- Remove the case, keep the phone unfolded and flat on a hard surface, out of the sun, ideally in front of a fan.
- The published runs were done with the phone kept cool (battery logged as low as 7.9 °C). If you cool a phone below room
  temperature, avoid condensation (let it warm up slowly in a closed bag) - at your own risk.
- `./log_thermal.sh 60 thermal.csv` logs battery %, temperature, memory and swap every 60 s (Termux:API or adb).

---

## 14. Caveats

- **One device, one run per configuration** (recall specs were repeated 1-9 times, shown as median/min/max). Treat small
  differences as noise.
- **Quality scores are LLM-assisted**, not a human blind grade; the blind packets were prepared but the human pass was not done.
- **Swap dominates speed** on a 12 GB phone; decode speeds for the 4B-Instruct and 8B models mostly reflect memory pressure,
  not the CPU. Expect very different numbers on a machine with enough RAM.
- The thinking-on (first-run) quality scores are kept for comparison but are depressed by the llama-completion limitation
  described above; the Qwen3-4B-Instruct-2507 models were not re-run because they have no thinking channel.
- `scenarios/` content is synthetic and fictional (names, numbers, companies).
- Battery/temperature readings come from Android's battery sensor via Termux:API or adb, not from the CPU die.

---

## 15. Repository layout

```
run_bench.sh              modes: info, speed, fit, ctx (llama-bench), func (recall), dev (developer tasks), quality (deprecated)
run_quality_12gb.sh       unattended 12 GB chain (steps 1-5), FROM resume
run_quality_nothink.sh    P01-P10 with thinking really off (llama-server), FROM resume, --smoke self-test
quality_nothink.py        prompt extraction, /no_think wrapping, chat request, <think> flagging, selftest
func_bench.py             recall test: haystack generator (gen), runner, scorer, report, selftest
dev_bench.py              developer tasks: runner, sandboxed graders, selftest, summary, blind pack, regrade
log_thermal.sh            battery / temperature / memory logger (used for the cooldown waits)
setup_termux.sh           Termux packages + llama.cpp af6b5243d build-nosme
download_models.sh        resumable, size-checked GGUF downloader (models.txt)
models.txt                the 10 GGUF files (7 ladder + 3 load-limit) with URLs and byte sizes
quality_prompts.md        P01-P10 + the 1-5 rubric
scenarios/                recall haystacks + needles, dev tasks + fixture source file
analysis/summarize.py     raw CSV/JSONL -> results/summary tables
analysis/plot_charts.py   results/summary -> charts
results/summary/          small CSV summaries + decision_table.md (the published numbers)
results/charts/           the published charts (PNG + PDF)
models/README.md          where to get the models (the .gguf files are git-ignored)
```

## License

MIT - see [LICENSE](LICENSE). Model weights are under their own licenses (see each Hugging Face model card).

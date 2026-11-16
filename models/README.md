# Models

The GGUF files are NOT in this repository (they are 1-12 GB each). Put them in this folder (`models/`), or anywhere
else and set `MODELS_DIR=/path/to/folder`. The scripts expect the exact file names below.

## Option A - the bundled downloader (works on Termux, Linux and macOS; needs only curl)

```bash
bash download_models.sh ladder   # the 7 benchmarked models, ~19.9 GB
bash download_models.sh fit      # optional: the 3 load-limit models, ~27.9 GB (they did NOT load on the 12 GB phone)
```
It resumes interrupted downloads and checks every file's byte size against `models.txt`.

## Option B - Hugging Face CLI (desktop / server)

```bash
pip install -U huggingface_hub          # provides the `hf` command
cd models
# the 7 benchmarked models ("ladder")
hf download unsloth/Qwen3-1.7B-GGUF                Qwen3-1.7B-Q4_K_M.gguf               --local-dir .
hf download Qwen/Qwen3-1.7B-GGUF                   Qwen3-1.7B-Q8_0.gguf                 --local-dir .
hf download unsloth/Qwen3-4B-Instruct-2507-GGUF    Qwen3-4B-Instruct-2507-Q4_0.gguf     --local-dir .
hf download unsloth/Qwen3-4B-Instruct-2507-GGUF    Qwen3-4B-Instruct-2507-Q4_K_M.gguf   --local-dir .
hf download unsloth/Qwen3-4B-Instruct-2507-GGUF    Qwen3-4B-Instruct-2507-Q8_0.gguf     --local-dir .
hf download unsloth/Qwen3.5-4B-GGUF                Qwen3.5-4B-Q4_K_M.gguf               --local-dir .
hf download Qwen/Qwen3-8B-GGUF                     Qwen3-8B-Q4_K_M.gguf                 --local-dir .
# optional: load-limit test ("fit")
hf download unsloth/gemma-3-12b-it-GGUF            gemma-3-12b-it-Q4_K_M.gguf           --local-dir .
hf download Qwen/Qwen3-14B-GGUF                    Qwen3-14B-Q4_K_M.gguf                --local-dir .
hf download unsloth/gpt-oss-20b-GGUF               gpt-oss-20b-Q4_K_M.gguf              --local-dir .
```

## Option C - plain curl (one file)

```bash
curl -L --fail -C - -o models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
```

## The models

| File | Hugging Face repo | Bytes | Thinking? | Role |
|---|---|---:|---|---|
| Qwen3-1.7B-Q4_K_M.gguf | unsloth/Qwen3-1.7B-GGUF | 1,107,409,472 | yes (hybrid) | ladder |
| Qwen3-1.7B-Q8_0.gguf | Qwen/Qwen3-1.7B-GGUF | 1,834,426,016 | yes (hybrid) | ladder |
| Qwen3-4B-Instruct-2507-Q4_0.gguf | unsloth/Qwen3-4B-Instruct-2507-GGUF | 2,375,773,280 | no | ladder |
| Qwen3-4B-Instruct-2507-Q4_K_M.gguf | unsloth/Qwen3-4B-Instruct-2507-GGUF | 2,497,281,120 | no | ladder |
| Qwen3-4B-Instruct-2507-Q8_0.gguf | unsloth/Qwen3-4B-Instruct-2507-GGUF | 4,280,405,600 | no | ladder |
| Qwen3.5-4B-Q4_K_M.gguf | unsloth/Qwen3.5-4B-GGUF | 2,740,937,888 | yes (default on) | ladder |
| Qwen3-8B-Q4_K_M.gguf | Qwen/Qwen3-8B-GGUF | 5,027,783,488 | yes (hybrid) | ladder |
| gemma-3-12b-it-Q4_K_M.gguf | unsloth/gemma-3-12b-it-GGUF | 7,300,778,336 | no | fit (did not load) |
| Qwen3-14B-Q4_K_M.gguf | Qwen/Qwen3-14B-GGUF | 9,001,752,960 | yes | fit (not reached) |
| gpt-oss-20b-Q4_K_M.gguf | unsloth/gpt-oss-20b-GGUF | 11,624,759,488 | yes | fit (not reached) |

Sizes were checked against Hugging Face on 2026-11-16.
`Qwen3-8B-Q5_K_M.gguf` was in the original ladder but was removed: Android killed Termux every time it loaded on the 12 GB phone.

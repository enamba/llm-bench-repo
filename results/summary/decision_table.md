# Decision table — on-device LLMs on Galaxy Z Fold 8 (12 GB)

Draft for the article. Numbers below are measured on this phone (llama.cpp af6b5243d, 6 threads, CPU). Quality scores are **LLM-assisted** (rubric 1–5); they are not a human blind grade. The first quality run left thinking on (despite `LLAMA_ARG_REASONING=off`). A later re-run of P01–P10 on the four thinking models (llama-server `--reasoning off` + `enable_thinking=false` + a `/no_think` suffix, `n_predict=2048`) turned thinking off: **40/40 answers flagged OK**, `has_think=0`, `unclosed=0`, `final_empty=0`. Thinking-on scores are kept as a separate column. 4B-Instruct-2507 was not re-run (it has no thinking channel). Decode speed is bimodal because of swap: always check both the median and the best observed value.

Nothing here is a recommendation to publish; it is a decision aid from this run only.

## Use case → what to pick

| Use case | Pick | Why (measured) | Watch out |
|---|---|---|---|
| Everyday Q&A, summaries, short reasoning on 12 GB | **Qwen3-4B-Instruct-2507 Q4_K_M** (or Q4_0) at 8k context, f16 KV | Quality mean 4.20/5 (42/50) and every prompt finished; no `<think>` channel, so no special server flags are needed. Func recall 10/10 whenever the document fits. File size 2.33 GiB (Q4_K_M) or 2.21 GiB (Q4_0). Q4_0 and Q4_K_M tied on quality. | Mid-run decode in the first quality run sat at 0.22–0.23 t/s under swap. P08 (Portuguese rivers) was weak for every 4B quant. |
| Fast everyday answers, small file | **Qwen3-1.7B Q8_0 with thinking off** | Thinking-off quality 4.20/5 (42/50) — tied with 4B-Instruct Q4. Decode in the re-run: median 27.42 t/s (25.02–33.36). Prefill median 291.24 t/s (139.14–418.75). Whole 10-prompt block wall 61.9 s. File 1.71 GiB. | Q4_K_M of the same family is weaker on the hard prompts (mean 3.60; P01 arrived at 07:50, P03 concluded 8 sheep). Dev is still modest (11/21 Q8, 8/21 Q4). P08 remains wrong (Douro as the longest river). |
| Highest quality in this set, if you can wait | **Qwen3-8B Q4_K_M at 8k, thinking off** | Thinking-off quality **4.70/5 (47/50)** — the best mean here, ahead of 4B-Instruct Q8_0 at 4.50. Eight prompts scored 5; P05 was 4 (du -h pipeline with a path-breaking awk); P08 was 3 (Tagus correct, Douro source wrong). | This entire re-run sat at 0.22–0.23 t/s (10/10 prompts). Wall 7938.1 s for 1779 generated tokens; P05 alone 2679.3 s; load 22 s. In the first quality run, the same 8B often decoded at 6.81 median / 7.15 max t/s when RAM was freer. Termux was killed on 8B Q5 at 8k and on 8B Q4 16k full. Not a set-and-forget model on 12 GB. |
| Slightly better answers than Q4 Instruct, still on 12 GB, no think flags | **Qwen3-4B-Instruct-2507 Q8_0**, 8k context | Quality mean 4.50/5 (45/50). Dev regrade 16/21 (76%), ahead of Q4_K_M (13/21) and just behind Qwen3.5. Clean P05 script; finished every quality prompt with no thinking channel. | File is 3.99 GiB. Func was only run at 8k (10/10), not at 16k. Leaves less RAM for a long KV cache. Decode median in the quality run was still 0.23 t/s (max 0.40). |
| Coding / shell / git assistant | **Qwen3.5-4B Q4_K_M with thinking off** | Led the 21-task dev suite after regrade: **17/21 (81%)**. With thinking actually off, quality rose from 2.00 (20/50) to **4.10 (41/50)**. Free-RAM decode on P01–P08 was 10.95–13.65 t/s. | P04 still raises TypeError (`num > None`). P09–P10 in this re-run dropped to 0.18 and 0.17 t/s (prefill 4.34 and 5.29 t/s; walls 255.7 s and 945.9 s) before the 8B block started — swap, not a model-quality failure. The llama-completion env flag alone does **not** turn thinking off in this commit. |
| Long-document lookup (facts must stay in context) | **4B-Instruct Q4_K_M (or Q4_0) with a context ≥ the document** | 16k document at 16k context: 10/10 on every 4B Q4_K_M repeat (n=5) and on the one 4B Q4_0 run. When you cut to 8k / 4k / 2k, recall falls to 5/10, 2/10, 1/10 — exactly the number of facts still in the window. 4B never invented a dropped fact (`not_found` only). | 8B cannot hold a 16k context on this phone (Termux killed). At a 4k cut, 8B got one *kept* fact wrong (`build_rack=Porto` vs `R-17`). Prefill of a 16k prompt on 4B Q4_K_M had a median TTFT of 1115 s (range 1080–1532 s). |
| 12B or larger (Gemma 12B Q4, Qwen3 14B Q4, gpt-oss 20B Q4) | **Do not use on this 12 GB phone** | Fit died while loading `gemma-3-12b-it-Q4_K_M` (11 Nov and 15 Nov). The two larger files were not started. | This is a hard ceiling for unattended CPU runs in Termux, not just a slow-down. |

## Quality means (LLM-assisted, /50)

| Model | Thinking on (first run, n=1024) | Thinking off (re-run, n=2048) | Primary |
|---|---:|---:|---:|
| Qwen3-1.7B-Q4_K_M | 3.10 (31/50) | 3.60 (36/50) | 3.60 |
| Qwen3-1.7B-Q8_0 | 2.60 (26/50) | 4.20 (42/50) | 4.20 |
| Qwen3-4B-Instruct-2507-Q4_0 | 4.20 (42/50) | — (not re-run) | 4.20 |
| Qwen3-4B-Instruct-2507-Q4_K_M | 4.20 (42/50) | — (not re-run) | 4.20 |
| Qwen3-4B-Instruct-2507-Q8_0 | 4.50 (45/50) | — (not re-run) | 4.50 |
| Qwen3.5-4B-Q4_K_M | 2.00 (20/50) | 4.10 (41/50) | 4.10 |
| Qwen3-8B-Q4_K_M | 3.50 (35/50) | 4.70 (47/50) | 4.70 |

## Thinking-off re-run speeds (measured, quality_nothink_perf_20261116_002005)

| Model | Load | Decode t/s (med / min–max) | Prefill t/s (med / min–max) | Med. TTFT | Wall (10 prompts) |
|---|---:|---|---|---:|---:|
| 1.7B Q4_K_M | 10 s | 35.69 / 19.73–45.35 | 179.92 / 20.46–272.32 | 478.8 ms | 61.7 s |
| 1.7B Q8_0 | 4 s | 27.42 / 25.02–33.36 | 291.24 / 139.14–418.75 | 249.1 ms | 61.9 s |
| Qwen3.5-4B Q4_K_M | 4 s | 12.06 / 0.17–13.65 | 44.33 / 4.34–93.78 | 1738.3 ms | 1318.7 s |
| 8B Q4_K_M | 22 s | 0.23 / 0.22–0.23 | 7.14 / 6.15–12.28 | 13350.8 ms | 7938.1 s |

Run window (Europe/Lisbon): 2026-11-16 00:20:05 → 03:08:33. 1.7B Q4 00:20:05–00:23:04; 1.7B Q8 00:23:04–00:26:53; 3.5-4B 00:26:53–00:51:41; 8B 00:51:41–03:08:33. All four `rc=0`, server alive, `finish_reason=stop` (none hit the 2048 cap).

## Pros and cons of each variable (with numbers from this run)

### Parameters (1.7B vs 4B vs 8B vs 12B+)

- **Pros of more parameters:** With thinking off, 8B leads quality (4.70) and was the only model to name the Tagus on P08 (score 3, still not fully correct). 4B-Instruct Q8_0 is next (4.50). 1.7B Q8_0 now ties 4B-Instruct Q4 at 4.20 once it actually emits a final. Dev pass rises from 8–11/21 (1.7B) to 13–16/21 (4B Instruct), 17/21 (Qwen3.5) or 14/21 (8B).
- **Cons:** RAM, stability, and room for context. 8B Q4 (4.68 GiB) is at the limit: 16k full died; Q5 (5.45 GiB) died at 8k. 12B+ (6.80 GiB and up) did not load. The 8B thinking-off block ran entirely under swap (0.22–0.23 t/s) after 3.5-4B had already collapsed on P09–P10.

### Quantization (Q4_0 / Q4_K_M / Q8_0; Q5 attempted only on 8B)

- **Pros of lower precision:** Q4_0 is 2.21 GiB vs Q8_0 3.99 GiB for the same 4B Instruct (~45% smaller, matching the usual rule of thumb). All three 4B Instruct quants tied or nearly tied on short-doc func (8k 10/10). Q4_0 and Q4_K_M tied at 4.20 on quality.
- **Cons of lower precision:** Q8_0 was a bit better on quality (4.50 vs 4.20) and on dev (16/21 vs 15/21 Q4_0 and 13/21 Q4_K_M). The 1.7B Q4_K_M lagged its Q8_0 sibling once thinking was off (3.60 vs 4.20), mainly on P01 and P03. Q5 on 8B never ran to completion, so there is **no quality number** for it — only the stability result.

### Context size

- **Pros of a larger window:** A 16k document is 10/10 on 4B when the window is 16k. The same document cut to 8k / 4k / 2k keeps 5 / 2 / 1 facts and recall follows that exactly. If the use case is “search this file”, context must be ≥ the prompt.
- **Cons:** KV cache RAM, slower prefill, OOM / Termux kill risk. 4B Q4_K_M 16k full: median prefill 13.57 t/s, median TTFT 1115 s, median wall 1636 s (n=5). 8B 16k full: killed. Cutting the window is not a soft degradation — the start of the document is simply dropped.

### KV cache q8_0 (one data point only)

- **Pros:** 4B Instruct Q4_0, 16k×16k, stayed at 10/10 recall. Roughly halves KV memory (not re-measured here; this is the usual design trade-off). Decode was not slower on that run (1.43 vs 1.17 t/s).
- **Cons:** Prefill 2.34× slower (11.59 → 4.96 t/s); TTFT 1306.1 → 3053.1 s (about 51 minutes vs 22). Do not generalise beyond this single 4B Q4_0 measurement.

### Thinking on/off (now measured both ways)

- `LLAMA_ARG_REASONING=off` via llama-completion did **not** suppress `<think>` in this commit (the completion tool never copies `enable_thinking` onto the real prompt path). The llama-server path used in the re-run did: 40/40 OK, no think leak in the saved JSON.
- Thinking-on empty finals (first run, `-n 1024`): 1.7B Q4 4/10, 1.7B Q8 5/10, 8B 3/10, Qwen3.5-4B 8/10. 4B-Instruct-2507: 0/10.
- Thinking off raised the four models by +0.50, +1.60, +2.10 and +1.20 points (1.7B Q4, 1.7B Q8, 3.5-4B, 8B). The remaining errors are content, not truncation (P08 for everyone; 1.7B Q4 arithmetic on P01/P03; 3.5-4B TypeError on P04; weak human-readable sizes on most P05 scripts).
- For daily use, prefer an Instruct variant (no think flags needed) **or** a confirmed server-side `enable_thinking=false`. Do not trust the llama-completion env flag alone on this commit.

## Practical default for this phone

If you want one line: **Qwen3-4B-Instruct-2507 Q4_K_M (or Q4_0) at 8k, f16 KV** — same quality as 1.7B Q8_0 with thinking off, no special flags, and more headroom for a long context. Use **1.7B Q8_0 with thinking off** when you want 25–33 t/s and a 1.71 GiB file. Step up to **4B-Instruct Q8_0** (4.50) if the extra 1.7 GiB still leaves comfortable RAM, or to **8B Q4_K_M with thinking off** (4.70) only for occasional runs you can watch and that you can start with free RAM (this re-run never left 0.22–0.23 t/s). Use 16k context only when the document needs it, and only on the 4B (not the 8B). Do not plan on 12B+ in Termux on 12 GB.

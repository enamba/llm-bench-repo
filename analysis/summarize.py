#!/usr/bin/env python3
"""
summarize.py - turns the per-run CSVs into the summary tables in results/summary/ (standard library only).

  func     func_report.csv (python3 func_bench.py report <dir>)        -> func_summary.csv
  dev      dev_regrade.csv (python3 dev_bench.py regrade <jsonl>)      -> dev_pass_rates.csv + dev_pass_matrix.csv
  speed    quality_nothink_perf_*.csv (run_quality_nothink.sh)          -> quality_nothink_speed.csv
  scores   quality_grades_merged.csv (1-5 grades, LLM-assisted)         -> quality_score_by_model.csv
  bench    raw llama-bench CSVs (run_bench.sh speed|fit|ctx, in --raw) -> llama_bench_summary.csv
  all      func, dev, speed and scores, reading from and writing to --dir (default: results/summary)

The 1-5 quality grades themselves are not computed by code: they were assigned per answer (LLM-assisted, same rubric
for every model) and stored in quality_grades*.csv. Re-grade them by hand with the blind packet if you need a human grade.
"""
import argparse, csv, glob, os, re, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SUMMARY = os.path.join(ROOT, "results", "summary")
MODEL_ORDER = ["Qwen3-1.7B-Q4_K_M", "Qwen3-1.7B-Q8_0", "Qwen3-4B-Instruct-2507-Q4_0", "Qwen3-4B-Instruct-2507-Q4_K_M",
               "Qwen3-4B-Instruct-2507-Q8_0", "Qwen3.5-4B-Q4_K_M", "Qwen3-8B-Q4_K_M"]
DEV_CATS = ["shell", "git", "termux", "regex", "error", "bugfix-python", "bugfix-js", "bugfix-sql", "write-python",
            "write-js", "read-file", "read-file-bugfix"]

def rd(path): return list(csv.DictReader(open(path, newline="")))
def wr(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    print("wrote", path, file=sys.stderr)
def num(v):
    try: return float(v)
    except (TypeError, ValueError): return None
def fmt(x, nd=2):
    if x is None: return ""
    return str(round(x, nd))
def order(models): return sorted(models, key=lambda m: (MODEL_ORDER.index(m) if m in MODEL_ORDER else 99, m))

def model_sizes():
    sizes = {}
    for line in open(os.path.join(ROOT, "models.txt")):
        if line.startswith("#") or "|" not in line: continue
        _, url, size, *_ = line.strip().split("|")
        sizes[os.path.basename(url)[:-5]] = int(size)
    return sizes

# ------------------------------------------------------------------ func (recall)
def cmd_func(src, dst):
    rows = rd(src); groups = {}
    for r in rows: groups.setdefault((r["model"], r["spec"]), []).append(r)
    cols = ["prefill_tps", "decode_tps", "ttft_s", "wall_s"]
    hdr = ["model", "spec", "n_ok", "n_killed", "truncated", "prompt_tokens", "needles_in_context", "correct", "recall",
           "not_found", "wrong", "recall_values"] + [f"{c}_{s}" for c in cols for s in ("median", "min", "max")]
    out = []
    for (m, spec), g in sorted(groups.items()):
        ok = [r for r in g if r["status"] == "ok"]; killed = [r for r in g if r["status"].startswith("KILLED")]
        if not ok:
            out.append([m, spec, 0, len(killed)] + [""] * (len(hdr) - 4)); continue
        f = ok[0]
        row = [m, spec, len(ok), len(killed), f["truncated"], f["prompt_tokens"], f["needles_in_context"], f["correct"],
               f["recall"], f["not_found"], f["wrong"], "|".join(r["recall"] for r in ok)]
        for c in cols:
            v = [num(r[c]) for r in ok if num(r[c]) is not None]
            row += [fmt(statistics.median(v), 3), fmt(min(v)), fmt(max(v))]
        out.append(row)
    wr(dst, hdr, out)

# ------------------------------------------------------------------ dev
def cmd_dev(src, dst_rates, dst_matrix):
    rows = rd(src); sizes = model_sizes(); models = order({r["model"] for r in rows})
    hdr = ["model", "size_bytes", "size_gib", "phone_pass", "phone_total", "phone_pass_rate", "regrade_pass", "regrade_total",
           "regrade_pass_rate", "n_grade_changed", "med_gen_tps", "min_gen_tps", "max_gen_tps", "med_prefill_tps", "med_ttft_ms",
           "sum_total_time_s", "oom_any"] + [f"pass_{c}" for c in DEV_CATS]
    out = []
    for m in models:
        g = [r for r in rows if r["model"] == m]; n = len(g)
        pp = sum(r["phone_auto_grade"] == "pass" for r in g); rp = sum(r["regrade_auto_grade"] == "pass" for r in g)
        gen = [num(r["gen_tps"]) for r in g if num(r["gen_tps"]) is not None]
        pre = [num(r["prefill_tps"]) for r in g if num(r["prefill_tps"]) is not None]
        ttft = [num(r["ttft_ms"]) for r in g if num(r["ttft_ms"]) is not None]
        tot = sum(num(r["total_time_s"]) or 0 for r in g)
        size = sizes.get(m)
        row = [m, size or "", fmt(size / 2**30, 3) if size else "", pp, n, fmt(pp / n, 4), rp, n, fmt(rp / n, 4),
               sum(r["grade_changed"] == "True" for r in g), fmt(statistics.median(gen)), fmt(min(gen)), fmt(max(gen)),
               fmt(statistics.median(pre)), fmt(statistics.median(ttft), 1), fmt(tot, 1), any(r["oom"] == "True" for r in g)]
        for c in DEV_CATS:
            gc = [r for r in g if r["category"] == c]
            row.append(f"{sum(r['regrade_auto_grade'] == 'pass' for r in gc)}/{len(gc)}")
        out.append(row)
    wr(dst_rates, hdr, out)
    qids = []
    for r in rows:
        if r["question_id"] not in qids: qids.append(r["question_id"])
    cat = {r["question_id"]: r["category"] for r in rows}
    cell = {(r["question_id"], r["model"]): (r["regrade_auto_grade"] if r["phone_auto_grade"] == r["regrade_auto_grade"]
                                             else f"{r['phone_auto_grade']}->{r['regrade_auto_grade']}") for r in rows}
    wr(dst_matrix, ["question_id", "category"] + models, [[q, cat[q]] + [cell.get((q, m), "") for m in models] for q in qids])

# ------------------------------------------------------------------ quality speed (thinking-off re-run)
def cmd_speed(srcs, dst):
    rows = [r for s in srcs for r in rd(s)]; models = []
    for r in rows:
        if r["model"] not in models: models.append(r["model"])
    hdr = ["model", "n", "n_ok", "med_gen_tps", "min_gen_tps", "max_gen_tps", "med_prefill_tps", "min_prefill_tps",
           "max_prefill_tps", "med_ttft_ms", "sum_wall_s", "sum_gen_tokens", "n_swap_lt_1tps", "load_s"]
    out = []
    for m in models:
        g = [r for r in rows if r["model"] == m]
        gen = [num(r["gen_tps"]) for r in g if num(r["gen_tps"]) is not None]
        pre = [num(r["prefill_tps"]) for r in g if num(r["prefill_tps"]) is not None]
        ttft = [num(r["ttft_ms"]) for r in g if num(r["ttft_ms"]) is not None]
        out.append([m, len(g), sum(r["rc"] == "0" and r["flag"] == "OK" for r in g), fmt(statistics.median(gen)), fmt(min(gen)),
                    fmt(max(gen)), fmt(statistics.median(pre)), fmt(min(pre)), fmt(max(pre)), fmt(statistics.median(ttft), 1),
                    fmt(sum(num(r["wall_s"]) or 0 for r in g), 1), sum(int(r["gen_tokens"] or 0) for r in g),
                    sum(v < 1 for v in gen), g[0]["load_s"]])
    wr(dst, hdr, out)

# ------------------------------------------------------------------ quality scores
def cmd_scores(src, dst):
    rows = rd(src); models = order({r["model"] for r in rows})
    hdr = ["model", "sum_thinking_on", "mean_thinking_on", "sum_thinking_off", "mean_thinking_off", "sum_primary", "mean_primary",
           "n_score_5_off", "n_score_1_off", "scores_thinking_on", "scores_thinking_off"]
    out = []
    for m in models:
        g = sorted([r for r in rows if r["model"] == m], key=lambda r: r["prompt_id"])
        on = [int(r["score_thinking_on"]) for r in g]; prim = [int(r["score_primary"]) for r in g]
        off = [int(r["score_thinking_off"]) for r in g if r["score_thinking_off"]]
        has_off = len(off) == len(g)
        out.append([m, sum(on), f"{sum(on) / len(on):.2f}", sum(off) if has_off else "", f"{sum(off) / len(off):.2f}" if has_off else "",
                    sum(prim), f"{sum(prim) / len(prim):.2f}", off.count(5) if has_off else "", off.count(1) if has_off else "",
                    " ".join(f"{r['prompt_id']}={r['score_thinking_on']}" for r in g),
                    " ".join(f"{r['prompt_id']}={r['score_thinking_off']}" for r in g) if has_off else "(no thinking channel; not re-run)"])
    wr(dst, hdr, out)

# ------------------------------------------------------------------ llama-bench (speed / fit / ctx)
def cmd_bench(raw, dst):
    hdr = ["mode", "run_id", "model", "rc", "n_threads", "flash_attn", "type_k", "n_prompt", "n_gen", "n_depth", "avg_ts",
           "stddev_ts", "memavail_mb_start", "batt_temp_start_c", "status"]
    out = []
    for f in sorted(glob.glob(os.path.join(raw, "*_cpu_*.csv")) + glob.glob(os.path.join(raw, "*_opencl_*.csv"))):
        if not os.path.basename(f).split("_")[0] in ("speed", "fit", "ctx"): continue
        for line in csv.reader(open(f, newline="")):
            if not line or line[0] == "phone": continue
            if len(line) > 11 and line[11].startswith("FAILED"):
                m = re.search(r"-m (\S+)", line[11]); model = os.path.basename(m.group(1))[:-5] if m else ""
                out.append([line[4], line[5], model, line[10], "", "", "", "", "", "", "", "", line[7], line[9], "FAILED"]); continue
            r = dict(zip(HDR_CACHE.get(f) or _hdr(f), line))
            out.append([r["mode"], r["run_id"], os.path.basename(r["model_filename"])[:-5], r["rc"], r["n_threads"], r["flash_attn"],
                        r["type_k"], r["n_prompt"], r["n_gen"], r["n_depth"], r["avg_ts"], r["stddev_ts"], r["memavail_mb_start"],
                        r["batt_temp_start_c"], "ok"])
    wr(dst, hdr, out)

HDR_CACHE = {}
def _hdr(f):
    HDR_CACHE[f] = next(csv.reader(open(f, newline=""))); return HDR_CACHE[f]

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("cmd", choices=["func", "dev", "speed", "scores", "bench", "all"])
    ap.add_argument("--raw", default=os.path.join(ROOT, "results", "raw", "fold8-12gb"), help="raw results folder (bench)")
    ap.add_argument("--dir", default=SUMMARY, help="folder with the input CSVs; outputs go to --out-dir")
    ap.add_argument("--out-dir", default=None, help="default: same as --dir")
    a = ap.parse_args(); d = a.dir; o = a.out_dir or d; os.makedirs(o, exist_ok=True)
    if a.cmd in ("func", "all"): cmd_func(os.path.join(d, "func_report.csv"), os.path.join(o, "func_summary.csv"))
    if a.cmd in ("dev", "all"): cmd_dev(os.path.join(d, "dev_regrade.csv"), os.path.join(o, "dev_pass_rates.csv"), os.path.join(o, "dev_pass_matrix.csv"))
    if a.cmd in ("speed", "all"):
        srcs = sorted(glob.glob(os.path.join(d, "quality_nothink_perf_*.csv")))
        if srcs: cmd_speed(srcs, os.path.join(o, "quality_nothink_speed.csv"))
        else: print("no quality_nothink_perf_*.csv in", d, file=sys.stderr)
    if a.cmd == "bench": cmd_bench(a.raw, os.path.join(o, "llama_bench_summary.csv"))
    if a.cmd in ("scores", "all"): cmd_scores(os.path.join(d, "quality_grades_merged.csv"), os.path.join(o, "quality_score_by_model.csv"))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
plot_charts.py - draws the four result charts from the CSVs in results/summary/ (needs matplotlib).

  recall_vs_context.png   recall (facts out of 10) vs context window for the same 16k-token document
  dev_pass_rate.png       developer tasks: on-phone auto-grade vs off-device regrade (21 tasks)
  quality_score.png       P01-P10 mean 1-5 score, thinking on (first run) vs thinking off (re-run)
  size_quality_speed.png  GGUF size vs primary quality score, bubble = max decode t/s

Usage:  python3 analysis/plot_charts.py [--dir results/summary] [--out results/charts_regen]
The PNGs shipped in results/charts/ are the ones used in the article; this script redraws the same data
(the layout and annotations can differ slightly). PDF copies are written next to each PNG.
"""
import argparse, csv, os, re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
SHORT = {"Qwen3-1.7B-Q4_K_M": "1.7B Q4_K_M", "Qwen3-1.7B-Q8_0": "1.7B Q8_0", "Qwen3-4B-Instruct-2507-Q4_0": "4B Inst. Q4_0",
         "Qwen3-4B-Instruct-2507-Q4_K_M": "4B Inst. Q4_K_M", "Qwen3-4B-Instruct-2507-Q8_0": "4B Inst. Q8_0",
         "Qwen3.5-4B-Q4_K_M": "3.5-4B Q4_K_M", "Qwen3-8B-Q4_K_M": "8B Q4_K_M"}
COLOR = {"Qwen3-1.7B-Q4_K_M": "#3b5b8a", "Qwen3-1.7B-Q8_0": "#9ac0dc", "Qwen3-4B-Instruct-2507-Q4_0": "#2a9d8f",
         "Qwen3-4B-Instruct-2507-Q4_K_M": "#8ac926", "Qwen3-4B-Instruct-2507-Q8_0": "#1b4332",
         "Qwen3.5-4B-Q4_K_M": "#e9c46a", "Qwen3-8B-Q4_K_M": "#e76f51"}
ORDER = list(SHORT)

def rd(d, name): return list(csv.DictReader(open(os.path.join(d, name), newline="")))
def save(fig, out, name):
    for ext in ("png", "pdf"): fig.savefig(os.path.join(out, f"{name}.{ext}"), dpi=150, bbox_inches="tight")
    plt.close(fig); print("wrote", os.path.join(out, name + ".png"))
def style(ax):
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False); ax.grid(alpha=0.3)

def recall(d, out):
    rows = rd(d, "func_summary.csv"); fig, ax = plt.subplots(figsize=(10, 5.5))
    for m in ("Qwen3-4B-Instruct-2507-Q4_K_M", "Qwen3-4B-Instruct-2507-Q4_0", "Qwen3-8B-Q4_K_M"):
        pts = []
        for r in rows:
            if r["model"] != m or not r["spec"].startswith("16k:") or not r["spec"].endswith(":f16"): continue
            ctx = int(r["spec"].split(":")[1])
            if r["n_ok"] != "0": pts.append((ctx, int(r["correct"]), int(r["wrong"])))
            else: ax.scatter([ctx], [0.15], marker="x", s=120, color=COLOR[m], zorder=5); ax.annotate(f"{SHORT[m]} {ctx//1024}k: KILLED", (ctx, 0.15), textcoords="offset points", xytext=(-150, 20), fontsize=8, color=COLOR[m])
        pts.sort()
        if not pts: continue
        ax.plot([p[0] for p in pts], [p[1] for p in pts], "o-", lw=2.5, ms=8, color=COLOR[m], label=f"{SHORT[m]} (16k doc, f16 KV)")
        for x, y, w in pts:
            ax.annotate(f"{y}/10" + (f" ({w} wrong)" if w else ""), (x, y), textcoords="offset points", xytext=(8, 6), fontsize=8, color=COLOR[m])
    ax.set_xscale("log", base=2); ax.set_xticks([2048, 4096, 8192, 16384]); ax.set_xticklabels(["2k", "4k", "8k", "16k"])
    ax.set_ylim(-0.5, 11); ax.set_xlabel("Context window (tokens) for a 16k-token document"); ax.set_ylabel("Facts recalled correctly (out of 10)")
    ax.set_title("Fact recall vs context cut - 16k document on Fold 8 (12 GB)"); ax.legend(loc="upper left"); style(ax)
    fig.text(0.01, -0.04, "When the document does not fit, its start is dropped. NOT_FOUND answers are not counted as wrong.", fontsize=8)
    save(fig, out, "recall_vs_context")

def dev(d, out):
    rows = {r["model"]: r for r in rd(d, "dev_pass_rates.csv")}; ms = [m for m in ORDER if m in rows]
    fig, ax = plt.subplots(figsize=(10, 5.5)); x = range(len(ms)); w = 0.38
    ax.bar([i - w / 2 for i in x], [100 * float(rows[m]["phone_pass_rate"]) for m in ms], w, color="#b0b0b0", label="On-phone auto_grade (shell graders broken, rc=126)")
    bars = ax.bar([i + w / 2 for i in x], [100 * float(rows[m]["regrade_pass_rate"]) for m in ms], w, color=[COLOR[m] for m in ms], label="Regraded off-device (dev_bench.py regrade)")
    for b, m in zip(bars, ms): ax.annotate(f"{rows[m]['regrade_pass']}/{rows[m]['regrade_total']}", (b.get_x() + b.get_width() / 2, b.get_height()), ha="center", xytext=(0, 3), textcoords="offset points", fontsize=8)
    ax.set_xticks(list(x)); ax.set_xticklabels([SHORT[m] for m in ms], rotation=20, ha="right"); ax.set_ylim(0, 108)
    ax.set_ylabel("Pass rate (%)"); ax.set_title(f"Dev benchmark - {rows[ms[0]]['regrade_total']} tasks, auto-grade pass rate"); ax.legend(loc="upper left"); style(ax)
    save(fig, out, "dev_pass_rate")

def quality(d, out):
    rows = {r["model"]: r for r in rd(d, "quality_score_by_model.csv")}; ms = [m for m in ORDER if m in rows]
    fig, ax = plt.subplots(figsize=(11, 5.5)); w = 0.38
    for i, m in enumerate(ms):
        r = rows[m]; on = float(r["mean_thinking_on"])
        if r["mean_thinking_off"]:
            off = float(r["mean_thinking_off"])
            ax.bar(i - w / 2, on, w, color=COLOR[m], alpha=0.35, hatch="//", edgecolor=COLOR[m])
            ax.bar(i + w / 2, off, w, color=COLOR[m], edgecolor="black", lw=0.6)
            ax.annotate(f"{on:.2f}", (i - w / 2, on), ha="center", xytext=(0, 3), textcoords="offset points", fontsize=8, color="#555")
            ax.annotate(f"{off:.2f}\n({r['sum_thinking_off']}/50)", (i + w / 2, off), ha="center", xytext=(0, 3), textcoords="offset points", fontsize=8, weight="bold")
        else:
            ax.bar(i, on, w * 1.6, color=COLOR[m], edgecolor="black", lw=0.6)
            ax.annotate(f"{on:.2f}\n({r['sum_thinking_on']}/50)", (i, on), ha="center", xytext=(0, 3), textcoords="offset points", fontsize=8, weight="bold")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor="#999", alpha=0.35, hatch="//", label="Thinking on (first run, n=1024)"),
                       Patch(facecolor="#555", label="Thinking off (re-run, n=2048)"),
                       Patch(facecolor=COLOR["Qwen3-4B-Instruct-2507-Q4_0"], label="4B-Instruct-2507 (no thinking channel; not re-run)")], loc="upper left", fontsize=8)
    ax.set_xticks(range(len(ms))); ax.set_xticklabels([SHORT[m] for m in ms], rotation=20, ha="right"); ax.set_ylim(0, 5.5)
    ax.set_ylabel("Mean rubric score (1-5)"); ax.set_title("Quality prompts P01-P10 - LLM-assisted rubric mean"); style(ax)
    fig.text(0.01, -0.04, "Scores are LLM-assisted (same 1-5 rubric for every model), not a human blind grade.", fontsize=8)
    save(fig, out, "quality_score")

def size_quality_speed(d, out):
    q = {r["model"]: r for r in rd(d, "quality_score_by_model.csv")}
    sizes = {r["model"]: float(r["size_gib"]) for r in rd(d, "dev_pass_rates.csv")}
    sp = {r["model"]: r for r in rd(d, "quality_nothink_speed.csv")}
    first = {r["model"]: r for r in rd(d, "quality_speed.csv")}   # first run (Instruct-2507 primary scores come from it)
    fig, ax = plt.subplots(figsize=(10, 6))
    OFF = {"Qwen3-1.7B-Q4_K_M": (-10, -45), "Qwen3-1.7B-Q8_0": (-60, 40), "Qwen3-4B-Instruct-2507-Q4_0": (-80, 45),
           "Qwen3-4B-Instruct-2507-Q4_K_M": (-20, -60), "Qwen3.5-4B-Q4_K_M": (25, -45),
           "Qwen3-4B-Instruct-2507-Q8_0": (-60, 40), "Qwen3-8B-Q4_K_M": (-150, -45)}
    for m in [m for m in ORDER if m in q]:
        s = sp.get(m) or first.get(m); mx = float(s["max_gen_tps"]); med = s["med_gen_tps"]
        ax.scatter(sizes[m], float(q[m]["mean_primary"]), s=40 + 40 * mx, color=COLOR[m], edgecolor="black", zorder=3)
        ax.annotate(f"{SHORT[m]}\nmed {med} / max {mx} t/s", (sizes[m], float(q[m]["mean_primary"])), textcoords="offset points", xytext=OFF.get(m, (10, -22)), fontsize=7,
                    arrowprops=dict(arrowstyle="-", color="#777", lw=0.6))
        if q[m]["mean_thinking_off"]:
            ax.scatter(sizes[m], float(q[m]["mean_thinking_on"]), marker="x", s=50, color=COLOR[m], zorder=3)
    ax.set_xlabel("GGUF file size (GiB)"); ax.set_ylabel("Quality mean (LLM-assisted, 1-5)"); ax.set_ylim(1.5, 5.3); style(ax)
    ax.set_title("Model size vs quality vs decode speed\n(dot = primary score; x = thinking-on score of the same model)")
    fig.text(0.01, -0.04, "Bubble area scales with the max decode t/s of the run behind the primary score. 8B and 4B-Instruct were swapping (~0.2 t/s).", fontsize=8)
    save(fig, out, "size_quality_speed")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dir", default=os.path.join(ROOT, "results", "summary"))
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "charts_regen"))
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    recall(a.dir, a.out); dev(a.dir, a.out); quality(a.dir, a.out); size_quality_speed(a.dir, a.out)

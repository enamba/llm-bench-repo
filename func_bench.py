#!/usr/bin/env python3
"""
func_bench.py - long-context RECALL test ("how much does each phone remember?").

A deterministic synthetic document (engineering notes of a fictional project, "Project Kestrel") with 10 planted
facts ("needles") at 5 %, 15 %, ... 95 % of the text. The model gets the document plus ONE question block asking for
all 10 facts as key=value lines, at temperature 0. Recall = correct facts / 10.

Each run is one spec DOC:CTX:KV, e.g. 16k:16384:f16
  DOC  nominal document size (2k 4k 8k 16k 32k; whole prompt incl. question fits in a context of that size)
  CTX  n_ctx given to llama-server. If CTX < DOC the document is TRUNCATED: paragraphs are dropped from the START
       until the prompt fits (= what a phone with less RAM has to do: it keeps the most recent part and forgets the rest)
  KV   KV cache type for K and V (f16, q8_0, q4_0)

Subcommands
  gen       (re)write scenarios/recall/*.txt + needles.json (fixed seed; the files ship with the package)
  run       run one spec against a running llama-server, append a row to the CSV + raw JSON
  oom       append an OOM / failed-to-load row
  skip      append a "skipped" row
  estimate  print the expected prefill time for a spec (ESTIMATE from measured llama-bench speeds)
  report    one readable table from every func_*.csv in a results dir + runs that died with Termux (progress files)
  selftest  check the generator and the scorer without a model
Standard library only (Termux: pkg install python).
"""
import argparse, csv, glob, json, os, random, re, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
RDIR = os.path.join(HERE, "scenarios", "recall")
SEED = 20261111
SIZES = {"2k": 2048, "4k": 4096, "8k": 8192, "16k": 16384, "32k": 32768}
CHARS_PER_TOKEN = 4.0       # Qwen3 tokenizer measures ~4.2 chars/token on this text (box, Qwen3-0.6B vocab); 4.0 leaves headroom
RESERVE = 600               # tokens kept free in the nominal context: ~255 template+question, 160 answer, margin
N_PREDICT = 160             # the 10-line answer needs ~110-130 tokens; caps a runaway answer (decode is ~1.2 t/s at 16k)
MARGIN = 32

NEEDLES = [
 ("release_manager", "Decision: Marisol Okafor will be the release manager for the 4.2 deploy freeze.",
  "full name of the release manager for the 4.2 deploy freeze", "Marisol Okafor", [r"okafor"]),
 ("staging_db_port", "Config change: the staging Postgres instance now listens on port 6431.",
  "port number the staging Postgres instance listens on", "6431", [r"\b6431\b"]),
 ("pdf_licence_key", "The licence key for the PDF renderer vendor is KX-58-TQ-9021; keep it out of the repository.",
  "licence key of the PDF renderer vendor", "KX-58-TQ-9021", [r"kx-?58-?tq-?9021"]),
 ("upload_limit_mb", "The maximum upload size for proof-of-delivery photos was raised to 375 MB.",
  "maximum upload size for proof-of-delivery photos, in MB", "375", [r"\b375\b"]),
 ("webhook_backoff_s", "The payment webhook retries now wait 47 seconds between attempts.",
  "seconds the payment webhook retries wait between attempts", "47", [r"\b47\b"]),
 ("android_codename", "The new Android driver app has the internal code name Bluefin.",
  "internal code name of the new Android driver app", "Bluefin", [r"bluefin"]),
 ("oncall_feb19", "On-call change: Teodoro Lindqvist covers the night of 19 February instead of the usual rota.",
  "full name of the person covering on-call on the night of 19 February", "Teodoro Lindqvist", [r"lindqvist"]),
 ("checkout_flag", "The new checkout flow is hidden behind the feature flag checkout_v3_shadow until the audit is done.",
  "name of the feature flag that hides the new checkout flow", "checkout_v3_shadow", [r"checkout_?v3_?shadow"]),
 ("build_rack", "The build farm was moved to rack R-17 in the Porto data centre.",
  "rack the build farm was moved to", "R-17", [r"\br-?17\b"]),
 ("session_ttl_s", "The session cache TTL was set to 930 seconds after the login incident.",
  "session cache TTL in seconds", "930", [r"\b930\b"]),
]
POSITIONS = [(i + 0.5) / len(NEEDLES) for i in range(len(NEEDLES))]   # 5 %, 15 %, ..., 95 %

SYSTEM = "You are a precise assistant. Answer only from the notes you are given. Never guess."

def question_block():
    lines = ["QUESTIONS. Using only the notes above, give the following values.",
             "Reply with exactly %d lines in the form key=value, in this order, with no other text." % len(NEEDLES),
             "If a value is not in the notes, write key=NOT_FOUND.", ""]
    lines += ["%s = %s" % (k, desc) for k, _, desc, _, _ in NEEDLES]
    return "\n".join(lines)

def user_message(doc):
    return "Below are the engineering notes of Project Kestrel.\n\n<notes>\n" + doc + "\n</notes>\n\n" + question_block()

# ---------------------------------------------------------------- generator
NAMES = ["Aiko Tanabe", "Bruno Salgado", "Chloe Brennan", "Dmitri Volkov", "Esther Mbeki", "Farid Haddad",
         "Greta Lund", "Hamza Yilmaz", "Isla McKenzie", "Jonas Weber", "Keiko Mori", "Leandro Prates"]
SVC = ["checkout", "inventory", "routing", "billing", "notifications", "search", "auth", "reporting", "tracking", "pricing"]
ENV = ["staging", "production", "the QA cluster", "the load-test cluster"]
TEST = ["integration", "end-to-end", "contract", "snapshot", "unit"]
STEP = ["lint", "unit-test", "docker build", "migration check", "artifact upload"]
FEAT = ["route planner", "driver rating", "parcel timeline", "returns wizard", "depot map", "shift calendar"]
PROC = ["database restore", "certificate rotation", "incident review", "hotfix release", "access review"]
ISSUE = ["late parcel notifications", "duplicated invoices", "wrong delivery windows", "slow tracking pages"]
JOB = ["invoice export", "search reindex", "warehouse sync", "analytics rollup", "backup verification"]
MEET = ["architecture review", "sprint demo", "retrospective", "planning session"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
KIND = ["platform sync", "standup", "backend guild", "mobile sync", "ops review"]
TEMPL = [
 "{n} reported that the {s} p95 latency was {ms} ms on {e}, down from {ms2} ms last week.",
 "The team agreed to move the {s} dashboards to the shared folder before the next review.",
 "{n} opened ticket KES-{tk} about flaky {t} tests in the {s} pipeline; it is assigned to {n2}.",
 "Build {bn} of the {s} service failed on the {st} step and was re-run after clearing the cache.",
 "Disk usage on build-agent-{h} reached {pct} %, so log archives older than {d} days were removed.",
 "{n} demoed the new {f} screen; most feedback was about colour contrast and empty states.",
 "The {s} service was upgraded to version {a}.{b}.{c} on {e} without downtime.",
 "Action item: {n} will document the {p} procedure in the wiki by {wd}.",
 "Customer support escalated {k} tickets about {i}; {n} is collecting logs from {e}.",
 "The nightly {j} job took {mi} minutes, which is within the agreed window.",
 "{n} reminded everyone that the {m} moves to {wd} at {hh}:{mm} next week.",
 "The {s} Redis cluster on {e} uses {g} GB of memory out of {g2} GB.",
 "The {s} health endpoint on {e} answers on port {port} and is polled every {k} seconds by the monitor.",
 "{n} and {n2} paired on the {s} refactor and expect to merge it after the {m}.",
 "Error rate for the {s} API on {e} stayed below {er} % for the whole week.",
]
BANNED = re.compile(r"postgres|webhook|release manager|licen[cs]e|upload|code name|on-call|feature flag|\brack\b|"
                    r"session cache|\b(47|375|930|6431)\b|r-?17\b", re.I)

def _sentence(R):
    while True:
        s = R.choice(TEMPL).format(
            n=R.choice(NAMES), n2=R.choice(NAMES), s=R.choice(SVC), e=R.choice(ENV), t=R.choice(TEST),
            st=R.choice(STEP), f=R.choice(FEAT), p=R.choice(PROC), i=R.choice(ISSUE), j=R.choice(JOB),
            m=R.choice(MEET), wd=R.choice(DAYS), ms=R.randint(80, 900), ms2=R.randint(90, 990),
            tk=R.randint(1000, 4999), bn=R.randint(1000, 9999), h=R.randint(1, 9), pct=R.randint(70, 96),
            d=R.randint(7, 90), a=R.randint(1, 6), b=R.randint(0, 30), c=R.randint(0, 12), k=R.randint(2, 40),
            mi=R.randint(12, 140), hh=R.randint(9, 16), mm=R.choice(["00", "15", "30", "45"]),
            g=R.randint(2, 30), g2=R.choice([32, 48, 64]), port=R.randint(7000, 9999), er=R.choice(["0.1", "0.2", "0.5", "1.2"]))
        if not BANNED.search(s): return s

def _pool(n_paras):
    R = random.Random(SEED); paras = []
    for i in range(n_paras):
        head = "Notes %04d (%s, %s):" % (i + 1, R.choice(KIND), R.choice(DAYS))
        paras.append(head + " " + " ".join(_sentence(R) for _ in range(R.randint(3, 6))))
    return paras

def build_doc(size_key):
    target_chars = (SIZES[size_key] - RESERVE) * CHARS_PER_TOKEN - sum(len(n[1]) + 1 for n in NEEDLES)
    pool = _pool(4000); paras = []; total = 0
    for p in pool:
        if total + len(p) > target_chars: break
        paras.append(p); total += len(p) + 2
    n = len(paras)
    for (k, sent, *_), f in sorted(zip(NEEDLES, POSITIONS), key=lambda x: x[1]):
        idx = min(n - 1, int(f * n)); paras[idx] = paras[idx] + " " + sent
    return paras

def cmd_gen(a):
    os.makedirs(RDIR, exist_ok=True)
    for sk in SIZES:
        paras = build_doc(sk)
        open(os.path.join(RDIR, "hay_%s.txt" % sk), "w").write("\n\n".join(paras) + "\n")
        txt = "\n\n".join(paras)
        pos = ["%s@%.1f%%" % (k, txt.index(s) / len(txt) * 100) for k, s, *_ in NEEDLES]
        print("%-4s %3d paragraphs %7d chars  %s" % (sk, len(paras), len(txt), " ".join(pos)))
    json.dump([{"key": k, "sentence": s, "question": q, "expected": e, "accept": acc, "position": p}
               for (k, s, q, e, acc), p in zip(NEEDLES, POSITIONS)],
              open(os.path.join(RDIR, "needles.json"), "w"), indent=1)
    open(os.path.join(RDIR, "question_block.txt"), "w").write(question_block() + "\n")

def load_doc(size_key):
    f = os.path.join(RDIR, "hay_%s.txt" % size_key)
    if not os.path.exists(f): cmd_gen(None)
    return [p for p in open(f).read().split("\n\n") if p.strip()]

# ---------------------------------------------------------------- scoring
def parse_answer(text):
    vals = {}
    for line in text.splitlines():
        line = line.strip().strip("-*` ").replace("**", "")
        m = re.match(r"^([A-Za-z0-9_]+)\s*[=:]\s*(.*)$", line)
        if m and m.group(1).lower() in KEYS and m.group(1).lower() not in vals:
            vals[m.group(1).lower()] = m.group(2).strip().strip("`'\" ")
    return vals

KEYS = {k for k, *_ in NEEDLES}
def score(text, kept_doc):
    vals = parse_answer(text); per = []
    for k, sent, _, exp, acc in NEEDLES:
        v = vals.get(k); in_ctx = sent in kept_doc
        if v is None: st = "missing"
        elif re.search(r"not[ _]?found|unknown|not (in|mentioned|provided)|^n/?a$|^none$", v, re.I): st = "not_found"
        elif any(re.search(p, v, re.I) for p in acc): st = "correct"
        else: st = "wrong"
        per.append({"key": k, "in_context": in_ctx, "value": v, "status": st})
    return per

# ---------------------------------------------------------------- server client
def post(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())

def messages(doc): return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_message(doc)}]

def fit(base, paras, budget):
    """Fewest paragraphs dropped from the START so that the templated prompt fits in budget tokens."""
    def build(drop):
        doc = "\n\n".join(paras[drop:])
        p = post(base + "/apply-template", {"messages": messages(doc)})["prompt"]
        return doc, p, len(post(base + "/tokenize", {"content": p, "add_special": True, "parse_special": True})["tokens"])
    doc, p, n = build(0)
    full = n
    if n <= budget: return doc, p, n, 0, full
    lo, hi = 0, len(paras)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if build(mid)[2] <= budget: hi = mid
        else: lo = mid
    doc, p, n = build(hi); return doc, p, n, hi, full

def stream_completion(base, prompt, n_predict, timeout):
    body = {"prompt": prompt, "n_predict": n_predict, "temperature": 0, "top_k": 1, "seed": 42,
            "cache_prompt": False, "stream": True}
    req = urllib.request.Request(base + "/completion", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time(); ttft = None; text = []; last = {}
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"): continue
            d = json.loads(line[5:].strip())
            if d.get("content"):
                if ttft is None: ttft = time.time() - t0
                text.append(d["content"])
            if d.get("stop"): last = d; break
    if not last:   # stream ended without the final chunk: the server died mid-answer (Android OOM killer)
        raise ConnectionError("stream ended without a final chunk after %.0f s (server died)" % (time.time() - t0))
    return "".join(text), last, ttft, time.time() - t0

COLS = ["ts", "phone", "backend", "model", "spec", "doc", "doc_nominal_tokens", "n_ctx", "kv", "truncated",
        "doc_full_prompt_tokens", "prompt_tokens", "paras_dropped", "paras_total", "needles_in_context",
        "correct", "not_found", "wrong", "missing", "recall", "recall_of_kept", "prefill_tps", "decode_tps",
        "ttft_s", "prefill_s", "wall_s", "load_s", "status", "error", "batt_temp_c", "memavail_mb", "run_id", "answer"]

def write_row(out, row):
    new = not os.path.exists(out) or os.path.getsize(out) == 0
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        if new: w.writeheader()
        w.writerow(row)

def parse_spec(spec):
    d, c, kv = (spec.split(":") + ["", "f16"])[:3]
    if d not in SIZES: raise SystemExit("bad spec %r: DOC must be one of %s" % (spec, " ".join(SIZES)))
    return d, int(c), kv or "f16"

def base_row(a):
    d, c, kv = parse_spec(a.spec)
    return {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "phone": a.phone, "backend": a.backend, "model": a.model_name,
            "spec": a.spec, "doc": d, "doc_nominal_tokens": SIZES[d], "n_ctx": c, "kv": kv,
            "truncated": "yes" if c < SIZES[d] else "no", "load_s": a.load_s, "batt_temp_c": a.temp,
            "memavail_mb": a.mem, "run_id": a.run_id}

def cmd_run(a):
    row = base_row(a); d, c, kv = parse_spec(a.spec); base = a.server.rstrip("/")
    paras = load_doc(d); stage = "tokenize"
    try:
        doc, prompt, n_in, dropped, full = fit(base, paras, c - N_PREDICT - MARGIN)
        est = estimate_s(a.model_name, n_in)
        print("[func] %s %s: prompt %d tokens (full document %d), dropped %d/%d paragraphs; "
              "ESTIMATED prefill %s + answer %s" % (a.model_name, a.spec, n_in, full, dropped, len(paras), fmt_range(est),
              fmt_range(estimate_decode_s(a.model_name, n_in))), file=sys.stderr)
        row.update({"doc_full_prompt_tokens": full, "prompt_tokens": n_in, "paras_dropped": dropped, "paras_total": len(paras),
                    "needles_in_context": sum(n[1] in doc for n in NEEDLES)})
        stage = "completion"; t_start = time.time()
        text, last, ttft, wall = stream_completion(base, prompt, N_PREDICT, a.timeout)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, json.JSONDecodeError) as e:
        if stage == "completion": row["wall_s"] = round(time.time() - t_start, 1)   # how long it ran before dying
        row.update({"status": "OOM_or_crash" if stage == "completion" else "server_error",
                    "error": "%s failed: %s" % (stage, e)})
        write_row(a.out, row); print("[func] server died / no answer during %s: %s" % (stage, e), file=sys.stderr)
        sys.exit(3)
    tm = last.get("timings", {}); per = score(text, doc)
    cnt = {s: sum(p["status"] == s for p in per) for s in ("correct", "not_found", "wrong", "missing")}
    kept = sum(p["in_context"] for p in per); ok_kept = sum(p["in_context"] and p["status"] == "correct" for p in per)
    row.update({"doc_full_prompt_tokens": full, "prompt_tokens": n_in, "paras_dropped": dropped, "paras_total": len(paras),
                "needles_in_context": kept, **cnt, "recall": round(cnt["correct"] / len(NEEDLES), 2),
                "recall_of_kept": round(ok_kept / kept, 2) if kept else "",
                "prefill_tps": round(tm.get("prompt_per_second") or 0, 2), "decode_tps": round(tm.get("predicted_per_second") or 0, 2),
                "ttft_s": round(ttft, 1) if ttft else "", "prefill_s": round((tm.get("prompt_ms") or 0) / 1000, 1),
                "wall_s": round(wall, 1), "status": "ok", "error": "", "answer": text.strip().replace("\n", " | ")})
    write_row(a.out, row)
    raw = os.path.join(a.raw_dir, "%s__%s.json" % (a.model_name, a.spec.replace(":", "_")))
    os.makedirs(a.raw_dir, exist_ok=True)
    json.dump({"row": row, "per_needle": per, "answer": text, "timings": tm, "prompt_tail": prompt[-1500:]},
              open(raw, "w"), indent=1, ensure_ascii=False)
    print("[func] recall %d/%d (facts kept in context %d, correct among kept %d); not_found %d, wrong %d; "
          "prefill %.1f t/s, decode %.1f t/s, TTFT %ss, wall %ss" % (cnt["correct"], len(NEEDLES), kept, ok_kept,
          cnt["not_found"], cnt["wrong"], row["prefill_tps"], row["decode_tps"], row["ttft_s"], row["wall_s"]), file=sys.stderr)

def cmd_mark(a, status):
    row = base_row(a); row.update({"status": status, "error": a.reason}); write_row(a.out, row)

# ---------------------------------------------------------------- estimates
# ESTIMATES (not measurements). Per-token cost at depth d is interpolated piecewise-linearly between measured
# llama-bench points (Fold 8, 6 threads, f16 KV, -fa on, phones cooled), Qwen3-4B-Instruct-2507 Q4_0:
#   prefill pp512 t/s: d=0 136 (12 GB) | 4k 22.6 (12 GB) / 17.2 (16 GB) | 8k 10.9 / 10.6 | 16k 7.2 (16 GB)
#   decode  tg32  t/s: d=0 24.7 (12 GB) | 4k 5.27 / 4.35 | 8k 2.23 / 2.19 | 16k 1.23 (16 GB)
# "low" uses the faster phone at each point, "high" the slower one. Beyond 16k the last slope is extrapolated.
# 8B Q4_K_M: only d=4k is measured (12 GB: pp 17.3, tg 4.52) -> 4B curve + a constant per-token offset at 4k.
# q8_0 KV is NOT modelled (on the x86 test box its prefill was ~5x slower than f16; on the phone: unknown).
PP = [(0, 1 / 136.0, 1 / 136.0), (4096, 1 / 22.6, 1 / 17.2), (8192, 1 / 10.9, 1 / 10.6), (16384, 1 / 7.2, 1 / 7.2)]
TG = [(0, 1 / 24.7, 1 / 24.7), (4096, 1 / 5.27, 1 / 4.35), (8192, 1 / 2.23, 1 / 2.19), (16384, 1 / 1.23, 1 / 1.23)]
OFF8B = {"pp": (1 / 17.3 - 1 / 22.6, 1 / 17.3 - 1 / 17.2), "tg": (1 / 4.52 - 1 / 5.27, 1 / 4.52 - 1 / 4.35)}
ANSWER_TOKENS = 130          # 10 short key=value lines
def _is8b(model): return bool(re.search(r"(7|8|9)b", model.lower()))
def _cost(pts, d, col):
    for (d0, *c0), (d1, *c1) in zip(pts, pts[1:]):
        if d <= d1 or d1 == pts[-1][0]:
            return c0[col] + (c1[col] - c0[col]) * (d - d0) / (d1 - d0)
def _pp_total(n, col, off):
    step = 256; return sum((_cost(PP, d + step / 2, col) + off) * min(step, n - d) for d in range(0, n, step))
def estimate_s(model, n):
    o = OFF8B["pp"] if _is8b(model) else (0, 0)
    v = [_pp_total(n, c, max(0.0, o[c])) for c in (0, 1)]; return min(v), max(v)
def estimate_decode_s(model, n):
    o = OFF8B["tg"] if _is8b(model) else (0, 0)
    v = [(_cost(TG, n, c) + max(0.0, o[c])) * ANSWER_TOKENS for c in (0, 1)]; return min(v), max(v)
def fmt_range(r):
    lo, hi = r
    if hi < 90: return "~%d s" % hi if hi - lo < 5 else "%d-%d s" % (lo, hi)
    lo, hi = round(lo / 60), max(1, round(hi / 60))
    return "~%d min" % hi if lo == hi else "%d-%d min" % (lo, hi)
def cmd_estimate(a):
    d, c, kv = parse_spec(a.spec); n = int(SIZES[d] * 0.93) if c >= SIZES[d] else c - 300
    p, g = estimate_s(a.model_name, n), estimate_decode_s(a.model_name, n)
    print("ESTIMATE %s %s: ~%d prompt tokens; prefill %s + answer %s = %s total, phone cooled (+ model load; up to ~1.5x if warm)" % (
        a.model_name, a.spec, n, fmt_range(p), fmt_range(g), fmt_range((p[0] + g[0], p[1] + g[1]))))

# ---------------------------------------------------------------- report
def cmd_report(a):
    rows = []
    for f in sorted(glob.glob(os.path.join(a.dir, "func_*.csv"))):
        if os.path.basename(f) == "func_report.csv": continue   # our own previous output
        rows += list(csv.DictReader(open(f)))
    done = {(r["run_id"], r["model"], r["spec"]) for r in rows}
    for f in sorted(glob.glob(os.path.join(a.dir, "func_progress_*.txt"))):
        rid = os.path.basename(f)[len("func_progress_"):-4]; open_ = {}
        for line in open(f):
            p = line.strip().split(",")
            if len(p) >= 3 and p[0] == "STARTING": open_[(p[1], p[2])] = 1
            if len(p) >= 3 and p[0] == "END": open_.pop((p[1], p[2]), None)
        for (m, s) in open_:
            if (rid, m, s) not in done:
                rows.append({"run_id": rid, "model": m, "spec": s, "status": "KILLED (Termux closed)", "phone": os.path.basename(a.dir.rstrip("/"))})
    hdr = ["phone", "model", "spec", "truncated", "prompt_tokens", "needles_in_context", "correct", "recall",
           "not_found", "wrong", "prefill_tps", "decode_tps", "ttft_s", "wall_s", "status"]
    print(",".join(hdr))
    for r in rows: print(",".join(str(r.get(h, "")) for h in hdr))

# ---------------------------------------------------------------- selftest
def cmd_selftest(a):
    ok = True
    for sk in SIZES:
        paras = build_doc(sk); txt = "\n\n".join(paras)
        for k, s, *_ in NEEDLES:
            if txt.count(s) != 1: print("FAIL needle %s count in %s" % (k, sk)); ok = False
        if build_doc(sk) != paras: print("FAIL not deterministic", sk); ok = False
    filler = "\n\n".join(_pool(300))
    if BANNED.search(filler): print("FAIL filler contains banned term"); ok = False
    doc = "\n\n".join(build_doc("4k"))
    perfect = "\n".join("%s=%s" % (k, e) for k, _, _, e, _ in NEEDLES)
    assert all(p["status"] == "correct" for p in score(perfect, doc)), "perfect answer not 10/10"
    messy = ("Here you go:\n- **release_manager**: Marisol Okafor\n`staging_db_port=64310`\npdf_licence_key = kx58tq9021\n"
             "upload_limit_mb=NOT_FOUND\nwebhook_backoff_s: 47 seconds\nandroid_codename=Marlin\n")
    st = [p["status"] for p in score(messy, doc)]
    exp = ["correct", "wrong", "correct", "not_found", "correct", "wrong"] + ["missing"] * 4
    if st != exp: print("FAIL scorer", st); ok = False
    half = "\n\n".join(build_doc("16k")[len(build_doc("16k")) // 2:])
    kept = [p["in_context"] for p in score("", half)]
    if kept != [False] * 5 + [True] * 5: print("FAIL in_context", kept); ok = False
    print("selftest", "OK" if ok else "FAILED"); sys.exit(0 if ok else 1)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("gen"); sp.add_parser("selftest")
    for name in ("run", "oom", "skip"):
        p = sp.add_parser(name)
        p.add_argument("--server", default="http://127.0.0.1:8090"); p.add_argument("--phone", required=True)
        p.add_argument("--backend", default="cpu"); p.add_argument("--model-name", required=True)
        p.add_argument("--spec", required=True); p.add_argument("--out", required=True)
        p.add_argument("--raw-dir", default="."); p.add_argument("--run-id", default="")
        p.add_argument("--load-s", default=""); p.add_argument("--temp", default=""); p.add_argument("--mem", default="")
        p.add_argument("--timeout", type=int, default=6 * 3600); p.add_argument("--reason", default="")
    p = sp.add_parser("estimate"); p.add_argument("--model-name", required=True); p.add_argument("--spec", required=True)
    p = sp.add_parser("report"); p.add_argument("dir")
    a = ap.parse_args()
    {"gen": cmd_gen, "selftest": cmd_selftest, "run": cmd_run, "estimate": cmd_estimate, "report": cmd_report,
     "oom": lambda a: cmd_mark(a, "failed_to_load"), "skip": lambda a: cmd_mark(a, "skipped")}[a.cmd](a)

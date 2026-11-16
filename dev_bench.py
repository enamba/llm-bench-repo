#!/usr/bin/env python3
"""
dev_bench.py - "developer" scenario: the phone as a local coding assistant (talks to llama-server).

  run       asks every question in scenarios/dev_questions.json, grades the answers, appends rows to a JSONL file
  oom       records "failed to load" rows for a model (when the server does not start)
  selftest  runs every grader on the reference answer (must pass) and on a wrong answer (must fail); no server needed
  summary   JSONL -> CSV without the long texts (+ pass rate per model printed to the terminal)
  blind     JSONL -> blind grading pack (dev_blind_grading.csv + dev_blind_key.csv)
  regrade   re-runs the official graders on the answers saved in a JSONL (off-device, no server needed) and writes
            phone grade vs regrade per task. Use it when the on-device sandbox could not execute shell tools
            (on the Fold 8 every shell/git grader returned rc=126 "Permission denied").

Objective grading (auto_grade = pass | fail | blocked | no_code | not_run):
  shell / git     the command is extracted from the answer and EXECUTED inside a fresh temporary directory that only
                  contains fixture files (or a small fixture git repository), with a timeout, a minimal environment
                  (HOME = the sandbox) and a list of forbidden commands/paths; then the stdout or the files are checked
  python_tests    the code block is run with python3 together with hidden assert-based tests (timeout)
  node_tests      same with node
  sql             the query runs on an in-memory SQLite database with fixture rows; the rows are compared
  regex_*         the pattern is tested with Python's re module against positive / negative examples
  keywords        expected keywords in the answer (lenient; the blind 1-5 rubric is the final word)
The sandbox is a best-effort guard for a phone without root (no containers); answers are deterministic (temp 0),
and DEV_EXEC=0 / --no-exec skips execution and only records the extracted commands.
Standard library only (python3; node is optional - node questions become not_run without it).
"""
import argparse, csv, json, os, random, re, shutil, sqlite3, subprocess, sys, tempfile, time
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
SCEN = os.path.join(HERE, "scenarios", "dev_questions.json")
FIXDIR = os.path.join(HERE, "scenarios", "dev_fixtures")
TIMEOUT = 15

# ------------------------------------------------------------------ safety
FORBIDDEN_CMDS = r"""sudo|su|doas|rm|rmdir|shred|dd|mkfs\S*|mount|umount|shutdown|reboot|poweroff|halt|curl|wget|ssh|scp|sftp|
rsync|nc|ncat|netcat|telnet|ftp|pkg|apt|apt-get|dpkg|pacman|pip|pip3|npm|npx|yarn|kill|pkill|killall|chmod|chown|chgrp|
eval|exec|source|python|python2|python3|node|perl|ruby|php|lua|crontab|at|nohup|setsid|am|pm|cmd|settings|svc|input|
termux-[\w-]+|adb|fastboot|ln|truncate|mkfifo""".replace("\n", "")
RE_FORBIDDEN = re.compile(r"(?:^|[\s;&|`(]|\$\()(?:" + FORBIDDEN_CMDS + r")(?=$|[\s;&|)`])")
RE_GIT_FORBIDDEN = re.compile(r"\bgit\s+(push|pull|fetch|clone|remote|submodule|config\s+--(global|system)|gc|prune|filter-branch|daemon|credential)\b")
ROOT_DIRS = ("etc|data|sdcard|storage|system|system_ext|proc|dev|sys|usr|bin|sbin|lib|lib64|home|root|tmp|var|mnt|vendor|apex|"
             "opt|run|srv|boot|odm|product|config|cache|acct|efs|metadata|Users|private|Volumes|media|snap")
RE_ABS_PATH = re.compile(r"(?<![\w.:-])/(?!dev/null\b|dev/stdin\b|dev/stdout\b|dev/stderr\b)(?:" + ROOT_DIRS + r")(?=/|\b)"
                         r"|(?:^|[\s=<>|;&(])/(?!dev/(?:null|stdin|stdout|stderr)\b)(?=[\w.-])"   # unquoted absolute path
                         r"|(?:^|[\s;&|(])/(?=$|[\s;&|)])|~|\$\{?(?:HOME|PREFIX|TMPDIR|EXTERNAL_STORAGE)\b|\.\.(?=/|\s|$|;|&|\||\))")
RE_CODE_FORBIDDEN = re.compile(r"\b(subprocess|os\.system|os\.popen|os\.remove|os\.unlink|os\.rmdir|shutil\.rmtree|socket|urllib|import\s+requests|from\s+requests|"
                               r"http\.client|child_process|fs\.rm|fs\.unlink|fs\.rmdir|fs\.writeFile|process\.kill|ctypes|__import__\(['\"]os)")

def shell_unsafe(cmd):
    if RE_FORBIDDEN.search(cmd): return "forbidden command: " + RE_FORBIDDEN.search(cmd).group(0).strip()
    if RE_GIT_FORBIDDEN.search(cmd): return "forbidden git subcommand: " + RE_GIT_FORBIDDEN.search(cmd).group(0)
    if RE_ABS_PATH.search(cmd): return "path outside the sandbox: " + RE_ABS_PATH.search(cmd).group(0)
    return None

def code_unsafe(code):
    m = RE_CODE_FORBIDDEN.search(code)
    return f"forbidden in code: {m.group(0)}" if m else None

# ------------------------------------------------------------------ extraction
RE_BLOCK = re.compile(r"```[ \t]*([\w+#.-]*)[^\n]*\n(.*?)```", re.S)

def blocks(answer):
    bs = [(lang.lower(), body.strip("\n")) for lang, body in RE_BLOCK.findall(answer)]
    if answer.count("```") % 2 == 1:  # answer cut by n_predict inside a code block: keep the unfinished block
        m = re.search(r"```[ \t]*([\w+#.-]*)[^\n]*\n((?:(?!```).)*)$", answer, re.S)
        if m: bs.append((m.group(1).lower(), m.group(2).strip("\n")))
    return bs

def extract_code(answer, langs, entry=None):
    bs = blocks(answer)
    cand = [b for l, b in bs if l in langs] or [b for l, b in bs if l == ""] or [b for _, b in bs]
    if entry:
        withdef = [b for b in cand if re.search(r"(def|function|const|let|var)\s+" + re.escape(entry) + r"\b", b)]
        if withdef: return withdef[0]
    if cand: return cand[0]
    if entry and re.search(r"(def|function)\s+" + re.escape(entry) + r"\b", answer): return answer.strip()
    return None

def extract_shell(answer):
    code = extract_code(answer, ("bash", "sh", "shell", "console", "zsh"))
    if code is None:
        line = next((l.strip().strip("`") for l in answer.splitlines() if l.strip()), "")
        code = line or None
    if code is None: return None
    lines = []
    for l in code.splitlines():
        l = re.sub(r"^\s*(\$|~\s*\$|#\s)\s*", "", l) if re.match(r"^\s*(\$|~\s*\$)\s", l) else l
        if l.strip() and not l.strip().startswith("#"): lines.append(l)
    return "\n".join(lines) or None

def extract_regex(answer):
    code = extract_code(answer, ("python", "py", "regex", "re", "text")) or answer.strip().splitlines()[0]
    m = re.search(r"""\br?(?:'''|\"\"\")(.+?)(?:'''|\"\"\")""", code, re.S) or \
        re.search(r"""(?:^|[\s(=,])r(["'])(.+?)\1""", code) or re.search(r"""(?:^|[\s(=,])(["'])(.+?)\1""", code)
    if m:
        pat = m.group(m.lastindex)
        raw = re.search(r"(?:^|[\s(=,])r[\"']", code) is not None
        return pat if raw else pat.encode().decode("unicode_escape")
    return code.strip().strip("/").strip()

# ------------------------------------------------------------------ fixtures / sandbox
def make_sandbox(fixtures, kind):
    d = tempfile.mkdtemp(prefix="devbench_")
    if kind == "files":
        for rel, content in fixtures["files"].items():
            p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write(content)
    elif kind == "git":
        env = git_env(d)
        run(["git", "init", "-q", "-b", "main"], d, env)
        for i, (rel, content, msg) in enumerate(fixtures["git"]["commits"]):
            open(os.path.join(d, rel), "w").write(content)
            e = dict(env, GIT_AUTHOR_DATE=f"2026-11-0{i+1}T10:00:00Z", GIT_COMMITTER_DATE=f"2026-11-0{i+1}T10:00:00Z")
            run(["git", "add", rel], d, e); run(["git", "commit", "-q", "-m", msg], d, e)
    return d

def git_env(d):
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": d, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Bench", "GIT_AUTHOR_EMAIL": "bench@example.com",
            "GIT_COMMITTER_NAME": "Bench", "GIT_COMMITTER_EMAIL": "bench@example.com", "GIT_TERMINAL_PROMPT": "0", "PAGER": "cat",
            "GIT_PAGER": "cat", "TMPDIR": d, "PREFIX": os.environ.get("PREFIX", "")}

def run(argv, cwd, env, inp=None, timeout=TIMEOUT):
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, input=inp, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError as e:
        return 127, "", str(e)

def norm_lines(s): return [l.strip().removeprefix("./") for l in s.strip().splitlines() if l.strip()]

def check_shell(cmd, check, fixtures, kind, do_exec):
    why = shell_unsafe(cmd)
    if why: return "blocked", why
    if not do_exec: return "not_run", "execution disabled"
    d = make_sandbox(fixtures, kind)
    try:
        rc, out, err = run(["bash", "--noprofile", "--norc", "-c", cmd], d, git_env(d))
        det = f"rc={rc} stdout={out.strip()[:120]!r} stderr={err.strip()[:120]!r}"
        if rc == 124: return "fail", "timeout"
        ok = True
        if "stdout_equals" in check: ok &= norm_lines(out) == norm_lines(check["stdout_equals"])
        if "stdout_lines_set" in check:
            lines = norm_lines(out); ok &= set(lines) == set(check["stdout_lines_set"]) and len(lines) == len(check["stdout_lines_set"])
            if check.get("sorted"): ok &= lines == sorted(lines)
        for rel in check.get("files_exist", []): ok &= os.path.exists(os.path.join(d, rel))
        for rel in check.get("files_absent", []): ok &= not os.path.exists(os.path.join(d, rel))
        for rel, content in check.get("file_equals", {}).items():
            p = os.path.join(d, rel); ok &= os.path.exists(p) and open(p).read() == content
        if "stdout_git_hash_of" in check:
            _, full, _ = run(["git", "log", "--format=%H", "--grep", check["stdout_git_hash_of"]], d, git_env(d))
            toks = out.split(); ok &= len(toks) == 1 and len(toks[0]) >= 7 and full.strip().startswith(toks[0])
        if "git_branch" in check:
            _, br, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], d, git_env(d))
            _, head, _ = run(["git", "rev-parse", "HEAD"], d, git_env(d)); _, main, _ = run(["git", "rev-parse", "main"], d, git_env(d))
            ok &= br.strip() == check["git_branch"] and head.strip() == main.strip()
        return ("pass" if ok else "fail"), det
    finally:
        shutil.rmtree(d, ignore_errors=True)

def run_code(lang, source, do_exec):
    if not do_exec: return "not_run", "execution disabled"
    exe = shutil.which("python3" if lang == "python" else "node")
    if not exe: return "not_run", f"{lang} not installed"
    d = tempfile.mkdtemp(prefix="devbench_")
    try:
        f = os.path.join(d, "t.py" if lang == "python" else "t.js"); open(f, "w").write(source)
        argv = [exe, "-I", f] if lang == "python" else [exe, f]
        rc, out, err = run(argv, d, {"PATH": os.environ.get("PATH", ""), "HOME": d, "TMPDIR": d, "LANG": "C.UTF-8"})
        if rc == 0: return "pass", "all tests passed"
        lines = [l for l in err.strip().splitlines() if l.strip() and not l.startswith("Node.js v")] or ["rc=%d" % rc]
        errl = [l for l in lines if re.search(r"(Error|Exception)\b", l)]
        return "fail", ("timeout" if rc == 124 else (errl or lines)[-1].strip()[:200])
    finally:
        shutil.rmtree(d, ignore_errors=True)

def norm(s): return " ".join(s.lower().split())

def grade(q, answer, fixtures, do_exec=True):
    """Returns (auto_grade, extracted, detail)."""
    g = q["grader"]; t = g["type"]
    if t == "keywords":
        a = norm(answer); hits = [k for k in g["keywords"] if norm(k) in a]
        ok = len(hits) == len(g["keywords"]) if g.get("all") else bool(hits)
        return ("pass" if ok else "fail"), "", f"keywords found: {hits}"
    if t == "shell":
        cmd = extract_shell(answer)
        if not cmd: return "no_code", "", "no command found"
        res, det = check_shell(cmd, g["check"], fixtures, q.get("fixture", "files"), do_exec)
        return res, cmd, det
    if t in ("python_tests", "node_tests"):
        lang = "python" if t == "python_tests" else "javascript"
        code = extract_code(answer, ("python", "py", "python3") if lang == "python" else ("javascript", "js", "node", "jsx", "ts"), g["entry"])
        if not code: return "no_code", "", "no code block"
        why = code_unsafe(code)
        if why: return "blocked", code, why
        if lang == "python":
            pre = ""
            if g.get("module"):
                pre = open(os.path.join(FIXDIR, g["module"])).read()
                pre = re.sub(r'\nif __name__ == "__main__":\n    demo\(\)\n?', "\n", pre)
            src = pre + "\n\n# ---- candidate ----\n" + code + "\n\n# ---- hidden tests ----\n" + g["tests"] + "\nprint('OK')\n"
            res, det = run_code("python", src, do_exec)
        else:
            body = re.sub(r"^\s*export\s+(default\s+)?", "", code, flags=re.M)
            body = re.sub(r"^\s*module\.exports.*$", "", body, flags=re.M)
            src = body + "\n\n// ---- hidden tests ----\nconst __assert = require('node:assert');\n" + g["tests"] + "\nconsole.log('OK');\n"
            res, det = run_code("javascript", src, do_exec)
        return res, code, det
    if t == "sql":
        code = extract_code(answer, ("sql", "sqlite"))
        if not code: return "no_code", "", "no code block"
        stmt = code.strip().rstrip(";").strip()
        if ";" in stmt or not re.match(r"(?is)^\s*(select|with)\b", stmt): return "blocked", code, "only one SELECT/WITH statement allowed"
        db = sqlite3.connect(":memory:"); db.executescript(g["setup"])
        try:
            rows = [list(r) for r in db.execute(stmt).fetchall()]
        except sqlite3.Error as e:
            return "fail", code, f"sqlite error: {e}"
        exp = g["expected_rows"]
        rows_n = [[(float(v) if isinstance(v, (int, float)) else v) for v in r] for r in rows]
        ok = rows_n == exp if g.get("ordered") else sorted(map(str, rows_n)) == sorted(map(str, exp))
        return ("pass" if ok else "fail"), code, f"rows={rows_n[:6]}"
    if t in ("regex_fullmatch", "regex_findall"):
        pat = extract_regex(answer)
        if not pat: return "no_code", "", "no pattern"
        try:
            rx = re.compile(pat)
        except re.error as e:
            return "fail", pat, f"invalid regex: {e}"
        if t == "regex_fullmatch":
            bad = [s for s in g["match"] if not rx.fullmatch(s)] + [s for s in g["no_match"] if rx.fullmatch(s)]
            return ("pass" if not bad else "fail"), pat, f"misclassified: {bad}"
        found = [m.group(0) for m in rx.finditer(g["text"])]
        return ("pass" if found == g["expected"] else "fail"), pat, f"found={found}"
    return "rubric", "", "no objective grader"

# ------------------------------------------------------------------ server
def post(url, body, timeout=3600):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def build_messages(q, system):
    content = q["question"]
    if q.get("context_file"):
        src = open(os.path.join(FIXDIR, q["context_file"])).read()
        content = f"FILE {q['context_file']}:\n```python\n{src}\n```\n\nQUESTION: {q['question']}"
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]

def count_tokens(base, messages):
    prompt = post(base + "/apply-template", {"messages": messages})["prompt"]
    return len(post(base + "/tokenize", {"content": prompt, "add_special": True, "parse_special": True})["tokens"])

def chat_stream(base, messages, n_predict):
    body = {"model": "local", "messages": messages, "stream": True, "stream_options": {"include_usage": True},
            "temperature": 0, "top_k": 1, "seed": 42, "max_tokens": n_predict, "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time(); ttft = None; text = []; timings = {}; usage = {}
    with urllib.request.urlopen(req, timeout=3600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"): continue
            p = line[5:].strip()
            if p == "[DONE]": break
            ev = json.loads(p)
            timings = ev.get("timings") or timings; usage = ev.get("usage") or usage
            for ch in ev.get("choices") or []:
                c = (ch.get("delta") or {}).get("content") or ""
                rc = (ch.get("delta") or {}).get("reasoning_content") or ""
                if (c or rc) and ttft is None: ttft = time.time() - t0
                text.append(c)
    return "".join(text), ttft, time.time() - t0, timings, usage

# ------------------------------------------------------------------ commands
COLS = ["ts", "phone", "backend", "test", "question_id", "category", "grader", "model", "n_ctx", "kv_cache", "n_predict",
        "input_tokens", "output_tokens", "cached_tokens", "prefill_tps", "gen_tps", "ttft_ms", "total_time_s",
        "oom", "error", "auto_grade", "grade_detail", "blind_score_1_5"]

def load():
    return json.load(open(SCEN))

def base_row(a, q):
    return {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "phone": a.phone, "backend": a.backend, "test": "dev", "scenario": "dev",
            "question_id": q["id"], "category": q["category"], "grader": q["grader"]["type"], "question": q["question"],
            "expected": q.get("expected", ""), "model": a.model_name, "n_ctx": a.n_ctx, "kv_cache": a.kv, "n_predict": a.n_predict,
            "input_tokens": None, "output_tokens": None, "cached_tokens": None, "prefill_tps": None, "gen_tps": None,
            "ttft_ms": None, "total_time_s": None, "oom": False, "error": "", "answer": "", "extracted": "",
            "auto_grade": "not_run", "grade_detail": "", "blind_score_1_5": ""}

def cmd_run(a):
    data = load(); base = a.server.rstrip("/"); only = set(filter(None, a.only.split(",")))
    out = open(a.out, "a")
    for q in data["questions"]:
        if only and q["id"] not in only: continue
        row = base_row(a, q); msgs = build_messages(q, data["system"])
        try:
            n_in = count_tokens(base, msgs); row["input_tokens"] = n_in
            if n_in + a.n_predict > a.n_ctx:
                row.update(error=f"context_exceeded: prompt {n_in} + n_predict {a.n_predict} > n_ctx {a.n_ctx}", auto_grade="not_run")
            else:
                text, ttft, total, tim, use = chat_stream(base, msgs, a.n_predict)
                row.update(answer=text, ttft_ms=round(ttft * 1000, 1) if ttft else None, total_time_s=round(total, 2),
                           output_tokens=tim.get("predicted_n", use.get("completion_tokens")), cached_tokens=tim.get("cache_n"),
                           prefill_tps=round(tim["prompt_per_second"], 2) if tim.get("prompt_per_second") else None,
                           gen_tps=round(tim["predicted_per_second"], 2) if tim.get("predicted_per_second") else None)
                res, ext, det = grade(q, text, data["fixtures"], not a.no_exec)
                row.update(auto_grade=res, extracted=ext, grade_detail=det)
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            row.update(error=f"server error: {e}", oom=True, auto_grade="failed")
            out.write(json.dumps(row, ensure_ascii=False) + "\n"); out.flush()
            print(f"[{q['id']}] server error ({e}) - stopping (likely OOM)", file=sys.stderr); sys.exit(2)
        out.write(json.dumps(row, ensure_ascii=False) + "\n"); out.flush()
        print(f"[{q['id']}] {q['category']:<16} in={row['input_tokens']} out={row['output_tokens']} ttft={row['ttft_ms']}ms "
              f"gen={row['gen_tps']} t/s -> {row['auto_grade']} {row['error'] or ''}", file=sys.stderr)

def cmd_oom(a):
    data = load()
    with open(a.out, "a") as out:
        for q in data["questions"]:
            row = base_row(a, q); row.update(oom=True, error=a.reason, auto_grade="failed")
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

def cmd_selftest(a):
    data = load(); bad = 0
    for q in data["questions"]:
        r1, _, d1 = grade(q, q["reference"], data["fixtures"])
        r2, _, d2 = grade(q, q["wrong"], data["fixtures"])
        ok = r1 == "pass" and r2 != "pass"; bad += not ok
        print(f"{'OK ' if ok else 'BAD'} {q['id']:<3} {q['grader']['type']:<16} reference={r1:<8} wrong={r2:<8} {'' if ok else d1 + ' | ' + d2}")
    # sandbox guard checks
    for cmd in ["rm -rf ~", "cat /etc/passwd", "ls '/sdcard/DCIM'", "cd / && ls", "curl http://x", "cd .. && ls", "git push origin main",
                "python3 -c 1", "echo $HOME", "ls $PREFIX/bin", "find /data -name x",
                "cd /app && mv a b", "cp notes.txt /mnt2/x", "echo x > /anything"]:
        why = shell_unsafe(cmd); bad += why is None
        print(f"{'OK ' if why else 'BAD'} guard blocks {cmd!r}: {why}")
    for cmd in ["sed -i 's/localhost/127.0.0.1/g' config.ini", "find . -name '*.py' | sort", "grep -c ERROR app.log > /dev/null",
                "awk '/ERROR/' app.log | wc -l", "cut -d/ -f2 paths.txt", "sed 's/data/x/' config.ini"]:
        why = shell_unsafe(cmd); bad += why is not None
        print(f"{'OK ' if not why else 'BAD'} guard allows {cmd!r}")
    print("selftest:", "PASSED" if not bad else f"{bad} problem(s)")
    sys.exit(1 if bad else 0)

def rows_from(files):
    for f in files:
        for line in open(f):
            if line.strip(): yield json.loads(line)

def cmd_summary(a):
    rows = list(rows_from(a.files))
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore"); w.writeheader()
        for r in rows: w.writerow(r)
    agg = {}
    for r in rows:
        k = (r["phone"], r["model"], r["n_ctx"]); s = agg.setdefault(k, {"pass": 0, "total": 0, "gen": [], "ttft": [], "oom": False})
        s["total"] += 1; s["pass"] += r["auto_grade"] == "pass"; s["oom"] |= bool(r["oom"])
        if r.get("gen_tps"): s["gen"].append(r["gen_tps"])
        if r.get("ttft_ms"): s["ttft"].append(r["ttft_ms"])
    med = lambda v: sorted(v)[len(v) // 2] if v else None
    print("phone,model,n_ctx,passed,total,median_gen_tps,median_ttft_ms,oom")
    for (ph, m, c), s in sorted(agg.items()):
        print(f"{ph},{m},{c},{s['pass']},{s['total']},{med(s['gen'])},{med(s['ttft'])},{s['oom']}")
    print(a.out, file=sys.stderr)

def cmd_blind(a):
    rows = [r for r in rows_from(a.files) if r.get("answer")]
    rnd = random.Random(a.seed); os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "dev_blind_grading.csv"), "w", newline="") as f1, open(os.path.join(a.out, "dev_blind_key.csv"), "w", newline="") as f2:
        w1, w2 = csv.writer(f1), csv.writer(f2)
        w1.writerow(["code", "question_id", "category", "question", "expected", "answer", "score_1_5", "comment"])
        w2.writerow(["code", "phone", "model", "n_ctx", "question_id", "auto_grade"])
        for i, r in enumerate(sorted(rows, key=lambda r: (r["question_id"], rnd.random()))):
            code = f"D{i+1:03d}"
            w1.writerow([code, r["question_id"], r["category"], r["question"], r["expected"], r["answer"], "", ""])
            w2.writerow([code, r["phone"], r["model"], r["n_ctx"], r["question_id"], r["auto_grade"]])
    print(f"{len(rows)} answers -> {a.out}/dev_blind_grading.csv (open dev_blind_key.csv only at the end)")

REGRADE_COLS = ["ts", "model", "question_id", "category", "grader", "n_ctx", "kv_cache", "input_tokens", "output_tokens",
                "prefill_tps", "gen_tps", "ttft_ms", "total_time_s", "oom", "error", "phone_auto_grade", "regrade_auto_grade",
                "grade_changed", "regrade_extracted", "regrade_detail", "phone_grade_detail"]

def cmd_regrade(a):
    data = load(); qs = {q["id"]: q for q in data["questions"]}
    rows = list(rows_from(a.files)); out = []
    for r in rows:
        q = qs.get(r["question_id"])
        if q is None or r.get("oom") or not r.get("answer"):
            res, ext, det = r.get("auto_grade", ""), r.get("extracted", ""), r.get("grade_detail", "")
        else:
            res, ext, det = grade(q, r["answer"], data["fixtures"], True)
        o = {k: r.get(k, "") for k in REGRADE_COLS}
        o.update(phone_auto_grade=r.get("auto_grade", ""), regrade_auto_grade=res, grade_changed=res != r.get("auto_grade", ""),
                 regrade_extracted=ext, regrade_detail=det, phone_grade_detail=r.get("grade_detail", ""))
        out.append(o)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REGRADE_COLS); w.writeheader(); w.writerows(out)
    agg = {}
    for o in out:
        s = agg.setdefault(o["model"], [0, 0, 0]); s[0] += o["phone_auto_grade"] == "pass"; s[1] += o["regrade_auto_grade"] == "pass"; s[2] += 1
    print("model,phone_pass,regrade_pass,total")
    for m, (p1, p2, n) in agg.items(): print(f"{m},{p1},{p2},{n}")
    print(a.out, file=sys.stderr)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1]); sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "oom"):
        p = sp.add_parser(name)
        p.add_argument("--server", default="http://127.0.0.1:8090"); p.add_argument("--phone", required=True)
        p.add_argument("--backend", default="cpu"); p.add_argument("--model-name", required=True)
        p.add_argument("--n-ctx", type=int, required=True); p.add_argument("--kv", default="f16")
        p.add_argument("--n-predict", type=int, default=768); p.add_argument("--out", required=True)
        p.add_argument("--only", default="", help="comma-separated question ids")
        p.add_argument("--no-exec", action="store_true", help="do not execute commands/code, only extract them")
        p.add_argument("--reason", default="server did not start (likely OOM)")
    sp.add_parser("selftest")
    p = sp.add_parser("summary"); p.add_argument("files", nargs="+"); p.add_argument("--out", default="dev_summary.csv")
    p = sp.add_parser("blind"); p.add_argument("files", nargs="+"); p.add_argument("--out", default="dev_blind_grading"); p.add_argument("--seed", type=int, default=7)
    p = sp.add_parser("regrade"); p.add_argument("files", nargs="+"); p.add_argument("--out", default="dev_regrade.csv")
    a = ap.parse_args()
    {"regrade": cmd_regrade, "run": cmd_run, "oom": cmd_oom, "selftest": cmd_selftest, "summary": cmd_summary, "blind": cmd_blind}[a.cmd](a)

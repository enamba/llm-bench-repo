#!/usr/bin/env python3
"""Helpers for the thinking-off quality re-run (P01-P10).

Used by run_quality_nothink.sh. Standard library only (python3).

  extract   print prompt ids (or one prompt) from quality_prompts.md
  flag      classify an answer: OK / THINK_BUT_HAS_FINAL / THINK_NO_FINAL / EMPTY
  ask       POST /v1/chat/completions, write txt/json, append perf+flags rows
  selftest  deterministic checks, no server needed
"""
from __future__ import annotations

import argparse, json, os, re, sys, time, urllib.error, urllib.request

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"
NO_THINK = "/no_think"


def extract_prompts(md_path):
    text = open(md_path, encoding="utf-8").read()
    out = []
    for m in re.finditer(r"^### (P\d+)\s*$", text, re.M):
        pid = m.group(1)
        rest = text[m.end():]
        m2 = re.search(r"```text\n(.*?)```", rest, re.S)
        if not m2:
            raise SystemExit(f"no ```text block for {pid} in {md_path}")
        out.append((pid, m2.group(1).strip("\n")))
    if len(out) != 10:
        raise SystemExit(f"expected 10 prompts, found {len(out)} in {md_path}")
    return out


def wrap_user(prompt, suffix=True):
    """Original prompt, optionally plus the Qwen soft switch on its own last line."""
    body = prompt.rstrip()
    if suffix:
        if not body.endswith(NO_THINK):
            body = body + "\n" + NO_THINK
    return body


def classify(content, reasoning_content=""):
    """Return (flag, has_think, unclosed, final_empty, final_text).

    flag:
      OK                 no think leak, non-empty final answer
      THINK_BUT_HAS_FINAL think/reasoning present, but a finished answer exists
      THINK_NO_FINAL     think/reasoning with no finished answer (the failure mode of the first run)
      EMPTY              nothing usable
    """
    content = content or ""
    reasoning = reasoning_content or ""
    has_open = THINK_OPEN in content or THINK_OPEN in reasoning
    has_reason = bool(reasoning.strip())
    has_think = has_open or has_reason

    if THINK_CLOSE in content:
        final = content.split(THINK_CLOSE, 1)[1].strip()
        unclosed = False
    elif THINK_OPEN in content:
        final = ""
        unclosed = True
    else:
        final = content.strip()
        unclosed = THINK_OPEN in reasoning and THINK_CLOSE not in reasoning

    # official no-think prefill is an empty <think>\\n\\n</think> in the *prompt*,
    # not in the completion. If the model echoes a fully empty think block and
    # then answers, treat the text after </think> as the final answer.
    if not final and has_reason and not unclosed:
        # thinking went to reasoning_content; content is the final channel
        final = content.strip()

    final_empty = not bool(final)
    if not has_think and not final_empty:
        flag = "OK"
    elif has_think and not final_empty:
        flag = "THINK_BUT_HAS_FINAL"
    elif has_think and final_empty:
        flag = "THINK_NO_FINAL"
    else:
        flag = "EMPTY"
    return flag, has_think, unclosed, final_empty, final


def post_chat(url, body, timeout):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer no-key"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_message(resp):
    choices = resp.get("choices") or []
    if not choices:
        return "", "", ""
    ch = choices[0]
    msg = ch.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ch.get("reasoning_content") or ""
    finish = ch.get("finish_reason") or ""
    return content, reasoning, finish


def timings_of(resp):
    tm = resp.get("timings") or {}
    usage = resp.get("usage") or {}
    prompt_n = tm.get("prompt_n", usage.get("prompt_tokens"))
    cache_n = tm.get("cache_n")
    if cache_n is None:
        cache_n = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    predicted_n = tm.get("predicted_n", usage.get("completion_tokens"))
    prompt_ms = tm.get("prompt_ms")
    predicted_ms = tm.get("predicted_ms")
    prefill_tps = tm.get("prompt_per_second")
    gen_tps = tm.get("predicted_per_second")
    ttft_ms = None
    if prompt_ms is not None:
        if predicted_ms is not None and predicted_n:
            ttft_ms = round(float(prompt_ms) + float(predicted_ms) / float(predicted_n), 1)
        else:
            ttft_ms = round(float(prompt_ms), 1)
    return {
        "prompt_tokens": prompt_n if prompt_n is not None else "NA",
        "cached_tokens": cache_n if cache_n is not None else "NA",
        "gen_tokens": predicted_n if predicted_n is not None else "NA",
        "prompt_ms": round(float(prompt_ms), 2) if prompt_ms is not None else "NA",
        "gen_ms": round(float(predicted_ms), 2) if predicted_ms is not None else "NA",
        "prefill_tps": round(float(prefill_tps), 2) if prefill_tps is not None else "NA",
        "gen_tps": round(float(gen_tps), 2) if gen_tps is not None else "NA",
        "ttft_ms": ttft_ms if ttft_ms is not None else "NA",
    }


def cmd_extract(a):
    prompts = extract_prompts(a.prompts)
    if a.id:
        for pid, text in prompts:
            if pid == a.id:
                sys.stdout.write(wrap_user(text, not a.no_suffix).rstrip() + "\n")
                return
        raise SystemExit(f"unknown prompt {a.id}")
    for pid, text in prompts:
        wrapped = wrap_user(text, not a.no_suffix)
        print(f"{pid}\t{len(text)} chars\t{len(wrapped)} wrapped\tends={wrapped.splitlines()[-1]!r}")


def cmd_flag(a):
    content = a.content
    if a.file:
        content = open(a.file, encoding="utf-8").read()
    flag, has_think, unclosed, final_empty, final = classify(content, a.reasoning or "")
    print(f"flag={flag} has_think={int(has_think)} unclosed={int(unclosed)} final_empty={int(final_empty)} final_chars={len(final)}")
    sys.exit(0 if flag in ("OK", "THINK_BUT_HAS_FINAL") else 2)


def cmd_ask(a):
    user = wrap_user(a.prompt, not a.no_suffix)
    body = {
        "messages": [{"role": "user", "content": user}],
        "temperature": a.temp,
        "seed": a.seed,
        "max_tokens": a.n_predict,
        "chat_template_kwargs": {"enable_thinking": False},
        "cache_prompt": True,
    }
    t0 = time.time()
    err = ""
    resp = None
    try:
        resp = post_chat(a.server.rstrip("/") + "/v1/chat/completions", body, a.timeout)
        rc = 0
    except Exception as e:
        rc = 1
        err = f"{type(e).__name__}: {e}"
        resp = {"error": err}
    wall_s = round(time.time() - t0, 1)
    content, reasoning, finish = pick_message(resp) if isinstance(resp, dict) else ("", "", "")
    if err and not content:
        flag, has_think, unclosed, final_empty, final = "EMPTY", False, False, True, ""
    else:
        flag, has_think, unclosed, final_empty, final = classify(content, reasoning)
    os.makedirs(os.path.dirname(a.out_txt) or ".", exist_ok=True)
    with open(a.out_txt, "w", encoding="utf-8") as f:
        f.write("user\n")
        f.write(user)
        f.write("\nassistant\n")
        if reasoning:
            f.write("<think>\n")
            f.write(reasoning)
            if not reasoning.endswith("\n"):
                f.write("\n")
            f.write("</think>\n")
        f.write(content or "")
        if finish:
            f.write(f"\n[finish_reason={finish}]\n")
    with open(a.out_json, "w", encoding="utf-8") as f:
        json.dump({"request": {k: body[k] for k in body if k != "messages"},
                   "user": user, "response": resp, "wall_s": wall_s, "error": err,
                   "flag": flag, "has_think": has_think, "unclosed": unclosed,
                   "final_empty": final_empty, "finish_reason": finish},
                  f, ensure_ascii=False, indent=2)
    tm = timings_of(resp) if isinstance(resp, dict) else {k: "NA" for k in
        ("prompt_tokens", "cached_tokens", "gen_tokens", "prompt_ms", "gen_ms", "prefill_tps", "gen_tps", "ttft_ms")}
    perf_line = ",".join(str(x) for x in [
        a.phone, a.backend, a.model, a.prompt_id, rc, a.load_s,
        tm["prompt_tokens"], tm["cached_tokens"], tm["prompt_ms"], tm["ttft_ms"],
        tm["gen_tokens"], tm["gen_tps"], tm["prefill_tps"], wall_s, finish or "",
        flag, int(has_think), int(unclosed), int(final_empty),
    ])
    if a.perf_csv:
        new = not os.path.exists(a.perf_csv)
        with open(a.perf_csv, "a", encoding="utf-8") as f:
            if new:
                f.write("phone,backend,model,prompt_id,rc,load_s,prompt_tokens,cached_tokens,prompt_ms,ttft_ms,gen_tokens,gen_tps,prefill_tps,wall_s,finish_reason,flag,has_think,unclosed,final_empty\n")
            f.write(perf_line + "\n")
    print(f"[{a.prompt_id}] rc={rc} flag={flag} gen={tm['gen_tokens']} t/s={tm['gen_tps']} wall={wall_s}s {err}")
    # non-zero only for transport errors; THINK_NO_FINAL is recorded but does not stop the ladder
    sys.exit(rc)


def cmd_selftest(_a=None):
    bad = 0

    def check(name, got, exp):
        nonlocal bad
        if got != exp:
            print(f"FAIL {name}: got {got!r} expected {exp!r}")
            bad += 1
        else:
            print(f"OK   {name}")

    flag, has, uncl, empty, final = classify("Answer: 07:40", "")
    check("plain ok", (flag, has, empty, final), ("OK", False, False, "Answer: 07:40"))

    flag, has, uncl, empty, final = classify("<think>\nfoo\n</think>\n\nAnswer: 5 minutes", "")
    check("closed think with final", (flag, has, uncl, empty, final.startswith("Answer:")),
          ("THINK_BUT_HAS_FINAL", True, False, False, True))

    flag, has, uncl, empty, final = classify("<think>\nI am still thinking about 07:", "")
    check("unclosed think no final", (flag, has, uncl, empty), ("THINK_NO_FINAL", True, True, True))

    flag, has, uncl, empty, final = classify("", "long chain of thought without an answer")
    check("reasoning only", (flag, has, empty), ("THINK_NO_FINAL", True, True))

    flag, has, uncl, empty, final = classify("The farmer has 9 sheep.", "brief thought")
    check("reasoning plus content", (flag, has, empty), ("THINK_BUT_HAS_FINAL", True, False))

    flag, has, uncl, empty, final = classify("", "")
    check("empty", (flag, empty), ("EMPTY", True))

    wrapped = wrap_user("Hello.", True)
    check("suffix appended", wrapped.endswith("\n/no_think"), True)
    check("suffix not doubled", wrap_user(wrapped, True).count("/no_think"), 1)
    check("suffix can be disabled", wrap_user("Hello.", False), "Hello.")

    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quality_prompts.md")
    if os.path.isfile(here):
        ps = extract_prompts(here)
        check("10 prompts", len(ps), 10)
        check("P01 id", ps[0][0], "P01")
        check("P10 id", ps[-1][0], "P10")
        check("P01 has arrival question", "07:40" in open(here, encoding="utf-8").read() or "final destination" in ps[0][1], True)
        check("P10 is JSON instruction", "JSON" in ps[-1][1], True)
    else:
        print("SKIP prompt-file checks (quality_prompts.md not next to this script)")

    print("selftest: " + ("PASSED" if bad == 0 else f"{bad} problem(s)"))
    sys.exit(0 if bad == 0 else 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("extract")
    s.add_argument("--prompts", required=True)
    s.add_argument("--id")
    s.add_argument("--no-suffix", action="store_true")
    s.set_defaults(func=cmd_extract)

    s = sub.add_parser("flag")
    s.add_argument("--content", default="")
    s.add_argument("--reasoning", default="")
    s.add_argument("--file")
    s.set_defaults(func=cmd_flag)

    s = sub.add_parser("ask")
    s.add_argument("--server", required=True)
    s.add_argument("--prompt", required=True)
    s.add_argument("--prompt-id", required=True)
    s.add_argument("--model", required=True)
    s.add_argument("--out-txt", required=True)
    s.add_argument("--out-json", required=True)
    s.add_argument("--perf-csv", default="")
    s.add_argument("--phone", default="")
    s.add_argument("--backend", default="cpu")
    s.add_argument("--load-s", default="NA")
    s.add_argument("--temp", type=float, default=0.0)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--n-predict", type=int, default=2048)
    s.add_argument("--timeout", type=int, default=14400)
    s.add_argument("--no-suffix", action="store_true")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("selftest")
    s.set_defaults(func=cmd_selftest)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()

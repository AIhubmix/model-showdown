#!/usr/bin/env python3
"""One-shot model showdown: same prompt -> N models via aihubmix chat/completions.

Usage: python3 run_showdown.py <episode_dir> [--models m1,m2,...] [--record SECONDS]
Reads <episode_dir>/task.md, writes work_<model>/index.html, raw_<model>.json, metrics.json

- Streams (SSE) with a 30s heartbeat log — long-thinking models (Kimi K3) get their
  connection killed by upstream proxies if no bytes flow for ~10-15 min.
- metrics.json is written incrementally after EACH model finishes (a killed run
  loses nothing).
- --record N pipelines recording: each model's artifact is recorded for N seconds
  as soon as its generation finishes, overlapping the slower models' generation.
"""
import argparse
import base64
import concurrent.futures as cf
import glob as globmod
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GATEWAY = "https://aihubmix.com/v1/chat/completions"
API_KEY = os.environ["AIHUBMIX_API_KEY"]

# USD per 1M tokens (input, output)
PRICING = {
    "kimi-k3": (3.0, 15.0),
    "coding-kimi-k3": (3.0, 15.0),  # list price; the coding channel bills differently
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-8-think": (5.0, 25.0),
    "gpt-5.6-sol": (5.0, 30.0),
}

# display name, max_tokens override (K3 always thinks at max effort — reasoning
# eats the budget). No fallback channels: the badge must reflect the real model
# (coding-* channels bill differently and would misrepresent identity/cost).
# Default lineup = every model at its MAX reasoning mode:
#   kimi-k3 always reasons at max effort (not configurable)
#   claude-opus-4-8-think is the thinking variant of Opus 4.8
#   gpt-5.6-sol gets reasoning_effort=max, its top tier (verified effective on chat/completions)
# "params" is merged verbatim into the request payload.
MODELS = {
    "kimi-k3": {"display": "Kimi K3", "max_tokens": 100000, "retries": 12},
    "claude-opus-4-8-think": {"display": "Claude Opus 4.8", "max_tokens": 100000},
    "gpt-5.6-sol": {"display": "GPT-5.6 Sol", "max_tokens": 100000,
                    "params": {"reasoning_effort": "max"}},
    # non-thinking / substitute channels — NOT in the default lineup,
    # pick explicitly via --models
    "claude-opus-4-8": {"display": "Claude Opus 4.8", "lineup": False},
    "coding-kimi-k3": {"display": "Kimi K3", "max_tokens": 100000,
                       "retries": 8, "lineup": False},
}
DEFAULT_MAX_TOKENS = 60000

REQUEST_TIMEOUT_S = 1200  # per socket read while streaming
RETRIES = 3
RETRY_WAIT_S = 60
HEARTBEAT_S = 30


def build_user_content(prompt, ep_dir):
    """Text-only content, or multimodal [text + image_url] when the episode
    ships a reference image (ref.png / ref.jpg)."""
    refs = sorted(r for r in globmod.glob(f"{ep_dir}/ref*")
                  if r.rsplit(".", 1)[-1].lower() in ("png", "jpg", "jpeg", "webp"))
    if not refs:
        return prompt
    parts = [{"type": "text", "text": prompt}]
    for ref in refs:
        ext = ref.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
        with open(ref, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/{mime};base64,{b64}"}})
    return parts


def call_model(model, prompt, label):
    """Stream the completion (SSE) and reassemble an OpenAI-style response dict."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MODELS.get(model, {}).get("max_tokens", DEFAULT_MAX_TOKENS),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    payload.update(MODELS.get(model, {}).get("params", {}))
    req = urllib.request.Request(
        GATEWAY,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "text/event-stream",
        },
    )
    content, usage, finish, model_echo, err = [], {}, None, None, None
    chunks = 0
    t0 = time.time()
    next_beat = t0 + HEARTBEAT_S
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        for raw in resp:
            now = time.time()
            if now >= next_beat:
                print(f"[{label}] streaming… {chunks} chunks, "
                      f"{sum(len(c) for c in content)} content chars, "
                      f"{now - t0:.0f}s elapsed", flush=True)
                next_beat = now + HEARTBEAT_S
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            chunks += 1
            if "error" in chunk:
                err = chunk["error"]
                break
            model_echo = chunk.get("model") or model_echo
            if chunk.get("usage"):
                usage = chunk["usage"]
            for ch in chunk.get("choices") or []:
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    content.append(delta["content"])
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    if err:
        return {"error": err}
    return {
        "model": model_echo,
        "usage": usage,
        "choices": [{
            "message": {"role": "assistant", "content": "".join(content)},
            "finish_reason": finish,
        }],
    }


def extract_html(content):
    blocks = re.findall(r"```(?:html)?\s*\n(.*?)```", content, re.DOTALL)
    html_blocks = [b for b in blocks if "<html" in b.lower() or "<!doctype" in b.lower()]
    if html_blocks:
        return max(html_blocks, key=len).strip()
    stripped = content.strip()
    if stripped.lower().startswith("<!doctype") or stripped.lower().startswith("<html"):
        return stripped
    if blocks:
        return max(blocks, key=len).strip()
    return None


def record_artifact(ep_dir, model, seconds):
    """Record one model's artifact (called as soon as its generation finishes)."""
    cmd = ["node", os.path.join(SCRIPT_DIR, "record.mjs"), ep_dir,
           "--seconds", str(seconds), "--model", model]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 120)
    ok = proc.returncode == 0
    print(f"[{model}] recording {'done' if ok else 'FAILED'} "
          f"in {time.time() - t0:.0f}s", flush=True)
    if not ok:
        print(f"[{model}] record stderr: {proc.stderr[-500:]}", flush=True)
    return ok


def run_one(requested, prompt, ep_dir, record_seconds):
    meta = MODELS.get(requested, {"display": requested})
    retries = meta.get("retries", RETRIES)
    last_err = None
    for attempt in range(1, retries + 1):
        t0 = time.time()
        try:
            data = call_model(requested, prompt, requested)
        except Exception as e:  # noqa: BLE001
            last_err = f"attempt {attempt}: {e}"
            print(f"[{requested}] FAIL {last_err}", flush=True)
            time.sleep(RETRY_WAIT_S)
            continue
        if "error" in data:
            last_err = f"attempt {attempt}: {data['error'].get('message', data['error'])}"
            print(f"[{requested}] API ERROR {last_err}", flush=True)
            time.sleep(RETRY_WAIT_S)
            continue
        latency = time.time() - t0
        with open(f"{ep_dir}/raw_{requested}.json", "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        content = data["choices"][0]["message"]["content"] or ""
        html = extract_html(content)
        if not html and attempt < retries:
            last_err = f"attempt {attempt}: 200 OK but no code in response"
            print(f"[{requested}] EMPTY {last_err}", flush=True)
            time.sleep(RETRY_WAIT_S)
            continue
        work = f"{ep_dir}/work_{requested}"
        os.makedirs(work, exist_ok=True)
        if html:
            with open(f"{work}/index.html", "w") as f:
                f.write(html)
        usage = data.get("usage", {})
        pin, pout = PRICING.get(requested, (0, 0))
        cost = (usage.get("prompt_tokens", 0) * pin + usage.get("completion_tokens", 0) * pout) / 1e6
        print(f"[{requested}] OK in {latency:.0f}s, cost ${cost:.2f}, "
              f"html={'yes' if html else 'NO'}", flush=True)
        result = {
            "requested": requested,
            "display": meta["display"],
            "served_by": requested,
            "model_echo": data.get("model"),
            "latency_s": round(latency, 1),
            "usage": usage,
            "cost_usd": round(cost, 4),
            "finish_reason": data["choices"][0].get("finish_reason"),
            "code_extracted": bool(html),
            "code_lines": html.count("\n") + 1 if html else 0,
            "error": None,
        }
        if html and record_seconds:
            result["recorded"] = record_artifact(ep_dir, requested, record_seconds)
        return result
    return {"requested": requested, "display": meta["display"], "error": last_err,
            "code_extracted": False}


def next_episode_dir():
    """Auto-increment: episodes/ep01, ep02, … under the script's directory."""
    base = os.path.join(SCRIPT_DIR, "episodes")
    nums = [int(m.group(1)) for d in globmod.glob(f"{base}/ep*")
            if (m := re.match(r"ep(\d+)$", os.path.basename(d)))]
    return os.path.join(base, f"ep{(max(nums) if nums else 0) + 1:02d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode_dir", nargs="?", default=None,
                    help="omit to auto-create the next episodes/epNN")
    ap.add_argument("--task", metavar="FILE",
                    help="prompt file to copy into the episode as task.md")
    ap.add_argument("--ref", metavar="IMAGE", action="append", default=[],
                    help="reference image(s) to send along with the prompt")
    ap.add_argument("--models",
                    default=",".join(m for m, c in MODELS.items() if c.get("lineup", True)))
    ap.add_argument("--record", type=int, default=0, metavar="SECONDS",
                    help="record each artifact for N seconds as soon as it lands")
    args = ap.parse_args()

    if args.episode_dir:
        ep_dir = args.episode_dir.rstrip("/")
        if not os.path.isabs(ep_dir):
            ep_dir = os.path.join(os.getcwd(), ep_dir)
    else:
        ep_dir = next_episode_dir()
    os.makedirs(ep_dir, exist_ok=True)
    print(f"episode: {ep_dir}", flush=True)

    if args.task:
        with open(args.task) as f:
            task_text = f.read()
        with open(f"{ep_dir}/task.md", "w") as f:
            f.write(task_text)
    for i, ref in enumerate(args.ref):
        ext = ref.rsplit(".", 1)[-1].lower()
        with open(ref, "rb") as src, open(f"{ep_dir}/ref{i or ''}.{ext}", "wb") as dst:
            dst.write(src.read())

    if not os.path.exists(f"{ep_dir}/task.md"):
        sys.exit(f"no task.md in {ep_dir} — pass --task FILE or create it first")
    with open(f"{ep_dir}/task.md") as f:
        prompt = build_user_content(f.read(), ep_dir)
    models = args.models.split(",")

    t0 = time.time()
    results = []
    lock = threading.Lock()
    metrics_path = f"{ep_dir}/metrics.json"

    def flush_metrics(done):
        payload = {"task": f"{ep_dir}/task.md", "wall_s": round(time.time() - t0, 1),
                   "done": done, "total": len(models), "results": results}
        tmp = metrics_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, metrics_path)

    with cf.ThreadPoolExecutor(max_workers=len(models)) as ex:
        futs = {ex.submit(run_one, m, prompt, ep_dir, args.record): m for m in models}
        for fut in cf.as_completed(futs):
            with lock:
                results.append(fut.result())
                flush_metrics(len(results))
    print(json.dumps({"wall_s": round(time.time() - t0, 1), "results": results},
                     ensure_ascii=False, indent=1))
    if not all(r["code_extracted"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()

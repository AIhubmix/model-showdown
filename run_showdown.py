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
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# showdown.config.json 外置网关/品牌/模型覆盖，便于外部用户 BYOK 自部署；
# 缺文件时回落到内置默认，老工作流不受影响。
CONFIG_PATH = os.path.join(SCRIPT_DIR, "showdown.config.json")
CONFIG = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH) as _f:
        CONFIG = json.load(_f)

GATEWAY = CONFIG.get("gateway", "https://aihubmix.com/v1/chat/completions")
API_KEY_ENV = CONFIG.get("api_key_env", "AIHUBMIX_API_KEY")
# key 校验放在 main() 里而非 import 时：webapp 等调用方需要无 key 也能 import
# 读 MODELS/PRICING（BYOK 的 key 是每个任务经环境变量传给子进程的）
API_KEY = os.environ.get(API_KEY_ENV)

# USD per 1M tokens (input, output)
PRICING = {
    "kimi-k3": (3.0, 15.0),
    "coding-kimi-k3": (3.0, 15.0),  # list price; the coding channel bills differently
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-8-think": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
    "gpt-5.6-sol": (5.0, 30.0),
    # qwen3.8-max-preview 官方未公布 per-token 牌价（订阅制预览），暂用前代 qwen3.7-max 牌价估算
    "qwen3.8-max-preview": (1.25, 3.75),
    "gemini-3.6-flash": (1.50, 7.50),
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
    "kimi-k3": {"display": "Kimi K3", "max_tokens": 160000, "retries": 12},
    "claude-opus-4-8-think": {"display": "Claude Opus 4.8", "max_tokens": 100000},
    # effort is ONLY effective via /v1/responses — chat/completions silently
    # drops reasoning_effort for this model (verified by 3x3 A/B test)
    "gpt-5.6-sol": {"display": "GPT-5.6 Sol", "max_tokens": 100000,
                    "endpoint": "responses",
                    "params": {"reasoning": {"effort": "max"}}},
    # non-thinking / substitute / guest contestants — NOT in the default lineup,
    # pick explicitly via --models
    "claude-opus-4-8": {"display": "Claude Opus 4.8", "lineup": False},
    # Fable 5: adaptive reasoning via native /v1/messages (NOT chat/completions).
    # 128000 is claude-fable-5's hard ceiling on the gateway (verified via
    # direct curl 400). max_tokens must always be set explicitly — omitting it
    # does NOT fall back to that ceiling, it silently drops to ~4096, which the
    # thinking budget blows through instantly (finish_reason=length, empty
    # content).
    # endpoint=messages: switched from chat/completions hoping incremental
    # content_block_delta events would keep the wire hot — this did NOT fix
    # anything, both endpoints behave identically for this model/gateway, and
    # neither is worse than the other. Kept endpoint=messages since it's the
    # architecturally correct protocol.
    # ROOT CAUSE (confirmed via AIHubMix backend tid export, ep04): for a
    # real task (ref image + long prompt) the model needs ~12-13min before
    # its *first* content byte reaches the gateway (all of it is silent
    # server-side "thinking" — no partial frames, on either endpoint). Some
    # intermediary (proxy/LB — ours or AIHubMix's) kills idle client
    # connections at ~10-11min, i.e. 1-2min *before* that first byte would
    # arrive. This is NOT proportional to budget_tokens — 100k/40k/18k budgets
    # all died at the same ~10-11min mark, and the real run still used 47k
    # thinking_tokens despite budget_tokens=18000 (budget_tokens is a soft
    # hint here, not a hard cap). So lowering the budget doesn't help; the
    # generation itself still needs the same wall-clock time regardless.
    # reasoning:{effort:"max",display:"summarized"} was also tried as a
    # workaround: 400 invalid field on /v1/messages; on /v1/chat/completions
    # the request never even registered on the AIHubMix dashboard — dead end.
    # WORKAROUND when this happens: the generation still completes
    # successfully server-side even though the client connection dies —
    # ask the user to pull the AIHubMix dashboard record for the request's
    # tid (shown in error bodies, or found via the dashboard's request log)
    # and export it; the complete response (incl. the ```html block) can be
    # recovered from there and written directly to work_<model>/index.html,
    # no need to re-run generation. See episodes/ep04's recovery for tid
    # 2026072014131970707025835020540.
    "claude-fable-5": {"display": "Claude Fable 5", "max_tokens": 128000, "lineup": False,
                       "endpoint": "messages",
                       "params": {"thinking": {"type": "enabled", "budget_tokens": 40000}}},
    "coding-kimi-k3": {"display": "Kimi K3", "max_tokens": 100000,
                       "retries": 8, "lineup": False},
    # Qwen3.8 Max Preview：大 HTML 产物给足输出预算；显式 --models 选入
    "qwen3.8-max-preview": {"display": "Qwen3.8 Max", "max_tokens": 120000, "lineup": False},
    # Gemini 3.6 Flash (released 2026-07-21): native Google generateContent
    # protocol via the gateway's /gemini passthrough (x-goog-api-key auth,
    # SSE candidates[].content.parts[].text deltas). 65536 is its hard output
    # ceiling per the official API spec.
    "gemini-3.6-flash": {"display": "Gemini 3.6 Flash", "max_tokens": 65536, "lineup": False,
                         "endpoint": "gemini",
                         "params": {"generationConfig": {"maxOutputTokens": 65536,
                                                          "thinkingConfig": {"thinkingLevel": "high"}}}},
}
# config 里的 models/pricing 是对内置表的覆盖/追加（key 相同则整条替换）
PRICING.update({k: tuple(v) for k, v in CONFIG.get("pricing", {}).items()})
MODELS.update(CONFIG.get("models", {}))
DEFAULT_MAX_TOKENS = 60000

REQUEST_TIMEOUT_S = 3600  # per socket read while streaming
# 曾经是 1200s：reasoning=max + 大体积多模态输入(图片)的请求真实生成耗时会
# 稳定超过 30 分钟(TTFT 本身就要 11-12 分钟),1200s 会在服务端仍在正常吐流时
# 由客户端自己抢先掐断连接——不是网关/上游的问题，是这个值定小了。
RETRIES = 3
RETRY_WAIT_S = 60
HEARTBEAT_S = 30


def sniff_image_mime(data, ext):
    """真实字节优先判 mime，而非文件扩展名。Anthropic(claude) 会严格校验
    声明的 media type 与实际字节一致，png/jpeg 不符直接 400——曾因 .png 里
    装 jpeg 字节导致整轮生成白跑重启。扩展名仅作兜底。"""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp",
            "gif": "gif"}.get(ext, "png")


def build_user_content(prompt, ep_dir):
    """Text-only content, or multimodal [text + image_url] when the episode
    ships a reference image (ref.png / ref.jpg)."""
    refs = sorted(r for r in globmod.glob(f"{ep_dir}/ref*")
                  if r.rsplit(".", 1)[-1].lower() in ("png", "jpg", "jpeg", "webp", "gif"))
    if not refs:
        return prompt
    parts = [{"type": "text", "text": prompt}]
    for ref in refs:
        ext = ref.rsplit(".", 1)[-1].lower()
        with open(ref, "rb") as f:
            raw = f.read()
        mime = sniff_image_mime(raw, ext)
        b64 = base64.b64encode(raw).decode()
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/{mime};base64,{b64}"}})
    return parts


def to_responses_input(content):
    """Convert chat-style user content into Responses-API input parts."""
    if isinstance(content, str):
        return content
    parts = []
    for p in content:
        if p["type"] == "text":
            parts.append({"type": "input_text", "text": p["text"]})
        elif p["type"] == "image_url":
            parts.append({"type": "input_image", "image_url": p["image_url"]["url"]})
    return [{"role": "user", "content": parts}]


def to_messages_content(content):
    """Convert chat-style user content (text + image_url) into Anthropic
    Messages API native content blocks (text + image/base64)."""
    if isinstance(content, str):
        return content
    parts = []
    for p in content:
        if p["type"] == "text":
            parts.append({"type": "text", "text": p["text"]})
        elif p["type"] == "image_url":
            header, b64 = p["image_url"]["url"].split(",", 1)
            media_type = header.split(":")[1].split(";")[0]
            parts.append({"type": "image",
                          "source": {"type": "base64", "media_type": media_type, "data": b64}})
    return parts


def to_gemini_parts(content):
    """Convert chat-style user content (text + image_url) into Gemini native
    generateContent `parts` (text / inline_data base64)."""
    if isinstance(content, str):
        return [{"text": content}]
    parts = []
    for p in content:
        if p["type"] == "text":
            parts.append({"text": p["text"]})
        elif p["type"] == "image_url":
            header, b64 = p["image_url"]["url"].split(",", 1)
            mime_type = header.split(":")[1].split(";")[0]
            parts.append({"inline_data": {"mime_type": mime_type, "data": b64}})
    return parts


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


class Timeline:
    """Per-request client-side event timeline (UTC timestamps), written
    incrementally to ep_dir/timeline_<label>.json. Purpose: when a
    long-thinking model's connection dies mid-stream, this file lets you
    line up OUR observed timestamps (first_byte, first_content_delta,
    connection_dropped) directly against the AIHubMix backend's tid export
    timestamps for the same request — proving (not just inferring) whether
    the client disconnected before real content was sent, instead of relying
    on statistical correlation across separate runs. Survives a mid-stream
    exception since every mark() flushes immediately."""
    def __init__(self, ep_dir, label, model, endpoint):
        self.ep_dir = ep_dir
        self.label = label
        self.t0 = time.time()
        self.data = {
            "requested": model, "label": label, "endpoint": endpoint,
            "request_sent_utc": _iso_now(), "events": [], "outcome": None,
        }

    def mark(self, kind, **extra):
        self.data["events"].append({
            "t_rel_s": round(time.time() - self.t0, 2),
            "utc": _iso_now(),
            "kind": kind,
            **extra,
        })
        self.flush()

    def finish(self, outcome):
        self.data["outcome"] = outcome
        self.flush()

    def flush(self):
        if not self.ep_dir:
            return
        path = os.path.join(self.ep_dir, f"timeline_{self.label}.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def call_model_messages(model, prompt, label, ep_dir=None):
    """Same contract as call_model, but via the native Anthropic /v1/messages
    protocol (x-api-key auth, content_block_delta events). Needed for
    claude-fable-5: on /v1/chat/completions its thinking phase transmits as
    ONE opaque non-incremental marker — the whole thinking duration is a
    silent wire, which the upstream proxy idle-kills after ~10-15min on long
    thinking (verified: 3/3 failures at ~10.3min avg with a 100k budget).
    On /v1/messages, content streams as incremental content_block_delta
    events once generation starts, keeping the connection hot."""
    cfg = MODELS.get(model, {})
    payload = {
        "model": model,
        "max_tokens": cfg.get("max_tokens", DEFAULT_MAX_TOKENS),
        "messages": [{"role": "user", "content": to_messages_content(prompt)}],
        "stream": True,
    }
    payload.update(cfg.get("params", {}))
    req = urllib.request.Request(
        GATEWAY.replace("/chat/completions", "/messages"),
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "Accept": "text/event-stream",
        },
    )
    content, usage, finish, err = [], {}, None, None
    chunks = 0
    t0 = time.time()
    next_beat = t0 + HEARTBEAT_S
    conn_dropped = None
    tl = Timeline(ep_dir, label, model, "messages")
    first_byte = first_content = False
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            for raw in resp:
                if not first_byte:
                    first_byte = True
                    tl.mark("first_byte")
                now = time.time()
                if now >= next_beat:
                    tl.mark("heartbeat", events=chunks,
                            content_chars=sum(len(c) for c in content))
                    print(f"[{label}] streaming(messages)… {chunks} events, "
                          f"{sum(len(c) for c in content)} content chars, "
                          f"{now - t0:.0f}s elapsed", flush=True)
                    next_beat = now + HEARTBEAT_S
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                chunks += 1
                etype = ev.get("type", "")
                if etype == "content_block_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        if not first_content:
                            first_content = True
                            tl.mark("first_content_delta", events=chunks)
                        content.append(delta.get("text", ""))
                elif etype == "message_start":
                    u = (ev.get("message") or {}).get("usage") or {}
                    if u:
                        usage["prompt_tokens"] = u.get("input_tokens", 0)
                elif etype == "message_delta":
                    d = ev.get("delta") or {}
                    if d.get("stop_reason"):
                        finish = d["stop_reason"]
                    u = ev.get("usage") or {}
                    if u.get("output_tokens") is not None:
                        usage["completion_tokens"] = u["output_tokens"]
                        usage["total_tokens"] = usage.get("prompt_tokens", 0) + u["output_tokens"]
                elif etype == "error":
                    err = (ev.get("error") or {}).get("message", "stream error")
    except Exception as e:  # noqa: BLE001
        # See call_model: salvage partial text on a mid-stream connection drop
        # instead of discarding a possibly-complete generation.
        conn_dropped = str(e)
        tl.mark("connection_dropped", events=chunks,
                content_chars=sum(len(c) for c in content), error=conn_dropped)
        tl.finish("connection_dropped")
        print(f"[{label}] connection dropped after {chunks} events, "
              f"{sum(len(c) for c in content)} content chars: {conn_dropped}", flush=True)
        if not content:
            raise
    if not conn_dropped:
        tl.mark("stream_end", events=chunks, content_chars=sum(len(c) for c in content))
        tl.finish("error" if err else "ok")
    if err:
        return {"error": {"message": err}}
    if conn_dropped:
        finish = finish or "connection_dropped"
    return {
        "model": model,
        "usage": usage,
        "choices": [{
            "message": {"role": "assistant", "content": "".join(content)},
            "finish_reason": finish,
        }],
    }


def call_model_responses(model, prompt, label, ep_dir=None):
    """Same contract as call_model, but via /v1/responses (needed where
    reasoning effort is only honored on this endpoint)."""
    cfg = MODELS.get(model, {})
    payload = {
        "model": model,
        "input": to_responses_input(prompt),
        "max_output_tokens": cfg.get("max_tokens", DEFAULT_MAX_TOKENS),
        "stream": True,
    }
    payload.update(cfg.get("params", {}))
    req = urllib.request.Request(
        GATEWAY.replace("/chat/completions", "/responses"),
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "text/event-stream",
        },
    )
    text, usage, status, err = [], {}, None, None
    chunks = 0
    t0 = time.time()
    next_beat = t0 + HEARTBEAT_S
    conn_dropped = None
    tl = Timeline(ep_dir, label, model, "responses")
    first_byte = first_content = False
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            for raw in resp:
                if not first_byte:
                    first_byte = True
                    tl.mark("first_byte")
                now = time.time()
                if now >= next_beat:
                    tl.mark("heartbeat", events=chunks,
                            content_chars=sum(len(c) for c in text))
                    print(f"[{label}] streaming(responses)… {chunks} events, "
                          f"{sum(len(c) for c in text)} content chars, "
                          f"{now - t0:.0f}s elapsed", flush=True)
                    next_beat = now + HEARTBEAT_S
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                chunks += 1
                etype = ev.get("type", "")
                if etype == "response.output_text.delta":
                    if not first_content:
                        first_content = True
                        tl.mark("first_content_delta", events=chunks)
                    text.append(ev.get("delta") or "")
                elif etype in ("response.completed", "response.incomplete", "response.failed"):
                    r = ev.get("response") or {}
                    status = r.get("status")
                    u = r.get("usage") or {}
                    usage = {
                        "prompt_tokens": u.get("input_tokens", 0),
                        "completion_tokens": u.get("output_tokens", 0),
                        "total_tokens": u.get("total_tokens", 0),
                        "completion_tokens_details": u.get("output_tokens_details") or {},
                    }
                    if etype == "response.failed":
                        err = (r.get("error") or {}).get("message", "response.failed")
                elif etype == "error":
                    err = ev.get("message", "stream error")
    except Exception as e:  # noqa: BLE001
        # See call_model: salvage partial text on a mid-stream connection drop
        # instead of discarding a possibly-complete generation.
        conn_dropped = str(e)
        tl.mark("connection_dropped", events=chunks,
                content_chars=sum(len(c) for c in text), error=conn_dropped)
        tl.finish("connection_dropped")
        print(f"[{label}] connection dropped after {chunks} events, "
              f"{sum(len(c) for c in text)} content chars: {conn_dropped}", flush=True)
        if not text:
            raise
    if not conn_dropped:
        tl.mark("stream_end", events=chunks, content_chars=sum(len(c) for c in text))
        tl.finish("error" if err else "ok")
    if err:
        return {"error": {"message": err}}
    finish = {"completed": "stop", "incomplete": "length"}.get(status, status)
    if conn_dropped and not finish:
        finish = "connection_dropped"
    return {
        "model": model,
        "usage": usage,
        "choices": [{
            "message": {"role": "assistant", "content": "".join(text)},
            "finish_reason": finish,
        }],
    }


def call_model_gemini(model, prompt, label, ep_dir=None):
    """Same contract as call_model, but via the native Google generateContent
    protocol (x-goog-api-key auth, SSE candidates[].content.parts[].text
    deltas) — see aihubmix-env skill §2.3."""
    cfg = MODELS.get(model, {})
    payload = {
        "contents": [{"role": "user", "parts": to_gemini_parts(prompt)}],
        "generationConfig": {"maxOutputTokens": cfg.get("max_tokens", DEFAULT_MAX_TOKENS)},
    }
    payload.update(cfg.get("params", {}))
    base = GATEWAY.rsplit("/v1/", 1)[0]
    req = urllib.request.Request(
        f"{base}/gemini/v1beta/models/{model}:streamGenerateContent?alt=sse",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": API_KEY,
            "Accept": "text/event-stream",
        },
    )
    content, usage, finish, err = [], {}, None, None
    chunks = 0
    t0 = time.time()
    next_beat = t0 + HEARTBEAT_S
    conn_dropped = None
    tl = Timeline(ep_dir, label, model, "gemini")
    first_byte = first_content = False
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            for raw in resp:
                if not first_byte:
                    first_byte = True
                    tl.mark("first_byte")
                now = time.time()
                if now >= next_beat:
                    tl.mark("heartbeat", events=chunks,
                            content_chars=sum(len(c) for c in content))
                    print(f"[{label}] streaming(gemini)… {chunks} events, "
                          f"{sum(len(c) for c in content)} content chars, "
                          f"{now - t0:.0f}s elapsed", flush=True)
                    next_beat = now + HEARTBEAT_S
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                chunks += 1
                if "error" in ev:
                    err = (ev.get("error") or {}).get("message", "stream error")
                    continue
                for cand in ev.get("candidates") or []:
                    for part in (cand.get("content") or {}).get("parts") or []:
                        text = part.get("text")
                        if text:
                            if not first_content:
                                first_content = True
                                tl.mark("first_content_delta", events=chunks)
                            content.append(text)
                    if cand.get("finishReason"):
                        finish = cand["finishReason"]
                u = ev.get("usageMetadata") or {}
                if u:
                    usage = {
                        "prompt_tokens": u.get("promptTokenCount", 0),
                        "completion_tokens": u.get("candidatesTokenCount", 0),
                        "total_tokens": u.get("totalTokenCount", 0),
                        "thoughts_tokens": u.get("thoughtsTokenCount", 0),
                    }
    except Exception as e:  # noqa: BLE001
        # See call_model: salvage partial text on a mid-stream connection drop
        # instead of discarding a possibly-complete generation.
        conn_dropped = str(e)
        tl.mark("connection_dropped", events=chunks,
                content_chars=sum(len(c) for c in content), error=conn_dropped)
        tl.finish("connection_dropped")
        print(f"[{label}] connection dropped after {chunks} events, "
              f"{sum(len(c) for c in content)} content chars: {conn_dropped}", flush=True)
        if not content:
            raise
    if not conn_dropped:
        tl.mark("stream_end", events=chunks, content_chars=sum(len(c) for c in content))
        tl.finish("error" if err else "ok")
    if err:
        return {"error": {"message": err}}
    finish = {"STOP": "stop", "MAX_TOKENS": "length"}.get(finish, finish)
    if conn_dropped:
        finish = finish or "connection_dropped"
    return {
        "model": model,
        "usage": usage,
        "choices": [{
            "message": {"role": "assistant", "content": "".join(content)},
            "finish_reason": finish,
        }],
    }


def call_model(model, prompt, label, ep_dir=None):
    """Stream the completion (SSE) and reassemble an OpenAI-style response dict."""
    endpoint = MODELS.get(model, {}).get("endpoint")
    if endpoint == "responses":
        return call_model_responses(model, prompt, label, ep_dir)
    if endpoint == "messages":
        return call_model_messages(model, prompt, label, ep_dir)
    if endpoint == "gemini":
        return call_model_gemini(model, prompt, label, ep_dir)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    max_tokens = MODELS.get(model, {}).get("max_tokens", DEFAULT_MAX_TOKENS)
    if max_tokens is not None:  # None = omit entirely, let the gateway pick its own ceiling
        payload["max_tokens"] = max_tokens
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
    conn_dropped = None
    tl = Timeline(ep_dir, label, model, "chat/completions")
    first_byte = first_content = False
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            for raw in resp:
                if not first_byte:
                    first_byte = True
                    tl.mark("first_byte")
                now = time.time()
                if now >= next_beat:
                    tl.mark("heartbeat", chunks=chunks,
                            content_chars=sum(len(c) for c in content))
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
                        if not first_content:
                            first_content = True
                            tl.mark("first_content_delta", chunks=chunks)
                        content.append(delta["content"])
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
    except Exception as e:  # noqa: BLE001
        # Connection can die mid-stream (upstream idle-kill on a long silent
        # thinking phase — seen with claude-fable-5). Don't throw away
        # whatever content already arrived: the model may have finished (or
        # nearly finished) generating server-side even though the socket
        # never delivered the tail. Salvage it and let extract_html decide.
        conn_dropped = str(e)
        tl.mark("connection_dropped", chunks=chunks,
                content_chars=sum(len(c) for c in content), error=conn_dropped)
        tl.finish("connection_dropped")
        print(f"[{label}] connection dropped after {chunks} chunks, "
              f"{sum(len(c) for c in content)} content chars: {conn_dropped}", flush=True)
        if not content:
            raise
    if not conn_dropped:
        tl.mark("stream_end", chunks=chunks, content_chars=sum(len(c) for c in content))
        tl.finish("error" if err else "ok")
    if err:
        return {"error": err}
    if conn_dropped:
        finish = finish or "connection_dropped"
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


FFMPEG = ["npx", "--no-install", "--prefix", os.path.join(SCRIPT_DIR, "video"),
          "remotion", "ffmpeg"]  # Remotion 自带 ffmpeg，免装系统 ffmpeg


def _luma(webm, t):
    """webm 在 t 秒处的平均亮度(0-255)，取不到返回 -1。"""
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return -1
    p = os.path.join(SCRIPT_DIR, "_luma_probe.png")
    subprocess.run(FFMPEG + ["-y", "-loglevel", "error", "-ss", str(t), "-i", webm,
                             "-frames:v", "1", p], capture_output=True)
    if not os.path.exists(p):
        return -1
    v = ImageStat.Stat(Image.open(p).convert("L")).mean[0]
    os.remove(p)
    return v


def trim_leading_black(webm, floor=45, step=0.5, upto=18):
    """掐掉录屏开头的黑帧(模型常把开始界面做成黑底标题屏)，让文件从第一帧内容起播 →
    合成时 startFrom 恒为 0、从头播即可，无需人工抽帧挑起点。检测不到 PIL 时静默跳过。
    Why 掐头而非跳过：auto-demo 靠"无输入"触发，录屏不能注入按键去发车(会卡标题屏)，
    所以标题屏那几秒黑必然被录进来，只能事后掐掉。"""
    if _luma(webm, 0) < 0:  # 无 PIL/ffmpeg，跳过
        return
    t = 0.0
    while t < upto and _luma(webm, t) < floor:
        t += step
    if t <= 0.01 or t >= upto:  # 首帧已有内容 / 全程都黑：不动
        return
    tmp = webm + ".trim.webm"
    r = subprocess.run(FFMPEG + ["-y", "-loglevel", "error", "-i", webm, "-ss", str(round(t, 2)),
                                 "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "6",
                                 "-b:v", "6M", "-an", tmp], capture_output=True)
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        os.replace(tmp, webm)
        print(f"[trim] 掐头 {t:.1f}s 黑标题屏 → {os.path.basename(webm)} 从内容帧起播", flush=True)
    elif os.path.exists(tmp):
        os.remove(tmp)


def record_artifact(ep_dir, model, seconds, rec_w=None, rec_h=None):
    """Record one model's artifact (called as soon as its generation finishes).
    rec_w/rec_h 控制录屏视口：横屏内容(如赛车)必须传 1280x720，否则默认
    720x960 竖屏会把横屏画面压扁——曾因此把 3 遍录屏返工。"""
    cmd = ["node", os.path.join(SCRIPT_DIR, "record.mjs"), ep_dir,
           "--seconds", str(seconds), "--model", model]
    if rec_w:
        cmd += ["--width", str(rec_w)]
    if rec_h:
        cmd += ["--height", str(rec_h)]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 120)
    ok = proc.returncode == 0
    print(f"[{model}] recording {'done' if ok else 'FAILED'} "
          f"in {time.time() - t0:.0f}s", flush=True)
    if not ok:
        print(f"[{model}] record stderr: {proc.stderr[-500:]}", flush=True)
        return ok
    # 首帧非黑：自动掐掉开头黑标题屏，保证 startFrom=0 从头播就有画面
    trim_leading_black(os.path.join(ep_dir, "recordings", f"{model}.webm"))
    return ok


def run_one(requested, prompt, ep_dir, record_seconds, rec_w=None, rec_h=None):
    meta = MODELS.get(requested, {"display": requested})
    retries = meta.get("retries", RETRIES)
    last_err = None
    for attempt in range(1, retries + 1):
        t0 = time.time()
        try:
            data = call_model(requested, prompt, requested, ep_dir)
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
            result["recorded"] = record_artifact(ep_dir, requested, record_seconds,
                                                  rec_w, rec_h)
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
    ap.add_argument("--rec-size", metavar="WxH", default="720x960",
                    help="录屏视口。横屏内容(赛车/宽场景)用 1280x720，默认 720x960 竖屏")
    args = ap.parse_args()
    if not API_KEY:
        sys.exit(f"missing API key: export {API_KEY_ENV}=sk-... "
                 f"(any OpenAI-compatible gateway works; default is aihubmix.com)")
    try:
        rec_w, rec_h = (int(x) for x in args.rec_size.lower().split("x"))
    except ValueError:
        sys.exit(f"--rec-size 需形如 1280x720，收到: {args.rec_size}")

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
        futs = {ex.submit(run_one, m, prompt, ep_dir, args.record, rec_w, rec_h): m
                for m in models}
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

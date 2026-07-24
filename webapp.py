#!/usr/bin/env python3
"""Self-hosted web UI for showdown — create, browse, and inspect comparison runs.

    python3 webapp.py [--port 7788] [--host 127.0.0.1]

Layout: first screen is the create form; below it, a feed of every generated
episode (episodes/epNN + episodes/web/*). Clicking a card opens the detail
view — left side shows every model's recording auto-playing, right side pins
the prompt + shared run info, and clicking a work reveals its per-model
params, response stats, and wire protocol.

Key acquisition mirrors the playground (aihubmix-chat):
  · Account mode — Clerk sign-in, pick a key (masked list from /call/tkn/);
    requests carry `Bearer <Clerk JWT>` + `X-Pg-Token-Id`, the gateway swaps
    in the real key — the full key never touches this process or disk. JWTs
    are short-lived, so the job page keeps pushing fresh ones (keep it open).
  · Manual mode — paste an sk-key (BYOK); memory-only, never stored/logged.

MVP for trusted networks — binds 127.0.0.1 by default; put real auth in front
before exposing it anywhere public.
"""
import argparse
import glob
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_showdown  # noqa: E402  (for MODELS + API_KEY_ENV; import needs no key)
import showdown_db as db  # noqa: E402  (MySQL via SHOWDOWN_DB_URL, else SQLite)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EPISODES_DIR = os.path.join(SCRIPT_DIR, "episodes")
WEB_EP_DIR = os.path.join(EPISODES_DIR, "web")
KEY_ENV = run_showdown.API_KEY_ENV

_web_cfg = run_showdown.CONFIG.get("web", {})
# env 哨兵值 "none"：显式关闭该能力（部署机不改仓库里的 config 文件）
def _cfg(env_key, cfg_key, default=""):
    v = os.environ.get(env_key) or _web_cfg.get(cfg_key, default)
    return "" if v == "none" else v


CLERK_PK = _cfg("SHOWDOWN_CLERK_PK", "clerk_publishable_key")
SERVER_DOMAIN = _cfg("SHOWDOWN_SERVER_DOMAIN", "server_domain",
                     "https://aihubmix.com").rstrip("/")
GCS_BUCKET = _cfg("SHOWDOWN_GCS_BUCKET", "gcs_bucket")
# 反代前缀（如 SHOWDOWN_BASE_PATH=/showdown 挂在 playground.aihubmix.com/showdown/）；
# 生成的所有站内 URL 带上 B，请求进来时剥掉再路由
B = os.environ.get("SHOWDOWN_BASE_PATH", "").rstrip("/")
# 反代场景下，LB 转发到源站时会把 Host 头改写成源站 FQDN（供 Caddy 选站/签证书），
# 浏览器发来的 Referer 却是公网域名——同站校验要把公网域名也算作"本站"
PUBLIC_HOST = os.environ.get("SHOWDOWN_PUBLIC_HOST", "")
CAPTION_MODEL = os.environ.get("SHOWDOWN_CAPTION_MODEL") or _web_cfg.get("caption_model", "gpt-4o-mini")

# 参考图 → 生成 build prompt 的指令：核心是"还原"——布局/配色/材质/动效逐条钉死
CAPTION_INSTRUCTION = """You are a meticulous visual analyst helping write a build brief for a coding exercise. First, study the attached image and describe exactly what it shows. Then write that up as a prompt asking a model to build a similar scene/interface as ONE self-contained HTML file.

The prompt you write must:
1) Lay out the structure: every visible element/object, its position and proportions in the frame.
2) Pin down the visual system in implementation-ready detail — color palette (hex codes), typography, spacing, materials, lighting, shadows, glow. The goal is that the result closely matches the image, so explicitly call out framing, proportions, and details that are easy to get wrong.
3) Specify motion: what should animate and how; require an auto-demo that starts by itself ~1s after load with no user input.
4) Technical constraints: single self-contained HTML file, no external assets.
5) End with exactly: Return ONLY the complete HTML file in a single ```html code block.

Output just the prompt text, no preamble, no commentary."""

# 任务元数据在 DB（重启不丢）；粘贴的 sk-key 只驻内存 —— 安全设计使然，
# 重启会丢 queued 手动任务的 key（recover_on_boot 会把它们标失败提示重提）
SECRETS = {}       # job_id -> pasted key
SECRETS_LOCK = threading.Lock()

PROTO = {"responses": "OpenAI /v1/responses", "messages": "Anthropic /v1/messages",
         "gemini": "Google generateContent"}

# ---------- AIHubmix Design System (tokens.css subset, light theme) ----------
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Old+Standard+TT:wght@400;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');
:root {
  --ah-primary:#2563EB; --ah-primary-hover:#6180F9; --ah-primary-pressed:#0049DF;
  --ah-bg:#FCFCFC; --ah-card:#FFFFFF;
  --ah-success:#A4E522; --ah-success-text:#5C991F; --ah-warning-text:#E59100;
  --ah-danger-text:#F2320C; --ah-disabled:rgba(53,57,65,0.10);
  --ah-fg-1:#111111; --ah-fg-2:#353941; --ah-fg-3:#626773;
  --ah-border:rgba(17,17,17,0.08); --ah-border-strong:rgba(17,17,17,0.16);
  --ah-font-display:'Old Standard TT','Noto Serif SC',serif;
  --ah-font-body:'Inter','Noto Sans SC','PingFang SC',-apple-system,system-ui,sans-serif;
  --ah-font-mono:'JetBrains Mono','SF Mono',Menlo,monospace;
  --ah-shadow-card:0px 2px 8px rgba(0,0,0,0.08);
  --ah-shadow-lift:0px 8px 24px rgba(0,0,0,0.10);
  --ah-ease:cubic-bezier(0.2,0.8,0.2,1);
}
* { box-sizing:border-box; }
body { margin:0; background:var(--ah-bg); color:var(--ah-fg-2);
  font-family:var(--ah-font-body); font-size:16px; line-height:1.55;
  touch-action:manipulation; }
/* 键盘可达性：所有可交互元素统一 focus ring（playground .ah-topnav 同款） */
:is(a,button,input,select,textarea,[tabindex]):focus-visible {
  outline:none; box-shadow:0 0 0 3px rgba(37,99,235,0.3); }
button { cursor:pointer; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration:0.01ms !important;
    animation-duration:0.01ms !important; }
}
.wrap { max-width:1080px; margin:0 auto; padding:40px 24px 80px; }
h1 { font-family:var(--ah-font-display); font-size:32px; color:var(--ah-fg-1);
  font-weight:700; margin:0 0 4px; }
h2 { font-family:var(--ah-font-display); font-size:24px; color:var(--ah-fg-1);
  font-weight:700; margin:40px 0 16px; }
.sub { color:var(--ah-fg-3); font-size:14px; margin-bottom:28px; }
.card { background:var(--ah-card); border:1px solid var(--ah-border);
  border-radius:16px; box-shadow:var(--ah-shadow-card); padding:24px; margin-bottom:24px; }
label { display:block; font-size:14px; font-weight:600; color:var(--ah-fg-1); margin:16px 0 6px; }
label:first-child { margin-top:0; }
.hint { font-weight:400; color:var(--ah-fg-3); font-size:12px; margin-left:6px; }
textarea, input[type=text], input[type=password], select {
  width:100%; border:1px solid var(--ah-border-strong); border-radius:8px;
  padding:10px 12px; font-family:var(--ah-font-body); font-size:14px; color:var(--ah-fg-2);
  background:var(--ah-card); outline:none; transition:border-color 120ms var(--ah-ease), box-shadow 120ms var(--ah-ease); }
textarea:focus, input:focus, select:focus { border-color:var(--ah-primary);
  box-shadow:0 0 0 3px rgba(37,99,235,0.15); }
textarea { min-height:110px; font-family:var(--ah-font-mono); font-size:13px; resize:vertical; }
.models { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:8px; }
.model-opt { display:flex; align-items:center; gap:8px; border:1px solid var(--ah-border);
  border-radius:8px; padding:7px 12px; font-size:13.5px; cursor:pointer;
  transition:background 120ms var(--ah-ease); }
.model-opt:hover { background:rgba(17,17,17,0.03); }
.model-opt code { font-family:var(--ah-font-mono); font-size:10.5px; color:var(--ah-fg-3); }
.row { display:flex; gap:16px; }
.row > div { flex:1; }
.btn { display:inline-flex; align-items:center; gap:8px; background:var(--ah-primary);
  color:#fff; border:0; border-radius:8px; padding:12px 24px; font-size:15px;
  font-weight:600; font-family:var(--ah-font-body); cursor:pointer; margin-top:20px;
  transition:background 120ms var(--ah-ease); }
.btn:hover { background:var(--ah-primary-hover); }
.btn:active { background:var(--ah-primary-pressed); transform:scale(0.98); }
.btn:disabled { opacity:0.5; cursor:default; transform:none; }
.btn2 { display:inline-flex; align-items:center; background:var(--ah-card);
  color:var(--ah-primary); border:1px solid var(--ah-primary); border-radius:8px;
  padding:8px 16px; font-size:14px; font-weight:600; cursor:pointer;
  transition:background 120ms var(--ah-ease); }
.btn2:hover { background:rgba(37,99,235,0.06); }
.tabs { display:flex; gap:2px; border:1px solid var(--ah-border); border-radius:8px;
  padding:3px; width:fit-content; margin-bottom:12px; }
.tab { border:0; background:transparent; border-radius:6px; padding:6px 16px;
  font-size:13px; font-weight:600; color:var(--ah-fg-3); cursor:pointer;
  font-family:var(--ah-font-body); }
.tab.active { background:var(--ah-primary); color:#fff; }
.tag { display:inline-block; border-radius:8px; padding:2px 10px; font-size:12px; font-weight:600; }
.tag.queued  { background:var(--ah-disabled); color:var(--ah-fg-3); }
.tag.running { background:rgba(245,182,39,0.14); color:var(--ah-warning-text); }
.tag.done    { background:rgba(164,229,34,0.18); color:var(--ah-success-text); }
.tag.failed  { background:rgba(242,80,48,0.12); color:var(--ah-danger-text); }
.tag.arena   { background:rgba(38,204,235,0.14); color:#0e7490; }
.tag.web     { background:rgba(102,38,235,0.10); color:#6626EB; }
pre.log { background:#1e1e1e; color:#e0e0e0; border-radius:8px; padding:16px;
  font-family:var(--ah-font-mono); font-size:12px; line-height:1.5; overflow-x:auto;
  max-height:420px; overflow-y:auto; }
table { width:100%; border-collapse:collapse; font-size:14px; }
td, th { text-align:left; padding:8px 10px; border-bottom:1px solid var(--ah-border); }
th { color:var(--ah-fg-3); font-size:12px; font-weight:600; }
a { color:var(--ah-primary); text-decoration:none; }
a:hover { color:var(--ah-primary-hover); }
.files a { display:inline-block; margin:4px 12px 4px 0; font-family:var(--ah-font-mono); font-size:13px; }
.acct-line { display:flex; align-items:center; gap:12px; font-size:14px; }
.dropzone { border:1.5px dashed var(--ah-border-strong); border-radius:8px; padding:14px;
  cursor:pointer; transition:border-color 120ms var(--ah-ease), background 120ms var(--ah-ease); }
.dropzone.over { border-color:var(--ah-primary); background:rgba(37,99,235,0.05); }
.ref-previews { display:flex; gap:10px; flex-wrap:wrap; }
.ref-previews:not(:empty) { margin-top:10px; }
.ref-item { position:relative; }
.ref-item img { width:88px; height:88px; object-fit:cover; border-radius:8px;
  border:1px solid var(--ah-border); display:block; }
.ref-item button { position:absolute; top:-7px; right:-7px; width:20px; height:20px;
  border-radius:50%; border:0; background:var(--ah-fg-2); color:#fff; font-size:12px;
  line-height:1; cursor:pointer; }
.model-opt.novision { opacity:0.38; pointer-events:none; }

/* ---------- feed ---------- */
.feed { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:16px; }
a.feed-card { display:block; background:var(--ah-card); border:1px solid var(--ah-border);
  border-radius:16px; overflow:hidden; color:var(--ah-fg-2); box-shadow:var(--ah-shadow-card);
  transition:transform 120ms var(--ah-ease), box-shadow 120ms var(--ah-ease); }
a.feed-card:hover { transform:translateY(-2px); box-shadow:var(--ah-shadow-lift); color:var(--ah-fg-2); }
.feed-card .thumb { aspect-ratio:4/3; background:#0b0e14; overflow:hidden; }
.feed-card .thumb img { width:100%; height:100%; object-fit:cover; display:block; }
.feed-card .body { padding:12px 14px 14px; }
.feed-card .t { font-weight:600; font-size:14.5px; color:var(--ah-fg-1);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.feed-card .m { color:var(--ah-fg-3); font-size:12px; margin-top:4px;
  font-family:var(--ah-font-mono); display:flex; gap:8px; align-items:center; flex-wrap:wrap; }

/* ---------- watch (detail) ---------- */
.watch { display:grid; grid-template-columns:minmax(0,1fr) 380px; gap:24px; align-items:start; }
@media (max-width:960px) { .watch { grid-template-columns:1fr; } }
.works { display:grid; gap:16px; }
.work { background:var(--ah-card); border:2px solid var(--ah-border); border-radius:16px;
  overflow:hidden; cursor:pointer; box-shadow:var(--ah-shadow-card);
  transition:border-color 120ms var(--ah-ease); }
.work.sel { border-color:var(--ah-primary); }
.work video { width:100%; display:block; background:#000; }
.work .wh { display:flex; align-items:center; justify-content:space-between;
  padding:10px 14px; font-size:14px; }
.work .wh b { color:var(--ah-fg-1); display:flex; align-items:center; gap:8px; }
.dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
.cost { font-family:var(--ah-font-mono); color:var(--ah-success-text); font-weight:700; font-size:13px; }
.side { position:sticky; top:24px; display:grid; gap:16px; }
.side .card { margin:0; }
pre.prompt { background:rgba(17,17,17,0.03); border:1px solid var(--ah-border);
  border-radius:8px; padding:12px; font-family:var(--ah-font-mono); font-size:12px;
  line-height:1.5; max-height:260px; overflow:auto; white-space:pre-wrap; }
.kv { font-size:13px; }
.kv div { display:flex; justify-content:space-between; gap:12px; padding:5px 0;
  border-bottom:1px solid var(--ah-border); }
.kv div:last-child { border-bottom:0; }
.kv span:first-child { color:var(--ah-fg-3); flex:none; }
.kv span:last-child { font-family:var(--ah-font-mono); font-size:12px; text-align:right;
  overflow-wrap:anywhere; }
"""

CLERK_BOOT = """
<script type="module">
  import {{ Clerk }} from 'https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/+esm';
  window._clerkReady = (async () => {{
    const clerk = new Clerk({pk});
    await clerk.load();
    window._clerk = clerk;
    return clerk;
  }})().catch(e => {{ window._clerkErr = String(e); return null; }});
</script>
"""


def page(title, body, refresh=None, head="", body_attrs=""):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{meta}
<script>window.BASE={json.dumps(B)};</script>{head}
<title>{html.escape(title)}</title><style>{CSS}</style></head><body{body_attrs}><div class="wrap">
{body}</div></body></html>"""


def lineup_models():
    return [(mid, cfg.get("display", mid), cfg.get("lineup", True) is not False)
            for mid, cfg in run_showdown.MODELS.items()]


# ---------- model catalog (playground 同源：/api/v1/models?type=llm) ----------

_CATALOG = {"ts": 0.0, "rows": []}
_CATALOG_LOCK = threading.Lock()
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,80}$")


def infer_endpoint(row):
    """playground catalog.ts inferNative 同款：claude_api→messages，
    responses→/v1/responses，其余走 chat/completions（返回 None=默认协议）。"""
    eps = [t.strip().lower() for t in str(row.get("endpoints") or "").split(",")]
    mid = str(row.get("model_id") or "").lower()
    if "claude_api" in eps or "claude" in mid:
        return "messages"
    if "responses" in eps or "responses_api" in eps:
        return "responses"
    return None


def get_catalog():
    """LLM 目录，10 分钟缓存。接口挂了返回上次结果/空表（picker 退化为内置模型）。"""
    with _CATALOG_LOCK:
        if time.time() - _CATALOG["ts"] < 600 and _CATALOG["rows"]:
            return _CATALOG["rows"]
    try:
        req = urllib.request.Request(f"{SERVER_DOMAIN}/api/v1/models?type=llm&sort_by=order")
        with urllib.request.urlopen(req, timeout=15) as r:
            rows = (json.load(r).get("data")) or []
    except Exception:  # noqa: BLE001
        return _CATALOG["rows"]
    out = []
    for row in rows:
        mid = row.get("model_id") or row.get("id")
        if not mid or mid == "auto" or not MODEL_ID_RE.match(mid):
            continue
        pricing = row.get("pricing") or {}
        modalities = str(row.get("input_modalities") or row.get("modalities") or "")
        out.append({"id": mid,
                    "name": row.get("model_name") or row.get("name") or mid,
                    "endpoint": infer_endpoint(row),
                    "max_output": int(row.get("max_output") or 0),
                    "image": "image" in modalities.lower(),
                    "input": float(pricing.get("input") or row.get("input_price") or 0),
                    "output": float(pricing.get("output") or row.get("output_price") or 0)})
    with _CATALOG_LOCK:
        if out:
            _CATALOG.update(ts=time.time(), rows=out)
    return _CATALOG["rows"]


def picker_models():
    """配置内模型置顶（带调优参数，默认阵容预选），目录其余模型可搜索追加。"""
    conf, seen = [], set()
    for mid, cfg in run_showdown.MODELS.items():
        pin, pout = run_showdown.PRICING.get(mid, (0, 0))
        conf.append({"id": mid, "name": cfg.get("display", mid), "configured": True,
                     "default": cfg.get("lineup", True) is not False,
                     "image": True,  # 内置阵容都验证过多模态 ref 图链路
                     "input": pin, "output": pout})
        seen.add(mid)
    return conf + [m for m in get_catalog() if m["id"] not in seen]


def fetch_role(jwt):
    """playground 同源用户接口(/call/usr/self)取 role。取不到一律按 0（无特权）。"""
    req = urllib.request.Request(f"{SERVER_DOMAIN}/call/usr/self",
                                 headers={"Authorization": f"Bearer {jwt}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.load(r)
        data = body.get("data") or body
        return int(data.get("role") or 0)
    except Exception:  # noqa: BLE001
        return 0


BRAND_MIN_ROLE = 10  # 站方人员起（one-api 语义：10=admin, 100=root）


def catalog_model_cfg(mid):
    """为目录选出的非内置模型生成 run_showdown 临时配置 + 牌价。"""
    m = next((x for x in get_catalog() if x["id"] == mid), None)
    if not m:
        return None, None
    cfg = {"display": m["name"],
           "max_tokens": min(m["max_output"], 128000) or 60000}
    if m["endpoint"]:
        cfg["endpoint"] = m["endpoint"]
    return cfg, (m["input"], m["output"])


# ---------- episode index ----------

def load_results(ep_dir):
    results = []
    for path in sorted(glob.glob(f"{ep_dir}/metrics*.json")):
        try:
            with open(path) as f:
                for r in json.load(f)["results"]:
                    if r.get("code_extracted"):
                        results.append(r)
        except (ValueError, KeyError):
            continue
    return results


def arena_episodes():
    """episodes already featured in the public gallery (docs/rounds.js)."""
    try:
        with open(os.path.join(SCRIPT_DIR, "docs", "rounds.js")) as f:
            return set(re.findall(r'episode:\s*"([^"]+)"', f.read()))
    except OSError:
        return set()


def ep_title(ep_dir, results):
    meta_path = os.path.join(ep_dir, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                t = json.load(f).get("title")
            if t:
                return t
        except ValueError:
            pass
    task = os.path.join(ep_dir, "task.md")
    if os.path.exists(task):
        with open(task) as f:
            first = " ".join(f.read().split()).lstrip("# ").strip()
        if first:
            return first[:80] + ("…" if len(first) > 80 else "")
    return os.path.basename(ep_dir)


def scan_episodes():
    """Every episode with at least one recorded artifact, newest first."""
    arena = arena_episodes()
    eps = []
    for d in sorted(glob.glob(f"{EPISODES_DIR}/ep*") + glob.glob(f"{WEB_EP_DIR}/*")):
        if not os.path.isdir(d):
            continue
        results = load_results(d)
        models = []
        for r in results:
            m = r["requested"]
            if os.path.exists(os.path.join(d, "recordings", f"{m}.webm")):
                models.append(r)
        if not models:
            continue
        ep_id = os.path.relpath(d, EPISODES_DIR)  # "ep13" or "web/0724-..."
        poster = next((f"poster_{r['requested']}.png" for r in models
                       if os.path.exists(os.path.join(d, "recordings",
                                                      f"poster_{r['requested']}.png"))), None)
        eps.append({
            "id": ep_id, "dir": d, "title": ep_title(d, results), "models": models,
            "poster": poster, "cost": sum(r.get("cost_usd", 0) for r in models),
            "mtime": os.path.getmtime(d), "arena": os.path.basename(d) in arena,
            "web": ep_id.startswith("web/"),
        })
    eps.sort(key=lambda e: (-e["arena"], -e["mtime"]))
    return eps


def find_episode(ep_id):
    d = os.path.realpath(os.path.join(EPISODES_DIR, ep_id))
    if not (d.startswith(os.path.realpath(EPISODES_DIR) + os.sep) and os.path.isdir(d)):
        return None
    for e in scan_episodes():
        if e["id"] == ep_id:
            return e
    return None


# ---------- pages ----------

KEY_SECTION_JS = """
<script type="module">
  // module：保证在 head 里的 Clerk 引导 module 之后按序执行（普通 script 会抢跑）
  const $ = (id) => document.getElementById(id);
  let authMode = 'account';
  function setMode(m) {
    authMode = m;
    $('tab-account').classList.toggle('active', m === 'account');
    $('tab-manual').classList.toggle('active', m === 'manual');
    $('pane-account').hidden = m !== 'account';
    $('pane-manual').hidden = m !== 'manual';
  }
  $('tab-account').onclick = () => setMode('account');
  $('tab-manual').onclick = () => setMode('manual');

  // 本机 operator 部署（配置了服务器 env key，如本地自用）直接放开品牌开关；
  // 公网部署不应配置服务器 env key
  if (document.body.dataset.operator === '1') {
    const br = document.getElementById('brand-row');
    if (br) br.hidden = false;
  }

  async function initAccount() {
    const st = $('acct-status');
    if (!window._clerkReady) { st.textContent = 'account mode is not configured on this deployment — paste a key instead'; setMode('manual'); return; }
    const clerk = await window._clerkReady;
    if (!clerk) { st.textContent = 'Clerk failed to load (origin not allowed?) — paste a key instead'; setMode('manual'); return; }
    if (!clerk.user) {
      st.textContent = 'sign in to pick one of your gateway keys';
      $('sign-in').hidden = false;
      $('sign-in').onclick = () => clerk.openSignIn();
      clerk.addListener(({ user }) => { if (user) location.reload(); });
      return;
    }
    st.textContent = 'loading your keys…';
    const jwt = await clerk.session.getToken();
    // 品牌开关仅 role>=10(站方)可见；服务端提交时会用 JWT 再校验一次，前端只管展示
    fetch(window.BASE + '/api/user', { headers: { Authorization: 'Bearer ' + jwt } })
      .then(r => r.json())
      .then(u => { if ((u.role || 0) >= 10) $('brand-row').hidden = false; })
      .catch(() => {});
    const r = await fetch(window.BASE + '/api/keys', { headers: { Authorization: 'Bearer ' + jwt } });
    if (!r.ok) { st.textContent = 'could not load keys (' + r.status + ') — paste a key instead'; setMode('manual'); return; }
    const keys = await r.json();
    if (!keys.length) { st.textContent = 'no keys on this account — create one in the console, or paste a key'; return; }
    const sel = $('key-select');
    sel.innerHTML = keys.map(k => `<option value="${k.id}">${k.name} · ${k.masked}</option>`).join('');
    sel.hidden = false;
    st.textContent = `signed in as ${clerk.user.primaryEmailAddress?.emailAddress || clerk.user.id} —`;
  }
  initAccount();

  // 搜索过滤会把已勾选但不在当前列表里的模型移出 DOM —— 提交前从 checked 集合
  // 统一注入 hidden inputs，可见 checkbox 全部 disable 防重复
  function syncModels(form) {
    form.querySelectorAll('input.m-hidden').forEach(n => n.remove());
    form.querySelectorAll('#model-list input[type=checkbox]').forEach(cb => { cb.disabled = true; });
    checked.forEach(id => {
      const inp = document.createElement('input');
      inp.type = 'hidden'; inp.name = 'models'; inp.value = id; inp.className = 'm-hidden';
      form.appendChild(inp);
    });
  }
  function submitLoading(form) {   // loading-buttons：异步期间禁用并给反馈
    const btn = form.querySelector('button[type=submit]');
    btn.disabled = true; btn.textContent = 'Queuing…';
  }
  document.getElementById('showdown-form').addEventListener('submit', async (e) => {
    syncModels(e.target);
    if (authMode !== 'account') { submitLoading(e.target); return; }  // manual: plain POST
    e.preventDefault();
    const clerk = window._clerk;
    if (!clerk?.user) { alert('sign in first, or switch to "Paste a key"'); return; }
    document.getElementById('f-token-id').value = $('key-select').value;
    document.getElementById('f-jwt').value = await clerk.session.getToken();
    submitLoading(e.target);
    e.target.submit();
  });

  // ---- model picker: live catalog + search, tuned lineup pinned & pre-checked ----
  const MAX_PICK = 4;
  const list = $('model-list');
  let MODELS = [];
  const checked = new Set();
  function renderModels(q) {
    q = (q || '').toLowerCase();
    const vis = MODELS.filter(m => !q || m.id.toLowerCase().includes(q) ||
                                    m.name.toLowerCase().includes(q)).slice(0, 60);
    list.innerHTML = vis.map(m => `
      <label class="model-opt"><input type="checkbox" name="models" value="${m.id}"
        ${checked.has(m.id) ? 'checked' : ''}> ${m.name}
        <code>${m.id}${m.input || m.output ? ` · $${m.input}/${m.output}` : ''}</code></label>`
    ).join('') || '<span class="hint" style="margin:0">no match</span>';
  }
  // 参考图区仅当「已选模型全部支持图片输入」时展示（先选模型，再给图）
  function updateRefSection() {
    const ms = [...checked].map(id => MODELS.find(x => x.id === id));
    document.getElementById('ref-section').hidden =
      !(checked.size > 0 && ms.every(m => m && m.image));
  }
  list.addEventListener('change', (ev) => {
    const cb = ev.target;
    if (cb.checked && checked.size >= MAX_PICK) {
      cb.checked = false;
      alert(`pick at most ${MAX_PICK} models per run`);
      return;
    }
    cb.checked ? checked.add(cb.value) : checked.delete(cb.value);
    updateRefSection();
  });
  document.getElementById('model-search').addEventListener('input',
    (ev) => renderModels(ev.target.value));
  fetch(window.BASE + '/api/models').then(r => r.json()).then(ms => {
    MODELS = ms;
    ms.filter(m => m.default).forEach(m => checked.add(m.id));
    renderModels('');
    updateRefSection();
  }).catch(() => { list.innerHTML = '<span class="hint" style="margin:0">catalog unavailable</span>'; });

  // ---- reference images: drag & drop / paste / browse -> base64 hidden inputs ----
  // （playground 同思路：客户端 FileReader 转 base64 dataURL，随请求体走）
  const MAX_REFS = 3, MAX_REF_MB = 6;
  const refs = [];   // dataURL strings
  const dz = $('dropzone'), previews = $('ref-previews'), fileInput = $('ref-file');
  function renderRefs() {
    previews.innerHTML = refs.map((d, i) => `
      <span class="ref-item"><img src="${d}" alt="reference image ${i + 1}">
        <button type="button" data-i="${i}" title="remove" aria-label="remove reference image ${i + 1}">×</button></span>`).join('');
    $('drop-hint').textContent = refs.length
      ? `${refs.length}/${MAX_REFS} attached — drop/paste/click to add more`
      : 'drop images here, paste, or click to browse';
    $('vision-note').hidden = !refs.length;
    renderModels($('model-search').value);   // 有参考图时置灰非视觉模型
  }
  const origRender = renderModels;
  renderModels = function(q) {
    origRender(q);
    if (!refs.length) return;
    list.querySelectorAll('.model-opt').forEach(el => {
      const cb = el.querySelector('input');
      const m = MODELS.find(x => x.id === cb.value);
      if (m && !m.image) {
        el.classList.add('novision');
        if (cb.checked) { cb.checked = false; checked.delete(cb.value); }
      }
    });
  };
  // 拖图立刻起草 prompt：便宜视觉模型看图写还原型 build brief，可随意手改；
  // 用户手动改过且非空时不覆盖
  const ta = document.querySelector('textarea[name=prompt]');
  let manualEdited = !!ta.value.trim();
  ta.addEventListener('input', () => { manualEdited = true; });
  async function draftPrompt() {
    if (manualEdited && ta.value.trim()) return;
    const prev = ta.value;
    ta.value = 'Drafting a build prompt from your reference image…';
    const body = { image: refs[0] };
    if (authMode === 'account' && window._clerk?.user) {
      body.jwt = await window._clerk.session.getToken();
      body.token_id = $('key-select')?.value || '';
    } else {
      const k = document.querySelector('input[name=api_key]').value.trim();
      if (k) body.key = k;
    }
    try {
      const r = await fetch(window.BASE + '/api/caption', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok || !d.prompt) throw new Error(d.error || ('HTTP ' + r.status));
      ta.value = d.prompt;
      manualEdited = false;
    } catch (e) {
      ta.value = prev;
      alert('prompt drafting failed: ' + e.message + ' — write it manually');
    }
  }
  function addFiles(fileList) {
    for (const f of fileList) {
      if (!/^image\\/(png|jpeg|webp)$/.test(f.type)) continue;
      if (refs.length >= MAX_REFS) { alert(`up to ${MAX_REFS} reference images`); break; }
      if (f.size > MAX_REF_MB * 1024 * 1024) { alert(`${f.name}: over ${MAX_REF_MB}MB`); continue; }
      const rd = new FileReader();
      rd.onload = () => {
        refs.push(rd.result); renderRefs();
        if (refs.length === 1) draftPrompt();
      };
      rd.readAsDataURL(f);
    }
  }
  dz.addEventListener('click', (ev) => { if (ev.target.tagName !== 'BUTTON') fileInput.click(); });
  dz.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener('change', () => { addFiles(fileInput.files); fileInput.value = ''; });
  previews.addEventListener('click', (ev) => {
    if (ev.target.tagName === 'BUTTON') { refs.splice(+ev.target.dataset.i, 1); renderRefs(); }
  });
  ['dragover', 'dragenter'].forEach(t => dz.addEventListener(t, (ev) => {
    ev.preventDefault(); dz.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(t => dz.addEventListener(t, (ev) => {
    ev.preventDefault(); dz.classList.remove('over'); }));
  dz.addEventListener('drop', (ev) => addFiles(ev.dataTransfer.files));
  document.querySelector('textarea[name=prompt]').addEventListener('paste', (ev) => {
    const files = [...(ev.clipboardData?.items || [])]
      .filter(it => it.kind === 'file').map(it => it.getAsFile()).filter(Boolean);
    if (files.length) { ev.preventDefault(); addFiles(files); }
  });
  const origSync = syncModels;
  syncModels = function(form) {
    origSync(form);
    form.querySelectorAll('input.r-hidden').forEach(n => n.remove());
    refs.forEach(d => {
      const inp = document.createElement('input');
      inp.type = 'hidden'; inp.name = 'refs'; inp.value = d; inp.className = 'r-hidden';
      form.appendChild(inp);
    });
  };
</script>
"""


def create_form_html(prefill=""):
    server_key_hint = (" (server key configured — manual field may stay empty)"
                       if os.environ.get(KEY_ENV) else "")
    return f"""
<div class="card" id="create"><form method="post" action="{B}/submit" id="showdown-form">
  <label>Models <span class="hint">live catalog (/api/v1/models) · tuned lineup pinned
  first · pick up to 4<span id="vision-note" hidden> · <b>ref images attached — models
  without image input are disabled</b></span></span></label>
  <input type="text" id="model-search" placeholder="search models…" autocomplete="off">
  <div class="models" id="model-list" style="max-height:264px;overflow:auto;margin-top:8px">
    <span class="hint" style="margin:0">loading catalog…</span>
  </div>
  <div id="ref-section" hidden>
    <label>Reference images <span class="hint">every selected model supports image
    input · drop / paste / click · up to 3 · dropping one auto-drafts the prompt below</span></label>
    <div class="dropzone" id="dropzone" role="button" tabindex="0"
         aria-label="Upload reference images: drop, paste, or press Enter to browse">
      <span class="hint" style="margin:0" id="drop-hint">drop images here, paste, or click to browse</span>
      <div class="ref-previews" id="ref-previews"></div>
      <input type="file" id="ref-file" accept="image/png,image/jpeg,image/webp" multiple hidden>
    </div>
  </div>
  <label>Prompt <span class="hint">what should every model build? single-file HTML
  with an auto-demo works best — auto-drafted from your reference image, edit freely</span></label>
  <textarea name="prompt" required placeholder="Build a playable ... as one self-contained HTML file ...">{html.escape(prefill)}</textarea>
  <div class="row">
    <div><label>Title <span class="hint">shown on the scoreboard</span></label>
    <input type="text" name="title" placeholder="Model Showdown"></div>
    <div style="flex:0 0 160px"><label>Record seconds</label>
    <select name="seconds"><option>18</option><option selected>26</option><option>40</option></select></div>
  </div>
  <label>API key <span class="hint">account keys stay in the gateway — the full key
  never reaches this server; manual keys live in memory for this job only{server_key_hint}</span></label>
  <div class="tabs">
    <button type="button" class="tab active" id="tab-account">My account keys</button>
    <button type="button" class="tab" id="tab-manual">Paste a key</button>
  </div>
  <div id="pane-account">
    <div class="acct-line">
      <span id="acct-status" class="hint" style="margin:0">…</span>
      <select id="key-select" hidden style="width:auto;min-width:280px"></select>
      <button type="button" class="btn2" id="sign-in" hidden>Sign in</button>
    </div>
  </div>
  <div id="pane-manual" hidden>
    <input type="password" name="api_key" autocomplete="off" placeholder="sk-...">
  </div>
  <div id="brand-row" hidden>
    <label class="model-opt" style="width:fit-content;margin-top:16px">
      <input type="checkbox" name="brand" value="on" checked> Brand logo &amp; watermark
      <span class="hint">staff only — UGC runs never carry the logo</span>
    </label>
  </div>
  <input type="hidden" name="token_id" id="f-token-id">
  <input type="hidden" name="jwt" id="f-jwt">
  <button class="btn" type="submit">Run the showdown</button>
</form></div>"""


def home_html(prefill=""):
    active = [j for j in db.list_recent(20) if j["status"] in ("queued", "running")]
    active_html = ""
    if active:
        rows = "".join(
            f'<tr><td><a href="{B}/job/{j["id"]}">{j["id"]}</a></td>'
            f'<td>{html.escape(j["title"])}</td>'
            f'<td><span class="tag {j["status"]}">{j["status"].upper()}</span></td></tr>'
            for j in active)
        active_html = (f'<div class="card" style="padding:16px 24px"><label>In progress</label>'
                       f"<table>{rows}</table></div>")
    cards = []
    for e in scan_episodes():
        thumb = (f'<img src="{B}/media/{e["id"]}/{e["poster"]}" loading="lazy" '
                 f'alt="{html.escape(e["title"])} poster">' if e["poster"] else "")
        badges = ""
        if e["arena"]:
            badges += '<span class="tag arena">ARENA ROUND</span>'
        if e["web"]:
            badges += '<span class="tag web">WEB</span>'
        names = " · ".join(r["display"] for r in e["models"][:4])
        cards.append(f"""
<a class="feed-card" href="{B}/watch/{e["id"]}">
  <div class="thumb">{thumb}</div>
  <div class="body">
    <div class="t">{html.escape(e["title"])}</div>
    <div class="m"><span>{len(e["models"])} models</span><span>${e["cost"]:.2f}</span>{badges}</div>
    <div class="m" style="margin-top:2px">{html.escape(names)}</div>
  </div>
</a>""")
    head = CLERK_BOOT.format(pk=json.dumps(CLERK_PK)) if CLERK_PK else ""
    return page("Model Showdown", f"""
<h1>Model Showdown</h1>
<div class="sub">Same prompt · one shot each · real costs — generate a publish-ready
side-by-side comparison, then browse every run below. All models served via AIHubMix.</div>
{create_form_html(prefill)}
{active_html}
<h2>Runs</h2>
<div class="feed">{"".join(cards) or '<div class="sub">nothing yet — create the first one above</div>'}</div>
{KEY_SECTION_JS}""", head=head,
                body_attrs=' data-operator="1"' if os.environ.get(KEY_ENV) else "")


def watch_html(e):
    """Detail view: left = auto-playing works, right = pinned prompt/common info
    + per-work params revealed on click."""
    task_path = os.path.join(e["dir"], "task.md")
    prompt_text = ""
    if os.path.exists(task_path):
        with open(task_path) as f:
            prompt_text = f.read()
    wall = None
    mpath = os.path.join(e["dir"], "metrics.json")
    if os.path.exists(mpath):
        try:
            with open(mpath) as f:
                wall = json.load(f).get("wall_s")
        except ValueError:
            pass
    accents = ["#38bdf8", "#f97316", "#a78bfa", "#22c55e", "#34d399", "#f472b6"]
    works, detail = [], {}
    for i, r in enumerate(e["models"]):
        m = r["requested"]
        cfg = run_showdown.MODELS.get(m, {})
        rep_path = os.path.join(e["dir"], "recordings", f"report_{m}.json")
        verdict = "SHIPPED"
        if os.path.exists(rep_path):
            try:
                with open(rep_path) as f:
                    rep = json.load(f)
                verdict = ("FROZE" if rep.get("frozen")
                           else "RAN W/ ERRORS" if rep.get("consoleErrors") else "SHIPPED")
            except ValueError:
                pass
        u = r.get("usage", {}) or {}
        rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        ct = u.get("completion_tokens", 0)
        detail[m] = {
            "display": r["display"], "model": m, "accent": accents[i % len(accents)],
            "protocol": PROTO.get(cfg.get("endpoint"), "OpenAI /v1/chat/completions"),
            "params": cfg.get("params") or {}, "max_tokens": cfg.get("max_tokens"),
            "latency_s": r.get("latency_s"), "cost": r.get("cost_usd"),
            "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": ct,
            "reasoning_tokens": rt,
            "reasoning_share": f"{rt / ct * 100:.0f}%" if ct and rt else "—",
            "finish_reason": r.get("finish_reason"), "code_lines": r.get("code_lines"),
            "verdict": verdict,
            "raw": f"{B}/media/{e['id']}/raw_{m}.json"
                   if os.path.exists(os.path.join(e["dir"], f"raw_{m}.json")) else None,
            "artifact": f"{B}/media/{e['id']}/work_{m}.html"
                        if os.path.exists(os.path.join(e["dir"], f"work_{m}", "index.html")) else None,
        }
        works.append(f"""
<div class="work" data-m="{m}" id="w-{i}" role="button" tabindex="0"
     aria-label="inspect {html.escape(r["display"])} details">
  <video src="{B}/media/{e["id"]}/{m}.webm" autoplay muted loop playsinline
         aria-label="{html.escape(r["display"])} artifact recording"></video>
  <div class="wh"><b><span class="dot" style="background:{accents[i % len(accents)]}"></span>
  {html.escape(r["display"])}</b><span class="cost">${r.get("cost_usd", 0):.2f} · {r.get("latency_s", 0):.0f}s</span></div>
</div>""")
    n = len(works)
    cols = 2 if n >= 2 else 1
    # 分享区：mp4 只从本站下载（无公网直链，防盗链/滥用）；分享按钮带文案+画廊
    # 链接，视频由用户下载后在发帖页自行附上
    gcs_html = ""
    dist_dir = os.path.join(e["dir"], "dist")
    vids = sorted(f for f in os.listdir(dist_dir)) if os.path.isdir(dist_dir) else []
    vids = [f for f in vids if f.endswith(".mp4")]
    if vids:
        gallery = run_showdown.CONFIG.get("brand", {}).get(
            "gallery", "https://aihubmix.github.io/model-showdown/")
        share_text = urllib.parse.quote(
            f"{e['title']} — same prompt, one shot each. The bill is real. {gallery}")
        share_btns = (
            f'<a class="btn2" style="padding:5px 12px;font-size:12.5px" target="_blank" '
            f'rel="noopener" href="https://twitter.com/intent/tweet?text={share_text}">Share on X</a>'
            f'<a class="btn2" style="padding:5px 12px;font-size:12.5px" target="_blank" '
            f'rel="noopener" href="https://www.reddit.com/submit?url={urllib.parse.quote(gallery)}'
            f'&title={urllib.parse.quote(e["title"])}">Post to Reddit</a>')
        gcs_html = ('<label style="display:flex;align-items:center;justify-content:space-between">'
                    f'Share <span style="display:flex;gap:8px">{share_btns}</span></label>'
                    '<div class="files">'
                    + "".join(f'<a href="{B}/media/{e["id"]}/{urllib.parse.quote(nm)}" download>'
                              f"⬇ {html.escape(nm)}</a>" for nm in vids)
                    + '</div><span class="hint" style="margin:0">download the mp4, attach it '
                    'when you post — videos are never exposed as public URLs</span>')
    ref_html = ""
    ref_files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(e["dir"], "ref*"))
                       if os.path.splitext(p)[1].lower() in (".png", ".jpg", ".jpeg", ".webp"))
    if ref_files:
        ref_html = ('<label>Reference images</label><div class="ref-previews">'
                    + "".join(f'<span class="ref-item"><a href="{B}/media/{e["id"]}/{nm}" target="_blank">'
                              f'<img src="{B}/media/{e["id"]}/{nm}" alt="{nm}"></a></span>'
                              for nm in ref_files) + "</div>")
    common_rows = "".join(
        f"<div><span>{k}</span><span>{html.escape(str(v))}</span></div>"
        for k, v in [("episode", e["id"]), ("models", n),
                     ("total cost", f"${e['cost']:.2f}"),
                     ("wall time", f"{wall:.0f}s" if wall else "—"),
                     ("created", time.strftime("%Y-%m-%d %H:%M", time.localtime(e["mtime"]))),
                     ("gateway", "AIHubMix (one gateway, one shot each)")])
    badges = ('<span class="tag arena">ARENA ROUND</span> ' if e["arena"] else "")
    return page(e["title"], f"""
<div class="sub" style="margin-bottom:12px"><a href="{B}/">← all runs</a></div>
<h1 style="font-size:26px">{html.escape(e["title"])}</h1>
<div class="sub">{badges}click a work to inspect its params, response and wire protocol</div>
<div class="watch">
  <div class="works" style="grid-template-columns:repeat({cols},1fr)">{"".join(works)}</div>
  <div class="side">
    <div class="card">
      <label style="display:flex;align-items:center;justify-content:space-between">Prompt
        <span style="display:flex;gap:8px">
          <button type="button" class="btn2" id="copy-prompt" style="padding:5px 12px;font-size:12.5px">Copy</button>
          <a class="btn2" href="{B}/?from={urllib.parse.quote(e["id"])}#create"
             style="padding:5px 12px;font-size:12.5px">Remix ↗</a>
        </span>
      </label>
      <pre class="prompt" id="prompt-text">{html.escape(prompt_text) or "—"}</pre>
      {ref_html}
      <label>Run</label>
      <div class="kv">{common_rows}</div>
      {gcs_html}
    </div>
    <div class="card" id="sel-card">
      <label>Selected work <span class="hint">click a video on the left</span></label>
      <div class="kv" id="sel-kv"><div><span>—</span><span>nothing selected</span></div></div>
      <div class="files" id="sel-links"></div>
    </div>
  </div>
</div>
<script>
  const DETAIL = {json.dumps(detail)};
  const works = document.querySelectorAll('.work');
  function select(el) {{
    works.forEach(w => w.classList.remove('sel'));
    el.classList.add('sel');
    const d = DETAIL[el.dataset.m];
    const rows = [
      ['model', d.model], ['display', d.display], ['protocol', d.protocol],
      ['request params', Object.keys(d.params).length ? JSON.stringify(d.params) : '—'],
      ['max_tokens', d.max_tokens ?? 'default'],
      ['latency', d.latency_s != null ? d.latency_s + 's' : '—'],
      ['cost', d.cost != null ? '$' + d.cost.toFixed(4) : '—'],
      ['prompt tokens', d.prompt_tokens ?? '—'],
      ['completion tokens', d.completion_tokens ?? '—'],
      ['reasoning tokens', (d.reasoning_tokens || 0) + ' (' + d.reasoning_share + ')'],
      ['finish_reason', d.finish_reason ?? '—'],
      ['code lines', d.code_lines ?? '—'], ['verdict', d.verdict],
    ];
    document.getElementById('sel-kv').innerHTML = rows.map(([k, v]) =>
      `<div><span>${{k}}</span><span>${{String(v).replace(/</g,'&lt;')}}</span></div>`).join('');
    const links = [];
    if (d.artifact) links.push(`<a href="${{d.artifact}}" target="_blank" rel="noopener">artifact ↗</a>`);
    if (d.raw) links.push(`<a href="${{d.raw}}" target="_blank" rel="noopener">raw response ↗</a>`);
    document.getElementById('sel-links').innerHTML = links.join('');
    document.querySelector('#sel-card label').firstChild.textContent = d.display + ' ';
  }}
  works.forEach(w => {{
    w.addEventListener('click', () => select(w));
    w.addEventListener('keydown', (ev) => {{
      if (ev.key === 'Enter' || ev.key === ' ') {{ ev.preventDefault(); select(w); }}
    }});
  }});
  if (works.length) select(works[0]);
  document.getElementById('copy-prompt').addEventListener('click', async (ev) => {{
    await navigator.clipboard.writeText(document.getElementById('prompt-text').textContent);
    ev.target.textContent = 'Copied ✓';
    setTimeout(() => {{ ev.target.textContent = 'Copy'; }}, 1500);
  }});
</script>""")


def job_html(job):
    st = job["status"]
    body = [f'<h1>Job {job["id"]}</h1>',
            f'<div class="sub">{html.escape(job["title"])} · '
            f'<span class="tag {st}">{st.upper()}</span></div>']
    if st == "done":
        body.append(f'<div class="card">Done — <a href="{B}/watch/web/{job["id"]}">watch it ↗</a> '
                    f"or grab the share pack below.</div>")
    if job["auth_mode"] == "account" and st in ("queued", "running"):
        body.append('<div class="card" style="padding:14px 24px"><span class="hint" style="margin:0">'
                    'account-key job: keep this page open — it refreshes the short-lived '
                    'sign-in token the gateway needs (or resubmit with a pasted key for '
                    'unattended runs)</span></div>')
    if st == "failed" and job.get("fail_reason"):
        body.append(f'<div class="card" style="padding:14px 24px"><span class="hint" '
                    f'style="margin:0;color:var(--ah-danger-text)">'
                    f'{html.escape(job["fail_reason"])}</span></div>')
    if st == "queued":
        ahead = db.queued_ahead(job["created"])
        body.append(f'<div class="card">Position in queue: {ahead + 1} '
                    f"(jobs run one at a time — recording needs the GPU to itself)</div>")
    log_path = os.path.join(job["ep_dir"], "run.log")
    if st in ("running", "done", "failed") and os.path.exists(log_path):
        with open(log_path, errors="replace") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 6000))
            tail = f.read()
        body.append(f'<div class="card"><label>Log</label>'
                    f'<pre class="log">{html.escape(tail)}</pre></div>')
    if st == "done":
        dist = os.path.join(job["ep_dir"], "dist")
        files = sorted(os.listdir(dist)) if os.path.isdir(dist) else []
        links = "".join(f'<a href="{B}/job/{job["id"]}/dl/{urllib.parse.quote(n)}">{html.escape(n)}</a>'
                        for n in files)
        body.append(f'<div class="card"><label>Share pack</label>'
                    f'<div class="files">{links}</div></div>')
        meta_path = os.path.join(job["ep_dir"], "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    if json.load(f).get("gcs"):
                        body.append('<div class="card" style="padding:14px 24px">'
                                    '<span class="hint" style="margin:0">archived to GCS '
                                    '(private bucket — videos are never exposed as public '
                                    'URLs)</span></div>')
            except ValueError:
                pass
    body.append(f'<div class="sub"><a href="{B}/">← home</a></div>')
    refresh = 5 if st in ("queued", "running") else None
    head = ""
    if job["auth_mode"] == "account" and st in ("queued", "running") and CLERK_PK:
        # 每次自刷新都推一枚新 JWT（Clerk token 分钟级过期；重试/下一条请求现读文件）
        head = CLERK_BOOT.format(pk=json.dumps(CLERK_PK)) + f"""
<script type="module">
  const clerk = await window._clerkReady;
  if (clerk?.user) {{
    const jwt = await clerk.session.getToken();
    fetch('{B}/job/{job["id"]}/token', {{ method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ jwt }}) }});
  }}
</script>"""
    return page(f"Job {job['id']}", "\n".join(body), refresh=refresh, head=head)


def decode_ref_image(data_url, max_bytes=6 * 1024 * 1024):
    """dataURL → (ext, bytes)。按真实 magic bytes 判型（Anthropic 严格校验
    声明 mime 与字节一致，扩展名/前缀不可信），超限或非图返回 None。"""
    m = re.match(r"data:image/(?:png|jpeg|webp);base64,(.+)$", data_url, re.S)
    if not m:
        return None
    import base64
    try:
        data = base64.b64decode(m.group(1), validate=False)
    except Exception:  # noqa: BLE001
        return None
    if not data or len(data) > max_bytes:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ("png", data)
    if data[:3] == b"\xff\xd8\xff":
        return ("jpg", data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("webp", data)
    return None


# ---------- job execution ----------

def write_auth_file(job, jwt):
    path = os.path.join(job["ep_dir"], ".auth.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"jwt": jwt, "token_id": job["token_id"]}, f)
    return path


def worker():
    while True:
        job = db.claim_next()
        if not job:
            time.sleep(3)
            continue
        with SECRETS_LOCK:
            key = SECRETS.get(job["id"])
        env = os.environ.copy()
        auth_path = None
        if job["auth_mode"] == "account":
            auth_path = os.path.join(job["ep_dir"], ".auth.json")
            env["SHOWDOWN_AUTH_FILE"] = auth_path
            env["SHOWDOWN_GATEWAY"] = f"{SERVER_DOMAIN}/v1/chat/completions"
        elif key:
            env[KEY_ENV] = key
        if job.get("extra_models"):
            env["SHOWDOWN_EXTRA_MODELS"] = json.dumps(job["extra_models"])
            env["SHOWDOWN_EXTRA_PRICING"] = json.dumps(job["extra_pricing"])
        cmd = [sys.executable, os.path.join(SCRIPT_DIR, "showdown.py"), job["ep_dir"],
               "--task", os.path.join(job["ep_dir"], "task.md"),
               "--models", job["models"], "--seconds", str(job["seconds"]),
               "--title", job["title"], "--formats", "wide,square"]
        if not job.get("brand"):
            cmd.append("--no-brand")
        # 整段兜底：任何一步（含日志写入本身）异常都不能杀死 worker 线程，
        # 也必须保证任务最终落到终态——否则重启前它会永远显示 running
        ok, reason = False, "run failed — see log"
        try:
            with open(os.path.join(job["ep_dir"], "run.log"), "w") as log:
                r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, timeout=3 * 3600)
            ok = r.returncode == 0
        except Exception as e:  # noqa: BLE001
            reason = f"worker error: {str(e)[:180]}"
            try:
                with open(os.path.join(job["ep_dir"], "run.log"), "a") as log:
                    log.write(f"\n{reason}\n")
            except OSError:
                pass
        finally:
            if auth_path and os.path.exists(auth_path):
                os.remove(auth_path)
            with SECRETS_LOCK:
                SECRETS.pop(job["id"], None)  # key 用完即弃
        try:
            if ok and GCS_BUCKET:
                upload_dist_to_gcs(job)  # 结果落 meta.json，页面从那读
        except Exception:  # noqa: BLE001
            pass  # 归档失败不影响任务结果，本地 dist 仍可下载
        db.set_status(job["id"], "done" if ok else "failed", None if ok else reason)


def upload_dist_to_gcs(job):
    """成片分享包上传 GCS（公开可读 bucket），返回 [(name, public_url)]。
    失败不影响任务状态——本地 dist 仍可下载，错误记进 run.log。"""
    dist = os.path.join(job["ep_dir"], "dist")
    files = sorted(f for f in os.listdir(dist)) if os.path.isdir(dist) else []
    if not files:
        return []
    dest = f"gs://{GCS_BUCKET}/runs/{job['id']}/"
    r = subprocess.run(["gcloud", "storage", "cp"]
                       + [os.path.join(dist, f) for f in files] + [dest],
                       capture_output=True, text=True, timeout=600)
    with open(os.path.join(job["ep_dir"], "run.log"), "a") as log:
        if r.returncode != 0:
            log.write(f"\n[gcs] upload FAILED: {r.stderr[-500:]}\n")
            return []
        log.write(f"\n[gcs] uploaded {len(files)} files to {dest}\n")
    urls = [(f, f"https://storage.googleapis.com/{GCS_BUCKET}/runs/{job['id']}/"
                f"{urllib.parse.quote(f)}") for f in files]
    # 落到 meta.json，重启后 watch 页仍能取到
    meta_path = os.path.join(job["ep_dir"], "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except ValueError:
            pass
    meta["gcs"] = {"bucket": GCS_BUCKET, "files": urls}
    with open(meta_path, "w") as f:
        json.dump(meta, f, ensure_ascii=False)
    return urls


# ---------- http ----------

MEDIA_TYPES = {".webm": "video/webm", ".mp4": "video/mp4", ".png": "image/png",
               ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".json": "application/json", ".md": "text/plain; charset=utf-8",
               ".html": "text/html; charset=utf-8", ".wav": "audio/wav"}


class Handler(BaseHTTPRequestHandler):
    server_version = "showdown-web"

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *a):  # 不打请求日志（body/头里有 key 与 JWT）
        pass

    def _proxy_keys(self):
        """代理 playground 同款 key 列表接口（浏览器直连会撞 CORS，服务端转发）。"""
        jwt = self.headers.get("Authorization", "")
        if not jwt.startswith("Bearer "):
            return self._send(401, "[]", "application/json")
        req = urllib.request.Request(f"{SERVER_DOMAIN}/call/tkn/?num=10000",
                                     headers={"Authorization": jwt})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            return self._send(e.code, "[]", "application/json")
        except Exception:  # noqa: BLE001
            return self._send(502, "[]", "application/json")
        if body.get("success") is False:
            return self._send(403, "[]", "application/json")
        keys = [{"id": str(k.get("id") or k.get("token_id") or k.get("name")),
                 "name": k.get("name") or "Unnamed Key",
                 "masked": k.get("key") or k.get("masked_key") or "****"}
                for k in (body.get("data") or []) if k.get("status") in (None, 1)]
        return self._send(200, json.dumps(keys), "application/json")

    def _same_site(self):
        """视频防盗链（MVP 级）：Referer 必须是本站页面——直贴 URL、外站 <video>
        嵌入都拿不到。正式部署换签名 URL/签名 Cookie。"""
        ref_host = urllib.parse.urlparse(self.headers.get("Referer", "")).netloc
        if not ref_host:
            return False
        host = self.headers.get("Host", "")
        return ref_host == host or (bool(PUBLIC_HOST) and ref_host == PUBLIC_HOST)

    def _media(self, rest):
        """/media/<ep-id>/<file> — recordings first, then episode root/dist; whitelisted
        types only; realpath must stay inside episodes/ (no traversal)."""
        name = os.path.basename(urllib.parse.unquote(rest[-1]))
        ep_id = "/".join(rest[:-1])
        ep_dir = os.path.realpath(os.path.join(EPISODES_DIR, ep_id))
        if not ep_dir.startswith(os.path.realpath(EPISODES_DIR) + os.sep):
            return self._send(404, "not found", "text/plain")
        ext = os.path.splitext(name)[1]
        if ext not in MEDIA_TYPES or name.startswith("."):
            return self._send(404, "not found", "text/plain")
        if ext in (".mp4", ".webm") and not self._same_site():
            return self._send(403, "videos are only served to this site's own pages",
                              "text/plain")
        m = re.fullmatch(r"work_(.+)\.html", name)
        candidates = ([os.path.join(ep_dir, f"work_{m.group(1)}", "index.html")] if m else
                      [os.path.join(ep_dir, "recordings", name), os.path.join(ep_dir, name),
                       os.path.join(ep_dir, "dist", name)])
        for path in candidates:
            if os.path.exists(path) and os.path.realpath(path).startswith(ep_dir + os.sep):
                with open(path, "rb") as f:
                    # artifact html 走 sandbox 头，别给它同源权力
                    extra = ({"Content-Security-Policy": "sandbox allow-scripts allow-pointer-lock"}
                             if m else {"Cache-Control": "max-age=3600"})
                    return self._send(200, f.read(), MEDIA_TYPES[ext], extra)
        return self._send(404, "not found", "text/plain")

    def _strip_base(self):
        if B and self.path.startswith(B):
            self.path = self.path[len(B):] or "/"

    def do_GET(self):
        self._strip_base()
        url = urllib.parse.urlparse(self.path)
        parts = url.path.strip("/").split("/")
        if url.path in ("/", ""):
            # Remix：/?from=<ep-id> 把该期 prompt 预填进创建表单
            prefill = ""
            src = (urllib.parse.parse_qs(url.query).get("from") or [""])[0]
            if src:
                e = find_episode(src)
                task = os.path.join(e["dir"], "task.md") if e else None
                if task and os.path.exists(task):
                    with open(task) as f:
                        prefill = f.read().strip()
            return self._send(200, home_html(prefill))
        if parts[0] == "healthz":
            return self._send(200, "ok", "text/plain")
        if parts[0] == "api" and len(parts) == 2 and parts[1] == "keys":
            return self._proxy_keys()
        if parts[0] == "api" and len(parts) == 2 and parts[1] == "models":
            return self._send(200, json.dumps(picker_models()), "application/json",
                              {"Cache-Control": "max-age=300"})
        if parts[0] == "api" and len(parts) == 2 and parts[1] == "user":
            jwt = self.headers.get("Authorization", "")
            if not jwt.startswith("Bearer "):
                return self._send(401, '{"role":0}', "application/json")
            return self._send(200, json.dumps({"role": fetch_role(jwt[7:])}),
                              "application/json")
        if parts[0] == "media" and len(parts) >= 3:
            return self._media(parts[1:])
        if parts[0] == "watch" and len(parts) >= 2:
            e = find_episode("/".join(parts[1:]))
            if not e:
                return self._send(404, page("Not found", "<h1>run not found</h1>"))
            return self._send(200, watch_html(e))
        if parts[0] == "job" and len(parts) >= 2:
            job = db.get(parts[1])
            if not job:
                return self._send(404, page("Not found", "<h1>job not found</h1>"))
            if len(parts) == 2:
                return self._send(200, job_html(job))
            if len(parts) == 4 and parts[2] == "dl":
                name = os.path.basename(urllib.parse.unquote(parts[3]))
                path = os.path.join(job["ep_dir"], "dist", name)
                if not os.path.exists(path):
                    return self._send(404, page("Not found", "<h1>file not found</h1>"))
                ext = os.path.splitext(name)[1]
                if ext in (".mp4", ".webm") and not self._same_site():
                    return self._send(403, "videos are only served to this site's own pages",
                                      "text/plain")
                with open(path, "rb") as f:
                    return self._send(200, f.read(), MEDIA_TYPES.get(ext, "application/octet-stream"),
                                      {"Content-Disposition": f'attachment; filename="{name}"'}
                                      if ext == ".mp4" else None)
        return self._send(404, page("Not found", "<h1>404</h1>"))

    def do_POST(self):
        self._strip_base()
        parts = self.path.strip("/").split("/")
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        # 参考图 → build prompt（便宜视觉模型看图起草，还原优先）
        if len(parts) == 2 and parts[0] == "api" and parts[1] == "caption":
            try:
                body = json.loads(raw)
            except ValueError:
                return self._send(400, '{"error":"bad json"}', "application/json")
            img = decode_ref_image(str(body.get("image", "")))
            if not img:
                return self._send(400, '{"error":"invalid image"}', "application/json")
            ext, data = img
            import base64
            data_url = f"data:image/{'jpeg' if ext == 'jpg' else ext};base64," \
                       f"{base64.b64encode(data).decode()}"
            jwt, token_id, key = (str(body.get(k, "")) for k in ("jwt", "token_id", "key"))
            if jwt and token_id:
                auth = {"Authorization": f"Bearer {jwt}", "X-Pg-Token-Id": token_id}
            elif key or os.environ.get(KEY_ENV):
                auth = {"Authorization": f"Bearer {key or os.environ[KEY_ENV]}"}
            else:
                return self._send(401, '{"error":"no key available"}', "application/json")
            payload = {"model": CAPTION_MODEL, "max_tokens": 1200,
                       "messages": [{"role": "user", "content": [
                           {"type": "text", "text": CAPTION_INSTRUCTION},
                           {"type": "image_url", "image_url": {"url": data_url}}]}]}
            req = urllib.request.Request(
                f"{SERVER_DOMAIN}/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **auth})
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    resp = json.load(r)
                text = (resp.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            except urllib.error.HTTPError as e:
                return self._send(e.code, json.dumps({"error": f"upstream {e.code}"}),
                                  "application/json")
            except Exception as e:  # noqa: BLE001
                return self._send(502, json.dumps({"error": str(e)[:200]}), "application/json")
            if not text.strip():
                return self._send(502, '{"error":"empty caption"}', "application/json")
            return self._send(200, json.dumps({"prompt": text.strip()}, ensure_ascii=False),
                              "application/json")

        # 任务页推新 JWT：只更新 jwt，token_id 以服务端任务记录为准（防篡改）
        if len(parts) == 3 and parts[0] == "job" and parts[2] == "token":
            job = db.get(parts[1])
            if not job or job["auth_mode"] != "account" or job["status"] not in ("queued", "running"):
                return self._send(404, "{}", "application/json")
            try:
                jwt = json.loads(raw).get("jwt") or ""
            except ValueError:
                jwt = ""
            if jwt:
                write_auth_file(job, jwt)
            return self._send(200, "{}", "application/json")

        if self.path != "/submit":
            return self._send(404, page("Not found", "<h1>404</h1>"))
        form = urllib.parse.parse_qs(raw.decode())
        get = lambda k: (form.get(k) or [""])[0].strip()  # noqa: E731
        prompt = get("prompt")
        extra_models, extra_pricing, models = {}, {}, []
        for m in dict.fromkeys(form.get("models", [])):  # 去重保序
            if m in run_showdown.MODELS:
                models.append(m)
            elif MODEL_ID_RE.match(m):
                cfg, price = catalog_model_cfg(m)
                if cfg:  # 目录里查得到才接受，防任意 id 注入
                    models.append(m)
                    extra_models[m] = cfg
                    extra_pricing[m] = list(price)
        key, token_id, jwt = get("api_key"), get("token_id"), get("jwt")
        title = get("title") or "Model Showdown"
        try:
            seconds = max(8, min(60, int(get("seconds") or "26")))
        except ValueError:
            seconds = 26
        if not prompt or not models:
            return self._send(400, page("Invalid", "<h1>prompt and at least one model required</h1>"))
        refs = []
        for d in form.get("refs", [])[:3]:
            img = decode_ref_image(d)
            if img:
                refs.append(img)
        auth_mode = "account" if (token_id and jwt) else "manual"
        if auth_mode == "manual" and not key and not os.environ.get(KEY_ENV):
            return self._send(400, page("Invalid",
                f"<h1>no key: sign in and pick one, paste one, or set {KEY_ENV} on the server</h1>"))
        # 品牌露出白名单：UGC 内容合法性不可控，logo/水印默认不带。
        # 开关只对 role>=10(账户模式,服务端用 JWT 复核) 或本机 operator(服务器
        # env key,未粘贴外部 key) 生效——前端 checkbox 不可信，这里是唯一裁决点
        brand = False
        if get("brand") == "on":
            if auth_mode == "account":
                brand = fetch_role(jwt) >= BRAND_MIN_ROLE
            elif not key and os.environ.get(KEY_ENV):
                brand = True
        job_id = time.strftime("%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        ep_dir = os.path.join(WEB_EP_DIR, job_id)
        os.makedirs(ep_dir, exist_ok=True)
        with open(os.path.join(ep_dir, "task.md"), "w") as f:
            f.write(prompt + "\n")
        # ref{,1,2}.<ext>：run_showdown build_user_content 会自动扫描 ep_dir/ref*
        # 并作为多模态 image 块随 prompt 发给每个模型
        for i, (ext, data) in enumerate(refs):
            with open(os.path.join(ep_dir, f"ref{i or ''}.{ext}"), "wb") as f:
                f.write(data)
        with open(os.path.join(ep_dir, "meta.json"), "w") as f:
            json.dump({"title": title}, f, ensure_ascii=False)
        job = {"id": job_id, "ep_dir": ep_dir, "models": ",".join(models),
               "seconds": seconds, "title": title, "status": "queued",
               "created": time.time(), "auth_mode": auth_mode,
               "token_id": token_id or None, "brand": brand,
               "extra_models": extra_models, "extra_pricing": extra_pricing}
        if auth_mode == "account":
            write_auth_file(job, jwt)
        if key:
            with SECRETS_LOCK:
                SECRETS[job_id] = key
        db.insert(job)
        self.send_response(303)
        self.send_header("Location", f"{B}/job/{job_id}")
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7788)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; keep 127.0.0.1 unless you have auth in front")
    args = ap.parse_args()
    os.makedirs(WEB_EP_DIR, exist_ok=True)
    db.init()
    db.recover_on_boot(bool(os.environ.get(KEY_ENV)), SECRETS)
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    mode = f"account mode ON ({SERVER_DOMAIN})" if CLERK_PK else "manual keys only"
    print(f"showdown web: http://{args.host}:{args.port}  [{mode}]  (jobs land in episodes/web/)")
    srv.serve_forever()


if __name__ == "__main__":
    main()

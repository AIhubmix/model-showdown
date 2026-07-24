#!/usr/bin/env python3
"""Self-hosted web UI for showdown: submit a prompt, queue it, download the share pack.

    python3 webapp.py [--port 7788] [--host 127.0.0.1]

Stdlib only. Jobs run sequentially through showdown.py (GPU recording must be
serial). Key acquisition mirrors the playground (aihubmix-chat) exactly:

  · Account mode — Clerk sign-in, pick one of your gateway keys (list comes
    from GET {server_domain}/call/tkn/, masked). Requests then carry
    `Authorization: Bearer <Clerk JWT>` + `X-Pg-Token-Id: <key id>` and the
    gateway swaps in the real key — the full key never touches this process
    or disk. Clerk JWTs are short-lived, so the job page keeps pushing fresh
    ones while the job runs (keep it open); run_showdown re-reads the auth
    file on every request/retry.
  · Manual mode — paste a full sk-key (BYOK); held in memory for the job's
    lifetime only, passed via env, never stored or logged.

Account mode needs `web.clerk_publishable_key` in showdown.config.json (or
env SHOWDOWN_CLERK_PK / SHOWDOWN_SERVER_DOMAIN to override — e.g. point both
at the test suite for local development; the pk_live instance only allows
aihubmix.com origins). Without it the account tab explains itself and manual
mode still works.

MVP for trusted networks — binds 127.0.0.1 by default; put real auth in front
before exposing it anywhere public.
"""
import argparse
import html
import json
import os
import queue
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_EP_DIR = os.path.join(SCRIPT_DIR, "episodes", "web")
KEY_ENV = run_showdown.API_KEY_ENV

_web_cfg = run_showdown.CONFIG.get("web", {})
CLERK_PK = os.environ.get("SHOWDOWN_CLERK_PK") or _web_cfg.get("clerk_publishable_key", "")
SERVER_DOMAIN = (os.environ.get("SHOWDOWN_SERVER_DOMAIN")
                 or _web_cfg.get("server_domain", "https://aihubmix.com")).rstrip("/")

JOBS = {}          # id -> job dict (manual keys held in memory only, never persisted)
JOBS_LOCK = threading.Lock()
JOB_QUEUE = queue.Queue()

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
  --ah-ease:cubic-bezier(0.2,0.8,0.2,1);
}
* { box-sizing:border-box; }
body { margin:0; background:var(--ah-bg); color:var(--ah-fg-2);
  font-family:var(--ah-font-body); font-size:16px; line-height:1.55; }
.wrap { max-width:860px; margin:0 auto; padding:48px 24px 80px; }
h1 { font-family:var(--ah-font-display); font-size:32px; color:var(--ah-fg-1);
  font-weight:700; margin:0 0 4px; }
.sub { color:var(--ah-fg-3); font-size:14px; margin-bottom:32px; }
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
textarea { min-height:180px; font-family:var(--ah-font-mono); font-size:13px; resize:vertical; }
.models { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:8px; }
.model-opt { display:flex; align-items:center; gap:8px; border:1px solid var(--ah-border);
  border-radius:8px; padding:8px 12px; font-size:14px; cursor:pointer;
  transition:background 120ms var(--ah-ease); }
.model-opt:hover { background:rgba(17,17,17,0.03); }
.model-opt code { font-family:var(--ah-font-mono); font-size:11px; color:var(--ah-fg-3); }
.btn { display:inline-flex; align-items:center; gap:8px; background:var(--ah-primary);
  color:#fff; border:0; border-radius:8px; padding:12px 24px; font-size:15px;
  font-weight:600; font-family:var(--ah-font-body); cursor:pointer; margin-top:24px;
  transition:background 120ms var(--ah-ease); }
.btn:hover { background:var(--ah-primary-hover); }
.btn:active { background:var(--ah-primary-pressed); }
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


def page(title, body, refresh=None, head=""):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{meta}{head}
<title>{html.escape(title)}</title><style>{CSS}</style></head><body><div class="wrap">
{body}</div></body></html>"""


def lineup_models():
    return [(mid, cfg.get("display", mid), cfg.get("lineup", True) is not False)
            for mid, cfg in run_showdown.MODELS.items()]


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

  async function initAccount() {
    const st = $('acct-status');
    if (!window._clerkReady) { st.textContent = 'account mode is not configured on this deployment — paste a key instead'; setMode('manual'); return; }
    const clerk = await window._clerkReady;
    if (!clerk) { st.textContent = 'Clerk failed to load (origin not allowed?): ' + (window._clerkErr || '') + ' — paste a key instead'; setMode('manual'); return; }
    if (!clerk.user) {
      st.textContent = 'sign in to pick one of your gateway keys';
      $('sign-in').hidden = false;
      $('sign-in').onclick = () => clerk.openSignIn();
      clerk.addListener(({ user }) => { if (user) location.reload(); });
      return;
    }
    st.textContent = 'loading your keys…';
    const jwt = await clerk.session.getToken();
    const r = await fetch('/api/keys', { headers: { Authorization: 'Bearer ' + jwt } });
    if (!r.ok) { st.textContent = 'could not load keys (' + r.status + ') — paste a key instead'; setMode('manual'); return; }
    const keys = await r.json();
    if (!keys.length) { st.textContent = 'no keys on this account — create one in the console, or paste a key'; return; }
    const sel = $('key-select');
    sel.innerHTML = keys.map(k => `<option value="${k.id}">${k.name} · ${k.masked}</option>`).join('');
    sel.hidden = false;
    st.textContent = `signed in as ${clerk.user.primaryEmailAddress?.emailAddress || clerk.user.id} —`;
  }
  initAccount();

  document.getElementById('showdown-form').addEventListener('submit', async (e) => {
    if (authMode !== 'account') return;           // manual: plain POST
    e.preventDefault();
    const clerk = window._clerk;
    if (!clerk?.user) { alert('sign in first, or switch to "Paste a key"'); return; }
    document.getElementById('f-token-id').value = $('key-select').value;
    document.getElementById('f-jwt').value = await clerk.session.getToken();
    e.target.submit();
  });
</script>
"""


def home_html():
    opts = "".join(
        f'<label class="model-opt"><input type="checkbox" name="models" value="{mid}"'
        f'{" checked" if default else ""}> {html.escape(disp)} <code>{mid}</code></label>'
        for mid, disp, default in lineup_models())
    with JOBS_LOCK:
        rows = "".join(
            f'<tr><td><a href="/job/{j["id"]}">{j["id"]}</a></td>'
            f'<td>{html.escape(j["title"])}</td>'
            f'<td><span class="tag {j["status"]}">{j["status"].upper()}</span></td>'
            f'<td>{time.strftime("%m-%d %H:%M", time.localtime(j["created"]))}</td></tr>'
            for j in sorted(JOBS.values(), key=lambda x: -x["created"])[:20])
    jobs_card = (f'<div class="card"><label>Recent jobs</label><table>'
                 f"<tr><th>id</th><th>title</th><th>status</th><th>created</th></tr>{rows}"
                 f"</table></div>") if rows else ""
    server_key_hint = (" (server key configured — manual field may stay empty)"
                       if os.environ.get(KEY_ENV) else "")
    head = CLERK_BOOT.format(pk=json.dumps(CLERK_PK)) if CLERK_PK else ""
    return page("Model Showdown", f"""
<h1>Model Showdown</h1>
<div class="sub">Same prompt · one shot each · real costs — generates a publish-ready
side-by-side comparison video. All models served via AIHubMix.</div>
<div class="card"><form method="post" action="/submit" id="showdown-form">
  <label>Prompt <span class="hint">what should every model build? single-file HTML
  with an auto-demo works best</span></label>
  <textarea name="prompt" required placeholder="Build a playable ... as one self-contained HTML file ..."></textarea>
  <label>Models</label>
  <div class="models">{opts}</div>
  <label>Title <span class="hint">shown on the scoreboard</span></label>
  <input type="text" name="title" placeholder="Model Showdown">
  <label>Record seconds</label>
  <select name="seconds"><option>18</option><option selected>26</option><option>40</option></select>
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
  <input type="hidden" name="token_id" id="f-token-id">
  <input type="hidden" name="jwt" id="f-jwt">
  <button class="btn" type="submit">Run the showdown</button>
</form></div>
{jobs_card}
{KEY_SECTION_JS}""", head=head)


def job_html(job):
    st = job["status"]
    body = [f'<h1>Job {job["id"]}</h1>',
            f'<div class="sub">{html.escape(job["title"])} · '
            f'<span class="tag {st}">{st.upper()}</span></div>']
    if job["auth_mode"] == "account" and st in ("queued", "running"):
        body.append('<div class="card" style="padding:14px 24px"><span class="hint" style="margin:0">'
                    'account-key job: keep this page open — it refreshes the short-lived '
                    'sign-in token the gateway needs (or resubmit with a pasted key for '
                    'unattended runs)</span></div>')
    if st == "queued":
        with JOBS_LOCK:
            ahead = sum(1 for j in JOBS.values()
                        if j["status"] == "queued" and j["created"] < job["created"])
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
        links = "".join(f'<a href="/job/{job["id"]}/dl/{urllib.parse.quote(n)}">{html.escape(n)}</a>'
                        for n in files)
        body.append(f'<div class="card"><label>Share pack</label>'
                    f'<div class="files">{links}</div></div>')
    body.append('<div class="sub"><a href="/">← new showdown</a></div>')
    refresh = 5 if st in ("queued", "running") else None
    head = ""
    if job["auth_mode"] == "account" and st in ("queued", "running") and CLERK_PK:
        # 每次自刷新都推一枚新 JWT（Clerk token 分钟级过期；重试/下一条请求现读文件）
        head = CLERK_BOOT.format(pk=json.dumps(CLERK_PK)) + f"""
<script type="module">
  const clerk = await window._clerkReady;
  if (clerk?.user) {{
    const jwt = await clerk.session.getToken();
    fetch('/job/{job["id"]}/token', {{ method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ jwt }}) }});
  }}
</script>"""
    return page(f"Job {job['id']}", "\n".join(body), refresh=refresh, head=head)


# ---------- job execution ----------

def write_auth_file(job, jwt):
    path = os.path.join(job["ep_dir"], ".auth.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"jwt": jwt, "token_id": job["token_id"]}, f)
    return path


def worker():
    while True:
        job_id = JOB_QUEUE.get()
        with JOBS_LOCK:
            job = JOBS[job_id]
            job["status"] = "running"
        env = os.environ.copy()
        auth_path = None
        if job["auth_mode"] == "account":
            auth_path = os.path.join(job["ep_dir"], ".auth.json")
            env["SHOWDOWN_AUTH_FILE"] = auth_path
            env["SHOWDOWN_GATEWAY"] = f"{SERVER_DOMAIN}/v1/chat/completions"
        elif job["key"]:
            env[KEY_ENV] = job["key"]
        cmd = [sys.executable, os.path.join(SCRIPT_DIR, "showdown.py"), job["ep_dir"],
               "--task", os.path.join(job["ep_dir"], "task.md"),
               "--models", job["models"], "--seconds", str(job["seconds"]),
               "--title", job["title"], "--formats", "wide,square"]
        try:
            with open(os.path.join(job["ep_dir"], "run.log"), "w") as log:
                r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, timeout=3 * 3600)
            ok = r.returncode == 0
        except Exception as e:  # noqa: BLE001
            with open(os.path.join(job["ep_dir"], "run.log"), "a") as log:
                log.write(f"\nworker error: {e}\n")
            ok = False
        finally:
            if auth_path and os.path.exists(auth_path):
                os.remove(auth_path)
        with JOBS_LOCK:
            job["status"] = "done" if ok else "failed"
            job["key"] = None  # key 用完即弃


# ---------- http ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "showdown-web"

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
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

    def do_GET(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if self.path == "/" or self.path == "":
            return self._send(200, home_html())
        if parts[0] == "healthz":
            return self._send(200, "ok", "text/plain")
        if parts[0] == "api" and len(parts) == 2 and parts[1] == "keys":
            return self._proxy_keys()
        if parts[0] == "job" and len(parts) >= 2:
            with JOBS_LOCK:
                job = JOBS.get(parts[1])
            if not job:
                return self._send(404, page("Not found", "<h1>job not found</h1>"))
            if len(parts) == 2:
                return self._send(200, job_html(job))
            if len(parts) == 4 and parts[2] == "dl":
                name = os.path.basename(urllib.parse.unquote(parts[3]))
                path = os.path.join(job["ep_dir"], "dist", name)
                if not os.path.exists(path):
                    return self._send(404, page("Not found", "<h1>file not found</h1>"))
                ctype = ("video/mp4" if name.endswith(".mp4") else
                         "image/png" if name.endswith(".png") else
                         "application/json" if name.endswith(".json") else
                         "text/plain; charset=utf-8")
                with open(path, "rb") as f:
                    return self._send(200, f.read(), ctype)
        return self._send(404, page("Not found", "<h1>404</h1>"))

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        # 任务页推新 JWT：只更新 jwt，token_id 以服务端任务记录为准（防篡改）
        if len(parts) == 3 and parts[0] == "job" and parts[2] == "token":
            with JOBS_LOCK:
                job = JOBS.get(parts[1])
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
        models = [m for m in form.get("models", []) if m in run_showdown.MODELS]
        key, token_id, jwt = get("api_key"), get("token_id"), get("jwt")
        title = get("title") or "Model Showdown"
        try:
            seconds = max(8, min(60, int(get("seconds") or "26")))
        except ValueError:
            seconds = 26
        if not prompt or not models:
            return self._send(400, page("Invalid", "<h1>prompt and at least one model required</h1>"))
        auth_mode = "account" if (token_id and jwt) else "manual"
        if auth_mode == "manual" and not key and not os.environ.get(KEY_ENV):
            return self._send(400, page("Invalid",
                f"<h1>no key: sign in and pick one, paste one, or set {KEY_ENV} on the server</h1>"))
        job_id = time.strftime("%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        ep_dir = os.path.join(WEB_EP_DIR, job_id)
        os.makedirs(ep_dir, exist_ok=True)
        with open(os.path.join(ep_dir, "task.md"), "w") as f:
            f.write(prompt + "\n")
        job = {"id": job_id, "ep_dir": ep_dir, "models": ",".join(models),
               "seconds": seconds, "title": title, "status": "queued",
               "created": time.time(), "auth_mode": auth_mode,
               "key": key or None, "token_id": token_id or None}
        if auth_mode == "account":
            write_auth_file(job, jwt)
        with JOBS_LOCK:
            JOBS[job_id] = job
        JOB_QUEUE.put(job_id)
        self.send_response(303)
        self.send_header("Location", f"/job/{job_id}")
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7788)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; keep 127.0.0.1 unless you have auth in front")
    args = ap.parse_args()
    os.makedirs(WEB_EP_DIR, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    mode = f"account mode ON ({SERVER_DOMAIN})" if CLERK_PK else "manual keys only"
    print(f"showdown web: http://{args.host}:{args.port}  [{mode}]  (jobs land in episodes/web/)")
    srv.serve_forever()


if __name__ == "__main__":
    main()

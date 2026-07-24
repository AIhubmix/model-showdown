"""Job persistence for the showdown web service.

    SHOWDOWN_DB_URL=mysql://user:pass@host:3306/dbname   -> MySQL (PyMySQL, 部署态)
    unset                                                -> SQLite (stdlib, 本地开发)

One table, one writer (the single worker) — no migration framework, no ORM.
Secrets never touch the database: pasted keys live in the web process's memory
(SECRETS dict in webapp.py), account-mode JWTs live in ep_dir/.auth.json with
0600 and are deleted after the run. A restart therefore loses pasted keys by
design; recover_on_boot() marks those jobs failed with a resubmit hint.
"""
import json
import os
import sqlite3
import threading
import time
import urllib.parse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_URL = os.environ.get("SHOWDOWN_DB_URL", "")
_LOCK = threading.Lock()

_DDL = """CREATE TABLE IF NOT EXISTS jobs (
  id VARCHAR(40) PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  models TEXT,
  seconds INTEGER,
  status VARCHAR(16),
  auth_mode VARCHAR(16),
  brand INTEGER,
  ep_dir TEXT,
  extra TEXT,
  fail_reason VARCHAR(255),
  created DOUBLE,
  updated DOUBLE
)"""

_KIND = "mysql" if DB_URL.startswith("mysql") else "sqlite"
_PH = "%s" if _KIND == "mysql" else "?"
_conn = None


def _connect():
    global _conn
    if _KIND == "mysql":
        import pymysql  # 唯一三方依赖，仅 MySQL 模式需要（pip install pymysql）
        u = urllib.parse.urlparse(DB_URL)
        _conn = pymysql.connect(host=u.hostname, port=u.port or 3306,
                                user=u.username or "", password=u.password or "",
                                database=u.path.lstrip("/"), autocommit=True,
                                charset="utf8mb4")
    else:
        path = os.path.join(SCRIPT_DIR, "episodes", "web", "jobs.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def _exec(sql, args=(), fetch=False):
    """串行化所有访问（单 worker + 低频页面读，锁足够）；MySQL 断连自动重连一次。"""
    with _LOCK:
        for attempt in (1, 2):
            try:
                cur = _conn.cursor()
                cur.execute(sql.replace("?", _PH) if _KIND == "mysql" else sql, args)
                if fetch:
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, r)) for r in cur.fetchall()]
                if _KIND == "sqlite":
                    _conn.commit()
                return None
            except Exception:  # noqa: BLE001
                if attempt == 2 or _KIND == "sqlite":
                    raise
                _connect()


def init():
    _connect()
    _exec(_DDL)


def insert(job):
    # token_id 是 key 的 id（非密钥本体），随任务持久化以便重启后账户模式继续收 JWT；
    # 粘贴的 sk-key 与 JWT 本体永不入库
    _exec("INSERT INTO jobs (id,title,models,seconds,status,auth_mode,brand,ep_dir,"
          "extra,fail_reason,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
          (job["id"], job["title"], job["models"], job["seconds"], job["status"],
           job["auth_mode"], 1 if job.get("brand") else 0, job["ep_dir"],
           json.dumps({"extra_models": job.get("extra_models") or {},
                       "extra_pricing": job.get("extra_pricing") or {},
                       "token_id": job.get("token_id")}),
           None, job["created"], job["created"]))


def set_status(job_id, status, fail_reason=None):
    _exec("UPDATE jobs SET status=?, fail_reason=?, updated=? WHERE id=?",
          (status, fail_reason, time.time(), job_id))


def _row_to_job(r):
    extra = json.loads(r.get("extra") or "{}")
    return {"id": r["id"], "title": r["title"], "models": r["models"],
            "seconds": r["seconds"], "status": r["status"],
            "auth_mode": r["auth_mode"], "brand": bool(r["brand"]),
            "ep_dir": r["ep_dir"], "created": r["created"],
            "fail_reason": r.get("fail_reason"),
            "token_id": extra.get("token_id"),
            "extra_models": extra.get("extra_models") or {},
            "extra_pricing": extra.get("extra_pricing") or {}}


def get(job_id):
    rows = _exec("SELECT * FROM jobs WHERE id=?", (job_id,), fetch=True)
    return _row_to_job(rows[0]) if rows else None


def list_recent(limit=20):
    rows = _exec("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,), fetch=True)
    return [_row_to_job(r) for r in rows]


def queued_ahead(created):
    rows = _exec("SELECT COUNT(*) AS n FROM jobs WHERE status='queued' AND created<?",
                 (created,), fetch=True)
    return rows[0]["n"] if rows else 0


def claim_next():
    """单 worker：取最老的 queued 并标 running。"""
    rows = _exec("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1",
                 fetch=True)
    if not rows:
        return None
    job = _row_to_job(rows[0])
    set_status(job["id"], "running")
    job["status"] = "running"
    return job


def recover_on_boot(has_server_key, secrets):
    """重启恢复：running 一律判中断失败；queued 的手动 key 任务若 key 已随内存
    丢失且服务器也没有兜底 key，标失败提示重新提交。"""
    _exec("UPDATE jobs SET status='failed', fail_reason='interrupted by restart', "
          "updated=? WHERE status='running'", (time.time(),))
    for r in _exec("SELECT * FROM jobs WHERE status='queued'", fetch=True) or []:
        job = _row_to_job(r)
        if job["auth_mode"] == "manual" and job["id"] not in secrets and not has_server_key:
            set_status(job["id"], "failed",
                       "pasted key was lost on service restart — resubmit")

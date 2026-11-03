"""Loopback-only, read-only NARROW task dashboard.

The dashboard intentionally exposes summaries, never check output, raw notes,
provider prompts, tool payloads, environment values, or credential material.
It has no mutation endpoints and refuses non-loopback clients and Host headers.
"""
from __future__ import annotations

import ipaddress
import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .core import (
    NarrowError,
    Store,
    changed_paths,
    classify_paths,
    now_iso,
    snapshot_hash,
    task_workspace,
    unsafe_workspace_links,
)


_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>NARROW Control Center</title>
  <link rel="stylesheet" href="/assets/app.css">
  <script defer src="/assets/app.js"></script>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <a class="brand" href="/" aria-label="NARROW home"><span class="brand-mark">N</span><span>NARROW</span></a>
      <div class="live"><span class="pulse"></span><span>LOCAL CONTROL CENTER</span><span id="updated">Connecting…</span></div>
    </header>
    <main>
      <section class="hero">
        <div><p class="eyebrow">TASK CONTINUITY · SCOPE · EVIDENCE</p><h1>Keep the work<br><span>moving safely.</span></h1>
        <p class="lede">A live, read-only view of your local NARROW task journal. Your project data stays on this device.</p></div>
        <div class="hero-badge"><div class="badge-ring">N</div><span>LOCAL-FIRST<br>READ-ONLY</span></div>
      </section>
      <section class="metrics" id="metrics" aria-label="Task metrics">
        <article class="metric"><span>ACTIVE TASKS</span><strong id="active-count">—</strong><small>Not yet closed</small></article>
        <article class="metric"><span>READY FOR REVIEW</span><strong id="review-count">—</strong><small>Checks and scope passed</small></article>
        <article class="metric"><span>VERIFIED COMPLETE</span><strong id="complete-count">—</strong><small>Human acceptance recorded</small></article>
        <article class="metric"><span>NEEDS ATTENTION</span><strong id="attention-count">—</strong><small>Blocked or review required</small></article>
      </section>
      <section class="section-head"><div><p class="eyebrow">WORKSPACE</p><h2>Your tasks</h2></div><span class="refresh-label"><span class="refresh-dot"></span>Refreshes every 3 seconds</span></section>
      <section id="tasks" class="task-grid" aria-live="polite"><div class="empty">Loading local tasks…</div></section>
      <section class="detail-layout">
        <div class="panel detail-panel"><div class="panel-head"><div><p class="eyebrow">SELECTED TASK</p><h2 id="detail-title">Choose a task</h2></div><span id="detail-status" class="status-pill">—</span></div>
          <p id="detail-goal" class="detail-goal">Select a task card to inspect its latest state.</p>
          <div class="detail-stats"><div><span>CHANGED FILES</span><strong id="changed-count">—</strong></div><div><span>ACCEPTANCE</span><strong id="acceptance-count">—</strong></div><div><span>VERIFICATION</span><strong id="check-count">—</strong></div></div>
          <div class="subhead">Scope snapshot</div><div id="scope" class="scope-list"><span class="muted">No task selected</span></div>
          <div class="subhead">Verification</div><div id="checks" class="check-list"><span class="muted">No task selected</span></div>
        </div>
        <div class="panel timeline-panel"><div class="panel-head"><div><p class="eyebrow">SECONDHAND MEMORY</p><h2>Recent activity</h2></div><span class="lock">PRIVATE TO THIS DEVICE</span></div><div id="timeline" class="timeline"><div class="empty">Select a task to view activity.</div></div></div>
      </section>
      <footer><span> NARROW · AI can start the work. NARROW helps make sure it gets finished safely.</span><span>NO CLOUD · NO API KEY · READ-ONLY</span></footer>
    </main>
  </div>
  <div id="error" class="toast" role="status" hidden></div>
</body>
</html>
"""

_CSS = r"""
:root{color-scheme:dark;--bg:#0b0d12;--panel:#121722;--panel2:#161d29;--line:#263142;--text:#f1f4fa;--muted:#929db0;--quiet:#667286;--accent:#e6a34b;--green:#60d6a5;--red:#ff7b78;--blue:#8ab6ff;--radius:18px;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% 0%,#202137 0,transparent 38%),var(--bg);color:var(--text);min-height:100vh}.shell{max-width:1440px;margin:auto;padding:0 32px}.topbar{height:76px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #202938}.brand{display:flex;gap:11px;align-items:center;color:var(--text);font-weight:800;letter-spacing:.16em;text-decoration:none}.brand-mark{width:34px;height:34px;border-radius:11px;background:linear-gradient(135deg,#f1b65e,#cf763e);color:#20170e;display:grid;place-items:center;font-weight:900;letter-spacing:0}.live{display:flex;align-items:center;gap:9px;color:#94a0b3;font-size:10px;letter-spacing:.13em}.live #updated{margin-left:12px;letter-spacing:0;font-size:11px;color:#748096}.pulse,.refresh-dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 12px #60d6a566}.hero{display:flex;justify-content:space-between;align-items:center;padding:54px 0 38px}.eyebrow{margin:0 0 10px;color:var(--accent);font-size:10px;font-weight:800;letter-spacing:.2em}.hero h1{margin:0;font-size:clamp(38px,6vw,66px);line-height:1.02;letter-spacing:-.055em}.hero h1 span{color:#e7ad5e}.lede{max-width:540px;color:var(--muted);line-height:1.7;font-size:14px}.hero-badge{width:116px;height:116px;border:1px solid #55412c;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#d2b18a;font-size:9px;text-align:center;letter-spacing:.12em}.badge-ring{width:39px;height:39px;display:grid;place-items:center;border:1px solid var(--accent);border-radius:13px;color:var(--accent);font-weight:900;font-size:18px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric,.panel,.task-card{background:linear-gradient(145deg,#151c28,#10151e);border:1px solid var(--line);border-radius:var(--radius)}.metric{padding:20px}.metric span,.detail-stats span{display:block;color:var(--muted);font-size:9px;letter-spacing:.15em;font-weight:750}.metric strong{display:block;margin:10px 0 2px;font-size:31px;letter-spacing:-.04em}.metric small{color:var(--quiet);font-size:11px}.section-head{display:flex;align-items:end;justify-content:space-between;margin:42px 0 16px}.section-head h2,.panel h2{margin:0;font-size:22px;letter-spacing:-.03em}.refresh-label{color:var(--quiet);font-size:11px;display:flex;align-items:center;gap:8px}.task-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.task-card{padding:17px;text-align:left;color:inherit;cursor:pointer;min-height:150px;transition:border-color .18s,transform .18s;background-color:transparent;width:100%}.task-card:hover,.task-card.selected{border-color:#b98043;transform:translateY(-2px)}.task-card .card-top{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:15px}.task-title{font-weight:700;font-size:14px;line-height:1.4}.task-goal{font-size:12px;color:var(--muted);line-height:1.55;margin:9px 0 16px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.card-meta{color:var(--quiet);font-size:10px;display:flex;justify-content:space-between;gap:8px}.status-pill{padding:6px 9px;border-radius:20px;font-size:9px;font-weight:800;letter-spacing:.06em;white-space:nowrap;background:#293141;color:#c6d0df}.status-pill.running,.status-pill.in_progress{background:#173b36;color:#81e2b9}.status-pill.ready_for_review,.status-pill.ready_for_human_review,.status-pill.verified_complete{background:#203e32;color:#81e2b9}.status-pill.needs_review,.status-pill.blocked{background:#482927;color:#ff9e91}.status-pill.pending{background:#3e3324;color:#eec17e}.detail-layout{display:grid;grid-template-columns:1.15fr .85fr;gap:12px;margin-top:14px}.panel{padding:22px;min-width:0}.panel-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.detail-goal{color:var(--muted);font-size:13px;line-height:1.7;min-height:43px}.detail-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:15px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.detail-stats strong{display:block;margin-top:8px;font-size:18px}.subhead{margin:19px 0 10px;color:#dce2ec;font-size:12px;font-weight:750}.scope-list,.check-list{display:grid;gap:8px}.scope-row,.check-row{display:flex;justify-content:space-between;gap:10px;font-size:11px;padding:9px 11px;border-radius:9px;background:#0d121a;color:#b9c3d2}.scope-row span:last-child,.check-row span:last-child{color:var(--muted)}.pass{color:var(--green)!important}.fail{color:var(--red)!important}.lock{font-size:8px;letter-spacing:.1em;color:var(--quiet);text-align:right}.timeline{display:grid;gap:0;margin-top:18px;max-height:430px;overflow:auto}.event{display:grid;grid-template-columns:13px 1fr;gap:10px;padding:0 0 16px}.event-rail{position:relative;display:flex;justify-content:center}.event-rail:after{content:"";position:absolute;top:9px;bottom:-2px;width:1px;background:#303b4d}.event:last-child .event-rail:after{display:none}.event-dot{width:7px;height:7px;border-radius:50%;background:var(--accent);margin-top:4px;z-index:1}.event-name{font-size:11px;font-weight:750}.event-time{font-size:9px;color:var(--quiet);margin-top:4px}.muted,.empty{color:var(--quiet);font-size:12px}.empty{padding:24px;border:1px dashed var(--line);border-radius:13px;text-align:center}.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:#5b2828;color:#fff;padding:12px 17px;border:1px solid #a14b46;border-radius:12px;font-size:12px;max-width:90vw}footer{padding:28px 0 36px;margin-top:34px;border-top:1px solid #202938;color:var(--quiet);font-size:9px;letter-spacing:.07em;display:flex;justify-content:space-between;gap:10px}
@media(max-width:850px){.shell{padding:0 18px}.task-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.detail-layout{grid-template-columns:1fr}.hero{padding-top:38px}.hero-badge{width:92px;height:92px}.metrics{gap:8px}.metric{padding:14px}.metric strong{font-size:26px}.metric span{font-size:8px}}
@media(max-width:560px){.topbar{height:62px}.live{font-size:8px}.live #updated{display:none}.hero{padding:34px 0 25px}.hero-badge{display:none}.hero h1{font-size:43px}.lede{font-size:12px}.metrics{grid-template-columns:repeat(2,1fr)}.section-head{margin-top:30px}.task-grid{grid-template-columns:1fr}.task-card{min-height:0}.panel{padding:17px}.section-head h2,.panel h2{font-size:19px}.refresh-label{font-size:9px}footer{flex-direction:column;line-height:1.5}}
"""

_JS = r"""
(() => {
  const $ = (id) => document.getElementById(id);
  let selected = null;
  let refreshing = false;
  const escStatus = (value) => String(value || 'unknown').toLowerCase().replace(/[^a-z0-9_-]/g, '_');
  const labelStatus = (value) => String(value || 'UNKNOWN').replaceAll('_', ' ');
  const safe = (value) => String(value ?? '');
  const setText = (id, value) => { $(id).textContent = safe(value); };
  const create = (tag, cls, text) => { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined) el.textContent = safe(text); return el; };
  const timeText = (value) => { try { return new Date(value).toLocaleString(); } catch { return safe(value); } };
  async function get(url) {
    const response = await fetch(url, {cache:'no-store', credentials:'same-origin'});
    if (!response.ok) throw new Error(`Dashboard request failed (${response.status})`);
    return response.json();
  }
  function renderTasks(tasks) {
    const host = $('tasks'); host.replaceChildren();
    if (!tasks.length) { host.append(create('div','empty','No tasks yet. Run `narrow start` in this repository to create your first task.')); return; }
    tasks.forEach(task => {
      const card = create('button','task-card' + (selected === task.id ? ' selected' : ''));
      card.type = 'button'; card.setAttribute('aria-label', `Open task ${safe(task.title)}`);
      const top = create('div','card-top');
      const badge = create('span',`status-pill ${escStatus(task.status)}`,labelStatus(task.status));
      const id = create('span','muted',safe(task.id).slice(0,10)); top.append(badge,id);
      card.append(top,create('div','task-title',task.title),create('p','task-goal',task.goal),create('div','card-meta',`${task.changed_files} changed files · ${task.acceptance_done}/${task.acceptance_total} acceptance`));
      card.addEventListener('click',()=>{selected=task.id; refresh();}); host.append(card);
    });
  }
  function renderDetail(data) {
    const t=data.task; setText('detail-title',t.title); setText('detail-goal',t.goal);
    const status=$('detail-status'); status.textContent=labelStatus(t.status); status.className=`status-pill ${escStatus(t.status)}`;
    setText('changed-count',data.workspace.changed_files); setText('acceptance-count',`${data.acceptance.done}/${data.acceptance.total}`);
    setText('check-count',`${data.checks.passed}/${data.checks.total} passed`);
    const scope=$('scope'); scope.replaceChildren();
    [['In scope',data.workspace.allowed],['Out of scope',data.workspace.outside],['Denied',data.workspace.denied],['Unsafe links',data.workspace.unsafe_links]].forEach(([name,items])=>{
      const row=create('div','scope-row'); row.append(create('span',name),create('span',items.length?items.join(', '):'None')); scope.append(row);
    });
    const checks=$('checks'); checks.replaceChildren();
    if(!data.checks.items.length) checks.append(create('span','muted','No verification runs recorded.'));
    data.checks.items.forEach(item=>{const row=create('div','check-row');row.append(create('span',item.name),create('span',item.passed && item.current_snapshot?'PASS · CURRENT':item.passed?'PASS · STALE':'FAIL'));row.lastChild.className=item.passed&&item.current_snapshot?'pass':'fail';checks.append(row);});
    const timeline=$('timeline'); timeline.replaceChildren();
    if(!data.activity.length) timeline.append(create('div','empty','No activity recorded yet.'));
    data.activity.slice().reverse().forEach(item=>{const event=create('div','event');const rail=create('div','event-rail');rail.append(create('span','event-dot'));event.append(rail);const body=create('div','event-body');body.append(create('div','event-name',item.title),create('div','event-time',timeText(item.created_at)));event.append(body);timeline.append(event);});
  }
  async function refresh() {
    if(refreshing) return; refreshing=true;
    try {
      const [overview,tasks]=await Promise.all([get('/api/v1/overview'),get('/api/v1/tasks')]);
      setText('active-count',overview.counts.active);setText('review-count',overview.counts.ready_for_review);setText('complete-count',overview.counts.verified_complete);setText('attention-count',overview.counts.needs_attention);
      selected = selected && tasks.some(x=>x.id===selected) ? selected : (tasks[0]?.id || null);renderTasks(tasks);setText('updated',`Updated ${new Date().toLocaleTimeString()}`);
      if(selected) renderDetail(await get(`/api/v1/tasks/${encodeURIComponent(selected)}`));
      else {setText('detail-title','No tasks found');setText('detail-goal','Start a task with the NARROW CLI.');}
      $('error').hidden=true;
    } catch(error) { $('error').textContent=error.message || 'Unable to load the local task journal.'; $('error').hidden=false; }
    finally { refreshing=false; }
  }
  refresh();window.setInterval(refresh,3000);
})();
"""


def _json_default(value: Any) -> str:
    return str(value)


def _safe_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    workspace = task_workspace(task)
    paths = changed_paths(workspace)
    accepted = sum(1 for item in task["acceptance"] if item.get("accepted"))
    return {
        "id": task["id"],
        "title": task["title"],
        "goal": task["goal"],
        "status": task["status"],
        "branch": task["branch"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "changed_files": len(paths),
        "acceptance_done": accepted,
        "acceptance_total": len(task["acceptance"]),
    }


def _safe_event_title(event: dict[str, Any]) -> str:
    names = {
        "TASK_CREATED": "Task created",
        "DRIVE_STARTED": "Agent drive started",
        "DRIVE_STOPPED": "Agent drive stopped",
        "AGENT_RUN_STARTED": "Agent started",
        "AGENT_RUN_FINISHED": "Agent finished",
        "AGENT_RUN_INTERRUPTED": "Agent interrupted",
        "REPAIR_REQUESTED": "Repair requested",
        "CHECK_PASSED": "Verification passed",
        "CHECK_FAILED": "Verification failed",
        "SCOPE_VIOLATION": "Scope violation detected",
        "BASE_CHECKOUT_CHANGED": "Original checkout changed",
        "CHANGE_LIMIT_REACHED": "Changed-file limit reached",
        "UNSAFE_WORKSPACE_LINK_DETECTED": "Unsafe workspace link detected",
        "READY_FOR_HUMAN_REVIEW": "Ready for human review",
        "FINISH_BLOCKED": "Completion gate blocked",
        "VERIFIED_COMPLETE": "Verified complete",
        "CLAUDE_SESSION_START": "Claude session started",
        "CLAUDE_SESSION_END": "Claude session ended",
        "CLAUDE_PRECOMPACT_HANDOFF": "Continuity handoff saved",
        "CLAUDE_SUBAGENT_STARTED": "Claude subagent started",
        "CLAUDE_SUBAGENT_STOPPED": "Claude subagent stopped",
        "CLAUDE_TASK_CREATED": "Claude task created",
        "CLAUDE_TASK_COMPLETED": "Claude task completed",
        "CLAUDE_TOOL_FAILED": "Claude tool failed",
    }
    return names.get(str(event.get("type") or ""), "Task activity")


def task_detail(store: Store, task_id: str) -> dict[str, Any]:
    task = store.get_task(task_id)
    workspace = task_workspace(task)
    unsafe = unsafe_workspace_links(workspace)
    paths = changed_paths(workspace)
    scope = classify_paths(paths, task["allowed"], task["denied"])
    current_snapshot = None if unsafe else snapshot_hash(workspace)
    with store.connect() as db:
        rows = db.execute(
            "SELECT check_name,snapshot_hash,exit_code,passed,duration_ms,created_at "
            "FROM check_runs WHERE task_id=? ORDER BY id DESC LIMIT 100",
            (task_id,),
        ).fetchall()
    seen: set[str] = set()
    checks: list[dict[str, Any]] = []
    for row in rows:
        name = row["check_name"]
        if name in seen:
            continue
        seen.add(name)
        checks.append({
            "name": name,
            "passed": bool(row["passed"]),
            "exit_code": row["exit_code"],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
            "current_snapshot": bool(current_snapshot and row["snapshot_hash"] == current_snapshot),
        })
    events = store.events(task_id, limit=80)
    activity = [{"title": _safe_event_title(item), "created_at": item["created_at"]} for item in events[-40:]]
    acceptance_done = sum(1 for item in task["acceptance"] if item.get("accepted"))
    return {
        "schema": "narrow.dashboard.task.v1",
        "task": {key: task[key] for key in ("id", "title", "goal", "status", "branch", "base_sha", "created_at", "updated_at")},
        "workspace": {
            "changed_files": len(paths),
            "allowed": scope["allowed"][:30],
            "outside": scope["outside"][:30],
            "denied": scope["denied"][:30],
            "unsafe_links": unsafe[:20],
        },
        "acceptance": {"done": acceptance_done, "total": len(task["acceptance"])},
        "checks": {"passed": sum(1 for row in checks if row["passed"] and row["current_snapshot"]), "total": len(checks), "items": checks},
        "activity": activity,
    }


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, store: Store, port: int):
        self.store = store
        super().__init__(("127.0.0.1", port), DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = "HTTP/1.1"
    server_version = "NARROW-Dashboard"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never write URLs or query strings to logs; requests are local-only.
        print(f"NARROW dashboard: {fmt % args}")

    def _allowed_client(self) -> bool:
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
            host = self.headers.get("Host", "").lower()
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed:
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != "http" or parsed.netloc.lower() not in allowed:
                    return False
            return True
        except (ValueError, OSError):
            return False

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if not self._allowed_client():
            self._send(b"Localhost access only.\n", "text/plain; charset=utf-8", 403)
            return
        path = urlsplit(self.path).path
        if path == "/":
            self._send(_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/assets/app.css":
            self._send(_CSS.encode("utf-8"), "text/css; charset=utf-8")
            return
        if path == "/assets/app.js":
            self._send(_JS.encode("utf-8"), "text/javascript; charset=utf-8")
            return
        if path == "/api/v1/overview":
            tasks = self.server.store.list_tasks(include_closed=True, limit=500)
            counts = {
                "active": sum(1 for task in tasks if task["status"] not in {"VERIFIED_COMPLETE", "CLOSED"}),
                "ready_for_review": sum(1 for task in tasks if task["status"] == "READY_FOR_REVIEW"),
                "verified_complete": sum(1 for task in tasks if task["status"] == "VERIFIED_COMPLETE"),
                "needs_attention": sum(1 for task in tasks if task["status"] in {"NEEDS_REVIEW", "BLOCKED"}),
            }
            self._json({"schema": "narrow.dashboard.overview.v1", "generated_at": now_iso(), "counts": counts})
            return
        if path == "/api/v1/tasks":
            tasks = self.server.store.list_tasks(include_closed=True, limit=200)
            self._json([_safe_task_summary(task) for task in tasks])
            return
        prefix = "/api/v1/tasks/"
        if path.startswith(prefix):
            task_id = path[len(prefix):]
            if not _TASK_ID.fullmatch(task_id):
                self._json({"error": "invalid_task_id"}, 400)
                return
            try:
                self._json(task_detail(self.server.store, task_id))
            except NarrowError:
                self._json({"error": "task_not_found"}, 404)
            return
        self._json({"error": "not_found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        self._send(b"Read-only dashboard.\n", "text/plain; charset=utf-8", 405)

    def do_PUT(self) -> None:  # noqa: N802
        self._send(b"Read-only dashboard.\n", "text/plain; charset=utf-8", 405)

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(b"Read-only dashboard.\n", "text/plain; charset=utf-8", 405)


def run_dashboard(store: Store, port: int = 8765, *, open_browser: bool = False) -> int:
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise NarrowError("Dashboard port must be an integer between 1024 and 65535.")
    try:
        server = DashboardServer(store, port)
    except OSError as exc:
        raise NarrowError(f"Could not start the local dashboard on 127.0.0.1:{port}: {exc}") from exc
    url = f"http://127.0.0.1:{port}/"
    print("NARROW Control Center — read-only local dashboard")
    print(f"Open: {url}")
    print("Bound to 127.0.0.1 only. No external network listener or mutation endpoint is available.")
    print("Press Ctrl+C to stop the dashboard.\n")
    if open_browser:
        try:
            webbrowser.open(url, new=2)
        except Exception:
            print("Could not open a browser automatically; use the URL above.")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping NARROW dashboard…")
    finally:
        server.shutdown()
        server.server_close()
    return 0

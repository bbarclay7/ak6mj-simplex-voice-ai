#!/usr/bin/env python3
"""AIOC Bot Dashboard — web UI for monitoring, message board, and configuration.

Runs as a separate process from main.py. Reads shared state from disk:
  logs/bot_YYYYMMDD.log   — live log stream
  messages/*.json          — message board (via MessageBoard)
  config.yaml              — configuration / prompts

Usage:
    make dashboard           → http://localhost:8080
    python dashboard.py -p 9090
"""

import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import signal

import uvicorn
import yaml
from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse,
    RedirectResponse, StreamingResponse,
)

# ── Globals set at startup ────────────────────────────────────────────────────

CONFIG_PATH = "config.yaml"
LOG_DIR = "logs"

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def get_mb():
    from message_board import MessageBoard
    return MessageBoard(load_config())


def today_log() -> str:
    return os.path.join(LOG_DIR, f"bot_{datetime.now().strftime('%Y%m%d')}.log")


_LEVEL_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ (ERROR|WARNING|DEBUG|INFO)")

def extract_level(line: str) -> str:
    m = _LEVEL_RE.match(line)
    return m.group(1) if m else "INFO"


def last_n_lines(path: str, n: int = 120) -> list[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return f.readlines()[-n:]
    except Exception:
        return []


# ── CSS & shared HTML ─────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d1117; color: #c9d1d9; font-family: 'Courier New', monospace; font-size: 14px; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }

nav { position: fixed; top: 0; left: 0; right: 0; height: 46px; background: #161b22;
      border-bottom: 1px solid #30363d; display: flex; align-items: center;
      padding: 0 20px; gap: 4px; z-index: 100; }
.brand { color: #3fb950; font-weight: bold; font-size: 15px; margin-right: 16px; letter-spacing: 1px; }
nav a { color: #8b949e; padding: 5px 12px; border-radius: 6px; font-size: 13px; }
nav a:hover { background: #21262d; color: #c9d1d9; text-decoration: none; }
nav a.active { background: #21262d; color: #58a6ff; }

.page { margin-top: 46px; padding: 24px; max-width: 1100px; margin-left: auto; margin-right: auto; }
h1 { font-size: 18px; color: #e6edf3; margin-bottom: 16px; }
h2 { font-size: 14px; color: #8b949e; text-transform: uppercase; letter-spacing: 1px;
     margin: 24px 0 10px; }

.panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
         padding: 16px; margin-bottom: 20px; }

.log-window { background: #010409; border: 1px solid #21262d; border-radius: 6px;
              height: 500px; overflow-y: auto; padding: 12px;
              font-size: 12.5px; line-height: 1.6; }
.log-line { white-space: pre-wrap; word-break: break-all; padding: 1px 0; }
.log-INFO    { color: #c9d1d9; }
.log-WARNING { color: #d29922; }
.log-ERROR   { color: #f85149; }
.log-DEBUG   { color: #6e7681; }

.status-bar { display: flex; gap: 20px; margin-bottom: 14px; flex-wrap: wrap; }
.stat { background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
        padding: 8px 14px; font-size: 12px; }
.stat-label { color: #6e7681; margin-right: 6px; }
.stat-val { color: #3fb950; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
       background: #3fb950; margin-right: 5px; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }

table { width: 100%; border-collapse: collapse; }
th { text-align: left; padding: 7px 12px; color: #6e7681; font-size: 11px;
     letter-spacing: .5px; border-bottom: 1px solid #30363d; }
td { padding: 8px 12px; border-bottom: 1px solid #21262d; vertical-align: middle; font-size: 13px; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #1c2128; }

.badge { display: inline-block; padding: 1px 8px; border-radius: 12px; font-size: 11px; }
.green { background: #12261e; color: #3fb950; border: 1px solid #238636; }
.gray  { background: #21262d; color: #6e7681; border: 1px solid #30363d; }
.amber { background: #2d1f00; color: #d29922; border: 1px solid #9e6a03; }

.form-row { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; align-items: flex-start; }
input[type=text], select, textarea {
    background: #0d1117; border: 1px solid #30363d; color: #c9d1d9;
    border-radius: 6px; padding: 6px 10px; font-family: inherit; font-size: 13px; }
input[type=text] { width: 140px; }
input[type=text]:focus, select:focus, textarea:focus { outline: none; border-color: #388bfd; }
textarea { width: 100%; min-height: 200px; resize: vertical; line-height: 1.5; }

.btn { padding: 6px 14px; border-radius: 6px; border: 1px solid #30363d; cursor: pointer;
       font-family: inherit; font-size: 13px; background: #21262d; color: #c9d1d9; }
.btn:hover { border-color: #8b949e; }
.btn-primary { background: #1f6feb; border-color: #1f6feb; color: #fff; }
.btn-primary:hover { background: #388bfd; border-color: #388bfd; }
.btn-danger  { background: transparent; border-color: #da3633; color: #f85149; }
.btn-danger:hover  { background: #da3633; color: #fff; }
.btn-sm { padding: 3px 8px; font-size: 11px; }

.note { color: #d29922; font-size: 12px; padding: 8px 12px; background: #2d1f00;
        border-left: 3px solid #d29922; border-radius: 0 6px 6px 0; margin-bottom: 14px; }
.success { color: #3fb950; font-size: 12px; padding: 8px 12px; background: #12261e;
           border-left: 3px solid #3fb950; border-radius: 0 6px 6px 0; margin-bottom: 14px; }
.empty { color: #6e7681; font-style: italic; padding: 20px; text-align: center; }

.two-col { display: grid; grid-template-columns: 220px 1fr; gap: 16px; }
.file-list { background: #010409; border: 1px solid #30363d; border-radius: 6px;
             height: 520px; overflow-y: auto; }
.file-item { padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #1c2128;
             font-size: 12px; color: #8b949e; }
.file-item:hover { background: #161b22; color: #c9d1d9; }
.file-item.active { color: #58a6ff; }
.file-item.wav-item { color: #3fb950; }
.file-pane { background: #010409; border: 1px solid #30363d; border-radius: 6px;
             height: 520px; overflow-y: auto; padding: 14px;
             font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-all; }
audio { width: 100%; margin: 6px 0; }

.about-grid { display: grid; grid-template-columns: 360px 1fr; gap: 28px; align-items: start; }
.comp-list { list-style: none; }
.comp-list li { padding: 10px 0; border-bottom: 1px solid #21262d; font-size: 13px; line-height: 1.5; }
.comp-list li:last-child { border-bottom: none; }
.comp-name { color: #3fb950; font-weight: bold; display: block; margin-bottom: 2px; }
.comp-desc { color: #8b949e; font-size: 12px; }
"""

def _nav(active: str) -> str:
    items = [("/", "Dashboard"), ("/messages", "Messages"),
             ("/transcripts", "Transcripts"), ("/prompts", "Prompts"), ("/about", "About")]
    links = "".join(
        f'<a href="{h}" class="{"active" if h == active else ""}">{l}</a>'
        for h, l in items
    )
    return f'<nav><span class="brand">&#9112; AK6MJ</span>{links}</nav>'


def _page(title: str, active: str, body: str, flash: str = "") -> HTMLResponse:
    flash_html = f'<div class="success">{flash}</div>' if flash else ""
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — AK6MJ Bot</title><style>{_CSS}</style>
</head><body>
{_nav(active)}
<div class="page">{flash_html}{body}</div>
</body></html>""")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="AK6MJ Dashboard", docs_url=None, redoc_url=None)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    cfg = load_config()
    callsign = cfg.get("callsign", "?")
    llm_mode = cfg.get("llm_mode", "ollama")
    dry_run = cfg.get("dry_run", False)
    stt_model = cfg.get("stt", {}).get("model", "?").split("/")[-1]
    tts_model = cfg.get("tts", {}).get("model_id", "?").split("/")[-1]

    # Load last 120 lines for initial display
    lines = last_n_lines(today_log())
    initial = ""
    for raw in lines:
        raw = raw.rstrip()
        if not raw:
            continue
        lvl = extract_level(raw)
        escaped = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        initial += f'<div class="log-line log-{lvl}">{escaped}</div>\n'

    body = f"""
<h1>Dashboard</h1>
<div class="status-bar">
  <div class="stat"><span class="stat-label">Callsign</span><span class="stat-val">{callsign}</span></div>
  <div class="stat"><span class="stat-label">LLM</span><span class="stat-val">{llm_mode}</span></div>
  <div class="stat"><span class="stat-label">STT</span><span class="stat-val">{stt_model}</span></div>
  <div class="stat"><span class="stat-label">TTS</span><span class="stat-val">{tts_model}</span></div>
  <div class="stat"><span class="stat-label">Mode</span><span class="stat-val">{"dry-run" if dry_run else "hardware"}</span></div>
  <div class="stat"><span class="dot"></span><span class="stat-val" id="status">connecting...</span></div>
  <div class="stat" style="margin-left:auto">
    <button class="btn btn-danger btn-sm" onclick="restartBot(this)">&#8635; Restart Bot</button>
  </div>
</div>
<div class="panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
    <span style="color:#8b949e;font-size:12px;">LIVE LOG — {today_log()}</span>
    <button class="btn btn-sm" onclick="paused=!paused;this.textContent=paused?'Resume':'Pause'">Pause</button>
  </div>
  <div class="log-window" id="log">{initial}</div>
</div>
<script>
const log = document.getElementById('log');
log.scrollTop = log.scrollHeight;
let paused = false;
const es = new EventSource('/stream');
es.onopen = () => document.getElementById('status').textContent = 'live';
es.onerror = () => document.getElementById('status').textContent = 'reconnecting...';
es.onmessage = e => {{
    if (e.data === ': keepalive') return;

    if (paused) return;
    const d = JSON.parse(e.data);
    const div = document.createElement('div');
    div.className = 'log-line ' + d.level;
    div.textContent = d.line;
    log.appendChild(div);
    // Keep last 500 lines to avoid memory growth
    while (log.children.length > 500) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
}};
async function restartBot(btn) {{
    if (!confirm('Send restart signal to the bot?')) return;
    btn.disabled = true;
    btn.textContent = 'Restarting...';
    const r = await fetch('/api/restart', {{method: 'POST'}});
    const d = await r.json();
    if (d.ok) {{
        btn.textContent = 'Restarted ✓';
        setTimeout(() => {{ btn.disabled = false; btn.textContent = '⟳ Restart Bot'; }}, 8000);
    }} else {{
        alert('Restart failed: ' + d.error);
        btn.disabled = false; btn.textContent = '⟳ Restart Bot';
    }}
}}
</script>"""
    return _page("Dashboard", "/", body)


@app.get("/stream")
async def stream_logs():
    """SSE: tail today's log file and push new lines."""
    async def generate():
        log_path = today_log()
        # Start at end of file — only stream new lines
        pos = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        idle = 0
        while True:
            await asyncio.sleep(0.25)
            if os.path.exists(log_path):
                try:
                    size = os.path.getsize(log_path)
                    if size < pos:
                        pos = 0  # file rotated
                    if size > pos:
                        with open(log_path) as f:
                            f.seek(pos)
                            new = f.read()
                            pos = f.tell()
                        for raw in new.splitlines():
                            if not raw.strip():
                                continue
                            lvl = extract_level(raw)
                            data = json.dumps({"line": raw, "level": f"log-{lvl}"})
                            yield f"data: {data}\n\n"
                        idle = 0
                        continue
                except Exception:
                    pass
            idle += 1
            if idle % 20 == 0:  # keepalive every ~5s
                yield ": keepalive\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Bot control ───────────────────────────────────────────────────────────────

@app.post("/api/restart")
def api_restart():
    pid_file = Path("bot.pid")
    if not pid_file.exists():
        return JSONResponse({"ok": False, "error": "bot.pid not found — is the bot running?"})
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGUSR1)
        return JSONResponse({"ok": True, "pid": pid})
    except ProcessLookupError:
        return JSONResponse({"ok": False, "error": f"No process at PID {pid} — bot may have already exited."})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ── Messages ──────────────────────────────────────────────────────────────────

@app.get("/messages", response_class=HTMLResponse)
def messages_page(saved: str = ""):
    mb = get_mb()
    cfg = load_config()
    msg_dir = Path(cfg.get("message_board", {}).get("dir", "messages"))

    # Personal messages: scan for callsign JSON files
    personal_rows = ""
    if msg_dir.exists():
        files = sorted(msg_dir.glob("[A-Z]*.json"))
        for f in files:
            try:
                msgs = json.loads(f.read_text())
            except Exception:
                continue
            cs = f.stem
            for i, m in enumerate(msgs):
                personal_rows += f"""<tr>
<td><strong>{m.get("to","?")}</strong></td>
<td>{m.get("from","?")}</td>
<td>{m.get("text","")}</td>
<td style="color:#6e7681;font-size:11px">{m.get("timestamp","")}</td>
<td><form method="post" action="/messages/personal/{cs}/{i}/delete" style="margin:0">
    <button class="btn btn-danger btn-sm">Delete</button></form></td>
</tr>"""
    if not personal_rows:
        personal_rows = '<tr><td colspan="5" class="empty">No pending personal messages.</td></tr>'

    # Bulletins
    bulletin_rows = ""
    for i, b in enumerate(mb._load(mb._bulletin_path, [])):
        active = b.get("active", True)
        badge = '<span class="badge green">active</span>' if active else '<span class="badge gray">expired</span>'
        expire_btn = ""
        if active:
            expire_btn = f"""<form method="post" action="/messages/bulletin/{i}/expire" style="margin:0">
<button class="btn btn-danger btn-sm">Expire</button></form>"""
        bulletin_rows += f"""<tr>
<td>{b.get("from","?")}</td>
<td>{b.get("text","")}</td>
<td>{badge}</td>
<td style="color:#6e7681;font-size:11px">{b.get("timestamp","")}</td>
<td>{expire_btn}</td>
</tr>"""
    if not bulletin_rows:
        bulletin_rows = '<tr><td colspan="5" class="empty">No bulletins posted.</td></tr>'

    flash = f'<div class="success">{saved}</div>' if saved else ""

    body = f"""{flash}
<h1>Message Board</h1>

<h2>Personal Messages</h2>
<div class="panel">
  <table>
    <tr><th>To</th><th>From</th><th>Message</th><th>Stored</th><th></th></tr>
    {personal_rows}
  </table>
  <form method="post" action="/messages/personal">
    <div class="form-row" style="margin-top:16px">
      <input type="text" name="to_call" placeholder="To callsign" style="width:120px">
      <input type="text" name="from_call" placeholder="From callsign" style="width:120px">
      <input type="text" name="text" placeholder="Message text" style="width:340px">
      <button class="btn btn-primary" type="submit">Store Message</button>
    </div>
  </form>
</div>

<h2>Bulletins (All-Stations)</h2>
<div class="panel">
  <table>
    <tr><th>From</th><th>Message</th><th>Status</th><th>Posted</th><th></th></tr>
    {bulletin_rows}
  </table>
  <form method="post" action="/messages/bulletin">
    <div class="form-row" style="margin-top:16px">
      <input type="text" name="from_call" placeholder="From callsign" style="width:120px">
      <input type="text" name="text" placeholder="Bulletin text" style="width:460px">
      <button class="btn btn-primary" type="submit">Post Bulletin</button>
    </div>
  </form>
</div>"""
    return _page("Messages", "/messages", body)


@app.post("/messages/personal")
def add_personal(to_call: str = Form(""), from_call: str = Form(""), text: str = Form("")):
    if to_call and text:
        get_mb().store_personal(from_call or "Dashboard", to_call.upper(), text)
    return RedirectResponse("/messages?saved=Message+stored.", status_code=303)


@app.post("/messages/personal/{callsign}/{idx}/delete")
def delete_personal(callsign: str, idx: int):
    cfg = load_config()
    msg_dir = Path(cfg.get("message_board", {}).get("dir", "messages"))
    path = msg_dir / f"{callsign.upper()}.json"
    if path.exists():
        try:
            msgs = json.loads(path.read_text())
            if 0 <= idx < len(msgs):
                msgs.pop(idx)
            if msgs:
                path.write_text(json.dumps(msgs, indent=2))
            else:
                path.unlink()
        except Exception:
            pass
    return RedirectResponse("/messages?saved=Message+deleted.", status_code=303)


@app.post("/messages/bulletin")
def add_bulletin(from_call: str = Form(""), text: str = Form("")):
    if text:
        get_mb().store_bulletin(from_call or "Dashboard", text)
    return RedirectResponse("/messages?saved=Bulletin+posted.", status_code=303)


@app.post("/messages/bulletin/{idx}/expire")
def expire_bulletin(idx: int):
    mb = get_mb()
    bulletins = mb._load(mb._bulletin_path, [])
    if 0 <= idx < len(bulletins):
        bulletins[idx]["active"] = False
        mb._save(mb._bulletin_path, bulletins)
    return RedirectResponse("/messages?saved=Bulletin+expired.", status_code=303)


# ── Transcripts ───────────────────────────────────────────────────────────────

@app.get("/transcripts", response_class=HTMLResponse)
def transcripts_page():
    log_dir = Path(LOG_DIR)
    log_files = sorted(log_dir.glob("bot_*.log"), reverse=True) if log_dir.exists() else []
    wav_files = sorted(
        list(log_dir.glob("rx_*.wav")) + list(log_dir.glob("tx_*.wav")),
        reverse=True,
    ) if log_dir.exists() else []

    log_items = "".join(
        f'<div class="file-item" onclick="loadLog(\'{f.name}\')">{f.name}</div>'
        for f in log_files
    )
    wav_items = "".join(
        f'<div class="file-item wav-item" onclick="loadWav(\'{f.name}\')">{f.name}</div>'
        for f in wav_files
    )

    if not log_items and not wav_items:
        log_items = '<div class="file-item" style="color:#6e7681">No files yet.</div>'

    body = f"""
<h1>Transcripts &amp; Recordings</h1>
<div class="two-col">
  <div>
    <h2>Log Files</h2>
    <div class="file-list">{log_items}</div>
    <h2 style="margin-top:16px">WAV Recordings</h2>
    <div class="file-list" style="height:200px">{wav_items}</div>
  </div>
  <div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <h2 style="margin:0" id="pane-title">Select a file →</h2>
    </div>
    <div class="file-pane" id="pane"><span style="color:#6e7681">Click a file to view.</span></div>
    <div id="audio-pane" style="margin-top:10px;display:none">
      <audio id="audio-player" controls></audio>
    </div>
  </div>
</div>
<script>
async function loadLog(name) {{
    document.getElementById('pane-title').textContent = name;
    document.getElementById('audio-pane').style.display = 'none';
    document.getElementById('pane').textContent = 'Loading...';
    const r = await fetch('/api/logfile/' + name);
    const text = await r.text();
    const pane = document.getElementById('pane');
    pane.textContent = text;
    pane.scrollTop = pane.scrollHeight;
    document.querySelectorAll('.file-item').forEach(e => e.classList.remove('active'));
}}
function loadWav(name) {{
    document.getElementById('pane-title').textContent = name;
    document.getElementById('pane').textContent = '';
    const ap = document.getElementById('audio-pane');
    ap.style.display = 'block';
    document.getElementById('audio-player').src = '/audio/' + name;
}}
</script>"""
    return _page("Transcripts", "/transcripts", body)


@app.get("/api/logfile/{filename}")
def get_logfile(filename: str):
    # Safety: only serve files inside log dir
    path = Path(LOG_DIR) / Path(filename).name
    if not path.exists() or path.suffix != ".log":
        return HTMLResponse("Not found", status_code=404)
    return HTMLResponse(path.read_text(), media_type="text/plain")


@app.get("/audio/{filename}")
def serve_audio(filename: str):
    path = Path(LOG_DIR) / Path(filename).name
    if not path.exists() or path.suffix != ".wav":
        return HTMLResponse("Not found", status_code=404)
    return FileResponse(path, media_type="audio/wav")


# ── Prompts ───────────────────────────────────────────────────────────────────

@app.get("/prompts", response_class=HTMLResponse)
def prompts_page(saved: str = ""):
    cfg = load_config()
    llm = cfg.get("llm", {})
    claude = cfg.get("claude", {})
    prompt = llm.get("system_prompt", "")
    max_tokens = llm.get("max_tokens", 200)
    temperature = llm.get("temperature", 0.7)
    claude_model = claude.get("model", "claude-opus-4-6")
    claude_max = claude.get("max_tokens", 300)
    llm_model = llm.get("model", "qwen3:32b")

    flash = f'<div class="success">{saved}</div>' if saved else ""

    body = f"""{flash}
<h1>Prompt &amp; Model Settings</h1>
<div class="note">&#9888; Changes are saved to config.yaml immediately. The dashboard always reads the current file, so this page reflects the latest values. The <strong>bot (main.py) must be restarted</strong> to pick up changes — the Ollama backend bakes the system prompt into memory at startup.</div>
<form method="post" action="/prompts">
<div class="panel">
  <h2>Ollama System Prompt</h2>
  <textarea name="system_prompt" rows="16">{prompt}</textarea>
  <div class="form-row" style="margin-top:12px">
    <div>
      <div style="color:#6e7681;font-size:11px;margin-bottom:4px">Ollama Model</div>
      <input type="text" name="llm_model" value="{llm_model}" style="width:200px">
    </div>
    <div>
      <div style="color:#6e7681;font-size:11px;margin-bottom:4px">Max Tokens</div>
      <input type="text" name="max_tokens" value="{max_tokens}" style="width:80px">
    </div>
    <div>
      <div style="color:#6e7681;font-size:11px;margin-bottom:4px">Temperature</div>
      <input type="text" name="temperature" value="{temperature}" style="width:80px">
    </div>
  </div>
</div>
<div class="panel">
  <h2>Claude API Settings</h2>
  <div class="form-row">
    <div>
      <div style="color:#6e7681;font-size:11px;margin-bottom:4px">Claude Model</div>
      <input type="text" name="claude_model" value="{claude_model}" style="width:220px">
    </div>
    <div>
      <div style="color:#6e7681;font-size:11px;margin-bottom:4px">Max Tokens</div>
      <input type="text" name="claude_max_tokens" value="{claude_max}" style="width:80px">
    </div>
  </div>
</div>
<button class="btn btn-primary" type="submit">Save to config.yaml</button>
</form>"""
    return _page("Prompts", "/prompts", body)


@app.post("/prompts")
def save_prompts(
    system_prompt: str = Form(""),
    llm_model: str = Form(""),
    max_tokens: str = Form("200"),
    temperature: str = Form("0.7"),
    claude_model: str = Form(""),
    claude_max_tokens: str = Form("300"),
):
    cfg = load_config()
    cfg.setdefault("llm", {})["system_prompt"] = system_prompt
    if llm_model:
        cfg["llm"]["model"] = llm_model.strip()
    try:
        cfg["llm"]["max_tokens"] = int(max_tokens)
    except ValueError:
        pass
    try:
        cfg["llm"]["temperature"] = float(temperature)
    except ValueError:
        pass
    cfg.setdefault("claude", {})
    if claude_model:
        cfg["claude"]["model"] = claude_model.strip()
    try:
        cfg["claude"]["max_tokens"] = int(claude_max_tokens)
    except ValueError:
        pass
    save_config(cfg)
    return RedirectResponse("/prompts?saved=Saved.+Restart+bot+to+apply.", status_code=303)


# ── About ─────────────────────────────────────────────────────────────────────

_DIAGRAM_SVG = """
<svg viewBox="0 0 320 700" width="320" height="700"
     xmlns="http://www.w3.org/2000/svg" style="display:block">
<defs>
  <marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
    <path d="M0,0 L0,6 L8,3 z" fill="#444d56"/>
  </marker>
</defs>

<!-- boxes: x=60 width=200, centers at y=40,108,176,244,312,380,448,516,584,652 -->
<!-- arrow helper: from bottom of box to top of next -->

<!-- 1 Baofeng HT RX -->
<rect x="60" y="20" width="200" height="40" rx="6"
      fill="#12261e" stroke="#238636" stroke-width="1.5"/>
<text x="160" y="38" text-anchor="middle" fill="#3fb950" font-size="12" font-family="monospace">Baofeng HT</text>
<text x="160" y="52" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">listening on 2m FM</text>

<!-- arrow -->
<line x1="160" y1="60" x2="160" y2="86" stroke="#444d56" stroke-width="1.5" marker-end="url(#arr)"/>
<text x="172" y="76" fill="#6e7681" font-size="9" font-family="monospace">audio</text>

<!-- 2 AIOC/Digirig -->
<rect x="60" y="88" width="200" height="40" rx="6"
      fill="#162032" stroke="#1f6feb" stroke-width="1.5"/>
<text x="160" y="106" text-anchor="middle" fill="#58a6ff" font-size="12" font-family="monospace">AIOC / Digirig</text>
<text x="160" y="120" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">USB audio + PTT serial</text>

<!-- arrow -->
<line x1="160" y1="128" x2="160" y2="154" stroke="#444d56" stroke-width="1.5" marker-end="url(#arr)"/>
<text x="172" y="144" fill="#6e7681" font-size="9" font-family="monospace">pcm</text>

<!-- 3 VOX -->
<rect x="60" y="156" width="200" height="40" rx="6"
      fill="#1a1a2a" stroke="#6e40c9" stroke-width="1.5"/>
<text x="160" y="174" text-anchor="middle" fill="#a371f7" font-size="12" font-family="monospace">VOX Detector</text>
<text x="160" y="188" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">RMS threshold −47 dBFS</text>

<!-- arrow -->
<line x1="160" y1="196" x2="160" y2="222" stroke="#444d56" stroke-width="1.5" marker-end="url(#arr)"/>
<text x="172" y="212" fill="#6e7681" font-size="9" font-family="monospace">chunk</text>

<!-- 4 STT -->
<rect x="60" y="224" width="200" height="40" rx="6"
      fill="#1a1a2a" stroke="#6e40c9" stroke-width="1.5"/>
<text x="160" y="242" text-anchor="middle" fill="#a371f7" font-size="12" font-family="monospace">STT — Whisper</text>
<text x="160" y="256" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">large-v3-turbo (Apple Silicon)</text>

<!-- arrow -->
<line x1="160" y1="264" x2="160" y2="290" stroke="#444d56" stroke-width="1.5" marker-end="url(#arr)"/>
<text x="172" y="280" fill="#6e7681" font-size="9" font-family="monospace">text</text>

<!-- 5 Compliance -->
<rect x="60" y="292" width="200" height="40" rx="6"
      fill="#2d1f00" stroke="#9e6a03" stroke-width="1.5"/>
<text x="160" y="310" text-anchor="middle" fill="#d29922" font-size="12" font-family="monospace">Compliance (Part 97)</text>
<text x="160" y="324" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">emergency · shutdown · filter</text>

<!-- arrow -->
<line x1="160" y1="332" x2="160" y2="358" stroke="#444d56" stroke-width="1.5" marker-end="url(#arr)"/>

<!-- 6 LLM -->
<rect x="60" y="360" width="200" height="40" rx="6"
      fill="#12261e" stroke="#238636" stroke-width="1.5"/>
<text x="160" y="378" text-anchor="middle" fill="#3fb950" font-size="12" font-family="monospace">LLM + Web Search</text>
<text x="160" y="392" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">Ollama qwen3:32b · DuckDuckGo</text>

<!-- side: memory -->
<rect x="270" y="360" width="44" height="40" rx="5"
      fill="#161b22" stroke="#30363d" stroke-width="1"/>
<text x="292" y="378" text-anchor="middle" fill="#6e7681" font-size="9" font-family="monospace">Memory</text>
<text x="292" y="390" text-anchor="middle" fill="#6e7681" font-size="9" font-family="monospace">Manager</text>
<line x1="270" y1="380" x2="260" y2="380" stroke="#30363d" stroke-width="1" marker-end="url(#arr)"/>

<!-- arrow -->
<line x1="160" y1="400" x2="160" y2="426" stroke="#444d56" stroke-width="1.5" marker-end="url(#arr)"/>

<!-- 7 Message Board relay -->
<rect x="60" y="428" width="200" height="40" rx="6"
      fill="#12261e" stroke="#238636" stroke-width="1.5"/>
<text x="160" y="446" text-anchor="middle" fill="#3fb950" font-size="12" font-family="monospace">Message Board</text>
<text x="160" y="460" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">personal msgs · bulletins</text>

<!-- arrow -->
<line x1="160" y1="468" x2="160" y2="494" stroke="#444d56" stroke-width="1.5" marker-end="url(#arr)"/>
<text x="172" y="484" fill="#6e7681" font-size="9" font-family="monospace">text</text>

<!-- 8 TTS -->
<rect x="60" y="496" width="200" height="40" rx="6"
      fill="#1a1a2a" stroke="#6e40c9" stroke-width="1.5"/>
<text x="160" y="514" text-anchor="middle" fill="#a371f7" font-size="12" font-family="monospace">TTS — Qwen3-TTS</text>
<text x="160" y="528" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">voice clone · 0.6B · Apple Silicon</text>

<!-- arrow -->
<line x1="160" y1="536" x2="160" y2="562" stroke="#444d56" stroke-width="1.5" marker-end="url(#arr)"/>
<text x="172" y="552" fill="#6e7681" font-size="9" font-family="monospace">audio</text>

<!-- 9 PTT TX -->
<rect x="60" y="564" width="200" height="40" rx="6"
      fill="#162032" stroke="#1f6feb" stroke-width="1.5"/>
<text x="160" y="582" text-anchor="middle" fill="#58a6ff" font-size="12" font-family="monospace">PTT + AIOC TX</text>
<text x="160" y="596" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">DTR=1 RTS=0 · USB serial</text>

<!-- arrow -->
<line x1="160" y1="604" x2="160" y2="630" stroke="#444d56" stroke-width="1.5" marker-end="url(#arr)"/>
<text x="172" y="620" fill="#6e7681" font-size="9" font-family="monospace">FM</text>

<!-- 10 Baofeng TX -->
<rect x="60" y="632" width="200" height="40" rx="6"
      fill="#12261e" stroke="#238636" stroke-width="1.5"/>
<text x="160" y="650" text-anchor="middle" fill="#3fb950" font-size="12" font-family="monospace">Baofeng HT</text>
<text x="160" y="664" text-anchor="middle" fill="#6e7681" font-size="10" font-family="monospace">transmitting on 2m FM</text>
</svg>
"""

@app.get("/about", response_class=HTMLResponse)
def about_page():
    cfg = load_config()
    callsign = cfg.get("callsign", "?")
    freq = "146.555 MHz"  # simplex

    body = f"""
<h1>About — {callsign} Bot</h1>
<div class="about-grid">
  <div>
    <div class="panel" style="padding:20px">{_DIAGRAM_SVG}</div>
  </div>
  <div>
    <div class="panel">
      <h2>Signal Flow</h2>
      <ul class="comp-list">
        <li>
          <span class="comp-name">Baofeng HT</span>
          <span class="comp-desc">Handheld VHF FM transceiver. Listens on {freq} (or programmed simplex). VOX enabled via software — squelch set to 3–5 to suppress noise floor.</span>
        </li>
        <li>
          <span class="comp-name">AIOC / Digirig USB</span>
          <span class="comp-desc">All-In-One Cable (AIOC) or Digirig Mobile: USB audio interface + PTT control. Audio captured via sounddevice; PTT keyed by DTR=High on the serial port (VID:1209 PID:7388).</span>
        </li>
        <li>
          <span class="comp-name">VOX Detector</span>
          <span class="comp-desc">Software voice-operated switch. Measures RMS dBFS on the audio stream; opens at −47 dBFS with 1 s hang time. Muted during TX to prevent self-triggering.</span>
        </li>
        <li>
          <span class="comp-name">STT — Whisper</span>
          <span class="comp-desc">OpenAI Whisper (mlx-community/whisper-large-v3-turbo) running on Apple Silicon via mlx-whisper. Primed with a NATO phonetics prompt for callsign recognition.</span>
        </li>
        <li>
          <span class="comp-name">Compliance (Part 97)</span>
          <span class="comp-desc">FCC §97 enforcement: detects emergency traffic (Mayday, Break Break), voice kill switch ("AK6MJ shut down"), and filters prohibited content from all transmissions. Station ID every 10 min (§97.119).</span>
        </li>
        <li>
          <span class="comp-name">LLM + Web Search</span>
          <span class="comp-desc">Ollama qwen3:32b (local) or Claude API. DuckDuckGo search triggered by keyword heuristics. Memory context from past QSOs injected per callsign.</span>
        </li>
        <li>
          <span class="comp-name">Message Board</span>
          <span class="comp-desc">Radio BBS: personal messages stored per callsign (cleared on delivery); bulletins persist until expired. Commands detected from transcribed speech.</span>
        </li>
        <li>
          <span class="comp-name">TTS — Qwen3-TTS</span>
          <span class="comp-desc">mlx-community/Qwen3-TTS-12Hz-0.6B running on Apple Silicon via mlx-audio. Voice-cloned from the station owner's reference audio. Output normalized to 90% peak before TX.</span>
        </li>
        <li>
          <span class="comp-name">PTT + AIOC TX</span>
          <span class="comp-desc">0.3 s settle delay after PTT on, then audio played via sounddevice to the AIOC. PTT released after playback, 0.5 s pause before VOX resumes.</span>
        </li>
      </ul>
    </div>
    <div class="panel">
      <h2>Key Constraints</h2>
      <ul class="comp-list">
        <li><span class="comp-name">Half-duplex</span>
            <span class="comp-desc">Single-threaded blocking loop — FM radio is half-duplex. No async in the main pipeline.</span></li>
        <li><span class="comp-name">Part 97 — Auto control</span>
            <span class="comp-desc">Permitted on 2m VHF. Callsign ID every 10 min. Emergency traffic triggers silence. Content filtered before every TX.</span></li>
        <li><span class="comp-name">Offline first</span>
            <span class="comp-desc">HF_HUB_OFFLINE=1 at runtime. All ML models cached locally. Use <code>make download-models</code> once before going off-grid.</span></li>
      </ul>
    </div>
  </div>
</div>"""
    return _page("About", "/about", body)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global CONFIG_PATH, LOG_DIR
    parser = argparse.ArgumentParser(description="AIOC Bot Dashboard")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("-p", "--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    CONFIG_PATH = args.config
    cfg = load_config()
    LOG_DIR = cfg.get("log_dir", "logs")

    print(f"Dashboard → http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

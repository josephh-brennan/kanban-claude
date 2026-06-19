#!/usr/bin/env python3
"""Local server for Claude Code Kanban board."""

import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8765
BASE_DIR = Path(__file__).parent
TASKS_FILE = BASE_DIR / "tasks.json"
PLANS_DIR = Path.home() / ".claude" / "plans"

MODEL_PRICING = {
    'claude-opus-4-7':            {'input': 15.0,  'output': 75.0,  'cache_read': 1.50,  'cache_write': 18.75},
    'claude-sonnet-4-6':          {'input': 3.0,   'output': 15.0,  'cache_read': 0.30,  'cache_write': 3.75},
    'claude-haiku-4-5-20251001':  {'input': 0.80,  'output': 4.0,   'cache_read': 0.08,  'cache_write': 1.00},
    'claude-opus-4-5-20251101':   {'input': 15.0,  'output': 75.0,  'cache_read': 1.50,  'cache_write': 18.75},
    'claude-sonnet-4-5-20250929': {'input': 3.0,   'output': 15.0,  'cache_read': 0.30,  'cache_write': 3.75},
    '_default':                   {'input': 3.0,   'output': 15.0,  'cache_read': 0.30,  'cache_write': 3.75},
}

_weekly_cache = {'data': None, 'ts': 0}


def compute_session_cost(jsonl_path):
    tokens = defaultdict(int)
    model_counts = defaultdict(int)
    session_date = None
    try:
        with open(jsonl_path, errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # First timestamp in the file = session start date
                if session_date is None:
                    ts = msg.get('timestamp', '')
                    if ts:
                        session_date = ts[:10]  # 'YYYY-MM-DD'
                if msg.get('type') != 'assistant':
                    continue
                message = msg.get('message', {})
                model = message.get('model', '')
                if model:
                    model_counts[model] += 1
                usage = message.get('usage', {})
                tokens['input'] += usage.get('input_tokens', 0)
                tokens['output'] += usage.get('output_tokens', 0)
                tokens['cache_read'] += usage.get('cache_read_input_tokens', 0)
                tokens['cache_write'] += usage.get('cache_creation_input_tokens', 0)
    except OSError:
        pass
    primary_model = max(model_counts, key=model_counts.get) if model_counts else ''
    pricing = MODEL_PRICING.get(primary_model, MODEL_PRICING['_default'])
    cost = (
        tokens['input'] * pricing['input'] / 1_000_000 +
        tokens['output'] * pricing['output'] / 1_000_000 +
        tokens['cache_read'] * pricing['cache_read'] / 1_000_000 +
        tokens['cache_write'] * pricing['cache_write'] / 1_000_000
    )
    return {
        'costUSD': round(cost, 4),
        'inputTokens': tokens['input'],
        'outputTokens': tokens['output'],
        'cacheReadTokens': tokens['cache_read'],
        'cacheWriteTokens': tokens['cache_write'],
        'model': primary_model,
        'sessionDate': session_date,
    }


def read_tasks():
    if not TASKS_FILE.exists():
        return []
    with open(TASKS_FILE) as f:
        return json.load(f)


def write_tasks(tasks):
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, indent=2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default access logs

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.send_file(BASE_DIR / "index.html", "text/html")

        elif path == "/api/tasks":
            self.send_json(read_tasks())

        elif path == "/api/prs":
            try:
                result = subprocess.run(
                    ['/bin/zsh', '-lc',
                     'gh search prs --author @me --state open --limit 30 --json title,url,number,repository'],
                    capture_output=True, text=True, timeout=10
                )
                prs = json.loads(result.stdout) if result.returncode == 0 else []
            except Exception:
                prs = []
            self.send_json(prs)

        elif path == "/api/claudemd":
            p = Path.home() / ".claude" / "CLAUDE.md"
            if not p.exists():
                self.send_json({"error": "CLAUDE.md not found"}, 404)
                return
            self.send_json({"content": p.read_text()})

        elif path == "/api/plans":
            if not PLANS_DIR.exists():
                self.send_json([])
                return
            plans = sorted(
                p.name for p in PLANS_DIR.glob("*.md") if p.is_file()
            )
            self.send_json(plans)

        elif path == "/api/plan":
            filename = qs.get("file", [None])[0]
            if not filename:
                self.send_json({"error": "missing file param"}, 400)
                return
            # Safety: only allow .md files in the plans directory
            plan_path = PLANS_DIR / Path(filename).name
            if not plan_path.exists():
                self.send_json({"error": "plan not found"}, 404)
                return
            content = plan_path.read_text()
            self.send_json({"content": content})

        elif path == "/api/session":
            plan_file = qs.get("planFile", [None])[0]
            project_dir = qs.get("projectDir", [None])[0] or ""
            if not plan_file and not project_dir:
                self.send_json({"error": "missing planFile or projectDir param"}, 400)
                return
            project_dir = project_dir.replace("~", str(Path.home()))
            projects_root = Path.home() / ".claude" / "projects"
            this_board_key = str(BASE_DIR).replace("/", "-")
            _empty = {'costUSD': 0, 'inputTokens': 0, 'outputTokens': 0, 'cacheReadTokens': 0, 'cacheWriteTokens': 0, 'model': ''}
            if not projects_root.exists():
                self.send_json({"sessionId": None, **_empty})
                return

            task_key_parts = set(project_dir.strip("/").split("/")) if project_dir else set()
            candidates = []

            if plan_file:
                # Match sessions that contain the plan filename, scored by directory overlap
                plan_name = Path(plan_file).name
                for jsonl in projects_root.glob("*/*.jsonl"):
                    proj_key = jsonl.parent.name
                    if this_board_key in proj_key:
                        continue
                    try:
                        if plan_name in jsonl.read_text(errors="ignore"):
                            proj_parts = set(proj_key.strip("-").split("-"))
                            score = len(task_key_parts & proj_parts)
                            candidates.append((score, jsonl.stat().st_mtime, jsonl.stem, jsonl))
                    except OSError:
                        continue
            else:
                # No plan file — match by project directory only, take most recent session
                for jsonl in projects_root.glob("*/*.jsonl"):
                    proj_key = jsonl.parent.name
                    if this_board_key in proj_key:
                        continue
                    try:
                        proj_parts = set(proj_key.strip("-").split("-"))
                        score = len(task_key_parts & proj_parts)
                        if score > 0:
                            candidates.append((score, jsonl.stat().st_mtime, jsonl.stem, jsonl))
                    except OSError:
                        continue

            if not candidates:
                self.send_json({"sessionId": None, **_empty})
                return

            best = max(candidates, key=lambda x: (x[0], x[1]))
            cost_data = compute_session_cost(best[3])
            self.send_json({"sessionId": best[2], **cost_data})

        elif path == "/api/weekly-stats":
            now = time.time()
            if now - _weekly_cache['ts'] < 300 and _weekly_cache['data'] is not None:
                self.send_json(_weekly_cache['data'])
                return

            projects_root = Path.home() / ".claude" / "projects"
            this_board_key = str(BASE_DIR).replace("/", "-")
            mtime_cutoff = now - 7 * 24 * 3600
            cutoff_date = datetime.fromtimestamp(mtime_cutoff).strftime('%Y-%m-%d')
            by_day = defaultdict(lambda: {'costUSD': 0.0, 'sessions': 0, 'inputTokens': 0, 'outputTokens': 0, 'cacheReadTokens': 0, 'cacheWriteTokens': 0})
            by_model = defaultdict(lambda: {'costUSD': 0.0, 'inputTokens': 0, 'outputTokens': 0, 'cacheReadTokens': 0, 'cacheWriteTokens': 0})

            if projects_root.exists():
                for jsonl in projects_root.glob("*/*.jsonl"):
                    if this_board_key in jsonl.parent.name:
                        continue
                    try:
                        # mtime pre-filter: sessionDate <= mtime, so skip if mtime is old
                        if jsonl.stat().st_mtime < mtime_cutoff:
                            continue
                        cd = compute_session_cost(jsonl)
                        date_str = cd.get('sessionDate') or datetime.fromtimestamp(jsonl.stat().st_mtime).strftime('%Y-%m-%d')
                        # Filter by actual session start date, not mtime
                        if date_str < cutoff_date:
                            continue
                        by_day[date_str]['costUSD'] += cd['costUSD']
                        by_day[date_str]['sessions'] += 1
                        by_day[date_str]['inputTokens'] += cd['inputTokens']
                        by_day[date_str]['outputTokens'] += cd['outputTokens']
                        by_day[date_str]['cacheReadTokens'] += cd['cacheReadTokens']
                        by_day[date_str]['cacheWriteTokens'] += cd['cacheWriteTokens']
                        if cd['model']:
                            by_model[cd['model']]['costUSD'] += cd['costUSD']
                            by_model[cd['model']]['inputTokens'] += cd['inputTokens']
                            by_model[cd['model']]['outputTokens'] += cd['outputTokens']
                            by_model[cd['model']]['cacheReadTokens'] += cd['cacheReadTokens']
                            by_model[cd['model']]['cacheWriteTokens'] += cd['cacheWriteTokens']
                    except OSError:
                        continue

            by_day_list = sorted(
                [{'date': k, **{kk: (round(vv, 4) if kk == 'costUSD' else vv) for kk, vv in v.items()}} for k, v in by_day.items()],
                key=lambda x: x['date']
            )
            by_model_dict = {
                m: {kk: (round(vv, 4) if kk == 'costUSD' else vv) for kk, vv in v.items()}
                for m, v in by_model.items()
            }
            week_total = {
                'costUSD': round(sum(d['costUSD'] for d in by_day_list), 4),
                'sessions': sum(d['sessions'] for d in by_day_list),
                'inputTokens': sum(d['inputTokens'] for d in by_day_list),
                'outputTokens': sum(d['outputTokens'] for d in by_day_list),
                'cacheReadTokens': sum(d['cacheReadTokens'] for d in by_day_list),
                'cacheWriteTokens': sum(d['cacheWriteTokens'] for d in by_day_list),
            }
            result = {'weekTotal': week_total, 'byDay': by_day_list, 'byModel': by_model_dict}
            _weekly_cache['data'] = result
            _weekly_cache['ts'] = now
            self.send_json(result)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/tasks":
            body = self.read_body()
            tasks = json.loads(body)
            write_tasks(tasks)
            self.send_json({"ok": True})

        elif path == "/api/plan":
            body = self.read_body()
            data = json.loads(body)
            filename = data.get("file", "")
            content = data.get("content", "")
            if not filename:
                self.send_json({"error": "missing file"}, 400)
                return
            plan_path = PLANS_DIR / Path(filename).name
            if not plan_path.exists():
                self.send_json({"error": "plan not found"}, 404)
                return
            plan_path.write_text(content)
            self.send_json({"ok": True})

        elif path == "/api/open-terminal":
            body = self.read_body()
            data = json.loads(body)

            cmd = data.get("cmd", "").strip()
            if not cmd:
                self.send_json({"error": "missing cmd"}, 400)
                return

            # Expand ~ in the command string
            cmd = cmd.replace("~", str(Path.home()))

            subprocess.Popen(['/bin/zsh', '-lc', cmd])
            self.send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Kanban board running at http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)

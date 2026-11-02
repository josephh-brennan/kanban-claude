#!/usr/bin/env python3
"""Local server for Claude Code Kanban board."""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8765
BASE_DIR = Path(__file__).parent
TASKS_FILE = BASE_DIR / "tasks.json"
PLANS_DIR = Path.home() / ".claude" / "plans"
# Board-owned copies of plans. Git-ignored. Filled by scripts/capture-plans.sh
# (Claude Code hooks) and by snapshot_plan() on link and board edits.
STORE_DIR = BASE_DIR / "data" / "plans"
SNAPSHOT_HASH_RE = re.compile(r"^[0-9a-f]{16}$")


def read_tasks():
    if not TASKS_FILE.exists():
        return []
    with open(TASKS_FILE) as f:
        return json.load(f)


def write_tasks(tasks):
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, indent=2)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_plan(src, source):
    """Store the current bytes of src. Same content is stored once. Returns its metadata."""
    content = src.read_bytes()
    digest = hashlib.sha256(content).hexdigest()[:16]
    meta_path = STORE_DIR / f"{digest}.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text())
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    # Content first, metadata last: the .json file is the commit marker.
    md_tmp = STORE_DIR / f"{digest}.md.tmp"
    md_tmp.write_bytes(content)
    md_tmp.replace(STORE_DIR / f"{digest}.md")
    meta = {"hash": digest, "name": src.name, "capturedAt": utc_now(), "source": source}
    json_tmp = STORE_DIR / f"{digest}.json.tmp"
    json_tmp.write_text(json.dumps(meta))
    json_tmp.replace(meta_path)
    return meta


def latest_snapshot(name):
    """Most recent snapshot captured under this plan filename, or None.

    capturedAt has one-second resolution, so ties are broken by the metadata
    file's mtime, which records the actual write order.
    """
    if not STORE_DIR.exists():
        return None
    matches = []
    for p in STORE_DIR.glob("*.json"):
        try:
            meta = json.loads(p.read_text())
            written = p.stat().st_mtime_ns
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("name") == name:
            matches.append((meta["capturedAt"], written, meta))
    return max(matches, key=lambda m: (m[0], m[1]))[2] if matches else None


def read_plan(name):
    """Live plan if it still exists, otherwise the latest snapshot. None if neither."""
    live = PLANS_DIR / name
    if live.exists():
        return {"content": live.read_text(), "source": "live"}
    snap = latest_snapshot(name)
    if snap:
        snap_path = STORE_DIR / f"{snap['hash']}.md"
        if SNAPSHOT_HASH_RE.match(snap["hash"]) and snap_path.exists():
            return {
                "content": snap_path.read_text(),
                "source": "snapshot",
                "capturedAt": snap["capturedAt"],
            }
    return None


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
            # Safety: basename only, so the lookup stays inside the plans directory
            plan = read_plan(Path(filename).name)
            if plan is None:
                self.send_json({"error": "plan not found and no snapshot was captured"}, 404)
                return
            self.send_json(plan)

        elif path == "/api/session":
            plan_file = qs.get("planFile", [None])[0]
            project_dir = qs.get("projectDir", [None])[0] or ""
            if not plan_file and not project_dir:
                self.send_json({"error": "missing planFile or projectDir param"}, 400)
                return
            project_dir = project_dir.replace("~", str(Path.home()))
            projects_root = Path.home() / ".claude" / "projects"
            this_board_key = str(BASE_DIR).replace("/", "-")
            if not projects_root.exists():
                self.send_json({"sessionId": None})
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
                self.send_json({"sessionId": None})
                return

            best = max(candidates, key=lambda x: (x[0], x[1]))
            self.send_json({"sessionId": best[2]})

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
            # Snapshot the edit now, so it survives even if the live file is removed before the next hook run
            snapshot_plan(plan_path, "board-edit")
            self.send_json({"ok": True})

        elif path == "/api/plan/capture":
            # Called when a plan is linked to a task: make sure its current content is stored
            data = json.loads(self.read_body())
            filename = Path(data.get("file", "")).name
            if not filename:
                self.send_json({"error": "missing file"}, 400)
                return
            live = PLANS_DIR / filename
            if live.exists():
                meta = snapshot_plan(live, "link")
                self.send_json({"captured": True, "capturedAt": meta["capturedAt"]})
                return
            snap = latest_snapshot(filename)
            self.send_json({"captured": snap is not None, "capturedAt": snap["capturedAt"] if snap else None})

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

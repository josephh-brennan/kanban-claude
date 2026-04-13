#!/usr/bin/env python3
"""Local server for Claude Code Kanban board."""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8765
BASE_DIR = Path(__file__).parent
TASKS_FILE = BASE_DIR / "tasks.json"
PLANS_DIR = Path.home() / ".claude" / "plans"


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
            if not plan_file:
                self.send_json({"error": "missing planFile param"}, 400)
                return
            plan_name = Path(plan_file).name
            project_dir = project_dir.replace("~", str(Path.home()))
            projects_root = Path.home() / ".claude" / "projects"
            this_board_key = str(BASE_DIR).replace("/", "-")
            if not projects_root.exists():
                self.send_json({"sessionId": None})
                return

            # Build expected project key from projectDir for scoring
            task_key_parts = set(project_dir.strip("/").split("/"))

            candidates = []
            for jsonl in projects_root.glob("*/*.jsonl"):
                proj_key = jsonl.parent.name
                # Skip this kanban board's own sessions
                if this_board_key in proj_key:
                    continue
                try:
                    if plan_name in jsonl.read_text(errors="ignore"):
                        # Score by how many path parts match the task's projectDir
                        proj_parts = set(proj_key.strip("-").split("-"))
                        score = len(task_key_parts & proj_parts)
                        candidates.append((score, jsonl.stat().st_mtime, jsonl.stem))
                except OSError:
                    continue

            best = max(candidates, default=(None, None, None))[2] if candidates else None
            self.send_json({"sessionId": best})

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

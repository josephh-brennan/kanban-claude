# Refactor proposal: durable plans and sessions

Status: proposal. Nothing here is implemented yet.

## 1. The problem

The board stores only a pointer to a plan. It stores `planFile` (a filename) and reads the content from `~/.claude/plans/` on every view. Session lookup works the same way: `/api/session` searches `~/.claude/projects/*/*.jsonl` each time it is called.

Those locations belong to Claude Code, not to the board. When Claude Code removes old plans or transcripts, the board loses them.

Current state, checked on this machine:

- `~/.claude/plans/` contains 0 files.
- `tasks.json` has 130 tasks. 59 of them set `planFile`. All 59 point to files that no longer exist.
- `~/.claude/projects/` has 18 transcript files. None are older than 30 days, so retention has not yet removed any. I did not find a `cleanupPeriodDays` key in the local settings files, so I have not confirmed the retention setting that applies here. Check managed settings or the Claude Code docs before relying on a specific number.

Root causes:

1. **Single source of truth is outside the project.** The board does not own its data.
2. **Links are by filename.** A plan has no identity beyond its name, and nothing records its content at link time.
3. **Session identity is inferred.** The board guesses the session by directory overlap, so the link can change over time and can be wrong.
4. **Reads and writes are mixed into one 222-line handler and one 900-line HTML file.** That makes it harder to add durable storage safely.

## 2. Target principle

**The board keeps its own copy of anything it depends on, at the moment it links it.** Claude Code's directories become a source to read from, not the place the board depends on.

## 3. Options

### A. Keep the current design and add error messages

Show "plan missing" when the file is gone. This is honest, but it does not recover anything.

Verdict: not sufficient on its own.

### B. Snapshot store with capture hooks (recommended)

- Create a board-owned store, for example `data/plans/<id>.md` plus a metadata record per snapshot. Keep it in the repo or in `~/.claude/kanban/`. Do not put it in `~/.claude/plans/`.
- Capture plans with a Claude Code hook. A `Stop` or `SessionEnd` hook runs a small script that copies any new or changed file in `~/.claude/plans/` into the store. Store the SHA-256 of the content so duplicates are skipped and later edits create a new snapshot.
- When a task links a plan, copy its current content and record the source session id at that moment.
- The board reads from the snapshot first. The live plan file is only a fallback.
- Session: store the session id when it is linked, plus `projectDir` and start time. If `claude --resume <id>` fails because the transcript was removed, the board can write the stored plan back into `~/.claude/plans/` and start a new session that points at it.
- One-time backfill: scan the 18 transcripts that still exist for `ExitPlanMode` tool inputs. These should contain plan text. This may recover some of the 59 missing plans. Treat it as a spike, because it depends on which transcripts still exist.

Pros: small change, works with the current stack, fixes the loss going forward, no new runtime.
Cons: capture depends on the hook running. A hook that fails silently loses data, so it needs logging and a periodic sweep.

### C. MCP server over plans and sessions

An MCP server would expose tools such as `list_plans`, `get_plan`, `search_sessions` and `get_session_summary`. Claude sessions and the board could both call it. It could also let Claude create or link tasks itself.

What it solves: a standard, read-only interface to history, and a way for Claude to query its own past plans.

What it does not solve: durability. An MCP server that reads `~/.claude/plans/` has the same data-loss problem. The server only helps if it also writes to the snapshot store (option B). MCP is pull-based, so Claude must call it. Nothing gets captured unless a session asks for it.

Verdict: a good second step, on top of B. Build it as a thin layer over the same store module. Do not use it as the capture mechanism.

### D. SQLite as the store (instead of `tasks.json` and plan files)

Tables for `tasks`, `plans` (with content and hash), `sessions` and `plan_links`. Gains: atomic writes, real queries, no whole-file rewrite on every save, and FTS for searching plans.

Cost: a schema and a migration from `tasks.json`.

Verdict: worth doing once there is more than one writer (hook, server, MCP). Not needed to stop data loss, because a JSON file plus a plans directory is enough. Move to SQLite when option C is built.

### E. Full frontend and backend rewrite (SPA, desktop app)

A Vite/React front end, or an Electron or Tauri app, with a typed API.

Verdict: not justified. The current UI works. A rewrite would not fix the data-loss problem, which is about where data lives.

### F. Claude Code plugin or mod

Put the board in Claude Code itself as a panel, with hooks for capture. This avoids a separate server and removes the port-and-browser setup.

Verdict: a valid longer-term direction, since the hooks are needed either way. Not the first step. Evaluate it after B is in place.

## 4. Recommendation

Do B now, then C, then D if the data layer grows. Skip E. Keep F as a later option.

Order of work:

1. **Stop the loss (small, do first).** Add the capture hook and a sweep that copies `~/.claude/plans/` into `data/plans/` with a hash and a timestamp. Log failures to a file.
2. **Snapshot on link.** When a task links a plan, copy the content and record the session id. Add `planSnapshot` to the task shape. Migrate existing tasks: for each linked plan that still exists, snapshot it. For the rest, mark the link as `lost` and show that in the UI.
3. **Read from the snapshot.** `/api/plan` returns the snapshot. The plan viewer shows a "snapshot from <date>" note. Keep the live file as a fallback only.
4. **Session fallback.** On open, if the stored session id has no transcript, restore the plan into `~/.claude/plans/` and start a fresh session. Stop re-guessing the session by directory.
5. **Split the code.** Move storage into `store.py`, HTTP routing into `server.py`, and the browser JavaScript into `app.js` with CSS in `styles.css`. Keep `index.html` as the shell. Do this after step 3, so each change is small and testable on its own.
6. **MCP (optional).** Expose the store through a read-only MCP server with `list_plans`, `get_plan` and `get_session`. Add write tools only if they are needed.

## 5. Other changes to make along the way

- **Remove the arbitrary-command endpoint.** `/api/open-terminal` runs whatever command the browser sends, and the server has no origin or content-type check (see `AGENTS.md`). Replace it with an action keyed by task id, where the server builds the command from stored fields. Add an origin check to all POST endpoints.
- **Validate `tasks.json` writes.** `POST /api/tasks` accepts any JSON. Validate the schema server-side, and write atomically (temp file plus rename).
- **Name the plan link honestly.** Rename `planFile` to `planRef` and store the snapshot id alongside it, so the old field still works during migration.
- **Drop heuristic session matching** once stored session ids exist.

## 6. Data model after step 2

```json
{
  "id": "uuid",
  "title": "...",
  "column": "todo",
  "planRef": "name-from-claude.md",
  "planSnapshot": {
    "id": "sha256-prefix",
    "capturedAt": "2026-11-02T10:00:00Z",
    "status": "ok"
  },
  "session": {
    "id": "session-uuid",
    "projectDir": "/abs/path",
    "startedAt": "2026-11-01T09:00:00Z"
  }
}
```

`planSnapshot.status` is `ok` or `lost`. Snapshot content lives in `data/plans/<sha>.md`.

## 7. Risks and open questions

- **Hook reliability.** If the capture hook is disabled or fails, the sweep is the backstop. Confirm which hook events fire in the current Claude Code version before relying on them.
- **Secrets in plans.** Plans may contain paths, names or other sensitive text. Keep the snapshot store out of git (`data/` in `.gitignore`), and do not copy plan text into commits, PRs or logs.
- **Backfill scope.** Recovery from transcripts only works for transcripts that still exist. Decide whether partial recovery is acceptable.
- **Where the store lives.** In the repo is simpler to back up but mixes personal data with code. `~/.claude/kanban/` keeps data out of the repo but needs its own backup. Pick one.

## 8. Decisions

Answered:

1. **Store location:** `data/` in this repo, git-ignored.
2. **MCP:** not used.
3. **Startup:** macOS LaunchAgent (`scripts/install-launchd.sh`).

Still open:

- Stay a local server with a browser UI, or move into a Claude Code plugin later? Current plan: stay as a browser UI.

## 9. What was built (step 1 and part of step 2)

- `scripts/capture-plans.sh`: Bash sweep of `~/.claude/plans/` into `data/plans/`. Registered as a `Stop` and `SessionEnd` hook.
- `server.py`: `snapshot_plan()`, `latest_snapshot()`, `read_plan()`. `GET /api/plan` falls back to the latest snapshot. New `POST /api/plan/capture`. Board saves snapshot immediately.
- `index.html`: linking a plan calls capture. A snapshot shown from a plan with no live file is labelled as a saved copy and cannot be edited.
- `.gitignore`: `data/` and `__pycache__/`.
- LaunchAgent: `scripts/install-launchd.sh` and `scripts/uninstall-launchd.sh`.

Differences from the plan in sections 3 and 4:

- **No pinning to a snapshot hash at link time.** A task still stores only `planRef` (filename). The viewer shows the live file, or the latest snapshot for that name. The sweep runs every time a Claude turn ends, so the latest snapshot is almost always the newest version. Pinning would have shown stale content, so it was dropped. `planSnapshot` is not part of the task shape.
- **Session fallback (step 4) is not built.** Session lookup still uses the existing heuristic.
- **Backfill is not built.** The 59 linked plans are already gone from `~/.claude/plans/`, and no snapshots exist for them. Recovering them from transcripts is a separate spike.

Known gaps:

- The `Stop` and `SessionEnd` hook registration lives in `~/.claude/settings.json`, which was not changed in this session. See the hook setup note in `AGENTS.md`.
- Capture happens only when a Claude turn ends or a session ends. A plan that is created and then deleted within a single turn is not captured.

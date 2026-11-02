#!/usr/bin/env bash
# Copy Claude Code plans into the board's snapshot store (data/plans/).
#
# Runs from Claude Code Stop and SessionEnd hooks. Idempotent: a plan whose
# content is already stored is skipped. Always exits 0 so it can never break
# a Claude Code session; failures are written to data/logs/capture.log.
#
# Overrides for testing: KANBAN_PLANS_DIR, KANBAN_DATA_DIR.

set -u

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLANS_DIR="${KANBAN_PLANS_DIR:-$HOME/.claude/plans}"
DATA_DIR="${KANBAN_DATA_DIR:-$REPO_DIR/data}"
STORE_DIR="$DATA_DIR/plans"
LOG_FILE="$DATA_DIR/logs/capture.log"

mkdir -p "$STORE_DIR" "$(dirname "$LOG_FILE")" 2>/dev/null || exit 0

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG_FILE" 2>/dev/null
}

shopt -s nullglob
for plan in "$PLANS_DIR"/*.md; do
  name="$(basename "$plan")"
  tmp="$STORE_DIR/.tmp-$$-$name"

  # Copy first, then hash the copy, so the stored name always matches its bytes.
  if ! cp "$plan" "$tmp" 2>/dev/null; then
    log "copy failed: $name"
    rm -f "$tmp"
    continue
  fi
  hash="$(shasum -a 256 "$tmp" 2>/dev/null | cut -c1-16)"
  if [[ -z "$hash" ]]; then
    log "hash failed: $name"
    rm -f "$tmp"
    continue
  fi

  # The .json sidecar is the commit marker: it is written last.
  if [[ -e "$STORE_DIR/$hash.json" ]]; then
    rm -f "$tmp"
    continue
  fi

  if ! mv "$tmp" "$STORE_DIR/$hash.md" 2>/dev/null; then
    log "store failed: $name"
    rm -f "$tmp"
    continue
  fi
  captured_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"hash":"%s","name":"%s","capturedAt":"%s","source":"hook"}\n' \
    "$hash" "$name" "$captured_at" >"$STORE_DIR/$hash.json.tmp" \
    && mv "$STORE_DIR/$hash.json.tmp" "$STORE_DIR/$hash.json"
  log "captured $name as $hash"
done

exit 0

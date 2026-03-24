#!/usr/bin/env python3
"""
Autohealth Blocker — PreToolUse interventionist hook for Claude Code.

Conservative blocking: only denies clearly pathological patterns.
Currently blocks:
  - 5th+ consecutive identical tool call (except Bash)
  - 3rd+ repetition of a length-2 or length-3 cycle

Reads session state written by autohealth-monitor.py.

Installation:
  1. Symlink or copy to ~/.claude/hooks/autohealth-blocker.py
  2. Add to ~/.claude/settings.json under hooks.PreToolUse
  3. Requires autohealth-monitor.py to be active (shares session state)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

SESSION_DIR = Path("/tmp")
_CLAUDE_HOME = Path.home() / ".claude"
TRACE_LOG = _CLAUDE_HOME / "hooks" / "autohealth-trace.jsonl"

# Tools that naturally repeat and should not be blocked on consecutive runs
REPEAT_EXEMPT = frozenset({"Bash"})

# Minimum repetitions before the blocker fires
CONSECUTIVE_BLOCK_THRESHOLD = 5
CYCLE_BLOCK_REPS = 3


def _trace(event: str, session_id: str, **fields: object) -> None:
    try:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            "source": "blocker",
            "session": session_id[:12],
            **fields,
        }
        with open(TRACE_LOG, "a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _session_path(session_id: str) -> Path:
    return SESSION_DIR / f"autohealth-session-{session_id}.json"


def _load_tools(session_id: str) -> list[str]:
    path = _session_path(session_id)
    if not path.exists():
        return []
    try:
        state = json.loads(path.read_text())
        return state.get("tools", [])
    except Exception:
        return []


def _check_consecutive(proposed: str, history: list[str]) -> int:
    """Return consecutive run length of proposed tool at end of history."""
    count = 1
    for t in reversed(history):
        if t == proposed:
            count += 1
        else:
            break
    return count


def _check_cycle_reps(proposed: str, history: list[str]) -> tuple[tuple[str, ...] | None, int]:
    """
    Check if appending `proposed` would complete a 3rd+ repetition of a
    length-2 or length-3 pattern. Returns (pattern, reps) or (None, 0).
    """
    candidate = history + [proposed]
    for length in (2, 3):
        if len(candidate) < length * CYCLE_BLOCK_REPS:
            continue
        tail = candidate[-(length * CYCLE_BLOCK_REPS):]
        pattern = tuple(tail[:length])
        reps = 0
        j = 0
        while j + length <= len(tail) and tuple(tail[j : j + length]) == pattern:
            reps += 1
            j += length
        if reps >= CYCLE_BLOCK_REPS:
            return pattern, reps
    return None, 0


def _sanitize_session_id(raw: str) -> str:
    clean = raw.replace("/", "").replace("\\", "").replace("..", "")
    return clean[:64] if clean else "default"


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    tool_name: str = payload.get("tool_name") or payload.get("toolName") or ""
    session_id = _sanitize_session_id(
        payload.get("session_id") or payload.get("sessionId") or "default"
    )

    if not tool_name:
        sys.exit(0)

    history = _load_tools(session_id)

    # Check 1: consecutive same-tool run (exempt Bash)
    if tool_name not in REPEAT_EXEMPT:
        consecutive = _check_consecutive(tool_name, history)
        if consecutive >= CONSECUTIVE_BLOCK_THRESHOLD:
            reason = (
                f"Blocked: {tool_name} called {consecutive} times consecutively. "
                "Break the pattern — try a different approach or tool."
            )
            _trace("block", session_id, tool=tool_name,
                   trigger="consecutive", count=consecutive, reason=reason)
            print(json.dumps({"permissionDecision": "deny", "reason": reason}))
            sys.exit(0)

    # Check 2: short cycle about to hit 3rd repetition
    pattern, reps = _check_cycle_reps(tool_name, history)
    if pattern is not None:
        pattern_str = "\u2192".join(pattern)
        reason = (
            f"Blocked: pattern {pattern_str} has repeated {reps} times. "
            "This loop is not converging — reassess your approach."
        )
        _trace("block", session_id, tool=tool_name,
               trigger="cycle", pattern=list(pattern), reps=reps, reason=reason)
        print(json.dumps({"permissionDecision": "deny", "reason": reason}))
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Autohealth Monitor — PostToolUse advisory hook for Claude Code.

Computes a composite health score from 6 signals extracted from the hook
payload. When health drops below threshold, injects a warning into the
conversation via additionalContext.

Signals:
  - compression ratio (zlib)  — repetitive tool sequences
  - file revisit frequency    — same file touched repeatedly
  - bash error rate           — commands failing
  - tool diversity (entropy)  — stuck on one tool type
  - blind retry rate          — retrying failed calls without changes
  - null edit rate            — edits that didn't change file content

Installation:
  1. Symlink or copy to ~/.claude/hooks/autohealth-monitor.py
  2. Add to ~/.claude/settings.json under hooks.PostToolUse
  3. No model file or training required — all signals computed directly

Configuration (env vars):
  AUTOHEALTH_THRESHOLD  — health below this triggers warnings (default: 0.45)
  AUTOHEALTH_COOLDOWN   — min steps between warnings (default: 10)
  AUTOHEALTH_INTERVAL   — analyze every N calls (default: 1 = every call)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
import zlib
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CLAUDE_HOME = Path.home() / ".claude"
_HOOKS_DIR = _CLAUDE_HOME / "hooks"
TRACE_LOG = _HOOKS_DIR / "autohealth-trace.jsonl"
SESSION_DIR = Path("/tmp")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW = 12
HEALTH_THRESHOLD = float(os.environ.get("AUTOHEALTH_THRESHOLD", "0.45"))
MIN_STEPS_BETWEEN_WARNINGS = int(os.environ.get("AUTOHEALTH_COOLDOWN", "10"))
ANALYSIS_INTERVAL = int(os.environ.get("AUTOHEALTH_INTERVAL", "1"))


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


def _new_state() -> dict:
    return {
        "tools": [],
        "recent_files": [],
        "bash_errors": [],
        "cwd": "",
        "warnings_issued": 0,
        "last_warning_step": 0,
        "last_call": None,
        "blind_retries": [],
        "file_content_hashes": {},
        "null_edits": [],
    }


def _session_path(session_id: str) -> Path:
    return SESSION_DIR / f"autohealth-session-{session_id}.json"


def _sanitize_session_id(raw: str) -> str:
    clean = raw.replace("/", "").replace("\\", "").replace("..", "")
    return clean[:64] if clean else "default"


def _load_session(session_id: str) -> dict:
    path = _session_path(session_id)
    if not path.exists():
        return _new_state()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data.get("tools"), list):
            return _new_state()
        defaults = _new_state()
        for key, val in defaults.items():
            if key not in data:
                data[key] = val
        return data
    except (json.JSONDecodeError, OSError):
        return _new_state()


def _save_session(session_id: str, state: dict) -> None:
    try:
        _session_path(session_id).write_text(json.dumps(state))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


def _hash_args(tool_name: str, tool_input: dict) -> str:
    """Hash the identity-relevant args for a tool call."""
    if tool_name == "Bash":
        key = tool_input.get("command", "")
    elif tool_name in ("Read", "Write", "Edit"):
        key = tool_input.get("file_path", "")
    elif tool_name in ("Grep", "Glob"):
        key = f"{tool_input.get('pattern', '')}:{tool_input.get('path', '')}"
    else:
        key = json.dumps(tool_input, sort_keys=True, default=str)
    return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:12]


def _hash_result(result: str) -> str:
    """Hash tool result for content-change detection."""
    return hashlib.md5(result.encode(), usedforsecurity=False).hexdigest()[:12]


def _is_tool_failure(tool_name: str, tool_result: str) -> bool:
    """Detect whether a tool call failed based on its result."""
    r = tool_result[:500].lower()
    if tool_name == "Bash":
        return any(s in r for s in [
            "error:", "traceback", "command not found",
            "permission denied", "no such file", "fatal:",
        ])
    if tool_name == "Edit":
        return any(s in r for s in [
            "not unique", "not found", "no match",
        ])
    if tool_name == "Read":
        return any(s in r for s in [
            "not found", "permission denied", "no such file",
        ])
    return False


def _normalized_entropy(tools: list[str]) -> float:
    if len(tools) < 2:
        return 1.0
    counts = Counter(tools)
    n = len(tools)
    entropy = -sum((c / n) * math.log2(c / n) for c in counts.values())
    max_entropy = math.log2(len(counts)) if len(counts) > 1 else 1.0
    return min(1.0, entropy / max_entropy) if max_entropy > 0 else 0.0


def _compute_health(state: dict) -> tuple[float, str, dict[str, float]]:
    """Returns (health_score, dominant_signal_name, signals_dict)."""
    tools: list[str] = state["tools"]
    window_tools = tools[-WINDOW:]

    if len(window_tools) < 4:
        return 1.0, "warmup", {}

    signals: dict[str, float] = {}

    # 1. Compression ratio — low = repetitive = bad
    seq = ":".join(window_tools).encode()
    compressed = zlib.compress(seq)
    raw_ratio = len(compressed) / max(1, len(seq))
    signals["compression"] = min(1.0, max(0.0, raw_ratio))

    # 2. File revisit frequency
    recent_files: list[str] = state["recent_files"][-WINDOW:]
    if recent_files:
        file_counts = Counter(recent_files)
        max_revisits = max(file_counts.values())
        signals["file_revisit"] = max(0.0, 1.0 - max(0.0, max_revisits - 2) / 5)
    else:
        signals["file_revisit"] = 1.0

    # 3. Bash error rate
    recent_errors: list[bool] = state["bash_errors"][-WINDOW:]
    if recent_errors:
        error_rate = sum(1 for e in recent_errors if e) / len(recent_errors)
        signals["error_rate"] = 1.0 - error_rate
    else:
        signals["error_rate"] = 1.0

    # 4. Tool diversity (Shannon entropy)
    signals["diversity"] = _normalized_entropy(window_tools)

    # 5. Blind retry rate
    recent_retries: list[bool] = state.get("blind_retries", [])[-WINDOW:]
    if recent_retries:
        retry_rate = sum(1 for r in recent_retries if r) / len(recent_retries)
        signals["blind_retry"] = 1.0 - retry_rate
    else:
        signals["blind_retry"] = 1.0

    # 6. Null edit rate
    recent_nulls: list[bool] = state.get("null_edits", [])[-WINDOW:]
    if recent_nulls:
        null_rate = sum(1 for n in recent_nulls if n) / len(recent_nulls)
        signals["null_edit"] = 1.0 - null_rate
    else:
        signals["null_edit"] = 1.0

    weights = {
        "compression": 0.20,
        "file_revisit": 0.20,
        "error_rate": 0.20,
        "diversity": 0.15,
        "blind_retry": 0.15,
        "null_edit": 0.10,
    }

    health = sum(weights[k] * signals[k] for k in weights)
    worst = min(signals, key=lambda k: signals[k])

    return health, worst, signals


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


def _trace(event: str, session_id: str, step: int, **fields: object) -> None:
    try:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            "session": session_id[:12],
            "step": step,
            **fields,
        }
        with open(TRACE_LOG, "a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    tool_name: str = payload.get("tool_name") or payload.get("toolName") or ""
    session_id = _sanitize_session_id(
        payload.get("session_id") or payload.get("sessionId") or "default"
    )
    cwd: str = payload.get("cwd", "")

    if not tool_name:
        sys.exit(0)

    state = _load_session(session_id)
    state["tools"].append(tool_name)
    if cwd and not state.get("cwd"):
        state["cwd"] = cwd

    # Extract tool input/result
    tool_input = payload.get("tool_input") or {}
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            tool_input = {}

    tool_result = str(payload.get("tool_result", "") or "")
    file_path = ""

    if tool_name in ("Read", "Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            state["recent_files"].append(file_path)

    if tool_name == "Bash":
        is_error = any(s in tool_result[:500].lower() for s in [
            "error:", "traceback", "command not found",
            "permission denied", "no such file", "fatal:",
            "failed", "exception",
        ])
        state["bash_errors"].append(is_error)

    # --- Blind retry detection ---
    args_hash = _hash_args(tool_name, tool_input)
    failed = _is_tool_failure(tool_name, tool_result)
    last_call = state.get("last_call")
    is_blind_retry = (
        last_call is not None
        and last_call.get("failed")
        and last_call.get("tool") == tool_name
        and last_call.get("args_hash") == args_hash
    )
    state["blind_retries"].append(is_blind_retry)
    state["last_call"] = {"tool": tool_name, "args_hash": args_hash, "failed": failed}

    # --- Null edit detection ---
    content_hashes: dict[str, str] = state.get("file_content_hashes", {})
    is_null_edit = False

    if tool_name == "Read" and file_path and tool_result:
        current_hash = _hash_result(tool_result)
        prev_hash = content_hashes.get(file_path)

        if prev_hash is not None and current_hash == prev_hash:
            recent = list(zip(state["tools"][-6:], state["recent_files"][-6:]))
            had_intervening_edit = any(
                t == "Edit" and f == file_path for t, f in recent
            )
            if had_intervening_edit:
                is_null_edit = True

        content_hashes[file_path] = current_hash

    state["file_content_hashes"] = content_hashes
    state["null_edits"].append(is_null_edit)

    tools: list[str] = state["tools"]
    step = len(tools)

    if step % ANALYSIS_INTERVAL != 0:
        _save_session(session_id, state)
        sys.exit(0)

    health, worst_signal, signals = _compute_health(state)

    if not signals:
        _save_session(session_id, state)
        sys.exit(0)

    _trace("checkpoint", session_id, step,
        tool=tool_name,
        health=round(health, 3),
        signals={k: round(v, 3) for k, v in signals.items()},
        dominant=worst_signal,
        cwd=cwd or None,
    )

    if health >= HEALTH_THRESHOLD:
        _save_session(session_id, state)
        sys.exit(0)

    last_warn = state.get("last_warning_step", 0)
    if step - last_warn < MIN_STEPS_BETWEEN_WARNINGS:
        _trace("suppressed", session_id, step, reason="cooldown", health=round(health, 3))
        _save_session(session_id, state)
        sys.exit(0)

    # Build human-readable detail
    recent_files: list[str] = state["recent_files"][-WINDOW:]
    file_counts = Counter(recent_files) if recent_files else Counter()
    most_visited_file = max(file_counts, key=lambda k: file_counts[k]) if file_counts else ""
    max_revisits = file_counts[most_visited_file] if most_visited_file else 0

    recent_errors: list[bool] = state["bash_errors"][-WINDOW:]
    error_count = sum(1 for e in recent_errors if e)
    total_bash = len(recent_errors)

    recent_retries: list[bool] = state.get("blind_retries", [])[-WINDOW:]
    retry_count = sum(1 for r in recent_retries if r)

    recent_nulls: list[bool] = state.get("null_edits", [])[-WINDOW:]
    null_count = sum(1 for n in recent_nulls if n)

    detail_map = {
        "compression": f"repetitive sequence (ratio={signals['compression']:.2f})",
        "file_revisit": (
            f"file revisit ({most_visited_file} touched {max_revisits}\u00d7)"
            if recent_files else "file revisit"
        ),
        "error_rate": f"error rate ({error_count}/{total_bash} Bash calls failed)",
        "diversity": f"low tool diversity (entropy={signals['diversity']:.2f})",
        "blind_retry": f"blind retry ({retry_count} retries of failed calls without changes)",
        "null_edit": f"null edits ({null_count} edits had no effect on file content)",
    }
    detail = detail_map.get(worst_signal, worst_signal)

    message = f"autohealth: {detail}, health={health:.2f}. Consider reassessing approach."

    state["warnings_issued"] = state.get("warnings_issued", 0) + 1
    state["last_warning_step"] = step

    _trace("warning", session_id, step,
        tool=tool_name,
        health=round(health, 3),
        signals={k: round(v, 3) for k, v in signals.items()},
        dominant=worst_signal,
        message=message,
        warnings_total=state["warnings_issued"],
    )

    _save_session(session_id, state)

    print(json.dumps({"additionalContext": message}))
    sys.exit(0)


if __name__ == "__main__":
    main()

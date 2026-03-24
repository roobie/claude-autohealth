# claude-autohealth

> **Alpha** — This is early-stage, experimental software. Signal weights, thresholds, and detection heuristics are based on limited real-world data and will change. YMMV. Feedback and contributions welcome.

Behavioral health monitor for Claude Code agents. Detects pathological patterns (loops, errors, stuck edits) in real-time via PostToolUse hooks and injects advisory feedback into the conversation.

See [blog post at bjro.dev](https://bjro.dev/claude-autohealth)

## How it works

The monitor runs as a Claude Code hook on every tool call. It extracts signals from the payload and computes a composite health score:

| Signal | Weight | What it detects |
|---|---|---|
| Compression ratio (zlib) | 0.20 | Repetitive tool sequences |
| File revisit frequency | 0.20 | Same file touched repeatedly |
| Bash error rate | 0.20 | Commands failing repeatedly |
| Tool diversity (entropy) | 0.15 | Stuck on one tool type |
| Blind retry rate | 0.15 | Retrying failed calls without changes |
| Null edit rate | 0.10 | Edits that didn't change file content |

When health drops below threshold, a warning is injected into the conversation via `additionalContext`, telling the agent which signal is problematic.

No trained model, no external dependencies, stdlib-only Python.

## Installation

### 1. Copy or symlink the hooks

```bash
# Symlink (recommended — easy to update)
ln -s /path/to/claude-autohealth/hooks/autohealth-monitor.py ~/.claude/hooks/autohealth-monitor.py

# Or copy
cp hooks/autohealth-monitor.py ~/.claude/hooks/
```

### 2. Register the hook

Add to `~/.claude/settings.json` (or `.claude/settings.json` in a project):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/autohealth-monitor.py",
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```

### 3. (Optional) Enable the blocker

The blocker is a PreToolUse hook that denies clearly pathological tool calls (5+ consecutive same tool, 3+ cycle repetitions). More aggressive than the monitor.

```bash
ln -s /path/to/claude-autohealth/hooks/autohealth-blocker.py ~/.claude/hooks/autohealth-blocker.py
```

Add to settings:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/autohealth-blocker.py",
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```

## Configuration

All configuration via environment variables:

| Variable | Default | Description |
|---|---|---|
| `AUTOHEALTH_THRESHOLD` | `0.45` | Health below this triggers warnings |
| `AUTOHEALTH_COOLDOWN` | `10` | Minimum steps between warnings |
| `AUTOHEALTH_INTERVAL` | `1` | Analyze every N tool calls (1 = every call) |

Set in your shell, in `mise.toml`, or in your project's environment config.

## Dashboard

A Textual TUI for visualizing trace data across sessions. Requires `textual`:

```bash
uv run autohealth-dashboard.py
```

Shows:
- Session list with health color coding and project names
- Health timeline sparkline per session
- Event detail table with per-signal breakdown
- Aggregate stats

Keys: `q` quit, `r` reload, arrows to navigate sessions.

## Trace log

All events are logged to `~/.claude/hooks/autohealth-trace.jsonl`:

```json
{"ts":"2026-03-24T01:10:25Z","event":"checkpoint","session":"dd309388-635","step":5,"tool":"Bash","health":0.957,"signals":{"compression":0.875,"file_revisit":1.0,"error_rate":1.0,"diversity":0.971,"blind_retry":1.0,"null_edit":1.0},"dominant":"compression","cwd":"/home/user/project"}
```

Events: `checkpoint` (analysis), `warning` (advisory injected), `suppressed` (warning held back by cooldown), `block` (blocker denied a tool call).

## Files

```
hooks/
  autohealth-monitor.py   — PostToolUse advisory hook (the main thing)
  autohealth-blocker.py   — PreToolUse blocker (optional, conservative)
autohealth-dashboard.py   — Textual TUI dashboard
```

## Requirements

- Python 3.12+
- Claude Code with hooks support
- No external dependencies for the hooks (stdlib only)
- `textual>=1.0` for the dashboard (`uv run` handles this automatically)

## License

Apache-2.0

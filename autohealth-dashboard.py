#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["textual>=1.0"]
# ///
"""
Autohealth Dashboard — TUI for visualizing agent health trace data.

Reads from ~/.claude/hooks/autohealth-trace.jsonl and displays:
  - Session list with health color coding
  - Health timeline sparkline per session
  - Event detail table with per-signal breakdown
  - Aggregate stats bar

Usage:
  uv run autohealth-dashboard.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListView,
    ListItem,
    Sparkline,
    Static,
)

TRACE_LOG = Path.home() / ".claude" / "hooks" / "autohealth-trace.jsonl"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TraceEvent:
    ts: str
    event: str
    session: str
    step: int
    tool: str | None
    health: float | None
    signals: dict[str, float] | None
    dominant: str | None
    reason: str | None
    message: str | None
    warnings_total: int | None
    cwd: str | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TraceEvent:
        return cls(
            ts=d.get("ts", ""),
            event=d.get("event", ""),
            session=d.get("session", ""),
            step=d.get("step", 0),
            tool=d.get("tool"),
            health=d.get("health"),
            signals=d.get("signals"),
            dominant=d.get("dominant"),
            reason=d.get("reason"),
            message=d.get("message"),
            warnings_total=d.get("warnings_total"),
            cwd=d.get("cwd"),
            raw=d,
        )


@dataclass
class SessionSummary:
    session_id: str
    events: list[TraceEvent] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.session_id[:8]

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def warning_count(self) -> int:
        return sum(1 for e in self.events if e.event == "warning")

    @property
    def block_count(self) -> int:
        return sum(1 for e in self.events if e.event == "block")

    @property
    def avg_health(self) -> float | None:
        healths = [e.health for e in self.events if e.health is not None]
        return sum(healths) / len(healths) if healths else None

    @property
    def latest_ts(self) -> str:
        return self.events[-1].ts if self.events else ""

    @property
    def checkpoints(self) -> list[TraceEvent]:
        return [e for e in self.events if e.event == "checkpoint"]

    @property
    def cwd(self) -> str:
        for e in self.events:
            if e.cwd:
                return e.cwd
        return ""

    @property
    def project_name(self) -> str:
        c = self.cwd
        if c:
            return Path(c).name
        c = self._cwd_from_session_state()
        if c:
            return Path(c).name
        return "?"

    def _cwd_from_session_state(self) -> str:
        """Try to read cwd from the /tmp session state file."""
        path = Path("/tmp") / f"autohealth-session-{self.session_id}.json"
        if not path.exists():
            for candidate in Path("/tmp").glob(f"autohealth-session-{self.session_id}*.json"):
                path = candidate
                break
            else:
                return ""
        try:
            data = json.loads(path.read_text())
            return data.get("cwd", "")
        except (json.JSONDecodeError, OSError):
            return ""

    def health_color(self) -> str:
        h = self.avg_health
        if h is None:
            return "white"
        if h >= 0.65:
            return "green"
        if h >= 0.45:
            return "yellow"
        return "red"


def load_trace() -> tuple[list[SessionSummary], str | None]:
    if not TRACE_LOG.exists():
        return [], "No trace log found at ~/.claude/hooks/autohealth-trace.jsonl"

    sessions: dict[str, SessionSummary] = {}
    try:
        text = TRACE_LOG.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"Cannot read trace log: {exc}"

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        evt = TraceEvent.from_dict(d)
        if evt.session not in sessions:
            sessions[evt.session] = SessionSummary(session_id=evt.session)
        sessions[evt.session].events.append(evt)

    sorted_sessions = sorted(
        sessions.values(),
        key=lambda s: s.latest_ts,
        reverse=True,
    )
    return sorted_sessions, None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fmt_time(ts: str) -> str:
    if not ts:
        return ""
    try:
        return ts[11:19]
    except IndexError:
        return ts


def health_markup(h: float | None) -> str:
    if h is None:
        return "\u2014"
    if h >= 0.65:
        return f"[green]{h:.3f}[/green]"
    if h >= 0.45:
        return f"[yellow]{h:.3f}[/yellow]"
    return f"[red]{h:.3f}[/red]"


def event_style(event: str) -> str:
    mapping = {
        "checkpoint": "dim",
        "warning": "yellow",
        "suppressed": "bright_black",
        "block": "red",
    }
    return mapping.get(event, "")


def fmt_signal(signals: dict[str, float] | None, key: str) -> str:
    if not signals or key not in signals:
        return "\u2014"
    v = signals[key]
    if v >= 0.65:
        return f"[green]{v:.2f}[/green]"
    if v >= 0.45:
        return f"[yellow]{v:.2f}[/yellow]"
    return f"[red]{v:.2f}[/red]"


# ---------------------------------------------------------------------------
# Aggregate stats bar
# ---------------------------------------------------------------------------


class StatsBar(Static):
    DEFAULT_CSS = """
    StatsBar {
        height: 3;
        background: $boost;
        color: $text;
        padding: 0 2;
        content-align: left middle;
    }
    """

    def update_stats(self, sessions: list[SessionSummary]) -> None:
        total_events = sum(s.event_count for s in sessions)
        total_checkpoints = sum(len(s.checkpoints) for s in sessions)
        total_warnings = sum(s.warning_count for s in sessions)
        total_blocks = sum(s.block_count for s in sessions)
        all_healths = [
            e.health
            for s in sessions
            for e in s.events
            if e.health is not None and e.event == "checkpoint"
        ]
        avg_h = sum(all_healths) / len(all_healths) if all_healths else None
        avg_str = f"{avg_h:.3f}" if avg_h is not None else "\u2014"
        self.update(
            f"Total events: {total_events}  "
            f"Checkpoints: {total_checkpoints}  "
            f"Warnings: [yellow]{total_warnings}[/yellow]  "
            f"Blocks: [red]{total_blocks}[/red]  "
            f"Avg health: {avg_str}  "
            f"Sessions: {len(sessions)}"
        )


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


class AutohealthDashboard(App[None]):
    TITLE = "Autohealth Dashboard"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Reload"),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }

    #main-area {
        layout: horizontal;
        height: 1fr;
    }

    #session-panel {
        width: 34;
        border: solid $primary;
        height: 100%;
    }

    #session-panel Label {
        background: $boost;
        width: 100%;
        padding: 0 1;
        color: $text-muted;
        text-style: bold;
    }

    #session-list {
        height: 1fr;
    }

    #session-list > ListItem {
        padding: 0 1;
    }

    #right-panel {
        height: 100%;
        width: 1fr;
        border: solid $primary;
    }

    #right-panel Label.section-title {
        background: $boost;
        width: 100%;
        padding: 0 1;
        color: $text-muted;
        text-style: bold;
    }

    #sparkline-container {
        height: 6;
        padding: 0 1;
    }

    #health-sparkline {
        height: 5;
    }

    #detail-table {
        height: 1fr;
    }

    StatsBar {
        dock: bottom;
        height: 3;
    }
    """

    sessions: reactive[list[SessionSummary]] = reactive([], recompose=False)
    selected_index: reactive[int] = reactive(0)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-area"):
            with Vertical(id="session-panel"):
                yield Label("Sessions")
                yield ListView(id="session-list")
            with Vertical(id="right-panel"):
                yield Label("Health Timeline", classes="section-title")
                with Vertical(id="sparkline-container"):
                    yield Sparkline([], id="health-sparkline", summary_function=max)
                yield Label("Session Detail", classes="section-title", id="detail-title")
                yield DataTable(id="detail-table", zebra_stripes=True)
        yield StatsBar(id="stats-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_detail_table()
        self._load_and_render()

    def _setup_detail_table(self) -> None:
        table = self.query_one("#detail-table", DataTable)
        table.add_columns(
            "Time", "Step", "Event", "Tool", "Health",
            "Dominant", "Compr", "FileRev", "ErrRate", "Divers",
            "Retry", "NullEd",
        )

    def _load_and_render(self) -> None:
        sessions, error = load_trace()
        self.sessions = sessions

        lv = self.query_one("#session-list", ListView)
        lv.clear()

        if error or not sessions:
            lv.append(ListItem(Label(error or "No trace data")))
            self._clear_right_panel()
            stats = self.query_one("#stats-bar", StatsBar)
            stats.update_stats([])
            return

        for s in sessions:
            color = s.health_color()
            h_str = f"{s.avg_health:.3f}" if s.avg_health is not None else "\u2014\u2014"
            proj = s.project_name
            text = (
                f"[{color}]{s.short_id}[/{color}]"
                f" [bold]{proj}[/bold]"
                f"\n  [dim]{s.event_count}ev {s.warning_count}w[/dim]"
                f"  [{color}]{h_str}[/{color}]"
            )
            lv.append(ListItem(Label(text)))

        stats = self.query_one("#stats-bar", StatsBar)
        stats.update_stats(sessions)

        idx = min(self.selected_index, len(sessions) - 1)
        self._render_session(idx)
        lv.index = idx

    def _clear_right_panel(self) -> None:
        sparkline = self.query_one("#health-sparkline", Sparkline)
        sparkline.data = []
        table = self.query_one("#detail-table", DataTable)
        table.clear()

    def _render_session(self, idx: int) -> None:
        if not self.sessions or idx < 0 or idx >= len(self.sessions):
            self._clear_right_panel()
            return

        session = self.sessions[idx]

        title = self.query_one("#detail-title", Label)
        cwd_str = f"  [dim]{session.cwd}[/dim]" if session.cwd else ""
        title.update(f"Session {session.short_id}{cwd_str}")

        health_vals = [
            e.health
            for e in session.events
            if e.event == "checkpoint" and e.health is not None
        ]
        sparkline = self.query_one("#health-sparkline", Sparkline)
        sparkline.data = health_vals if health_vals else [0.0]

        table = self.query_one("#detail-table", DataTable)
        table.clear()

        for evt in session.events:
            time_str = fmt_time(evt.ts)
            step_str = str(evt.step)
            health_str = health_markup(evt.health)
            tool_str = evt.tool or "\u2014"

            has_signals = evt.event in ("checkpoint", "warning")

            if has_signals:
                dominant_str = evt.dominant or "\u2014"
                if evt.event == "warning":
                    dominant_str = f"[yellow]{dominant_str}[/yellow]"
                compression_str = fmt_signal(evt.signals, "compression")
                file_revisit_str = fmt_signal(evt.signals, "file_revisit")
                error_rate_str = fmt_signal(evt.signals, "error_rate")
                diversity_str = fmt_signal(evt.signals, "diversity")
                retry_str = fmt_signal(evt.signals, "blind_retry")
                null_edit_str = fmt_signal(evt.signals, "null_edit")
            else:
                dominant_str = evt.reason or "\u2014"
                compression_str = "\u2014"
                file_revisit_str = "\u2014"
                error_rate_str = "\u2014"
                diversity_str = "\u2014"
                retry_str = "\u2014"
                null_edit_str = "\u2014"

            style = event_style(evt.event)

            if style and not has_signals:
                row = tuple(
                    f"[{style}]{v}[/{style}]" for v in [
                        time_str, step_str, evt.event, tool_str,
                    ]
                ) + (
                    health_str, dominant_str,
                    compression_str, file_revisit_str,
                    error_rate_str, diversity_str,
                    retry_str, null_edit_str,
                )
            else:
                row = (
                    time_str, step_str, evt.event, tool_str,
                    health_str, dominant_str,
                    compression_str, file_revisit_str,
                    error_rate_str, diversity_str,
                    retry_str, null_edit_str,
                )

            table.add_row(*row)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        lv = self.query_one("#session-list", ListView)
        idx = lv.index
        if idx is not None and self.sessions:
            self.selected_index = idx
            self._render_session(idx)

    def action_reload(self) -> None:
        self._load_and_render()
        self.notify("Trace data reloaded")

    def action_quit(self) -> None:
        self.exit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = AutohealthDashboard()
    app.run()

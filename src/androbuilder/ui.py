"""Terminal UI helpers: logging, headers, timers, sizes, progress."""
from __future__ import annotations

import time
from datetime import timedelta

from rich.console import Console

console = Console()

# ── Global UI state ───────────────────────────────────────────────────────────
VERBOSE = False
NO_PROGRESS = False

_timings: list[tuple[str, float]] = []
_start = time.time()


def configure(*, verbose: bool = False, no_progress: bool = False) -> None:
    """Set global UI flags once from the CLI."""
    global VERBOSE, NO_PROGRESS
    VERBOSE = verbose
    NO_PROGRESS = no_progress


def log(msg: str) -> None:
    console.print(f"[cyan]⚙[/cyan] {msg}")


def ok(msg: str) -> None:
    console.print(f"  [green]✓[/green] {msg}")


def warn(msg: str) -> None:
    console.print(f"  [yellow]⚠[/yellow] {msg}")


def fail(msg: str) -> None:
    console.print(f"  [red]✗[/red] {msg}")
    raise SystemExit(1)


def vlog(msg: str) -> None:
    if VERBOSE:
        console.print(f"  [dim]┊[/dim] {msg}")


def header(text: str) -> None:
    console.print(f"\n[bold]{'═' * 44}[/bold]\n  [bold]{text}[/bold]\n[bold]{'─' * 44}[/bold]")


def subheader(text: str) -> None:
    console.print(f"\n  [cyan]▸[/cyan] [bold]{text}[/bold]")


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def fmt_duration(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))


class Timer:
    """Context manager that measures elapsed time and records it for the summary."""

    def __init__(self, label: str = "") -> None:
        self.label = label
        self.start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self) -> Timer:
        self.start = time.time()
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed = time.time() - self.start
        _timings.append((self.label, self.elapsed))
        if self.label:
            ok(f"{self.label} — {self.elapsed:.1f}s")


def timings() -> list[tuple[str, float]]:
    return _timings


def elapsed_total() -> float:
    return time.time() - _start


_summary_printed = False


def summary_printed() -> bool:
    return _summary_printed


def print_summary(artifact: str, status: str = "Success") -> None:
    global _summary_printed
    _summary_printed = True
    total = elapsed_total()
    color = {"Success": "green", "Cancelled": "yellow"}.get(status, "red")
    header("Build Summary")
    console.print(f"  [bold]Status:[/bold]      [{color}]{status}[/{color}]")
    console.print(f"  [bold]Total time:[/bold]  {fmt_duration(total)}")
    console.print(f"  [bold]Artifact:[/bold]    {artifact}")
    console.print()
    console.print("  [bold]Phase timing:[/bold]")
    for label, secs in _timings:
        bar_len = max(1, int(secs / total * 30)) if total else 1
        bar = "█" * bar_len + "░" * (30 - bar_len)
        console.print(f"  {bar}  {label:<22s} {secs:>6.1f}s")
    console.print()


def reset() -> None:
    """Reset timers (used between repeated invocations in tests)."""
    global _start
    _timings.clear()
    _start = time.time()

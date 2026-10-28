"""Render docs/demo/restore.svg: a real dry run, of a fictional snapshot.

The README's picture must not show anyone's machine, so the snapshot here is
made up (projects under /Users/you, made-up session ids), but everything
printed comes from the real code: restore commands are built by the same
adapter helper a capture uses, and the plan is printed by the real
`agent-tabs restore --all --create-missing` with the live window list empty --
the state after a reboot where iTerm reopened nothing. Nothing on the machine
running this script is read, typed into or created.

    python3 docs/demo/make_demo.py
"""

from __future__ import annotations

import argparse
import contextlib
import html
import io
import sys
import uuid
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent_tabs import adapters, iterm, restore  # noqa: E402
from agent_tabs import snapshot as snap_mod  # noqa: E402

HOME = "/Users/you/code"
# (title, kind, project directory, command for non-agent tabs)
WINDOWS: list[list[tuple[str, str, str, str | None]]] = [
    [
        ("Checkout flow refactor", "claude", "storefront", None),
        ("Payment webhooks", "claude", "storefront", None),
        ("Fix flaky cart tests", "codex", "storefront", None),
        ("storefront", "shell", "storefront", None),
    ],
    [
        ("Rate limiting design", "claude", "api", None),
        ("Migrate to Postgres 17", "codex", "api", None),
        ("staging", "ssh", "api", "ssh staging"),
    ],
    [
        ("Terraform plan review", "claude", "infra", None),
        ("Alert rules cleanup", "claude", "infra", None),
    ],
]
RESUME: dict[str, str] = {"claude": "claude --resume {}", "codex": "codex resume {}"}


def fake_snapshot() -> dict:
    windows = []
    for w, tabs in enumerate(WINDOWS, start=1):
        entries = []
        for t, (title, kind, project, command) in enumerate(tabs, start=1):
            cwd = f"{HOME}/{project}"
            session = str(uuid.uuid5(uuid.NAMESPACE_URL, f"agent-tabs-demo/{title}"))
            if kind in RESUME:
                info = adapters._result(kind, session, cwd, RESUME[kind].format(session))
            else:
                info = adapters._result(kind, None, cwd, command or "")
            entries.append({"tab_index": t, "pane_index": 1, "title": title, **info})
        windows.append({"window_index": w, "window_id": str(1000 + w), "tabs": entries})
    captured = datetime.now(timezone.utc) - timedelta(minutes=3)
    return {
        "version": snap_mod.SCHEMA_VERSION,
        "captured_at": captured.isoformat(timespec="seconds"),
        "windows": windows,
    }


def dry_run(snap: dict) -> str:
    loader = SourceFileLoader("agent_tabs_cli", str(ROOT / "bin" / "agent-tabs"))
    spec = spec_from_loader("agent_tabs_cli", loader)
    assert spec is not None
    cli = module_from_spec(spec)
    loader.exec_module(cli)

    def refuse(*_args: object) -> None:
        raise AssertionError("the demo must never touch iTerm")

    snap_mod.load = lambda path=None: snap  # type: ignore[assignment]
    snap_mod.pre_boot_warning = lambda s: None  # type: ignore[assignment]
    # After a reboot where iTerm reopened nothing, the one live window is the
    # new one the README says to open and run this from: an idle shell.
    launcher = {
        "window_index": 1,
        "window_id": "1",
        "tab_index": 1,
        "pane_index": 1,
        "tty": "/dev/ttys000",
        "title": "-zsh",
        "kind": None,
        "busy": False,
    }
    restore.live_tabs = lambda: [launcher]  # type: ignore[assignment]
    restore.current_window_index = lambda tabs: 1  # type: ignore[assignment]
    restore.current_tty = lambda: "ttys000"  # type: ignore[assignment]
    restore.running_sessions = lambda: {}  # type: ignore[assignment]
    iterm.create_window = refuse  # type: ignore[assignment]
    iterm.run_in_tab = refuse  # type: ignore[assignment]

    args = argparse.Namespace(
        file=None,
        go=False,
        all=True,
        create_missing=True,
        window=None,
        new_window=False,
        force=False,
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        cli.cmd_restore(args)
    return out.getvalue()


COMMAND = "agent-tabs restore --all --create-missing"
CHAR_W, LINE_H, PAD, BAR = 8.7, 19, 20, 34
COLOURS = {
    "bg": "#1e1f24",
    "bar": "#2b2d33",
    "text": "#d7dae0",
    "dim": "#8b919c",
    "prompt": "#7ee787",
    "head": "#79c0ff",
    "note": "#e3b341",
}


def to_svg(output: str) -> str:
    lines = [("prompt", f"$ {COMMAND}")] + [
        (
            "head"
            if line.startswith("snapshot window")
            else "note"
            if line.startswith(("Dry run", "Add --go"))
            else "dim"
            if line.startswith("Snapshot captured")
            else "text",
            line,
        )
        for line in output.rstrip("\n").split("\n")
    ]
    width = int(PAD * 3 + CHAR_W * max(len(text) for _, text in lines))
    height = BAR + PAD + LINE_H * len(lines) + PAD // 2
    c = COLOURS
    # Non-breaking spaces: some renderers collapse runs of plain spaces
    # despite xml:space, and the plan's columns depend on them.
    rows = []
    for i, (style, text) in enumerate(lines):
        y = BAR + PAD + LINE_H * i + 13
        weight = ' font-weight="600"' if style in ("prompt", "head") else ""
        rows.append(
            f'<text x="{PAD}" y="{y}" fill="{c[style]}"{weight}>{html.escape(text).replace(" ", chr(0xA0))}</text>'
        )
    dots = "".join(
        f'<circle cx="{18 + 20 * i}" cy="{BAR // 2}" r="6" fill="{colour}"/>'
        for i, colour in enumerate(("#ff5f57", "#febc2e", "#28c840"))
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="agent-tabs restore '
        f"--all --create-missing: a dry run listing, for each window lost in a "
        f'reboot, the command each tab would get">\n'
        f'<rect width="{width}" height="{height}" rx="10" fill="{c["bg"]}"/>\n'
        f'<path d="M0 10a10 10 0 0 1 10-10h{width - 20}a10 10 0 0 1 10 10v{BAR - 10}H0z" '
        f'fill="{c["bar"]}"/>\n{dots}\n'
        f'<g font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
        f'font-size="13" xml:space="preserve" style="white-space:pre">\n'
        + "\n".join(rows)
        + "\n</g>\n</svg>\n"
    )


if __name__ == "__main__":
    target = Path(__file__).with_name("restore.svg")
    target.write_text(to_svg(dry_run(fake_snapshot())))
    print(f"wrote {target.relative_to(ROOT)}")

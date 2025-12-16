#!/usr/bin/env python3
"""
Lightweight Git-based sync helper.
- Tracks configured directories, initializes repos if missing, and syncs changes.
- Designed for scheduled use (systemd timer/cron) at ~5 minute intervals.
- Skips expensive work when idle or offline to save power and bandwidth.
"""

from __future__ import annotations

__version__ = "1.0.0"

import argparse
import json
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_PATH = Path.home() / ".config" / "git-sync" / "config.json"
DEFAULT_INTERVAL_MINUTES = 5
DEFAULT_NETWORK_HOST = "github.com"
MIN_INTERVAL_MINUTES = 1


@dataclass
class Entry:
    path: Path
    remote: Optional[str]
    branch: str
    push: bool
    commit_message: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entry":
        return cls(
            path=Path(data["path"]).expanduser().resolve(),
            remote=data.get("remote"),
            branch=data.get("branch", "main"),
            push=bool(data.get("push", True)),
            commit_message=data.get("commit_message", "Auto-sync"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "remote": self.remote,
            "branch": self.branch,
            "push": self.push,
            "commit_message": self.commit_message,
        }


@dataclass
class Config:
    entries: List[Entry]
    interval_minutes: int
    network_host: str

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            cfg = cls(
                entries=[],
                interval_minutes=DEFAULT_INTERVAL_MINUTES,
                network_host=DEFAULT_NETWORK_HOST,
            )
            cfg.save()
            return cfg

        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)

        entries = [Entry.from_dict(item) for item in data.get("entries", [])]
        interval = max(
            MIN_INTERVAL_MINUTES, int(data.get("interval_minutes", DEFAULT_INTERVAL_MINUTES))
        )
        host = data.get("network_host", DEFAULT_NETWORK_HOST)
        return cls(entries=entries, interval_minutes=interval, network_host=host)

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "entries": [e.to_dict() for e in self.entries],
            "interval_minutes": self.interval_minutes,
            "network_host": self.network_host,
        }
        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


# --------------------------- helpers ---------------------------


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def repo_exists(path: Path) -> bool:
    return (path / ".git").exists()


def ensure_repo(path: Path, branch: str, remote: Optional[str]) -> None:
    if repo_exists(path):
        return

    log(f"Initializing repo at {path}")
    path.mkdir(parents=True, exist_ok=True)
    try:
        run_git(path, "init", "-b", branch)
    except subprocess.CalledProcessError:
        run_git(path, "init")
        run_git(path, "checkout", "-B", branch, check=False)

    if remote:
        run_git(path, "remote", "add", "origin", remote, check=False)


def current_branch(path: Path) -> Optional[str]:
    try:
        res = run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
        return res.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def set_branch(path: Path, branch: str) -> None:
    run_git(path, "checkout", "-B", branch, check=False)


def remote_exists(path: Path) -> bool:
    try:
        res = run_git(path, "remote")
        return bool(res.stdout.strip())
    except subprocess.CalledProcessError:
        return False


def upsert_remote(path: Path, remote: Optional[str]) -> None:
    if not remote:
        return
    if remote_exists(path):
        return
    run_git(path, "remote", "add", "origin", remote, check=False)


def has_changes(path: Path) -> bool:
    res = run_git(path, "status", "--porcelain", check=False)
    return bool(res.stdout.strip())


def commit_changes(path: Path, message: str) -> bool:
    if not has_changes(path):
        return False
    run_git(path, "add", "-A")
    run_git(path, "commit", "-m", message, check=False)
    return True


def push_changes(path: Path, branch: str) -> None:
    if not remote_exists(path):
        log(f"No remote configured for {path}; skipping push")
        return
    try:
        run_git(path, "push", "-u", "origin", branch, check=False)
    except subprocess.CalledProcessError as exc:
        log(f"Push failed for {path}: {exc.stderr or exc}")


def fetch_remote(path: Path, branch: str) -> bool:
    res = run_git(path, "fetch", "--prune", "origin", branch, check=False)
    if res.returncode != 0:
        log(f"Fetch failed for {path}: {res.stderr or res.stdout}")
        return False
    return True


def rebase_onto_remote(path: Path, branch: str) -> bool:
    res = run_git(path, "rebase", f"origin/{branch}", check=False)
    if res.returncode != 0:
        # Attempt to clean up to avoid leaving repo in rebase state
        run_git(path, "rebase", "--abort", check=False)
        log(f"Rebase failed for {path}: {res.stderr or res.stdout}")
        return False
    return True


def online(host: str) -> bool:
    try:
        with socket.create_connection((host, 443), timeout=2):
            return True
    except OSError:
        return False


# --------------------------- actions ---------------------------


def add_entry(args: argparse.Namespace) -> None:
    cfg = Config.load()
    new_entry = Entry(
        path=Path(args.path).expanduser().resolve(),
        remote=args.remote,
        branch=args.branch,
        push=not args.no_push,
        commit_message=args.commit_message,
    )

    if any(e.path == new_entry.path for e in cfg.entries):
        log(f"Path already tracked: {new_entry.path}")
        return

    cfg.entries.append(new_entry)
    cfg.save()
    log(f"Added {new_entry.path}")


def remove_entry(args: argparse.Namespace) -> None:
    cfg = Config.load()
    target = Path(args.path).expanduser().resolve()
    before = len(cfg.entries)
    cfg.entries = [e for e in cfg.entries if e.path != target]
    cfg.save()
    if len(cfg.entries) < before:
        log(f"Removed {target}")
    else:
        log(f"Path not found: {target}")


def list_entries(_: argparse.Namespace) -> None:
    cfg = Config.load()
    if not cfg.entries:
        log("No tracked paths yet. Use add <path> to start.")
        return
    for e in cfg.entries:
        flags = []
        if e.remote:
            flags.append(f"remote={e.remote}")
        flags.append(f"branch={e.branch}")
        flags.append("push" if e.push else "no-push")
        log(f"- {e.path} ({', '.join(flags)})")


def sync_entry(entry: Entry, cfg: Config, push_override: Optional[bool]) -> None:
    path = entry.path
    push = entry.push if push_override is None else push_override
    ensure_repo(path, entry.branch, entry.remote)
    upsert_remote(path, entry.remote)

    branch = current_branch(path) or entry.branch
    if branch != entry.branch:
        set_branch(path, entry.branch)
        branch = entry.branch

    if not has_changes(path):
        log(f"Idle: {path}")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"{entry.commit_message} ({timestamp})"
    did_commit = commit_changes(path, message)
    if not did_commit:
        log(f"Nothing to commit in {path}")
        return
    log(f"Committed changes in {path}")

    if not push:
        return

    if not online(cfg.network_host):
        log("Offline; will push on next run")
        return

    if remote_exists(path):
        if not fetch_remote(path, branch):
            return
        if not rebase_onto_remote(path, branch):
            return

    push_changes(path, branch)


def sync_all(args: argparse.Namespace) -> None:
    cfg = Config.load()
    if not cfg.entries:
        log("No tracked paths; add one first.")
        return
    for entry in cfg.entries:
        try:
            sync_entry(entry, cfg, push_override=args.no_push_all)
        except Exception as exc:  # noqa: BLE001
            log(f"Error syncing {entry.path}: {exc}")


def run_loop(args: argparse.Namespace) -> None:
    cfg = Config.load()
    interval = args.interval or cfg.interval_minutes
    if interval < MIN_INTERVAL_MINUTES:
        log(f"Interval too low ({interval}); using {MIN_INTERVAL_MINUTES} minute")
        interval = MIN_INTERVAL_MINUTES
    while True:
        sync_all(args)
        if args.once:
            return
        time.sleep(interval * 60)


# --------------------------- cli ---------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Git sync helper")
    sub = parser.add_subparsers(dest="command", required=True)

    add_cmd = sub.add_parser("add", help="Track a directory")
    add_cmd.add_argument("path", help="Directory to sync")
    add_cmd.add_argument("--remote", help="Remote URL to push to")
    add_cmd.add_argument("--branch", default="main", help="Branch name (default: main)")
    add_cmd.add_argument("--commit-message", default="Auto-sync", help="Base commit message")
    add_cmd.add_argument("--no-push", action="store_true", help="Do not push for this entry")
    add_cmd.set_defaults(func=add_entry)

    rm_cmd = sub.add_parser("remove", help="Stop tracking a directory")
    rm_cmd.add_argument("path", help="Directory to stop syncing")
    rm_cmd.set_defaults(func=remove_entry)

    list_cmd = sub.add_parser("list", help="List tracked directories")
    list_cmd.set_defaults(func=list_entries)

    sync_cmd = sub.add_parser("sync", help="Run one sync pass")
    sync_cmd.add_argument("--no-push-all", action="store_true", help="Disable pushes for this run")
    sync_cmd.set_defaults(func=sync_all)

    run_cmd = sub.add_parser("run", help="Run sync loop (for services)")
    run_cmd.add_argument(
        "--interval", type=int, help="Minutes between checks (default: config value)"
    )
    run_cmd.add_argument("--no-push-all", action="store_true", help="Disable pushes for this run")
    run_cmd.add_argument("--once", action="store_true", help="Run a single pass then exit")
    run_cmd.set_defaults(func=run_loop)

    return parser


def main(argv: List[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

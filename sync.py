#!/usr/bin/env python3
"""
Lightweight Git-based sync helper.
- Tracks configured directories, initializes repos if missing, and syncs changes.
- Designed for scheduled use (systemd timer/cron) at ~5 minute intervals.
- Skips expensive work when idle or offline to save power and bandwidth.
"""

from __future__ import annotations

__version__ = "1.1.0"

import argparse
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CONFIG_PATH = Path.home() / ".config" / "git-sync" / "config.json"
DEFAULT_LOCK_PATH = Path.home() / ".config" / "git-sync" / "lock"
DEFAULT_INTERVAL_MINUTES = 5
DEFAULT_NETWORK_HOST = "github.com"
DEFAULT_NETWORK_PORT = 22
MIN_INTERVAL_MINUTES = 1
DEFAULT_GIT_TIMEOUT_SECONDS = 30


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
    network_port: int

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            cfg = cls(
                entries=[],
                interval_minutes=DEFAULT_INTERVAL_MINUTES,
                network_host=DEFAULT_NETWORK_HOST,
                network_port=DEFAULT_NETWORK_PORT,
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
        port = int(data.get("network_port", DEFAULT_NETWORK_PORT))
        return cls(entries=entries, interval_minutes=interval, network_host=host, network_port=port)

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "entries": [e.to_dict() for e in self.entries],
            "interval_minutes": self.interval_minutes,
            "network_host": self.network_host,
            "network_port": self.network_port,
        }
        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


# --------------------------- helpers ---------------------------


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_git(
    path: Path,
    *args: str,
    check: bool = True,
    timeout_seconds: int = DEFAULT_GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=check,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )


@contextlib.contextmanager
def pid_lock(lock_path: Path) -> "contextlib.AbstractContextManager[None]":
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        try:
            stale_pid = int(lock_path.read_text(encoding="utf-8").strip())
        except Exception:
            stale_pid = None

        if stale_pid is not None:
            try:
                os.kill(stale_pid, 0)
                raise SystemExit(f"Another git-sync instance is running (pid {stale_pid}).")
            except ProcessLookupError:
                # Stale lock; remove and retry once
                with contextlib.suppress(OSError):
                    lock_path.unlink()
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except PermissionError:
                raise SystemExit(
                    f"Lockfile exists and pid {stale_pid} is not accessible; aborting."
                )
        else:
            raise SystemExit(f"Lockfile exists at {lock_path}; aborting.")

    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()


def write_pidfile(pidfile: Optional[Path]) -> None:
    if not pidfile:
        return
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()), encoding="utf-8")


def remove_pidfile(pidfile: Optional[Path]) -> None:
    if not pidfile:
        return
    with contextlib.suppress(OSError):
        pidfile.unlink()


def daemonize(logfile: Optional[Path], pidfile: Optional[Path]) -> None:
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)

    sys.stdin.flush()
    sys.stdout.flush()
    sys.stderr.flush()

    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    if logfile:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        out = os.open(str(logfile), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
        os.dup2(out, 1)
        os.dup2(out, 2)
        os.close(out)
    else:
        dn = os.open(os.devnull, os.O_WRONLY)
        os.dup2(dn, 1)
        os.dup2(dn, 2)
        os.close(dn)

    write_pidfile(pidfile)


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


def origin_branch_exists(path: Path, branch: str) -> bool:
    res = run_git(path, "show-ref", "--verify", f"refs/remotes/origin/{branch}", check=False)
    return res.returncode == 0


def ahead_behind(path: Path, branch: str) -> Tuple[int, int]:
    """Return (ahead, behind) relative to origin/branch.

    If origin/branch doesn't exist, returns (0, 0).
    """
    if not origin_branch_exists(path, branch):
        return (0, 0)
    res = run_git(
        path,
        "rev-list",
        "--left-right",
        "--count",
        f"origin/{branch}...{branch}",
        check=False,
    )
    if res.returncode != 0:
        return (0, 0)
    left_right = res.stdout.strip().split()
    if len(left_right) != 2:
        return (0, 0)
    behind = int(left_right[0])
    ahead = int(left_right[1])
    return (ahead, behind)


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
    return online_host_port(host, DEFAULT_NETWORK_PORT)


def online_host_port(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def infer_ssh_target(remote: Optional[str], cfg: Config) -> Tuple[str, int]:
    """Best-effort: infer SSH host/port from remote, else fall back to config."""
    if not remote:
        return (cfg.network_host, cfg.network_port)

    # scp-like: git@github.com:owner/repo.git
    if ":" in remote and "@" in remote and not remote.startswith("ssh://"):
        try:
            host_part = remote.split("@", 1)[1]
            host = host_part.split(":", 1)[0]
            return (host, DEFAULT_NETWORK_PORT)
        except Exception:
            return (cfg.network_host, cfg.network_port)

    # ssh://user@host:port/path
    if remote.startswith("ssh://"):
        rest = remote[len("ssh://") :]
        hostport = rest.split("/", 1)[0]
        if "@" in hostport:
            hostport = hostport.split("@", 1)[1]
        if ":" in hostport:
            host, port_s = hostport.rsplit(":", 1)
            with contextlib.suppress(ValueError):
                return (host, int(port_s))
        return (hostport, DEFAULT_NETWORK_PORT)

    return (cfg.network_host, cfg.network_port)


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

    if not push:
        if has_changes(path):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"{entry.commit_message} ({timestamp})"
            did_commit = commit_changes(path, message)
            if did_commit:
                log(f"Committed changes in {path}")
        else:
            log(f"Idle: {path}")
        return

    if not remote_exists(path):
        # Still commit locally even if push enabled but remote missing
        if has_changes(path):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"{entry.commit_message} ({timestamp})"
            did_commit = commit_changes(path, message)
            if did_commit:
                log(f"Committed changes in {path}")
        log(f"No remote configured for {path}; skipping push")
        return

    host, port = infer_ssh_target(entry.remote, cfg)
    if not online_host_port(host, port):
        log(f"Offline; will push on next run ({host}:{port})")
        return

    if not fetch_remote(path, branch):
        return

    # If we have local file changes, commit them first.
    if has_changes(path):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"{entry.commit_message} ({timestamp})"
        did_commit = commit_changes(path, message)
        if did_commit:
            log(f"Committed changes in {path}")

    # Rebase if we're behind (or diverged) and then push if we're ahead.
    ahead, behind = ahead_behind(path, branch)
    if behind > 0:
        if not rebase_onto_remote(path, branch):
            return
        ahead, behind = ahead_behind(path, branch)

    if ahead > 0 or not origin_branch_exists(path, branch):
        push_changes(path, branch)
    else:
        log(f"Up to date: {path}")


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

    stop_flag = {"stop": False}

    def _handle_signal(_signum: int, _frame: object) -> None:
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    lock_path = Path(args.lockfile).expanduser().resolve() if args.lockfile else DEFAULT_LOCK_PATH
    with pid_lock(lock_path):
        if args.daemon:
            daemonize(
                Path(args.logfile).expanduser().resolve() if args.logfile else None,
                Path(args.pidfile).expanduser().resolve() if args.pidfile else None,
            )
            log("Started in daemon mode")

        else:
            write_pidfile(Path(args.pidfile).expanduser().resolve() if args.pidfile else None)

        try:
            while True:
                sync_all(args)
                if args.once or stop_flag["stop"]:
                    return
                time.sleep(interval * 60)
        finally:
            remove_pidfile(Path(args.pidfile).expanduser().resolve() if args.pidfile else None)


def stop_daemon(args: argparse.Namespace) -> None:
    pidfile = Path(args.pidfile).expanduser().resolve()
    if not pidfile.exists():
        log(f"PID file not found: {pidfile}")
        return
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except Exception:
        log(f"Invalid PID file: {pidfile}")
        return
    try:
        os.kill(pid, signal.SIGTERM)
        log(f"Sent SIGTERM to pid {pid}")
    except ProcessLookupError:
        log(f"Process not found: pid {pid}")
    except PermissionError:
        log(f"No permission to stop pid {pid}")


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
    sync_cmd.add_argument(
        "--lockfile",
        help=f"Lock file path (default: {DEFAULT_LOCK_PATH})",
    )
    sync_cmd.set_defaults(func=sync_all)

    run_cmd = sub.add_parser("run", help="Run sync loop (for services)")
    run_cmd.add_argument(
        "--interval", type=int, help="Minutes between checks (default: config value)"
    )
    run_cmd.add_argument("--no-push-all", action="store_true", help="Disable pushes for this run")
    run_cmd.add_argument("--once", action="store_true", help="Run a single pass then exit")
    run_cmd.add_argument(
        "--daemon",
        action="store_true",
        help="Detach and run in background (writes --pidfile if provided)",
    )
    run_cmd.add_argument("--pidfile", help="Write process PID to this file")
    run_cmd.add_argument("--logfile", help="Append logs to this file (daemon mode)")
    run_cmd.add_argument(
        "--lockfile",
        help=f"Lock file path (default: {DEFAULT_LOCK_PATH})",
    )
    run_cmd.set_defaults(func=run_loop)

    stop_cmd = sub.add_parser("stop", help="Stop a daemon started with run --daemon")
    stop_cmd.add_argument("--pidfile", required=True, help="PID file used by the daemon")
    stop_cmd.set_defaults(func=stop_daemon)

    return parser


def main(argv: List[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # keep sync path locked too (single pass)
    if args.command == "sync":
        lock_path = Path(args.lockfile).expanduser().resolve() if args.lockfile else DEFAULT_LOCK_PATH
        with pid_lock(lock_path):
            args.func(args)
        return 0

    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

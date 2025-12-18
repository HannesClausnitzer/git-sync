# git-sync

A lightweight helper that automatically commits and pushes one or more local directories to a Git remote over SSH.

## What a background auto-sync tool should have

- **Per-repo config**: path, remote, branch, push on/off, commit message
- **Safe background operation**: single-instance lock, optional daemon mode, pid/log files, graceful shutdown
- **SSH-first connectivity checks**: don’t try to push when the SSH endpoint is unreachable
- **Correct push behavior**: fetch/rebase to avoid non-fast-forward pushes; push even if there are no new file changes (e.g. previously-offline commits)
- **Low overhead**: do nothing when idle; short timeouts for git operations
- **Clear logs**: enough output to understand what happened on each run

## Requirements

- Python 3
- `git`
- SSH configured for your remote (e.g. GitHub SSH keys and `ssh-agent`)

## Usage

Track a directory (existing repo or a plain folder):

```bash
./sync.py add ~/notes --remote git@github.com:USER/notes.git --branch main
```

List tracked directories:

```bash
./sync.py list
```

Run one sync pass:

```bash
./sync.py sync
```

Run continuously in the foreground (default interval from config):

```bash
./sync.py run
```

Run continuously in the background (daemon mode):

```bash
./sync.py run --daemon --pidfile ~/.config/git-sync/git-sync.pid --logfile ~/.config/git-sync/git-sync.log
```

Stop a daemon:

```bash
./sync.py stop --pidfile ~/.config/git-sync/git-sync.pid
```

## Configuration

Config is stored at:

- `~/.config/git-sync/config.json`

Defaults include:

- `interval_minutes`: 5
- `network_host`: `github.com`
- `network_port`: 22

`network_host` / `network_port` are used as a fallback if a tracked entry has no remote URL; when a remote is set, `git-sync` infers the SSH host/port from the remote when possible.

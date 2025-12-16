# git-sync

A lightweight, power-conscious helper to auto-commit and push tracked directories every few minutes. Config is stored at `~/.config/git-sync/config.json`.

## Features

- Automatic git initialization for new directories
- Configurable commit messages and branches
- Power-efficient: skips work when idle or offline
- No external dependencies (uses standard library)
- Systemd integration for background operation

## Install
1) Make the script executable:
  ```sh
  chmod +x sync.py
  ```
2) Optional: install a user-level systemd timer with the provided script (also installs a `gitsync` shim to `~/.local/bin` by default):
  ```sh
  chmod +x install.sh
  INTERVAL_MINUTES=5 ./install.sh
  systemctl --user daemon-reload
  systemctl --user enable --now git-sync.timer
  ```
  Ensure your chosen `BIN_DIR` (default `~/.local/bin`) is on PATH to run commands with `gitsync`.

## Configure directories
- Add a directory (after install you can use `gitsync` instead of `./sync.py`):
  ```sh
  gitsync add /path/to/dir --remote https://example.com/repo.git --branch main
  ```
- List tracked directories:
  ```sh
  gitsync list
  ```
- Remove a directory:
  ```sh
  gitsync remove /path/to/dir
  ```

## Run syncs
- Single pass:
  ```sh
  gitsync sync
  ```
- Continuous (every 5 minutes by default, or override):
  ```sh
  gitsync run --interval 5
  ```
- Disable pushes for a run: add `--no-push-all`.
- Run once then exit: add `--once` (useful in cron/systemd timers).

## Service (systemd user) example
Use `install.sh` if you prefer not to hand-write unit files. It writes `~/.config/systemd/user/git-sync.service` and `git-sync.timer` pointing at this checkout, and drops a `gitsync` shim into `BIN_DIR` (default `~/.local/bin`). Customize `INTERVAL_MINUTES`, `PYTHON_BIN`, or `BIN_DIR` when running the script.

## Configuration

Configuration is stored at `~/.config/git-sync/config.json`. You can edit this file manually or use the CLI commands.

Example config:
```json
{
  "entries": [
    {
      "path": "/home/user/notes",
      "remote": "https://github.com/user/notes.git",
      "branch": "main",
      "push": true,
      "commit_message": "Auto-sync notes"
    }
  ],
  "interval_minutes": 5,
  "network_host": "github.com"
}
```

## Notes
- Initializes git repos if missing; commits only when there are changes; pushes when online (host check defaults to `github.com`).
- Branch and commit message are configurable per entry; pushes can be disabled per entry or per run.
- Minimum loop interval is 1 minute; set `INTERVAL_MINUTES` accordingly.
- All dependencies are standard library; no external packages required.
- Pushes perform a fetch and rebase against the configured branch before pushing to reduce divergence; repositories with local-only work may require manual conflict resolution.

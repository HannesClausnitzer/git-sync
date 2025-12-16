import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sync import Config, Entry, has_changes, repo_exists


class TestEntry:
    def test_from_dict_minimal(self):
        data = {"path": "~/test"}
        entry = Entry.from_dict(data)
        assert entry.path == Path("~/test").expanduser().resolve()
        assert entry.remote is None
        assert entry.branch == "main"
        assert entry.push is True
        assert entry.commit_message == "Auto-sync"

    def test_from_dict_full(self):
        data = {
            "path": "~/test",
            "remote": "https://github.com/user/repo.git",
            "branch": "develop",
            "push": False,
            "commit_message": "Custom message",
        }
        entry = Entry.from_dict(data)
        assert entry.path == Path("~/test").expanduser().resolve()
        assert entry.remote == "https://github.com/user/repo.git"
        assert entry.branch == "develop"
        assert entry.push is False
        assert entry.commit_message == "Custom message"

    def test_to_dict(self):
        entry = Entry(
            path=Path("/home/user/test"),
            remote="https://github.com/user/repo.git",
            branch="develop",
            push=False,
            commit_message="Custom message",
        )
        expected = {
            "path": "/home/user/test",
            "remote": "https://github.com/user/repo.git",
            "branch": "develop",
            "push": False,
            "commit_message": "Custom message",
        }
        assert entry.to_dict() == expected


class TestConfig:
    def test_load_creates_default_if_missing(self, tmp_path):
        config_path = tmp_path / ".config" / "git-sync" / "config.json"
        with patch("sync.CONFIG_PATH", config_path):
            cfg = Config.load()
            assert cfg.entries == []
            assert cfg.interval_minutes == 5
            assert cfg.network_host == "github.com"
            assert config_path.exists()

    def test_save_and_load(self, tmp_path):
        config_path = tmp_path / ".config" / "git-sync" / "config.json"
        with patch("sync.CONFIG_PATH", config_path):
            cfg = Config(
                entries=[
                    Entry(
                        path=Path("/tmp/test"),
                        remote="https://github.com/user/repo.git",
                        branch="main",
                        push=True,
                        commit_message="Test",
                    )
                ],
                interval_minutes=10,
                network_host="gitlab.com",
            )
            cfg.save()
            loaded = Config.load()
            assert len(loaded.entries) == 1
            assert loaded.entries[0].path == Path("/tmp/test")
            assert loaded.entries[0].remote == "https://github.com/user/repo.git"
            assert loaded.interval_minutes == 10
            assert loaded.network_host == "gitlab.com"


class TestRepoFunctions:
    def test_repo_exists_true(self, tmp_path):
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()
        assert repo_exists(repo_path) is True

    def test_repo_exists_false(self, tmp_path):
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        assert repo_exists(repo_path) is False

    def test_has_changes_true(self, tmp_path):
        with patch("sync.run_git") as mock_run:
            mock_run.return_value = MagicMock(stdout="M file.txt\n")
            assert has_changes(tmp_path) is True

    def test_has_changes_false(self, tmp_path):
        with patch("sync.run_git") as mock_run:
            mock_run.return_value = MagicMock(stdout="")
            assert has_changes(tmp_path) is False

from pathlib import Path
import socket
import sys

import pytest

from uav_telemetry.cli import main
from uav_telemetry.mavlink.source import Encoder


@pytest.mark.parametrize("command", ["source", "proxy", "receive", "dashboard"])
def test_standalone_cli_execution(command, tmp_path, monkeypatch):
    args = ["telemetry-monitor", command, "--duration", "0.1", "--output", str(tmp_path)]
    if command in ("receive", "proxy"):
        args += ["--bind", "127.0.0.1:0"]
    if command in ("receive", "dashboard"):
        args += ["--snapshot", str(tmp_path / "snapshot.json")]
    if command == "dashboard":
        args += ["--port", "0"]
    monkeypatch.setattr(sys, "argv", args)
    main()
    if command == "source":
        assert (tmp_path / "source.jsonl").stat().st_size > 0
    if command == "proxy":
        assert (tmp_path / "proxy_stats.json").exists()
    if command == "receive":
        assert (tmp_path / "snapshot.json").exists()


def test_cli_invalid_config_error(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["telemetry-monitor", "source", "--duration", "-1"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2

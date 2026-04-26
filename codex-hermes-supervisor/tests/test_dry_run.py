from __future__ import annotations

from codex_hermes_supervisor.core.config import SupervisorConfig
from codex_hermes_supervisor.services.dry_run import run_dry_run


def test_run_dry_run_smoke_flow(tmp_path) -> None:
    config = SupervisorConfig.model_validate(
        {
            "state": {
                "root": str(tmp_path / "state"),
                "lock_timeout_seconds": 30,
                "heartbeat_seconds": 1,
            },
            "hermes": {
                "mode": "outbox",
                "outbox_dir": str(tmp_path / "outbox"),
            },
            "obsidian": {
                "enabled": False,
                "vault_root": "",
            },
        }
    )
    report = run_dry_run(config)
    assert report.check_passed is True
    assert report.finish_phase == "FINISHED"
    assert report.version_path.endswith(".py")
    assert report.active_path.endswith(".py")
    assert report.handoff_path.endswith(".md")

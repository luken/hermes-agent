from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from hermes_cli import kanban_db as kb
from tools import kanban_tools


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _profile_skill(home: Path, profile: str, skill: str) -> None:
    root = home / "profiles" / profile / "skills" / skill
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: test skill\n---\nUse it.\n",
        encoding="utf-8",
    )


def test_task_creation_and_reassignment_validate_profile_skill_inventory(
    kanban_home: Path,
) -> None:
    _profile_skill(kanban_home, "builder", "addon-workflow")
    (kanban_home / "profiles" / "tester" / "skills").mkdir(parents=True)
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="valid builder skill",
            assignee="builder",
            skills=["addon-workflow"],
        )
        with pytest.raises(ValueError, match="worker_configuration.*addon-workflow"):
            kb.assign_task(conn, task_id, "tester")
        with pytest.raises(ValueError, match="worker_configuration.*missing-skill"):
            kb.create_task(
                conn,
                title="invalid tester skill",
                assignee="tester",
                skills=["missing-skill"],
            )
        with pytest.raises(ValueError, match="assigned profile is unavailable"):
            kb.create_task(
                conn,
                title="missing profile",
                assignee="absent-profile",
                skills=["missing-skill"],
            )


def test_profile_skill_validation_honors_configured_skill_roots(
    kanban_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = tmp_path / "external-skills"
    skill = external / "shared-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: external test skill\n---\nUse it.\n",
        encoding="utf-8",
    )
    (kanban_home / "profiles" / "builder").mkdir(parents=True)
    import agent.skill_utils as skill_utils

    monkeypatch.setattr(skill_utils, "get_all_skills_dirs", lambda: [external])
    kb.validate_profile_skills("builder", ["shared-skill"])


def test_profile_skill_validation_allows_environment_hidden_forced_skill(
    kanban_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = (
        kanban_home
        / "profiles"
        / "reviewer"
        / "skills"
        / "devops"
        / "review-skill"
    )
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: review-skill\n"
        "description: test review skill\n"
        "environments: [kanban]\n"
        "---\n"
        "Use it.\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)

    from hermes_cli.profiles import resolve_profile_env
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.skills_tool import skill_view

    token = set_hermes_home_override(resolve_profile_env("reviewer"))
    try:
        loaded = json.loads(skill_view("review-skill", preprocess=False))
    finally:
        reset_hermes_home_override(token)

    assert loaded["success"] is True
    kb.validate_profile_skills("reviewer", ["review-skill"])


def test_profile_skill_validation_keeps_runtime_loadability_gates(
    kanban_home: Path,
) -> None:
    _profile_skill(kanban_home, "reviewer", "disabled-skill")
    unsupported = (
        kanban_home / "profiles" / "reviewer" / "skills" / "unsupported-skill"
    )
    unsupported.mkdir(parents=True)
    (unsupported / "SKILL.md").write_text(
        "---\n"
        "name: unsupported-skill\n"
        "description: test unsupported skill\n"
        "platforms: [unsupported-test-platform]\n"
        "---\n"
        "Use it.\n",
        encoding="utf-8",
    )
    (kanban_home / "profiles" / "reviewer" / "config.yaml").write_text(
        "skills:\n  disabled:\n    - disabled-skill\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        kb.validate_profile_skills(
            "reviewer", ["disabled-skill", "unsupported-skill"]
        )

    error = str(exc_info.value)
    assert "disabled-skill" in error
    assert "unsupported-skill" in error


def test_operator_can_repair_legacy_skills_only_while_task_is_unclaimed(
    kanban_home: Path,
) -> None:
    _profile_skill(kanban_home, "builder", "builder-only")
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="repairable metadata",
            assignee="builder",
            skills=["builder-only"],
        )
        assert kb.set_task_skills(conn, task_id, [])
        assert kb.get_task(conn, task_id).skills is None
        assert kb.set_task_skills(conn, task_id, ["builder-only"])
        assert kb.claim_task(conn, task_id) is not None
        with pytest.raises(RuntimeError, match="active worker"):
            kb.set_task_skills(conn, task_id, [])


def test_final_spawn_revalidates_legacy_task_skill(
    kanban_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _profile_skill(kanban_home, "builder", "temporary-skill")
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="skill removed after admission",
            assignee="builder",
            skills=["temporary-skill"],
        )
        task = kb.get_task(conn, task_id)
    assert task is not None
    (kanban_home / "profiles" / "builder" / "skills" / "temporary-skill" / "SKILL.md").unlink()
    with pytest.raises(ValueError, match="worker_configuration.*temporary-skill"):
        kb._default_spawn(task, str(tmp_path / "workspace"))


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def test_machine_handoff_requires_clean_commit_and_fresh_code_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "work")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "code")

    task = SimpleNamespace(id="t_one", workspace_kind="worktree", workspace_path=str(repo))
    monkeypatch.setenv("HERMES_KANBAN_TASK", task.id)
    monkeypatch.setenv("HERMES_SESSION_ID", "session-one")

    import agent.verification_evidence as evidence

    monkeypatch.setattr(
        evidence,
        "verification_status",
        lambda **_kwargs: {"status": "unverified", "evidence": None, "changed_paths": []},
    )
    receipt, error = kanban_tools._machine_handoff_gate(
        task, {"handoff": {"base_commit": base}}, transition="review"
    )
    assert receipt is None
    assert "fresh passing verification" in (error or "")

    monkeypatch.setattr(
        evidence,
        "verification_status",
        lambda **_kwargs: {
            "status": "passed",
            "changed_paths": [],
            "evidence": {
                "canonical_command": "pytest",
                "kind": "test",
                "scope": "full",
                "exit_code": 0,
            },
        },
    )
    receipt, error = kanban_tools._machine_handoff_gate(
        task, {"handoff": {"base_commit": base}}, transition="review"
    )
    assert error is None
    assert receipt is not None
    assert receipt["base_commit"] == base
    assert receipt["head_commit"] == _git(repo, "rev-parse", "HEAD")
    assert receipt["changed_files"] == ["app.py"]
    assert receipt["verification"]["status"] == "passed"

    receipt, error = kanban_tools._machine_handoff_gate(
        task,
        {"handoff": {"base_commit": base, "head_commit": base}},
        transition="review",
    )
    assert receipt is None
    assert error == "declared handoff head does not match worktree HEAD"

    (repo / "dirty.txt").write_text("not committed\n", encoding="utf-8")
    receipt, error = kanban_tools._machine_handoff_gate(
        task, {"handoff": {"base_commit": base}}, transition="review"
    )
    assert receipt is None
    assert error == "worktree is dirty; commit or remove all changes before handoff"


def test_machine_handoff_rejects_declared_nonancestor_instead_of_widening_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("root\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "root")
    _git(repo, "checkout", "-b", "other")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-m", "other")
    nonancestor = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "code")

    task = SimpleNamespace(id="t_two", workspace_kind="worktree", workspace_path=str(repo))
    monkeypatch.setenv("HERMES_KANBAN_TASK", task.id)
    receipt, error = kanban_tools._machine_handoff_gate(
        task, {"handoff": {"base_commit": nonancestor}}, transition="review"
    )
    assert receipt is None
    assert error == "declared patch base is not an ancestor of HEAD"


def test_fifth_changes_request_parks_task_for_supervisor_attention(
    kanban_home: Path,
) -> None:
    with kb.connect() as conn:
        task_id = kb.create_task(conn, title="bounded review", assignee="builder")
        base = "a" * 40
        for review_round in range(1, 6):
            implementation = kb.claim_task(conn, task_id)
            assert implementation is not None
            assert kb.request_review(
                conn,
                task_id,
                summary=f"round {review_round}",
                reviewer="reviewer" if review_round == 1 else None,
                expected_run_id=implementation.current_run_id,
                metadata={
                    "handoff_gate": {
                        "base_commit": base,
                        "head_commit": f"{review_round:040x}",
                    }
                },
            )
            review = kb.claim_review_task(conn, task_id)
            assert review is not None
            ok, implementer = kb.request_changes(
                conn,
                task_id,
                reason=f"fix round {review_round}",
                expected_run_id=review.current_run_id,
            )
            assert ok and implementer == "builder"
            task = kb.get_task(conn, task_id)
            assert task is not None
            assert task.status == ("triage" if review_round == 5 else "ready")

        escalations = [
            event for event in kb.list_events(conn, task_id=task_id)
            if event.kind == "review_escalated"
        ]
        assert len(escalations) == 1
        assert escalations[0].payload["review_round"] == 5
        assert escalations[0].payload["required_action"] == "supervisor_attention"


def test_reclaim_counts_failure_except_rollout_continuity(kanban_home: Path) -> None:
    with kb.connect() as conn:
        task_id = kb.create_task(conn, title="manual recovery", assignee="builder")
        assert kb.claim_task(conn, task_id) is not None
        assert kb.reclaim_task(conn, task_id, reason="missing worker")
        assert kb.get_task(conn, task_id).consecutive_failures == 1

        assert kb.claim_task(conn, task_id) is not None
        assert kb.reclaim_task(
            conn, task_id, reason="replaced controller Pod", continuity=True
        )
        assert kb.get_task(conn, task_id).consecutive_failures == 1

        strict_id = kb.create_task(conn, title="managed strict recovery", assignee="builder")
        assert kb.claim_task(conn, strict_id) is not None
        assert kb.reclaim_task(
            conn,
            strict_id,
            reason="missing worker",
            failure_limit=1,
        )
        strict = kb.get_task(conn, strict_id)
        assert strict.consecutive_failures == 1
        assert strict.status == "blocked"
        gave_up = [
            event for event in kb.list_events(conn, task_id=strict_id)
            if event.kind == "gave_up"
        ]
        assert len(gave_up) == 1
        assert gave_up[0].payload["failure_fingerprint"] == (
            "manual reclaim: missing worker"
        )

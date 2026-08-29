from scripts.check_omner_runtime_overlay import (
    copied_runtime_sources,
    is_runtime_path,
    missing_runtime_sources,
)


def test_runtime_path_filter_excludes_tests_and_non_runtime_assets():
    assert is_runtime_path("gateway/session.py")
    assert is_runtime_path("toolsets.py")
    assert is_runtime_path("skills/devops/sdlc-review/SKILL.md")
    assert not is_runtime_path("tests/gateway/test_matrix.py")
    assert not is_runtime_path("apps/desktop/src/types/hermes.ts")


def test_copy_must_land_at_matching_runtime_path():
    dockerfile = """
COPY --chown=root:root gateway/session.py /opt/hermes/gateway/session.py
COPY gateway/run.py /wrong/place/run.py
"""
    assert copied_runtime_sources(dockerfile) == {"gateway/session.py"}


def test_missing_runtime_file_fails_closed():
    dockerfile = "COPY gateway/session.py /opt/hermes/gateway/session.py\n"
    assert missing_runtime_sources(
        ["gateway/session.py", "gateway/run.py", "tests/gateway/test_matrix.py"],
        dockerfile,
    ) == ["gateway/run.py"]

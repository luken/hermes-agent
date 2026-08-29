#!/usr/bin/env python3
"""Refuse an Omner release image that omits changed Hermes runtime files."""

from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path, PurePosixPath


RUNTIME_TREES = {
    "acp_adapter",
    "agent",
    "gateway",
    "hermes_cli",
    "plugins",
    "skills",
    "tools",
    "tui_gateway",
}


def is_runtime_path(raw_path: str) -> bool:
    path = PurePosixPath(raw_path)
    if not path.parts or path.parts[0] == "tests":
        return False
    if len(path.parts) == 1:
        return path.suffix == ".py"
    if path.parts[0] not in RUNTIME_TREES:
        return False
    return path.suffix == ".py" or (
        path.parts[0] == "skills" and path.name == "SKILL.md"
    )


def copied_runtime_sources(dockerfile: str) -> set[str]:
    sources: set[str] = set()
    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if not line.startswith("COPY "):
            continue
        tokens = shlex.split(line)
        operands = [token for token in tokens[1:] if not token.startswith("--")]
        if len(operands) < 2:
            continue
        destination = operands[-1].rstrip("/")
        for source in operands[:-1]:
            expected = f"/opt/hermes/{source}".rstrip("/")
            if destination == expected:
                sources.add(source)
    return sources


def missing_runtime_sources(changed: list[str], dockerfile: str) -> list[str]:
    copied = copied_runtime_sources(dockerfile)
    return sorted(path for path in changed if is_runtime_path(path) and path not in copied)


def changed_files(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--dockerfile", type=Path, required=True)
    args = parser.parse_args()

    missing = missing_runtime_sources(
        changed_files(args.base),
        args.dockerfile.read_text(encoding="utf-8"),
    )
    if missing:
        print("changed runtime files missing from the deployment image overlay:")
        for path in missing:
            print(f"  {path}")
        return 1
    print("all changed Hermes runtime files are present in the deployment image overlay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

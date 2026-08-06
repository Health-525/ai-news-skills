"""Build a deterministic runtime-only Skill archive."""

from __future__ import annotations

import argparse
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _commit(value: str | None) -> str:
    commit = (value or "").strip().casefold()
    if not commit:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        commit = completed.stdout.strip().casefold()
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ValueError("commit must be a full 40-character Git hash")
    return commit


def _ensure_runtime_clean() -> None:
    completed = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "SKILL.md",
            "agents",
            "references",
            "scripts",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.stdout.strip():
        raise ValueError("runtime files must be committed before packaging")


def runtime_files() -> list[Path]:
    files = [ROOT / "SKILL.md", ROOT / "scripts" / "daily_pipeline.py"]
    files.extend((ROOT / "agents").glob("**/*"))
    files.extend((ROOT / "references").glob("**/*"))
    files.extend((ROOT / "scripts" / "radar").glob("**/*.py"))
    files.extend(
        (
            ROOT / "scripts" / "self_test.py",
            ROOT / "scripts" / "send_feishu_card.mjs",
        )
    )
    return sorted(
        {path for path in files if path.is_file() and "__pycache__" not in path.parts},
        key=lambda path: path.relative_to(ROOT).as_posix(),
    )


def build_archive(output: Path, commit: str) -> dict[str, object]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = runtime_files()
    if not files or ROOT / "SKILL.md" not in files:
        raise ValueError("runtime file set is incomplete")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, path.read_bytes())
        marker = zipfile.ZipInfo(".deployment-commit", date_time=(2020, 1, 1, 0, 0, 0))
        marker.compress_type = zipfile.ZIP_DEFLATED
        marker.external_attr = 0o600 << 16
        archive.writestr(marker, f"{commit}\n".encode("ascii"))
    return {
        "status": "built",
        "output": str(output),
        "commit": commit,
        "files": len(files) + 1,
        "bytes": output.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--commit")
    args = parser.parse_args()
    try:
        _ensure_runtime_clean()
        result = build_archive(args.output, _commit(args.commit))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

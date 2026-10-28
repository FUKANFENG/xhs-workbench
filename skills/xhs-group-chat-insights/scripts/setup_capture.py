#!/usr/bin/env python3
"""Install the pinned local runtime used by xhs-group-chat-insights."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from xhs_ext.private_paths import ensure_private_dir


SPIDER_URL = "https://github.com/cv-cat/Spider_XHS.git"
SPIDER_COMMIT = "23dab803a0465e35e01cf87137353a900b47e93d"
DEFAULT_RUNTIME = Path.home() / ".local" / "share" / "xhs-group-chat-insights" / "Spider_XHS"
DEFAULT_COOKIE = Path.home() / ".config" / "xhs-group-chat-insights" / "xhs-cookie.txt"


def run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def command_version(name: str, argument: str = "--version") -> tuple[str | None, str | None]:
    executable = shutil.which(name)
    if not executable:
        return None, None
    completed = subprocess.run(
        [executable, argument], capture_output=True, text=True, check=False, timeout=10
    )
    value = (completed.stdout or completed.stderr).strip().splitlines()
    return executable, (value[0] if value else None)


def major_version(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"v?(\d+)(?:\.\d+)", value)
    return int(match.group(1)) if match else None


def current_commit(root: Path) -> str | None:
    if not (root / ".git").exists() or not shutil.which("git"):
        return None
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def runtime_python(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def inspect(runtime_root: Path, cookie_file: Path) -> dict:
    git_path, git_version = command_version("git")
    node_path, node_version = command_version("node")
    npm_path, npm_version = command_version("npm")
    return {
        "runtime_root": str(runtime_root),
        "runtime_exists": runtime_root.exists(),
        "runtime_commit": current_commit(runtime_root),
        "tested_commit": SPIDER_COMMIT,
        "python": sys.executable,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "python_version_ok": sys.version_info >= (3, 10),
        "git": git_path,
        "git_version": git_version,
        "node": node_path,
        "node_version": node_version,
        "node_version_ok": bool(major_version(node_version) and major_version(node_version) >= 20),
        "npm": npm_path,
        "npm_version": npm_version,
        "cookie_file": str(cookie_file),
        "cookie_exists": cookie_file.is_file(),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Install the pinned Spider_XHS runtime for read-only group capture"
    )
    result.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    result.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE)
    result.add_argument(
        "--check",
        action="store_true",
        help="report prerequisites and planned paths without writing or downloading",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.runtime_root.expanduser().resolve()
    cookie = args.cookie_file.expanduser().resolve()
    status = inspect(root, cookie)
    if args.check:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if (
            status["python_version_ok"]
            and status["git"]
            and status["node_version_ok"]
            and status["npm"]
        ) else 2

    if not status["python_version_ok"]:
        raise RuntimeError("Python 3.10 or newer is required")
    if not status["git"]:
        raise RuntimeError("Git is required")
    if not status["node_version_ok"]:
        raise RuntimeError("Node.js 20 or newer is required")
    if not status["npm"]:
        raise RuntimeError("npm is required")

    if root.exists():
        commit = current_commit(root)
        if commit != SPIDER_COMMIT:
            raise RuntimeError(
                f"existing runtime is not the tested commit: {root}; "
                "use a different --runtime-root or inspect it manually"
            )
    else:
        ensure_private_dir(root.parent, tighten_existing_leaf=False)
        run(["git", "clone", "--no-checkout", SPIDER_URL, str(root)])
        run(["git", "-C", str(root), "checkout", SPIDER_COMMIT])

    python = runtime_python(root)
    if not python.is_file():
        run([sys.executable, "-m", "venv", str(root / ".venv")])
    run([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")])
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-r",
            str(Path(__file__).with_name("requirements-capture.txt")),
        ]
    )
    run([status["npm"], "ci"], cwd=root)
    run(
        [
            str(python),
            "-c",
            (
                "import qrcode; "
                "from apis.xhs_pc_login_apis import XHSLoginApi; "
                "from apis.xhs_live import XHSLiveAPI; "
                "assert hasattr(XHSLoginApi, 'generate_qrcode'); "
                "assert hasattr(XHSLiveAPI, 'get_group_message_history')"
            ),
        ],
        cwd=root,
    )

    cookie_parent_existed = cookie.parent.exists()
    ensure_private_dir(cookie.parent, tighten_existing_leaf=False)
    if os.name == "posix":
        if not cookie_parent_existed:
            cookie.parent.chmod(0o700)
        if cookie.exists():
            cookie.chmod(0o600)

    print(
        json.dumps(
            {
                "status": "runtime_ready",
                "runtime_root": str(root),
                "runtime_python": str(python),
                "cookie_file": str(cookie),
                "next_step": "run xhs_group_tool.py login-qr",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

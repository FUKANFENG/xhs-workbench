#!/usr/bin/env python3
"""Portable read-only wrapper for Xiaohongshu group capture and topic export."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from xhs_ext.private_paths import ensure_private_dir


TESTED_SPIDER_COMMIT = "23dab803a0465e35e01cf87137353a900b47e93d"
DEFAULT_SPIDER_ROOT = Path.home() / ".local" / "share" / "xhs-group-chat-insights" / "Spider_XHS"
DEFAULT_COOKIE_FILE = Path.home() / ".config" / "xhs-group-chat-insights" / "xhs-cookie.txt"


def spider_root(value: Path | None = None) -> Path:
    configured = value or (
        Path(os.environ["XHS_SPIDER_ROOT"])
        if os.environ.get("XHS_SPIDER_ROOT")
        else DEFAULT_SPIDER_ROOT
    )
    root = configured.expanduser().resolve()
    required = [
        root / "apis" / "xhs_live.py",
        root / "xhs_utils" / "xhs_pc" / "__init__.py",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Spider_XHS runtime is incomplete; missing: " + ", ".join(missing))
    return root


def cookie_path(value: Path | None = None) -> Path:
    configured = value or (
        Path(os.environ["XHS_COOKIE_FILE"])
        if os.environ.get("XHS_COOKIE_FILE")
        else DEFAULT_COOKIE_FILE
    )
    return configured.expanduser().resolve()


def private_dir(path: Path) -> Path:
    return ensure_private_dir(path, tighten_existing_leaf=False)


def write_private(path: Path, value: str, overwrite: bool = False) -> Path:
    path = path.expanduser().resolve()
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    private_dir(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    if os.name == "posix":
        path.chmod(0o600)
    return path


def runtime_python(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def reexec_in_runtime(args: argparse.Namespace) -> None:
    if args.command not in {"doctor", "login-qr", "group-chat"}:
        return
    root = spider_root(args.spider_root)
    target = runtime_python(root)
    if not target.is_file():
        return
    if Path(sys.executable).absolute() == target.absolute():
        return
    os.execv(str(target), [str(target), str(Path(__file__).resolve()), *sys.argv[1:]])


def add_runtime(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--spider-root",
        type=Path,
        help=(
            "Spider_XHS checkout; defaults to XHS_SPIDER_ROOT or "
            f"{DEFAULT_SPIDER_ROOT}"
        ),
    )


def add_cookie(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cookie-file",
        type=Path,
        help=(
            "private Cookie file; defaults to XHS_COOKIE_FILE or "
            f"{DEFAULT_COOKIE_FILE}"
        ),
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    root = spider_root(args.spider_root)
    sys.path.insert(0, str(root))
    os.environ["NODE_PATH"] = str(root / "node_modules")
    result = {
        "runtime_root": str(root),
        "python": sys.executable,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "python_version_ok": sys.version_info >= (3, 10),
        "tested_spider_commit": TESTED_SPIDER_COMMIT,
        "xhs_live_api_exists": (root / "apis" / "xhs_live.py").is_file(),
        "xhs_pc_auth_exists": (root / "xhs_utils" / "xhs_pc").is_dir(),
        "node_modules_exists": (root / "node_modules").is_dir(),
    }
    result["node_dependencies_ok"] = False
    result["missing_node_dependencies"] = []
    package_json = root / "package.json"
    if package_json.is_file() and (root / "node_modules").is_dir():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
            dependency_names = set((package.get("dependencies") or {}).keys())
            dependency_names.update((package.get("optionalDependencies") or {}).keys())
            missing = [
                name
                for name in sorted(dependency_names)
                if not (root / "node_modules" / Path(name)).exists()
            ]
            result["missing_node_dependencies"] = missing
            result["node_dependencies_ok"] = not missing
        except Exception as error:
            result["node_dependency_check_error"] = f"{type(error).__name__}: {error}"
    node = shutil.which("node")
    result["node_executable"] = node
    result["node_version"] = None
    result["node_version_ok"] = False
    if node:
        completed = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=False, timeout=5
        )
        version = completed.stdout.strip()
        result["node_version"] = version
        match = re.fullmatch(r"v?(\d+)(?:\.\d+){0,2}", version)
        result["node_version_ok"] = bool(match and int(match.group(1)) >= 20)

    result["runtime_git_commit"] = None
    result["tested_commit_match"] = None
    result["runtime_commit_acceptable"] = False
    if (root / ".git").exists() and shutil.which("git"):
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if completed.returncode == 0:
            result["runtime_git_commit"] = completed.stdout.strip()
            result["tested_commit_match"] = (
                result["runtime_git_commit"] == TESTED_SPIDER_COMMIT
            )
            result["runtime_commit_acceptable"] = result["tested_commit_match"]
    try:
        from apis.xhs_live import XHSLiveAPI
        from apis.xhs_pc_login_apis import XHSLoginApi
        from xhs_utils.xhs_pc import XHSPcAuth  # noqa: F401
        import qrcode  # noqa: F401

        login_methods = {
            "generate_init_cookies",
            "generate_qrcode",
            "check_qrcode_status",
            "ensure_webprofile",
            "get_user_info",
            "cookies_to_str",
            "close",
        }

        result.update(
            runtime_import_ok=True,
            qr_library_import_ok=True,
            login_api_methods_ok=all(
                hasattr(XHSLoginApi, method) for method in login_methods
            ),
            cookie_auth_factory_exists=hasattr(XHSPcAuth, "from_cookie"),
            group_history_method_exists=hasattr(
                XHSLiveAPI, "get_group_message_history"
            ),
        )
    except Exception as error:
        result.update(
            runtime_import_ok=False,
            qr_library_import_ok=False,
            login_api_methods_ok=False,
            cookie_auth_factory_exists=False,
            group_history_method_exists=False,
            runtime_import_error=f"{type(error).__name__}: {error}",
        )
    try:
        path = cookie_path(args.cookie_file)
    except FileNotFoundError:
        result.update(cookie_configured=False, cookie_exists=False)
    else:
        result.update(
            cookie_configured=True,
            cookie_file=str(path),
            cookie_exists=path.is_file(),
            cookie_mode=(oct(path.stat().st_mode & 0o777) if path.is_file() else None),
            cookie_private=(
                (os.name != "posix" or path.stat().st_mode & 0o077 == 0)
                if path.is_file()
                else False
            ),
        )
        result["cookie_valid"] = False
        result["cookie_validation_error"] = None
        if path.is_file():
            try:
                from xhs_ext.group_chat_exporter import read_cookie_file

                raw_cookie = read_cookie_file(path)
                names = {
                    item.split("=", 1)[0].strip()
                    for item in raw_cookie.split(";")
                    if "=" in item
                }
                if "web_session" not in names:
                    raise ValueError("cookie file must contain web_session")
                result["cookie_valid"] = True
            except Exception as error:
                result["cookie_validation_error"] = (
                    f"{type(error).__name__}: {error}"
                )
    result["ready_for_capture"] = bool(
        result["xhs_live_api_exists"]
        and result["xhs_pc_auth_exists"]
        and result["node_modules_exists"]
        and result["node_dependencies_ok"]
        and result["runtime_commit_acceptable"]
        and result["python_version_ok"]
        and result["node_version_ok"]
        and result["runtime_import_ok"]
        and result["cookie_auth_factory_exists"]
        and result["group_history_method_exists"]
        and result["cookie_exists"]
        and result.get("cookie_private", False)
        and result.get("cookie_valid", False)
    )
    result["ready_for_qr_login"] = bool(
        result["python_version_ok"]
        and result["node_version_ok"]
        and result["node_modules_exists"]
        and result["node_dependencies_ok"]
        and result["runtime_commit_acceptable"]
        and result["runtime_import_ok"]
        and result["qr_library_import_ok"]
        and result["login_api_methods_ok"]
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready_for_capture"] else 2


def cmd_login_qr(args: argparse.Namespace) -> int:
    root = spider_root(args.spider_root)
    sys.path.insert(0, str(root))
    os.environ["NODE_PATH"] = str(root / "node_modules")

    from apis.xhs_pc_login_apis import XHSLoginApi
    from xhs_ext.login_diagnostics import LoginDiagnostics
    import qrcode

    target = cookie_path(args.cookie_file)
    if target.exists() and not args.overwrite:
        raise FileExistsError(f"Cookie file exists; use --overwrite: {target}")
    qr_file = args.qr_file.expanduser().resolve() if args.qr_file else target.with_name(
        f"xhs-login-qr-{int(time.time())}.png"
    )
    if qr_file.exists() and not args.overwrite:
        raise FileExistsError(f"QR file exists; use --overwrite: {qr_file}")
    login = XHSLoginApi()
    diagnostic = LoginDiagnostics(login)
    qr_touched = False
    try:
        cookies = login.generate_init_cookies()
        diagnostic.set_stage("generating_qr")
        ok, message, qr_data = login.generate_qrcode(cookies)
        if not ok:
            raise RuntimeError(f"QR generation failed: {message}")
        cookies = qr_data["cookies"]

        diagnostic.set_stage("preflight")
        ok, status, cookies = login.check_qrcode_status(
            qr_data["qr_id"], qr_data["code"], cookies
        )
        if ok or status != "请扫描二维码":
            raise RuntimeError(f"QR preflight failed: {status}")
        diagnostic.set_stage("preparing_web_profile")
        login.ensure_webprofile(cookies)

        private_dir(qr_file.parent)
        qr_image = qrcode.make(qr_data["qr_url"])
        qr_touched = True
        qr_image.save(qr_file)
        if os.name == "posix":
            qr_file.chmod(0o600)
        print(f"qr_file={qr_file}", flush=True)
        print("status=waiting_for_scan", flush=True)

        diagnostic.set_stage("waiting_for_scan")
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            ok, status, cookies = login.check_qrcode_status(
                qr_data["qr_id"], qr_data["code"], cookies
            )
            if ok:
                diagnostic.set_stage("verifying_session")
                valid, user_info, cookies = login.get_user_info(cookies)
                if not valid or user_info.get("guest") is not False:
                    raise RuntimeError(
                        "QR confirmation did not produce an authenticated session"
                    )
                diagnostic.set_stage("saving_credentials")
                write_private(
                    target,
                    login.cookies_to_str(cookies) + "\n",
                    overwrite=args.overwrite,
                )
                print(f"status=login_succeeded cookie_file={target}", flush=True)
                return 0
            if "过期" in str(status):
                raise RuntimeError("QR code expired")
            diagnostic.set_stage("waiting_for_confirmation" if status == "请确认登录" else "waiting_for_scan")
            time.sleep(2)
        raise TimeoutError("timed out waiting for QR scan")
    except Exception as error:
        diagnostic.failed(error)
        raise
    finally:
        try:
            login.close()
        finally:
            if qr_touched:
                try:
                    qr_file.unlink()
                except OSError:
                    pass


def cmd_group_chat(args: argparse.Namespace) -> int:
    root = spider_root(args.spider_root)
    cookie = cookie_path(args.cookie_file)
    if not cookie.is_file():
        raise FileNotFoundError(f"Cookie file not found: {cookie}")
    if os.name == "posix" and cookie.stat().st_mode & 0o077:
        raise PermissionError("Cookie file must be private; run chmod 600 on it")

    sys.path.insert(0, str(root))
    os.environ["NODE_PATH"] = str(root / "node_modules")

    from xhs_ext.group_chat_exporter import (
        fetch_group_history,
        load_resume_state,
        parse_group_id,
        read_cookie_file,
        write_export,
    )
    from xhs_ext.xhs_im_apis import XHS_IM_Apis

    group_id = parse_group_id(args.chat_url)
    cookies = read_cookie_file(cookie)
    output_root = args.output_dir.expanduser().resolve()
    existing_json = output_root / group_id / "messages.json"
    if existing_json.exists() and not (args.resume or args.overwrite or args.update):
        raise FileExistsError(
            "an export already exists; use --update, --resume or --overwrite"
        )

    initial_messages = []
    initial_last_id = str(args.last_id)
    prior_pages = 0
    prior_completed = False
    if args.resume or args.update:
        resume = load_resume_state(output_root, group_id)
        if args.resume and resume["completed"]:
            print("checkpoint is already complete; nothing to resume")
            return 0
        initial_messages = resume["messages"]
        initial_last_id = "0" if args.update else resume["next_last_id"]
        prior_pages = resume["pages_fetched"]
        prior_completed = resume["completed"]
    if args.update and not existing_json.exists():
        raise FileNotFoundError("--update requires an existing export")

    def save_progress(state: dict) -> None:
        state["pages_fetched"] += prior_pages
        write_export(state, output_root)
        state["pages_fetched"] -= prior_pages
        print(
            f"page {state['pages_fetched'] + prior_pages}: "
            f"{len(state['messages'])} unique messages"
        )

    api = XHS_IM_Apis()
    try:
        state = fetch_group_history(
            api=api,
            group_id=group_id,
            cookies_str=cookies,
            initial_last_id=initial_last_id,
            max_pages=args.max_pages,
            limit=args.limit,
            delay=args.delay,
            initial_messages=initial_messages,
            stop_at_known_page=args.update,
            on_page=None if args.update else save_progress,
        )
    finally:
        api.close()

    if args.update and not state["completed"]:
        raise RuntimeError(
            "Incremental update reached the page limit before reconnecting to saved history; "
            "existing files were preserved. Increase --max-pages and retry."
        )

    state["pages_fetched"] += prior_pages
    if args.update:
        new_message_count = len(state["messages"]) - len(initial_messages)
        state["next_last_id"] = resume["next_last_id"]
        state["completed"] = prior_completed
    paths = write_export(state, output_root)
    print(f"exported {len(state['messages'])} unique messages")
    if args.update:
        print(f"added {new_message_count} new messages")
    print(f"json: {paths['json']}")
    print(f"csv: {paths['csv']}")
    print(f"checkpoint: {paths['checkpoint']}")
    if not state["completed"] and not args.update:
        print("page limit reached; run again with --resume to continue")
    return 0


def cmd_group_topics(args: argparse.Namespace) -> int:
    from xhs_ext.group_topic_exporter import write_topic_export

    result, paths = write_topic_export(
        args.messages_json,
        args.output_dir,
        overwrite=args.overwrite,
        unreplied_posts_only=args.unreplied_only,
    )
    print(
        f"selected_topics={result['topic_count']} total_topics={result['total_topic_count']} "
        f"source_messages={result['source_message_count']} "
        f"selected_topic_posts={result['selected_topic_post_count']} "
        f"json={paths['json']} csv={paths['csv']}"
    )
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description="Read-only Xiaohongshu group-chat helper")
    sub = top.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check runtime and Cookie readiness")
    add_runtime(doctor)
    add_cookie(doctor)
    doctor.set_defaults(func=cmd_doctor)

    login = sub.add_parser("login-qr", help="create a private Cookie file by QR login")
    add_runtime(login)
    add_cookie(login)
    login.add_argument("--qr-file", type=Path)
    login.add_argument("--timeout", type=int, default=180)
    login.add_argument("--overwrite", action="store_true")
    login.set_defaults(func=cmd_login_qr)

    group = sub.add_parser("group-chat", help="export an authorized group chat")
    group.add_argument("--chat-url", required=True)
    add_runtime(group)
    add_cookie(group)
    group.add_argument("--output-dir", required=True, type=Path)
    group.add_argument("--last-id", default="0")
    group.add_argument("--max-pages", type=int, default=1000)
    group.add_argument("--limit", type=int, default=30)
    group.add_argument("--delay", type=float, default=1.2)
    modes = group.add_mutually_exclusive_group()
    modes.add_argument("--resume", action="store_true")
    modes.add_argument("--update", action="store_true")
    modes.add_argument("--overwrite", action="store_true")
    group.set_defaults(func=cmd_group_chat)

    topics = sub.add_parser("group-topics", help="extract topics from an existing export")
    topics.add_argument("--messages-json", required=True, type=Path)
    topics.add_argument("--output-dir", required=True, type=Path)
    topics.add_argument("--unreplied-only", action="store_true")
    topics.add_argument("--overwrite", action="store_true")
    topics.set_defaults(func=cmd_group_topics)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        reexec_in_runtime(args)
        return args.func(args)
    except KeyboardInterrupt:
        print("error=Interrupted: cancelled by user", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"error={type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

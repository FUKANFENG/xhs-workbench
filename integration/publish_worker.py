"""Bounded adapter for dreammis/social-auto-upload (MIT), pinned by revision.

Reuse login and metadata entry, never the upstream final-submit retry loops.
Only explicit --action publish can select files or submit a note.
"""
import argparse
import asyncio
import contextlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from webapp.publish_store import PublishStore
from webapp.publishing import REVISION

CREATOR = "https://creator.xiaohongshu.com"
NOTE_LIST = "/api/galaxy/v2/creator/note/user/posted"
OUTPUT = sys.stdout


class PublishFailure(RuntimeError):
    pass


def emit(stage):
    OUTPUT.write("publish_progress=" + json.dumps({"stage": stage}) + "\n")
    OUTPUT.flush()


def atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
            json.dump(value, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def note_rows(body):
    """Only inspect own-note IDs and titles; do not retain complete responses."""
    rows = {}
    def visit(value):
        if isinstance(value, dict):
            note_id = next((value.get(k) for k in ("note_id", "noteId", "id") if re.fullmatch(r"[a-f0-9]{24}", str(value.get(k, "")))), None)
            title = next((value.get(k) for k in ("title", "display_title", "note_title") if isinstance(value.get(k), str)), None)
            if note_id and title is not None:
                rows[note_id] = {"id": note_id, "title": title}
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(body)
    return list(rows.values())


def matched_note(notes, title, verification):
    baseline = set(verification.get("baseline", []))
    expected = verification.get("expected_id")
    matches = [note for note in notes if note["id"] not in baseline and note["title"] == title and (not expected or note["id"] == expected)]
    return matches[0] if len(matches) == 1 else None


async def submit_once(page, store, draft, baseline, lookup):
    """Persist the irreversible boundary before dispatching exactly one click."""
    verification = {"baseline": [n["id"] for n in baseline], "started_at": time.time()}
    store.begin_submit(draft["id"], verification)
    emit("submitting")
    try:
        await page.get_by_role("button", name="发布", exact=True).click(timeout=10000)
        try:
            await page.wait_for_url("**/publish/success?**", timeout=30000)
        except Exception:
            pass  # A missing redirect never permits another click.
        query = parse_qs(urlsplit(page.url).query)
        expected = next((query[k][0] for k in ("noteId", "note_id", "id") if k in query and re.fullmatch(r"[a-f0-9]{24}", query[k][0])), None)
        if expected:
            verification["expected_id"] = expected
            store.mark(draft["id"], "submitting", _verification=verification)
        emit("verifying")
        for attempt in range(3):
            found = matched_note(await lookup(), draft["title"], verification)
            if found:
                store.mark(draft["id"], "published", note_id=found["id"], verified_at=time.time())
                emit("published")
                return
            if attempt < 2:
                await asyncio.sleep(3)
    except Exception:
        store.mark(draft["id"], "uncertain")
        raise PublishFailure("publish_uncertain") from None
    store.mark(draft["id"], "uncertain")
    raise PublishFailure("publish_uncertain")


async def read_notes(context):
    page = await context.new_page()
    try:
        async with page.expect_response(lambda r: urlsplit(r.url).hostname == "creator.xiaohongshu.com" and urlsplit(r.url).path == NOTE_LIST, timeout=30000) as pending:
            await page.goto(CREATOR + "/new/note-manager", wait_until="domcontentloaded", timeout=45000)
        response = await pending.value
        body = await response.json()
        if response.status != 200 or not isinstance(body, dict) or body.get("success") is False or body.get("code", 0) != 0 or not isinstance(body.get("data"), (dict, list)):
            raise PublishFailure("publish_verify")
        return note_rows(body)
    finally:
        await page.close()


async def ensure_creator(context):
    page = await context.new_page()
    try:
        await page.goto(CREATOR + "/publish/publish?from=homepage&target=video", wait_until="domcontentloaded", timeout=45000)
        await page.locator('input[type="file"], div[class*="login-box"]').first.wait_for(state="attached", timeout=20000)
        login = page.locator('div[class*="login-box"]').first
        if urlsplit(page.url).hostname != "creator.xiaohongshu.com" or not urlsplit(page.url).path.startswith("/publish/") or (await login.count() and await login.is_visible()) or not await page.locator('input[type="file"]').count():
            raise PublishFailure("publish_auth")
    finally:
        await page.close()


def load_upstream(runtime, auth):
    revision = subprocess.run(["git", "-C", str(runtime), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if revision != REVISION or importlib.metadata.version("patchright") != "1.58.2":
        raise PublishFailure("publish_runtime")
    os.environ.pop("SAU_XHS_CREATOR_BASE_URL", None)
    sys.path.insert(0, str(runtime))
    import conf
    # Upstream configures file loggers at import. Keep them private and disable
    # them before any browser state or content enters the uploader.
    private = auth / "environment"
    private.mkdir(exist_ok=True)
    conf.BASE_DIR = private
    from utils import log
    from loguru import logger
    logger.remove()
    conf.BASE_DIR = runtime
    conf.DEBUG_MODE = False
    conf.LOCAL_CHROME_HEADLESS = True
    from uploader.xiaohongshu_uploader import main as upstream
    from patchright.async_api import async_playwright
    from utils.base_social_media import set_init_script
    return upstream, async_playwright, set_init_script, logger


async def run(args):
    auth = args.data / "auth" / "publisher"
    auth.mkdir(parents=True, exist_ok=True)
    state_file, qr = auth / "storage-state.json", auth / "login-qr.png"
    upstream, playwright_factory, init_script, logger = load_upstream(args.runtime, auth)
    store = PublishStore(args.data / "publishing")
    if args.action == "login":
        candidate = auth / ("candidate-" + uuid.uuid4().hex + ".json")
        async def on_qr(info):
            source = Path(info["image_path"])
            if source.is_file() and source.parent.resolve() == auth.resolve():
                temporary = qr.with_suffix(".tmp")
                shutil.copyfile(source, temporary)
                os.chmod(temporary, 0o600)
                os.replace(temporary, qr)
                emit("qr")
        try:
            emit("login")
            result = await upstream.xiaohongshu_cookie_gen(str(candidate), qrcode_callback=on_qr, headless=True, max_checks=90)
            if not result.get("success"):
                raise PublishFailure("publish_login")
            async with playwright_factory() as playwright:
                browser = await playwright.chromium.launch(headless=True, channel="chromium")
                try:
                    context = await browser.new_context(storage_state=str(candidate))
                    await init_script(context)
                    await ensure_creator(context)
                finally:
                    await browser.close()
            atomic_json(state_file, json.loads(candidate.read_text(encoding="utf-8")))
            atomic_json(auth / "checked.json", {"state": "valid", "checked_at": time.time()})
            emit("logged_in")
        finally:
            candidate.unlink(missing_ok=True)
            try:
                qr.unlink(missing_ok=True)
            except OSError:
                pass
        return
    if not state_file.is_file():
        raise PublishFailure("publish_auth")
    async with playwright_factory() as playwright:
        browser = await playwright.chromium.launch(headless=True, channel="chromium")
        try:
            context = await browser.new_context(storage_state=str(state_file))
            await init_script(context)
            emit("auth")
            try:
                await ensure_creator(context)
            except PublishFailure:
                atomic_json(auth / "checked.json", {"state": "invalid", "checked_at": time.time()})
                raise
            atomic_json(auth / "checked.json", {"state": "valid", "checked_at": time.time()})
            if args.action in {"check", "inspect"}:
                if args.action == "inspect":
                    notes = await read_notes(context)
                    OUTPUT.write("publish_inspection=" + json.dumps({"creator_ready": True, "note_list_readable": True, "note_count": len(notes), "uploaded_files": False, "submitted": False}) + "\n")
                emit("checked")
                return
            draft = store.get(args.draft, private=True)
            if args.action == "verify":
                if draft["state"] != "uncertain" or not draft.get("_verification"):
                    raise PublishFailure("publish_verify")
                found = matched_note(await read_notes(context), draft["title"], draft["_verification"])
                if not found:
                    raise PublishFailure("publish_uncertain")
                store.mark(draft["id"], "published", note_id=found["id"], verified_at=time.time())
                emit("published")
                return
            if draft["state"] != "queued":
                raise PublishFailure("publish_frozen")
            store.mark(draft["id"], "preparing")
            baseline = await read_notes(context)  # Fail before uploading if results cannot be checked.
            paths = [str(store.asset_path(draft["id"], a["id"])) for a in draft["assets"]]
            if any(not Path(p).is_file() for p in paths):
                raise PublishFailure("publish_media")
            page = await context.new_page()
            emit("uploading")
            await page.goto(CREATOR + "/publish/publish?from=homepage&target=" + draft["kind"], wait_until="domcontentloaded", timeout=45000)
            if draft["kind"] == "image":
                tab = page.get_by_text("上传图文", exact=True)
                await tab.last.wait_for(state="visible", timeout=15000)
                await tab.last.click()
                upload = page.locator('input[type="file"][accept*=".jpg"], input[type="file"][accept*="image"]').last
                uploader = upstream.XiaoHongShuNote(image_paths=paths, title=draft["title"], desc=draft["body"], note=draft["body"], tags=draft["tags"], publish_date=0, account_file=str(state_file), headless=True)
            else:
                upload = page.locator('input[type="file"]').first
                uploader = upstream.XiaoHongShuVideo(title=draft["title"], file_path=paths[0], desc=draft["body"], tags=draft["tags"], publish_date=0, account_file=str(state_file), headless=True)
            await upload.set_input_files(paths)
            await page.locator('input[placeholder*="填写标题"]').wait_for(state="visible", timeout=480000)
            emit("metadata")
            warnings = []
            sink = logger.add(lambda message: warnings.append(True) if message.record["level"].name == "WARNING" and "话题" in message.record["message"] else None)
            try:
                if not draft["body"]:
                    # An empty requested body must clear any restored editor text.
                    await page.locator('p[data-placeholder*="输入正文描述"]').click()
                    await page.keyboard.press("ControlOrMeta+A")
                    await page.keyboard.press("Backspace")
                await uploader.fill_meta(page)
            finally:
                logger.remove(sink)
            if warnings:
                raise PublishFailure("publish_tags")
            button = page.get_by_role("button", name="发布", exact=True)
            await button.wait_for(state="visible", timeout=30000)
            for _ in range(120):
                if await button.is_enabled():
                    break
                await asyncio.sleep(1)
            else:
                raise PublishFailure("publish_not_ready")
            await submit_once(page, store, draft, baseline, lambda: read_notes(context))
            try:
                atomic_json(state_file, await context.storage_state())
            except OSError:
                pass  # Credential refresh failure cannot reverse a verified post.
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--action", choices=["login", "check", "inspect", "publish", "verify"], required=True)
    parser.add_argument("--draft", default="")
    args = parser.parse_args()
    code = 0
    with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            asyncio.run(asyncio.wait_for(run(args), timeout=330 if args.action == "login" else 1100))
        except Exception as error:
            code = 1
            if args.draft and args.action == "publish":
                try:
                    PublishStore(args.data / "publishing").interrupted(args.draft)
                except Exception:
                    pass
            known = {"publish_auth", "publish_runtime", "publish_login", "publish_uncertain", "publish_verify", "publish_frozen", "publish_media", "publish_tags", "publish_not_ready"}
            message = str(error) if isinstance(error, PublishFailure) and str(error) in known else "publish_failed"
            OUTPUT.write("error=" + message + "\n")
            OUTPUT.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(main())

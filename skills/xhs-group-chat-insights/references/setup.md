# Capture setup and authentication

Offline analysis and topic extraction use only Python's standard library. Live capture also needs the pinned Spider_XHS runtime and a private Cookie file. The Skill provides deterministic setup and QR-login commands; never store credentials in the repository.

## 1. Check prerequisites

```bash
python3 <skill-dir>/scripts/setup_capture.py --check
```

This read-only check requires Git, Python 3.10 or newer, and Node.js 20 or newer. The default locations are:

- Runtime: `~/.local/share/xhs-group-chat-insights/Spider_XHS`
- Cookie: `~/.config/xhs-group-chat-insights/xhs-cookie.txt`

`XHS_SPIDER_ROOT`, `XHS_COOKIE_FILE`, `--spider-root`, and `--cookie-file` can override the defaults for the capture CLI. Pass matching `--runtime-root` and `--cookie-file` values to the setup script when using custom locations.

## 2. Install the capture runtime

Installing downloads an external repository and dependencies. Obtain user approval before running:

```bash
python3 <skill-dir>/scripts/setup_capture.py
```

The script:

- clones Spider_XHS into the user-local data directory;
- checks out locally verified runtime commit `23dab803a0465e35e01cf87137353a900b47e93d` (2026-10-22 login fixes; see `README_LOCAL.md` for the local deployment);
- creates an isolated `.venv`;
- installs the pinned checkout's Python and Node dependencies;
- creates a private configuration directory without creating or exposing a Cookie.

It refuses to replace or switch an existing runtime at another commit. Spider_XHS is an external project and is not bundled with this skill. Its public README currently says it is for learning and exchange only and forbids commercial use. Follow its current usage notes, platform rules, and applicable law.

## 3. Authenticate by QR

Run this in a PTY. When it prints `qr_file`, show that local PNG to the user and keep the process running while they scan and confirm in the Xiaohongshu app:

```bash
python3 <skill-dir>/scripts/xhs_group_tool.py login-qr
```

The wrapper automatically re-launches itself with the runtime's virtual-environment Python. On success it writes the Cookie file with mode `600` and never prints the Cookie value. It refuses to replace an existing Cookie unless the user explicitly authorizes a replacement and `--overwrite` is supplied.

If the QR expires, run the command again. Stop on repeated failures, CAPTCHA, or platform-flow changes; do not bypass controls.

## 4. Verify readiness

```bash
python3 <skill-dir>/scripts/xhs_group_tool.py doctor
```

`ready_for_capture` is true only when the Python and Node versions, runtime imports, group-history method, Node dependencies, Cookie contents, and private file permissions pass. The command reports only paths and booleans, never credential values.

## Manual Cookie fallback

If the user explicitly prefers an existing authorized Cookie, save the raw `Cookie` request header on one line at the configured Cookie path, ensure it contains `a1` and `web_session`, and run `chmod 600`. Never paste it into a prompt, shared terminal transcript, issue, commit, or report.

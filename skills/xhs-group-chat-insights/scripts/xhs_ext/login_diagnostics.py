"""Login diagnostics containing only stages, numeric codes and source locations."""

import json
import sys
import traceback
from pathlib import Path
from urllib.parse import urlsplit


class LoginDiagnostics:
    def __init__(self, login):
        self.stage = "initializing_device"
        self.response = {}
        self.request = login.http.request
        login.http.request = self.observe_request

    def set_stage(self, stage):
        if stage != self.stage:
            self.stage = stage
            self.response = {}
            print("login_progress=" + stage, flush=True)

    def observe_request(self, method, url, **kwargs):
        if urlsplit(url).path == "/api/sns/web/v1/login/qrcode/status":
            self.set_stage("exchanging_session")
        self.response = {}
        response = self.request(method, url, **kwargs)
        self.response["http_status"] = response.status_code
        try:
            body = response.json()
        except ValueError:
            return response
        if isinstance(body, dict):
            if type(body.get("code")) is int:
                self.response["api_code"] = body["code"]
            if type(body.get("success")) is bool:
                self.response["api_success"] = body["success"]
            data = body.get("data")
            if isinstance(data, dict):
                for key in ("codeStatus", "code_status", "guest"):
                    if type(data.get(key)) in (int, bool):
                        self.response[key] = data[key]
        return response

    def failed(self, error):
        # Do not serialize exception messages, request URLs, headers or bodies.
        frames = traceback.extract_tb(error.__traceback__)
        diagnostic = {"stage": self.stage, **self.response,
                      "error_type": type(error).__name__}
        if frames:
            frame = frames[-1]
            diagnostic["source"] = f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        print("login_diagnostic=" + json.dumps(diagnostic), file=sys.stderr, flush=True)

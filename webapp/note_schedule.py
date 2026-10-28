"""Serial per-note scheduling on the existing shared job runner."""

import time

from .runtime import BusyError


class NoteSchedule:
    def __init__(self, service):
        self.service = service
        self.pending = {}
        service.notes.pause_monitors_for_restart()

    def tick(self):
        service = self.service
        with service.lock:
            if service.runner.busy:
                return
            due = next((item for item in service.notes.monitors()
                        if item["enabled"] and (item["next_run"] or 0) <= time.time()), None)
            if not due:
                return
            try:
                job = service.start_note_job({"mode": "comments", "note_id": due["note_id"],
                                              "max_comments": due["max_comments"], "include_replies": due["include_replies"],
                                              "max_replies": due["max_replies"]})
                self.pending[job["id"]] = (due["note_id"], due["version"])
            except BusyError:
                return
            except Exception:
                service.notes.finish_monitor(due["note_id"], due["version"], "failed", "定时任务未能启动，请检查登录和运行环境。")

    def finished(self, job):
        task = self.pending.pop(job["id"], None)
        if task:
            self.service.notes.finish_monitor(*task, job["status"], job.get("message", "任务已停止。"))

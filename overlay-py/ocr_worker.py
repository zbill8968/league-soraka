"""Long-lived OCR worker driven by files instead of stdio pipes.

The panel runs as a windowed process (no console) once packaged, and a child
PowerShell created from such a process does not get usable stdin/stdout handles -
the old pipe protocol then stalled until the 6 s timeout, which is exactly the
"click once, wait, click again" behaviour users saw.

Protocol (all files live in <data>/tmp):
  ready.flag   written by the worker once the OCR engine is up
  request.txt  "imagePath|outFile" written here
  response.txt "done" written by the worker when outFile is complete
"""

import json
import os
import subprocess
import threading
import time

CREATE_NO_WINDOW = 0x08000000


class OcrWorker:
    def __init__(self, script, language="zh-Hans-CN", work_dir=None):
        self.script = script
        self.language = language
        self.work_dir = work_dir
        self.proc = None
        self.lock = threading.Lock()

    # -- paths -------------------------------------------------------------
    def _dir(self):
        return self.work_dir

    def _path(self, name):
        return os.path.join(self._dir(), name)

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self.work_dir is None:
            return False
        if self.proc is not None and self.proc.poll() is None:
            if os.path.exists(self._path("ready.flag")):
                return True
        os.makedirs(self.work_dir, exist_ok=True)
        for name in ("ready.flag", "request.txt", "response.txt"):
            try:
                os.remove(self._path(name))
            except OSError:
                pass
        try:
            self.proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", self.script, "-Language", self.language, "-Dir", self.work_dir],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                close_fds=True,
            )
        except Exception:
            self.proc = None
            return False

        deadline = time.time() + 8
        while time.time() < deadline:
            if os.path.exists(self._path("ready.flag")):
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.03)
        return False

    # -- recognition -------------------------------------------------------
    def recognize(self, image_path, out_path, timeout=8):
        if not self.start():
            return None
        if not self.lock.acquire(timeout=timeout):
            return None
        try:
            for name in ("response.txt", "request.txt"):
                try:
                    os.remove(self._path(name))
                except OSError:
                    pass
            try:
                with open(self._path("request.txt"), "w", encoding="utf-8") as handle:
                    handle.write(f"{image_path}|{out_path}")
            except OSError:
                return None

            deadline = time.time() + timeout
            while time.time() < deadline:
                if os.path.exists(self._path("response.txt")):
                    break
                if self.proc is not None and self.proc.poll() is not None:
                    self.proc = None
                    break
                time.sleep(0.02)
            else:
                return None
        finally:
            self.lock.release()

        try:
            with open(out_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None

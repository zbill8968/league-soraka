"""Keep one Node classifier process alive.

The classifier loads catalog/stats/icon-index once at startup; per-offer requests
then cost a few milliseconds instead of ~0.5 s of Node startup plus JSON parsing.
"""

import json
import queue
import subprocess
import threading

CREATE_NO_WINDOW = 0x08000000


class ClassifyWorker:
    def __init__(self, script, data_dir, node_executable):
        self.script = script
        self.data_dir = data_dir
        self.node = node_executable
        self.proc = None
        self.lines = queue.Queue()
        self.lock = threading.Lock()

    def start(self):
        if self.proc is not None and self.proc.poll() is None:
            return True
        self.proc = subprocess.Popen(
            [self.node, self.script, "--data", self.data_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            self.lines.get(timeout=10)  # "ready"
            return True
        except queue.Empty:
            return False

    def _reader(self):
        stream = self.proc.stdout if self.proc else None
        if stream is None:
            return
        for line in stream:
            self.lines.put(line.strip())

    def classify(self, request, timeout=8):
        if not self.start():
            return None
        with self.lock:
            try:
                self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except Exception:
                self.proc = None
                return None
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty:
                self.proc = None
                return None
        try:
            data = json.loads(line)
        except Exception:
            return None
        return None if "error" in data else data

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None

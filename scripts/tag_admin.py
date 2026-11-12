#!/usr/bin/env python
"""Local tag-tuning backend for League Soraka.

Run: python scripts/tag_admin.py
Open: http://127.0.0.1:8765/

Only binds to loopback. It edits data/tag-overrides.json; tagrules.py merges that
file over the built-in rule table on the next teammate analysis.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))
import tagrules  # noqa: E402

OVERRIDES = os.path.join(DATA, "tag-overrides.json")
PAGE = os.path.join(ROOT, "scripts", "tag-admin.html")
OPS = {">=", "<=", ">", "<", "==", "!="}
BOOL_METRICS = {
    "win", "full_team", "recent5Loss", "recent4Win", "heartsteel", "steelWarmog",
    "mark", "offMeta", "isTank", "isMage", "isMarksman", "isAssassin", "isSupport",
    "isFighter", "isDamageClass", "critAugment", "mathWizard", "has_team",
    "recent5Loss", "recent4Win",
}
PERCENT_METRICS = {
    "winRate", "recent10WinRate", "topChampWinRate", "dmgShare", "takenShare",
    "healShare", "ccShare", "killShare", "assistShare", "deathShare", "goldShare",
    "kp", "killShareMinusDamageShare", "tankIndex", "supportShare", "nightRate",
    "uniqueChampRate", "topChampShare", "tankRate", "mageRate", "marksmanRate",
    "assassinRate", "supportRate", "fighterRate", "damageClassRate",
    "heartsteelRate", "tankItemRate", "supportItemRate", "damageItemRate",
    "steelWarmogRate", "offBuildRate", "offMetaRate", "kheadClassRate",
    "collectorRateInClass", "mathWizardRate", "critAugmentRate",
}
METRIC_LABELS = {
    "dmgShare": "伤害占比", "takenShare": "承伤占比", "healShare": "治疗占比",
    "ccShare": "控制占比", "killShare": "击杀占比", "assistShare": "助攻占比",
    "deathShare": "死亡占比", "goldShare": "金币占比", "winRate": "胜率",
    "kp": "参团率", "killShareMinusDamageShare": "击杀−伤害占比",
    "killsPerMin": "击杀/分", "deathsPerMin": "死亡/分", "assistsPerMin": "助攻/分",
    "dmgPerMin": "伤害/分", "takenPerMin": "承伤/分", "healPerMin": "治疗/分",
    "goldPerMin": "金币/分", "dmgPerKill": "伤害/击杀", "kda": "KDA",
    "min_games": "最少场次", "recent10WinRate": "最近10场胜率",
    "longestMedian": "最长存活中位数", "longest": "最长存活",
}


def read_overrides():
    try:
        with open(OVERRIDES, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_overrides(data):
    os.makedirs(DATA, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="tag-overrides-", suffix=".json", dir=DATA)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        if os.path.exists(OVERRIDES):
            try:
                with open(OVERRIDES, "rb") as src, open(OVERRIDES + ".bak", "wb") as dst:
                    dst.write(src.read())
            except OSError:
                pass
        os.replace(tmp, OVERRIDES)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _default_metrics():
    metrics = set()
    for rule in tagrules.RULES:
        for key in ("when", "per_game"):
            node = rule.get(key)
            stack = [node]
            while stack:
                current = stack.pop()
                if not isinstance(current, dict):
                    continue
                if "metric" in current:
                    metrics.add(str(current["metric"]))
                for group in ("all", "any"):
                    if isinstance(current.get(group), list):
                        stack.extend(current[group])
                if isinstance(current.get("not"), dict):
                    stack.append(current["not"])
    return sorted(metrics)


def metric_meta():
    out = {}
    for metric in _default_metrics():
        kind = "bool" if metric in BOOL_METRICS else ("percent" if metric in PERCENT_METRICS else "number")
        out[metric] = {"label": METRIC_LABELS.get(metric, metric), "kind": kind}
    return out


def _validate_condition(node, metrics, path):
    if node is None:
        return
    if not isinstance(node, dict):
        raise ValueError(f"{path}: 条件必须是对象")
    if "metric" in node:
        metric = str(node.get("metric"))
        if metric not in metrics:
            raise ValueError(f"{path}: 未知指标 {metric}")
        if node.get("op") not in OPS:
            raise ValueError(f"{path}: 非法运算符")
        value = node.get("value")
        if metric in BOOL_METRICS:
            if not isinstance(value, bool):
                raise ValueError(f"{path}: {metric} 必须是布尔值")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path}: {metric} 必须是数字")
        elif value != value or abs(value) > 1e7:
            raise ValueError(f"{path}: 数值超出范围")
        elif metric in PERCENT_METRICS and not 0 <= value <= 1:
            raise ValueError(f"{path}: {metric} 需在 0–100%")
        return
    for group in ("all", "any"):
        if group in node:
            if not isinstance(node[group], list):
                raise ValueError(f"{path}.{group}: 必须是数组")
            for index, child in enumerate(node[group]):
                _validate_condition(child, metrics, f"{path}.{group}[{index}]")
            return
    if isinstance(node.get("not"), dict):
        _validate_condition(node["not"], metrics, f"{path}.not")
        return
    raise ValueError(f"{path}: 未知条件结构")


def validate_rules(payload):
    if not isinstance(payload, dict):
        raise ValueError("请求必须是对象")
    submitted = payload.get("rules")
    if not isinstance(submitted, dict):
        raise ValueError("rules 必须是对象")
    defaults = {rule["id"]: rule for rule in tagrules.RULES}
    metrics = set(metric_meta())
    clean = {}
    for rule_id, patch in submitted.items():
        if rule_id not in defaults:
            raise ValueError(f"未知标签: {rule_id}")
        if not isinstance(patch, dict):
            raise ValueError(f"{rule_id}: 规则必须是对象")
        base = copy.deepcopy(defaults[rule_id])
        for key, value in patch.items():
            if key not in ("enabled", "name", "tone", "min_games", "min_hit_rate",
                           "priority", "when", "per_game"):
                continue
            base[key] = copy.deepcopy(value)
        if not isinstance(base.get("enabled", True), bool):
            raise ValueError(f"{rule_id}: enabled 必须是布尔值")
        if base.get("tone") not in ("positive", "neutral", "roast"):
            raise ValueError(f"{rule_id}: tone 非法")
        try:
            min_games = int(base.get("min_games", 0))
            min_hit = float(base.get("min_hit_rate", 0))
            priority = int(base.get("priority", 0))
        except (TypeError, ValueError):
            raise ValueError(f"{rule_id}: 样本/命中率/优先级必须是数字")
        if not 0 <= min_games <= 50:
            raise ValueError(f"{rule_id}: 最少场次需在 0–50")
        if not 0 <= min_hit <= 1:
            raise ValueError(f"{rule_id}: 命中率需在 0–100%")
        if not 0 <= priority <= 100:
            raise ValueError(f"{rule_id}: 优先级需在 0–100")
        name = base.get("name")
        if not isinstance(name, dict) or not all(isinstance(name.get(k, ""), str)
                                                 for k in ("roast", "neutral")):
            raise ValueError(f"{rule_id}: 标签名格式错误")
        for key in ("when", "per_game"):
            node = base.get(key)
            if node == "same":
                continue
            _validate_condition(node, metrics, f"{rule_id}.{key}")
        base["min_games"] = min_games
        base["min_hit_rate"] = min_hit
        base["priority"] = priority
        clean[rule_id] = base
    return {
        "version": 1,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rules": clean,
    }


def effective_payload():
    overrides = read_overrides()
    rules = tagrules.load_rules(DATA)
    return {
        "rules": rules,
        "metrics": metric_meta(),
        "overrides": overrides,
        "path": OVERRIDES,
        "updatedAt": overrides.get("updatedAt") or "",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LeagueSorakaAdmin/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, value):
        self._send(status, json.dumps(value, ensure_ascii=False))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(PAGE, "r", encoding="utf-8") as handle:
                    self._send(200, handle.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, "tag-admin.html missing", "text/plain; charset=utf-8")
        elif path == "/api/rules":
            self._json(200, effective_payload())
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 2 * 1024 * 1024:
                raise ValueError("请求过大")
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body or "{}")
            if path == "/api/rules":
                data = validate_rules(payload)
                write_overrides(data)
                tagrules._OVERRIDE_CACHE["mtime"] = None
                self._json(200, {"ok": True, "updatedAt": data["updatedAt"]})
            elif path == "/api/reset":
                write_overrides({"version": 1, "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rules": {}})
                tagrules._OVERRIDE_CACHE["mtime"] = None
                self._json(200, {"ok": True})
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception as error:
            self._json(400, {"ok": False, "error": str(error)})


def serve(port=None, browser=True):
    ports = [port] if port else list(range(8765, 8776))
    server = None
    chosen = None
    for candidate in ports:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            chosen = candidate
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit("no free port in 8765-8775")
    url = f"http://127.0.0.1:{chosen}/"
    print(f"[tag-admin] {url}")
    print(f"[tag-admin] overrides: {OVERRIDES}")
    if browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    serve(args.port, not args.no_browser)

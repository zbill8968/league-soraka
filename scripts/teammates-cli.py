#!/usr/bin/env python
"""Print play-style tags for the five champion-select allies.

Usage (administrator PowerShell, client open):
    python scripts/teammates-cli.py
    python scripts/teammates-cli.py --json
    python scripts/teammates-cli.py --stats
    python scripts/teammates-cli.py --demo
    python scripts/teammates-cli.py --probe
    python scripts/teammates-cli.py --clear-cache

This is the offline-testable prototype for the overlay panel. It uses the same
tagrules/teammates modules, so a tag that looks wrong here is wrong in the panel.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

import lcu  # noqa: E402
import teammates  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description="海克斯大乱斗队友画像（本机客户端只读）")
    parser.add_argument("--demo", action="store_true", help="使用内置 5 人样例，不读客户端")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--stats", action="store_true", help="附带指标，便于校准阈值")
    parser.add_argument("--probe", action="store_true", help="只检查数据源")
    parser.add_argument("--clear-cache", action="store_true", help="清空 .cache/teammates.json")
    parser.add_argument("--count", type=int, default=20, help="拉取最近多少场（默认 20）")
    return parser


def print_table(rows):
    print("海克斯大乱斗 · 队友画像")
    for row in rows:
        champion = row.get("champion_name") or "?"
        tags = row.get("tags") or []
        label = " · ".join(str(tag.get("name")) for tag in tags[:3]) or "数据不足"
        evidence = tags[0].get("evidence") if tags else (row.get("note") or "")
        prefix = (row.get("note") + " · ") if row.get("note") and tags else ""
        print("[%-8s] %-28s %s" % (champion, label, (prefix + str(evidence))[:110]))
        if row.get("reason"):
            print("           reason=%s  n=%s" % (row.get("reason"), row.get("n")))
    print()


def print_stats(rows):
    keys = [
        "n", "winRate", "dmgShare", "takenShare", "healShare", "ccShare",
        "killShare", "assistShare", "deathShare", "kp", "dmgPerKill",
        "killsPerMin", "deathsPerMin", "heartsteelRate", "topChampShare",
    ]
    for row in rows:
        metrics = row.get("metrics") or {}
        print("== %s ==" % (row.get("champion_name") or "?"))
        for key in keys:
            value = metrics.get(key)
            if isinstance(value, float):
                print("  %-18s %.3f" % (key, value))
            else:
                print("  %-18s %s" % (key, value))
        print()


def main():
    args = build_parser().parse_args()

    if args.clear_cache:
        teammates.clear_cache()
        print("cache cleared:", teammates.CACHE_PATH)
        return 0

    if args.probe:
        print(json.dumps(teammates.probe(), ensure_ascii=False, indent=2))
        return 0

    if args.demo:
        rows = teammates.demo_rows()
    else:
        session = lcu.champ_select()
        if session is None:
            print("读不到客户端选人数据。")
            print("  1) 确认英雄联盟客户端已启动；")
            print("  2) 先停在 ARAM 选人界面再执行；")
            print("  3) 仍读不到时，再用管理员 PowerShell（新版 lcu.py 会先试客户端日志，普通权限通常也可以）。")
            return 2
        team = session.get("team") or []
        if not team:
            print("客户端没有给出队友列表；请停在 ARAM 选人界面。")
            return 2
        rows = teammates.analyze_team(team, count=args.count, include_metrics=args.stats)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print_table(rows)
        if args.stats:
            print_stats(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

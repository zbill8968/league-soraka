"""
League Soraka overlay (Python/Tk edition).

Electron cannot start on this machine, so the overlay is a plain Tk window: a small
always-on-top panel that shows the recommendation. No full-screen transparency, no
click-through tricks - those proved unreliable on this display, and a compact panel
is easier to live with while playing.

Hotkeys (polled through GetAsyncKeyState, no keyboard hook):
  F8  capture the three cards and refresh the panel
  F9  save the current screen to data/screenshots/ (used to calibrate the layout)


Run:
  python overlay-py/overlay.py [--mock] [--demo SECONDS] [--data DIR] [--node PATH]
"""

import argparse
import ctypes
import calendar
import json
import os
import re
import ssl
import queue
import random
import subprocess
import threading
import sys
import time
import urllib.request

import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageGrab, ImageTk

from ocr_worker import OcrWorker
import classify_local
import opgg_builds
import item_icons
import premade
import augment_watch
import avatar
import champselect
import gamewindow
import lcu
import teammates
from teamutil import team_key as _team_key

VK = {"F9": 0x78}  # only used by --selftest diagnostics

if getattr(sys, "frozen", False):
    # PyInstaller build: data/, scripts/ and the OCR scripts live next to the exe.
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = bool(getattr(sys, "frozen", False))
LOG_PATH = os.path.join(ROOT, ".cache", "overlay-py.log")

BG = "#0b1014"
FG = "#f4f7fa"
DIM = "#9aa7b2"
GOLD = "#d8b45a"
GRADE_COLORS = {"S": "#ffd76a", "A": "#8fe08a", "B": "#7fc8ff", "C": "#c9c9c9", "D": "#ff8f8f", "?": "#ffb866"}


def score_color(score):
    """Hot-to-cold OP.GG score palette: 90+ red, 82+ orange, 74+ yellow, low gray."""
    if not isinstance(score, (int, float)):
        return "#6b7280"
    if score >= 90:
        return "#ff3b30"
    if score >= 80:
        return "#ff9500"
    if score >= 70:
        return "#ffd60a"
    if score >= 60:
        return "#c9d1d9"
    return "#6b7280"


def log(*parts):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (time.strftime("%H:%M:%S"), " ".join(str(p) for p in parts)))
    except Exception:
        pass


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="show demo cards, no capture")
    parser.add_argument("--demo", type=float, default=0, help="show demo cards for N seconds then quit")
    parser.add_argument("--data", default=os.path.join(ROOT, "data"))
    parser.add_argument("--node", default=os.environ.get("DSH_NODE", "node"))
    parser.add_argument("--policy", default="balanced")
    parser.add_argument("--selftest", type=float, default=0, help="inject F9 after N seconds, for diagnostics")
    parser.add_argument("--image", default="", help="use a saved screenshot instead of the live screen")
    parser.add_argument("--once", action="store_true", help="process once and exit (diagnostics)")
    parser.add_argument("--force-champ", action="store_true", help="treat the screen as champion select")
    parser.add_argument("--force-augment", action="store_true", help="treat the screen as the augment offer")
    parser.add_argument("--premade-demo", action="store_true", help="inject fake premade groups (layout test)")
    parser.add_argument("--champ-demo", action="store_true", help="inject fake champion rows (layout test)")
    parser.add_argument("--no-prefetch-champ", action="store_true", help="disable background champion-select prefetch")
    parser.add_argument("--no-mutex", action="store_true", help="skip the single-instance lock (testing)")
    parser.add_argument("--no-auto-augment", action="store_true", help="disable reroll screen watching")
    return parser.parse_args()


class Overlay:
    PANEL_W = 620
    ROW_H = 46

    def __init__(self, args):
        self.args = args
        self.data_dir = os.path.abspath(args.data)
        self.layout = self._read_json(os.path.join(self.data_dir, "layout.json"), None)
        self.champion = None
        self.level = 0
        self.is_dead = False
        self._post_death_until = 0.0
        self.champions = champselect.load_champions(self.data_dir)
        self.local_patch = self._read_json_patch()
        self.refreshed_champions = set()
        self.my_team_champions = []
        self.live_champion_ids = []
        self._champ_matches = None
        self._premade = None
        self._teammates = None
        index = {}
        for champ in (self.champions.get("champions") or []):
            for key in (champ.get("key"), champ.get("nameEn")):
                if key:
                    index[str(key).lower()] = champ.get("id")
        self._champ_id_by_name = index
        self._slug_by_champ_id = {
            int(champ.get("id")): str(champ.get("key") or "").lower()
            for champ in (self.champions.get("champions") or [])
            if champ.get("id")
        }
        self.config = self.load_config()
        self.prefetch_champ = (bool(self.config.get("prefetch_champ_select", True))
                               and not getattr(args, "no_prefetch_champ", False))
        self.prefetch_all_modes = bool(self.config.get("prefetch_champ_select_all_modes", False))
        self._prefetch_key = None
        self._prefetched_matches = None
        self._prefetched_premade = None
        self._prefetched_teammates = None
        self._prefetch_at = 0.0
        self._prefetching = False
        self._prefetch_started = 0.0
        self._prefetch_probe_running = False
        self._champ_live_running = False
        self._champ_live_sig = None
        self._champ_live_missing = False
        self._show_on_prefetch = False
        self._close_item = None
        self._close_ring = None
        self._closing = False
        self._panel_close_item = None
        self._panel_close_ring = None
        self._panel_closing = False
        self._badges = []
        self._item_photos = {}
        self.auto_augment = bool(self.config.get("auto_refresh_augments", True)) and not getattr(args, "no_auto_augment", False)
        self._watch_base = None
        self._watch_size = None
        self._watch_until = 0.0
        self._watch_candidate = None
        self._watch_job = False
        self._last_fg_log = 0.0
        self._last_alive = 0.0
        self._last_auto_scan = 0.0
        self._augment_scan_until = 0.0
        self._augment_badge_level = 0
        self._auto_scan_paused = False
        self._info_suppressed_until = 0.0
        self._auto_hold_until = 0.0
        self._auto_empty_retries = 0
        self._auto_request = False
        self.live_players = []
        self.live_self_team = None
        self.live_self_puuid = None
        self._live_party = None
        self._live_teammates = None
        self._live_started_for = None
        self._live_generation = 0
        self._live_roster_key = None
        self._live_game_time = None
        self._manual_show_pending = False
        self.builds = None
        self._builds_for = None
        self._builds_running = set()
        self._builds_failed = {}
        self._in_game = None
        self._in_game_at = 0.0
        self.last_champion_poll = 0.0
        self.busy = False
        self.result = None
        self.drag_origin = None
        self.key_state = {}
        self.last_trigger = {}

        self.root = tk.Tk()
        self.root.title("League Soraka")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.screen_w, self.screen_h = screen_w, screen_h
        height = 120 + 3 * self.ROW_H
        self.panel_w = min(self.PANEL_W, screen_w - 40)
        x = (screen_w - self.panel_w) // 2
        y = screen_h - height - 90
        self.root.geometry(f"{self.panel_w}x{height}+{x}+{y}")
        self.full_geometry = f"{self.panel_w}x{height}+{x}+{y}"
        self.full_height = height
        self.pill_w, self.pill_h = 76, 76
        self.pill_geometry = f"{self.pill_w}x{self.pill_h}+{screen_w - self.pill_w - 16}+{64}"

        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._avatar_images = {}
        self._avatar_item = None
        self._avatar_state = "open"
        self._blinking = False
        self._load_avatar()
        self.width = self.panel_w
        self.height = height

        self.font_grade = tkfont.Font(family="Microsoft YaHei", size=16, weight="bold")
        self.font_title = tkfont.Font(family="Microsoft YaHei", size=10, weight="bold")
        self.font_name = tkfont.Font(family="Microsoft YaHei", size=10)
        self.font_meta = tkfont.Font(family="Microsoft YaHei", size=9)
        self.font_plan = tkfont.Font(family="Microsoft YaHei", size=9)
        self.font_badge_score = tkfont.Font(family="Microsoft YaHei", size=30, weight="bold")
        self.font_badge_grade = tkfont.Font(family="Microsoft YaHei", size=15, weight="bold")
        self.font_badge_meta = tkfont.Font(family="Microsoft YaHei", size=8)

        self.pending = queue.Queue()
        self.button_rects = {}
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag_move)
        # Right click only collapses to the pill. It must never destroy the window:
        # that used to kill the whole panel for the rest of the game.
        self.canvas.bind("<Button-3>", self.on_right_click)


        self.root.after(80, self.poll_results)
        self.root.after(150, self.on_ready)
        self.root.bind("<Destroy>", lambda e: log("closed"))

        # Start hidden: the panel should only appear when there is something to
        # say (a recommendation, a screenshot confirmation, or an explicit hint).
        self.root.withdraw()
        self.hide_job = None
        tmp_dir = os.path.join(self.data_dir, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        self.ocr = OcrWorker(os.path.join(ROOT, "scripts", "ocr-daemon-file.ps1"),
                            work_dir=tmp_dir)
        threading.Thread(target=self._warm_ocr, daemon=True).start()
        log("layout", "found" if self.layout else "missing", self.data_dir)

    # ---------- data ----------

    @staticmethod
    def _read_json(path, fallback):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return fallback

    def save_layout(self, points):
        payload = dict(self.layout or {})
        payload.update({"version": 1, "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "icons": points})
        os.makedirs(self.data_dir, exist_ok=True)
        with open(os.path.join(self.data_dir, "layout.json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        self.layout = payload

    def _read_json_patch(self):
        try:
            with open(os.path.join(self.data_dir, "catalog.json"), "r", encoding="utf-8") as handle:
                return json.load(handle).get("patch")
        except Exception:
            return None

    def reload_data(self):
        self.local_patch = self._read_json_patch()
        self.champions = champselect.load_champions(self.data_dir)

    def _refresh_champion(self, champion):
        """Fetch this champion's OP.GG table (≈1 s) and hot-reload the classifier."""
        if FROZEN:
            return
        slug = re.sub(r"[^a-z0-9]", "", str(champion).lower())
        if not slug:
            return
        node = self.args.node
        script = os.path.join(ROOT, "scripts", "fetch-opgg.mjs")
        try:
            started = time.time()
            proc = subprocess.run(
                [node, script, "--data-dir", self.data_dir, "--champions", slug, "--force"],
                capture_output=True, timeout=90, creationflags=0x08000000,
            )
            ok = proc.returncode == 0
            log("refresh champion %s in %.1fs ok=%s" % (slug, time.time() - started, ok))
            if ok:
                self.reload_data()
        except Exception as error:
            log("refresh champion failed", repr(error))

    def _data_age_hours(self):
        try:
            with open(os.path.join(self.data_dir, "stats.json"), "r", encoding="utf-8") as handle:
                stamp = json.load(handle).get("fetchedAt")
            if not stamp:
                return 1e9
            when = calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
            return (time.time() - when) / 3600.0
        except Exception:
            return 1e9

    def _background_update(self):
        """Check OP.GG for a new patch and refresh the local snapshot when needed."""
        if FROZEN or self.args.image or self.args.once or self.args.demo or self.args.mock:
            return
        node = self.args.node
        script = os.path.join(ROOT, "scripts", "fetch-opgg.mjs")
        flags = 0x08000000
        try:
            proc = subprocess.run([node, script, "--check", "--data-dir", self.data_dir],
                                  capture_output=True, timeout=40, creationflags=flags)
            text = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
            info = json.loads(text[-1]) if text else {}
            remote = info.get("patch")
            age = self._data_age_hours()
            patch_changed = bool(remote) and remote != self.local_patch
            log("update: local", self.local_patch, "remote", remote, "age %.1fh" % age)
            if not patch_changed:
                log("update: patch unchanged, per-champion refresh only")
                return
            started = time.time()
            subprocess.run([node, script, "--data-dir", self.data_dir, "--force"],
                           capture_output=True, timeout=600, creationflags=flags)
            subprocess.run([node, os.path.join(ROOT, "scripts", "fetch-icons.mjs")],
                           capture_output=True, timeout=300, cwd=ROOT, creationflags=flags)
            subprocess.run([node, os.path.join(ROOT, "scripts", "build-icon-index.mjs")],
                           capture_output=True, timeout=180, cwd=ROOT, creationflags=flags)
            subprocess.run([node, os.path.join(ROOT, "scripts", "fetch-champions.mjs")],
                           capture_output=True, timeout=300, cwd=ROOT, creationflags=flags)
            self.reload_data()
            log("update: done in %.0fs, patch %s" % (time.time() - started, self.local_patch))
        except Exception as error:
            log("update failed", repr(error))

    def in_game(self):
        """True when the live game client answers on :2999 (cached for 5 s)."""
        now = time.time()
        if self._in_game is not None and now - self._in_game_at < 5:
            return self._in_game
        alive = False
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen("https://127.0.0.1:2999/liveclientdata/activeplayername",
                                        timeout=0.35, context=ctx) as res:
                alive = res.status == 200
        except Exception:
            alive = False
        self._in_game, self._in_game_at = alive, now
        return alive

    def _fallback_champion_slug(self, champion_id=0):
        """Resolve a champion id from LCU when the live raw name is unusable."""
        try:
            champion_id = int(champion_id or 0)
        except (TypeError, ValueError):
            champion_id = 0
        if champion_id:
            slug = self._slug_by_champ_id.get(champion_id)
            if slug:
                return slug
        try:
            info = premade.detect()
        except Exception:
            info = None
        self_puuid = premade.self_puuid()
        for puuid, meta in ((info or {}).get("players") or {}).items():
            if meta.get("isSelf") or puuid == self_puuid:
                slug = self._slug_by_champ_id.get(int(meta.get("championId") or 0))
                if slug:
                    log("champion resolved from LCU", slug)
                    return slug
        return ""

    def poll_champion(self, force=False):
        now = time.time()
        if not force and now - self.last_champion_poll < 5:
            return
        self.last_champion_poll = now
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen("https://127.0.0.1:2999/liveclientdata/allgamedata", timeout=0.4, context=ctx) as res:
                data = json.loads(res.read().decode("utf-8", "replace"))
            active = data.get("activePlayer") or {}
            name = active.get("summonerName")
            self.level = int(active.get("level") or 0)
            was_dead = self.is_dead
            self.is_dead = bool(active.get("isDead"))
            if self.is_dead and not was_dead:
                # The choice window can be opened at the fountain after the
                # respawn, so keep a short post-death grace window.
                self._post_death_until = time.time() + 20.0
                log("death detected; augment scan window open", "level", self.level)
            roster = []
            for player in data.get("allPlayers") or []:
                raw = player.get("rawChampionName") or ""
                key = raw.rsplit("_", 1)[-1].lower() if "_" in raw else ""
                roster.append({
                    "puuid": player.get("puuid"),
                    "name": player.get("summonerName") or "",
                    "team": _team_key(player.get("team")),
                    "championId": self._champ_id_by_name.get(key) or 0,
                    "rawChampionName": raw,
                })
            roster_key = tuple(sorted(p.get("puuid") for p in roster if p.get("puuid")))
            game_time = 0.0
            try:
                game_time = float((data.get("gameData") or {}).get("gameTime") or 0)
            except (TypeError, ValueError):
                pass
            new_game = False
            if (self._live_roster_key and len(roster_key) == 10
                    and len(self._live_roster_key) == 10 and roster_key != self._live_roster_key):
                new_game = True
            if self._live_game_time is not None and game_time + 30.0 < self._live_game_time:
                new_game = True
            if new_game:
                self._live_generation += 1
                self._live_teammates = None
                self._live_party = None
                self._live_started_for = None
                self.pending.put({"live_reset": True, "generation": self._live_generation})
                log("new game detected; cleared previous teammates", self._live_generation)
            self._live_roster_key = roster_key
            self._live_game_time = game_time
            self.live_players = roster
            self.live_self_puuid = next((p.get("puuid") for p in roster
                                         if p.get("name") == name and p.get("puuid")), None)
            my_team = None
            for player in data.get("allPlayers") or []:
                if player.get("summonerName") == name:
                    my_team = _team_key(player.get("team"))
                    break
            ids = []
            for player in data.get("allPlayers") or []:
                raw = player.get("rawChampionName") or ""
                key = raw.rsplit("_", 1)[-1].lower() if "_" in raw else ""
                champion_id = self._champ_id_by_name.get(key)
                if champion_id:
                    ids.append((player.get("team"), int(champion_id)))
            self.live_champion_ids = [cid for _team, cid in ids]
            if len(self.live_champion_ids) < 10:
                log("live champion ids", len(self.live_champion_ids), "of", len(data.get("allPlayers") or []))
            if my_team:
                self.my_team_champions = [cid for team, cid in ids if team == my_team]
                self.live_self_team = my_team
            for player in data.get("allPlayers") or []:
                if player.get("summonerName") != name:
                    continue
                # The Tencent client localises championName ("傲之追猎者"); the raw
                # name keeps the English key ("game_character_displayname_Rengar").
                raw = player.get("rawChampionName") or ""
                english = raw.rsplit("_", 1)[-1] if "_" in raw else ""
                if str(english).lower() in ("", "name", "none", "null", "unknown"):
                    english = ""
                if not english:
                    english = self._slug_by_champ_id.get(int(player.get("championId") or 0), "")
                self.champion = english or player.get("championName")
                if str(self.champion or "").lower() in ("", "name", "none", "null", "unknown"):
                    self.champion = self._fallback_champion_slug(player.get("championId")) or self.champion
                break
            if self.champion:
                self._maybe_start_builds()
            self._maybe_start_live_context()
            if self.champion and self.champion not in self.refreshed_champions:
                self.refreshed_champions.add(self.champion)
                threading.Thread(target=self._refresh_champion, args=(self.champion,), daemon=True).start()
        except Exception:
            pass

    def _periodic_champion(self):
        threading.Thread(target=self._poll_champion_bg, daemon=True).start()
        self.root.after(2000, self._periodic_champion)

    def _poll_champion_bg(self):
        try:
            self.poll_champion()
        except Exception:
            pass

    # ---------- hotkeys ----------

    def trigger(self, action="recommend"):
        """Mouse-only entry point: the pill and the panel buttons call this."""
        if action == "screenshot":
            self.save_screenshot()
        else:
            self.recommend()

    def on_hotkey(self, name):
        self.trigger("screenshot" if name == "F9" else "recommend")

    # ---------- rendering ----------

    def clear(self):
        self.canvas.delete("all")
        self.button_rects = {}
        self._close_item = None
        self._close_ring = None
        self._panel_close_item = None
        self._panel_close_ring = None

    def _clear_badges(self):
        for window in self._badges:
            try:
                window.destroy()
            except Exception:
                pass
        self._badges = []
        self._augment_badge_level = 0

    def _quit_with_press(self):
        """Press-feedback on the pill's ×: darken like a button, then close."""
        if self._closing:
            return
        self._closing = True
        log("quit press")
        try:
            # The close button is a dark circular button; pressing it means
            # dimming the fill and the glyph, not translating it.
            if self._close_ring:
                self.canvas.itemconfigure(self._close_ring, fill="#000000", outline="#343a40")
            if self._close_item:
                self.canvas.itemconfigure(self._close_item, fill="#555c63")
        except tk.TclError:
            pass
        self.root.after(140, self._destroy_after_press)

    def _close_panel_press(self):
        """Press feedback for the augment panel's ×, then collapse to the pill."""
        if self._panel_closing:
            return
        self._panel_closing = True
        try:
            if self._panel_close_ring:
                self.canvas.itemconfigure(self._panel_close_ring, fill="#000000", outline="#343a40")
            if self._panel_close_item:
                self.canvas.itemconfigure(self._panel_close_item, fill="#555c63")
        except tk.TclError:
            pass
        self.root.after(140, self._finish_panel_close)

    def _finish_panel_close(self):
        self._panel_closing = False
        self._info_suppressed_until = time.time() + 10.0
        self.show_pill()

    def _destroy_after_press(self):
        self._clear_badges()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def draw_buttons(self, augment=False):
        """Mouse fallback; augment mode only needs the reroll refresh button."""
        if augment:
            specs = [("refresh", "刷新", self.recommend)]
            x = self.width - 100
        else:
            specs = [("shot", "截图", self.save_screenshot), ("rec", "识别", self.recommend)]
            x = self.width - 190
        for key, label, action in specs:
            rect = (x, 6, x + 84, 28)
            self.canvas.create_rectangle(*rect, fill="#16202b", outline="#2a3540")
            self.canvas.create_text(x + 42, 17, text=label, fill=FG, font=self.font_meta)
            self.button_rects[key] = (rect, action)
            x += 92

    def on_click(self, event):
        log("click", event.x, event.y, "width", self.width, "pill", self.pill_w)
        if self.width == self.pill_w:
            for rect, action in self.button_rects.values():
                if rect[0] <= event.x <= rect[2] and rect[1] <= event.y <= rect[3]:
                    action()
                    return
            return
        for rect, action in self.button_rects.values():
            if rect[0] <= event.x <= rect[2] and rect[1] <= event.y <= rect[3]:
                log("button", action.__name__)
                action()
                return
        self.on_drag_start(event)

    @staticmethod
    def _card_key(result):
        return tuple((card.get("slot"), card.get("augmentId"), card.get("name"))
                     for card in (result.get("cards") or []))

    def show_game_info(self, force=False):
        """Persistent in-game panel when there is no augment card on screen."""
        if not force and time.time() < self._info_suppressed_until:
            return
        if not self.in_game():
            self.show_idle("未检测到对局", 8000)
            return
        previous = self.result or {}
        result = {
            "champion": self.champion or "当前对局",
            "mode": "champion" if self.champion else "global",
            "patch": previous.get("patch") or "?",
            "cards": [],
            "warnings": [],
            "_teammates": self._live_teammates,
            "_premade": self._live_party,
            "_builds": self.builds,
            "_anchors": [],
        }
        self.result = result
        self.render(result, auto_hide_ms=0)
        self._maybe_start_builds()
        self._maybe_start_live_context()

    def _item_photo(self, path, size):
        if not path or not os.path.exists(path):
            return None
        key = (path, size)
        cached = self._item_photos.get(key)
        if cached is not None:
            return cached
        try:
            image = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self._item_photos[key] = photo
            return photo
        except Exception:
            return None

    def _draw_item_icon(self, x, y, size, path, name=""):
        photo = self._item_photo(path, size)
        if photo is not None:
            self.canvas.create_image(x, y, image=photo, anchor="nw")
            return
        self.canvas.create_rectangle(x, y, x + size, y + size, fill="#151c24", outline="#2b3642")
        self.canvas.create_text(x + size / 2, y + size / 2, text=(name or "?")[:1], fill=DIM,
                                font=self.font_meta)

    def render(self, result, preserve_badges=False, auto_hide_ms=20000):
        # Wider left panel so the augment recommendation, OP.GG build and live
        # party/teammate context fit without covering the three card badges.
        self.width = min(436, max(300, int(self.screen_w * 0.23)))
        self._panel_closing = False
        self.clear()
        if not preserve_badges:
            self._clear_badges()
        self.result = result
        cards = result.get("cards") or []
        rec = result.get("recommendation") or {}
        keep = rec.get("keepSlot")
        reroll = set(rec.get("rerollSlots") or [])
        champion = result.get("champion") or self.champion or "通用数据"
        if result.get("mode") == "global":
            champion = "通用数据"
        patch = result.get("patch") or "?"
        height = min(680, self.screen_h - 120)
        x, y = 5, self.screen_h - height - 30
        self.height = height
        geometry = f"{self.width}x{height}+{x}+{y}"
        self.full_geometry = geometry
        try:
            self.root.geometry(geometry)
        except tk.TclError:
            pass

        self.canvas.create_text(14, 18, text=f"{champion}　·　patch {patch}",
                                fill=GOLD, font=self.font_title, anchor="w")
        self._panel_close_ring = self.canvas.create_oval(self.width - 22, 9, self.width - 6, 25,
                                                         fill="#12181f", outline=GOLD)
        self._panel_close_item = self.canvas.create_text(self.width - 14, 17, text="×",
                                                         fill="#e8e8e8", font=self.font_meta)
        self.button_rects["panel_close"] = ((self.width - 26, 4, self.width, 30), self._close_panel_press)

        top = 42
        # The left panel no longer duplicates the per-card augment ratings; those
        # stay only in the badges under the cards. Use the space for teammate tags.
        team_rows = result.get("_teammates")
        self.canvas.create_text(14, top, text="队友标签", fill=DIM, font=self.font_meta, anchor="w")
        row_y = top + 20
        if team_rows is None:
            self.canvas.create_text(18, row_y, text="分析中…", fill=DIM, font=self.font_meta, anchor="w")
            row_y += 26
        elif team_rows:
            for row in team_rows[:5]:
                tags = [t for t in (row.get("tags") or []) if t.get("kind") != "data"]
                label = " · ".join(str(t.get("name")) for t in tags[:3]) or "数据不足"
                champ = row.get("champion_name") or "?"
                avg = row.get("avgAugmentPerf")
                avg_text = f"{float(avg):.1f}" if isinstance(avg, (int, float)) else "—"
                self.canvas.create_text(18, row_y, text=f"[{champ}] {label} · 均分 {avg_text}"[:66],
                                        fill=FG, font=self.font_meta, anchor="w")
                row_y += 26
        else:
            self.canvas.create_text(18, row_y, text="队友数据获取中…（失败会自动重试）", fill=DIM,
                                    font=self.font_meta, anchor="w")
            row_y += 26

        # Party groups. If the client has no party data, omit the block entirely.
        party = result.get("_premade")
        if party:
            self.canvas.create_text(14, row_y + 8, text="组队情况", fill=DIM, font=self.font_meta, anchor="w")
            row_y += 28
            for group in party[:3]:
                side = {"ally": "我方", "enemy": "敌方"}.get(group.get("side") or "", "未知")
                names = " + ".join(group.get("names") or [])
                prefix = "疑似 " if group.get("inferred") else ""
                self.canvas.create_text(18, row_y, text=f"{prefix}{side} {len(group.get('names') or [])} 人：{names}"[:66],
                                        fill=GOLD, font=self.font_meta, anchor="w")
                row_y += 18

        # Three OP.GG builds, five item icons each, with a reference win rate.
        build_y = row_y + 12
        self.canvas.create_text(14, build_y, text="出装 · OP.GG 高胜率", fill=GOLD, font=self.font_meta, anchor="w")
        builds = result.get("_builds") or {}
        groups = builds.get("groups") or []
        row_y = build_y + 22
        icon_size = 36
        if not builds:
            attempted = self._builds_for == (self.champion or "").lower()
            msg = "出装暂不可用（需要联网获取 OP.GG）" if attempted else "出装加载中…"
            self.canvas.create_text(16, row_y, text=msg, fill=DIM, font=self.font_meta, anchor="w")
        elif not groups:
            self.canvas.create_text(16, row_y, text="出装数据不完整，稍后重试", fill=DIM, font=self.font_meta, anchor="w")
        else:
            ranks = ("1st", "2nd", "3rd")
            for index, group in enumerate(groups[:3]):
                y = row_y + index * (icon_size + 9)
                icons = group.get("icons") or []
                names = group.get("names") or []
                for slot, _item_id in enumerate((group.get("items") or [])[:5]):
                    path = icons[slot] if slot < len(icons) else None
                    name = names[slot] if slot < len(names) else ""
                    self._draw_item_icon(16 + slot * (icon_size + 4), y, icon_size, path, name)
                win = group.get("winRate")
                win_text = "—" if win is None else f"{float(win) * 100:.0f}%"
                self.canvas.create_text(self.width - 16, y + icon_size / 2,
                                        text=f"{ranks[index]}  {win_text}",
                                        fill=GOLD if index == 0 else FG, font=self.font_name, anchor="e")

        if not preserve_badges or not self._badges:
            self._show_badges(result)
            self._augment_badge_level = self.level
            # Badges stay until the player respawns or OCR reliably stops
            # seeing augment cards.
        self.show_panel(auto_hide_ms, geometry)
        self._start_augment_watch(None)

    def _show_badges(self, result):
        """Large OP.GG score badges under the three real augment cards."""
        self._clear_badges()
        anchors = result.get("_anchors") or []
        cards = result.get("cards") or []
        recognized = [card for card in cards if card.get("augmentId") is not None]
        if len(anchors) < 3 or len(recognized) < 2:
            self._clear_badges()
            return
        band = (self.layout or {}).get("scoreBand") or {}
        dy = float(band.get("dy", 3.3))
        for card in recognized[:3]:
            anchor = anchors[int(card.get("slot") or 0)]
            size = float(anchor.get("size") or 80)
            width = max(112, int(size * float(band.get("w", 1.5))))
            height = max(82, int(size * float(band.get("h", 1.02))))
            x = int(float(anchor.get("x", 0)) - width / 2)
            y = int(float(anchor.get("y", 0)) + size * dy)
            score = card.get("score")
            grade = str(card.get("grade") or "?")
            color = score_color(score)
            # Scale the typography with the detected card size so 1080p, 1440p
            # and 4K all read the same relative to the augment cards.
            scale = max(0.7, min(2.4, size / 150.0))
            grade_font = tkfont.Font(family="Microsoft YaHei", size=max(18, int(38 * scale)), weight="bold")
            score_font = tkfont.Font(family="Microsoft YaHei", size=max(9, int(14 * scale)))
            meta_font = tkfont.Font(family="Microsoft YaHei", size=max(6, int(8 * scale)))
            top = tk.Toplevel(self.root)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            top.configure(bg=BG)
            top.geometry(f"{width}x{height}+{x}+{y}")
            canvas = tk.Canvas(top, bg=BG, highlightthickness=0, bd=0)
            canvas.pack(fill="both", expand=True)
            canvas.create_rectangle(1, 1, width - 2, height - 2, outline=color, width=2)
            canvas.create_text(width * 0.50, height * 0.42, text=grade, fill=color,
                               font=grade_font, anchor="center")
            score_text = "—" if score is None else f"{float(score):.1f}"
            canvas.create_text(width * 0.50, height * 0.80, text=score_text, fill=color,
                               font=score_font, anchor="center")
            growth_mult = card.get("growthMultiplier")
            if card.get("growth") and growth_mult and abs(float(growth_mult) - 1.0) > 1e-9:
                canvas.create_text(width * 0.50, height * 0.96, text=f"成长 ×{float(growth_mult):.2f}",
                                   fill=DIM, font=meta_font, anchor="center")
            canvas.create_text(width * 0.10, height * 0.12, text=str(int(card.get("slot") or 0) + 1), fill=DIM,
                               font=meta_font, anchor="w")
            canvas.create_text(width * 0.88, height * 0.12, text="OP.GG", fill=DIM,
                               font=meta_font, anchor="e")
            self._badges.append(top)

    def _premade_worker(self, team, prefetch=False, key=None):
        """Poll the client while the draft lasts; party ids show up a bit later."""
        deadline = time.time() + 40
        while time.time() < deadline:
            groups = self._premade_groups(team, None)
            if groups is not None:
                self.pending.put({"premade_update": groups, "prefetch": prefetch, "key": key})
                return
            time.sleep(2.0)

    def _teammates_worker(self, team, prefetch=False, key=None):
        """Fetch recent ARAM games for the four allies in the background."""
        try:
            self_puuid = premade.self_puuid()
            allies = [member for member in (team or [])
                      if not self_puuid or member.get("puuid") != self_puuid]
            rows = teammates.analyze_team(allies, timeout=30.0)
            self.pending.put({"teammates_update": rows, "prefetch": prefetch, "key": key})
        except Exception as error:
            log("teammates failed", repr(error))
            if prefetch:
                # Keep the champion rows; just show "分析中/暂无" for tags.
                self.pending.put({"teammates_update": [], "prefetch": True, "key": key})
            else:
                self.pending.put({"teammates_error": str(error)})

    def _premade_groups(self, team=None, champions=None, context_puuids=None):
        """Party groups for the current game, validated against the current session."""
        if self.args.premade_demo:
            return [
                {"team": 100, "side": "ally", "names": ["提莫", "阿狸"]},
                {"team": 200, "side": "enemy", "names": ["盖伦", "金克丝", "拉克丝"]},
            ]

        puuids = list(context_puuids or [])
        puuids += [m.get("puuid") for m in (team or []) if m.get("puuid")]
        if not puuids and not champions:
            log("premade: no context yet")
        by_id = self.champions.get("by_id") or {}
        champion_of = {m.get("puuid"): m.get("championId") for m in (team or [])}

        info = None
        try:
            info = premade.detect(context_puuids=puuids or None,
                                  context_champions=champions or None,
                                  retry_seconds=1.6 if puuids or champions else 0.0)
        except Exception as error:
            log("premade failed", repr(error))

        if info and info.get("groups"):
            groups = []
            for group in info["groups"]:
                names = []
                for puuid in group["puuids"]:
                    cid = (info["players"].get(puuid) or {}).get("championId") or 0
                    champ = by_id.get(cid)
                    names.append((champ or {}).get("nameZh") or (champ or {}).get("nameEn")
                                 or (info["players"].get(puuid) or {}).get("name") or "?")
                groups.append({"team": group.get("team"), "side": group.get("side"),
                               "names": names, "inferred": False})
            log("premade exact", groups, "game", info.get("gameId"), info.get("phase"))
            return groups

        if info is not None and info.get("hasPartyIds"):
            # Valid data for this game, with party ids present, but no groups.
            log("premade exact none", info.get("gameId"), info.get("phase"))
            return []
        if info is not None:
            log("premade no party ids", info.get("gameId"), info.get("phase"))

        # Data stale or unavailable: infer from shared recent matches.
        if len(puuids) >= 2:
            try:
                inferred = premade.infer_from_history(puuids)
            except Exception as error:
                log("premade history failed", repr(error))
                inferred = []
            if inferred:
                me = premade.self_puuid()
                groups = []
                for members in inferred:
                    names = []
                    for puuid in members:
                        champ = by_id.get(champion_of.get(puuid) or 0)
                        names.append((champ or {}).get("nameZh") or (champ or {}).get("nameEn") or "?")
                    side = "ally" if me and me in members else ("enemy" if me else "unknown")
                    groups.append({"team": 0, "side": side, "names": names, "inferred": True})
                log("premade inferred", groups, premade.infer_meta())
                return groups
            log("premade inferred none", premade.infer_meta())
        return None

    def _draw_champ_rows(self, rows, x0, top, cell_w, per_row_h, cols, start_index=0):
        if not rows:
            return
        rows_per_col = max(1, (len(rows) + cols - 1) // cols)
        for index, champ in enumerate(rows):
            col = index // rows_per_col
            row = index % rows_per_col
            gx = x0 + col * cell_w
            gy = top + row * per_row_h
            color = GRADE_COLORS.get(champ.get("grade"), FG)
            self.canvas.create_text(gx, gy + 14, text=champ.get("grade") or "?", fill=color, font=self.font_grade, anchor="w")
            self.canvas.create_text(gx + 34, gy + 14, text=str(champ.get("name") or "?"), fill=FG, font=self.font_name, anchor="w")
            self.canvas.create_text(gx + cell_w - 14, gy + 14, text=f"#{champ.get('rank')}",
                                    fill=DIM, font=self.font_meta, anchor="e")
            self.canvas.create_line(gx, gy + per_row_h - 6, gx + cell_w - 12, gy + per_row_h - 6, fill="#1c2530")

    def render_champions(self, matches, premade=None, teammate_rows=None):
        self._clear_badges()
        try:
            self.root.attributes("-transparentcolor", "")
        except tk.TclError:
            pass
        if not matches:
            self.width = self.panel_w
            self.height = self.full_height
            try:
                self.root.geometry(self.full_geometry)
            except tk.TclError:
                pass
            self.clear()
            self.canvas.create_text(self.width // 2, 40, text="没认出英雄", fill=GOLD, font=self.font_title)
            self.canvas.create_text(self.width // 2, 80, text="请停在选人界面再点一次胶囊",
                                    fill=FG, font=self.font_name, width=self.width - 40, justify="center")
            self.show_panel(12000)
            return

        bench = [m for m in matches if m.get("zone") == "bench"][:24]
        current = [m for m in matches if m.get("zone") == "current"][:4]

        per_row_h = 40
        cols = 2
        bench_rows = max(0, (len(bench) + cols - 1) // cols)
        premade_lines = list(premade or [])
        premade_known = premade is not None
        # Do not reserve/show a "party unavailable" block when the client has not
        # provided party data; it only adds noise in champion select.
        premade_block = (30 + max(1, len(premade_lines)) * 26) if premade_known else 0
        height = (74 + bench_rows * per_row_h + 34 + 34 + len(current) * per_row_h
                  + 28 + premade_block)
        width = min(980, self.screen_w - 80)
        x = (self.screen_w - width) // 2
        y = max(20, self.screen_h - height - 90)
        geometry = f"{width}x{height}+{x}+{y}"

        self.width = width
        self.height = height
        try:
            self.root.geometry(geometry)
        except tk.TclError:
            pass
        self.clear()

        self.canvas.create_text(16, 18, text="选人 · OP.GG 海克斯梯度", fill=GOLD, font=self.font_title, anchor="w")
        self.canvas.create_text(width - 16, 18, text=f"可用 {len(bench)}", fill=DIM, font=self.font_meta, anchor="e")

        cell_w = (width - 28) // cols
        top = 46
        self.canvas.create_text(16, top - 6, text="可用（顶部备选）", fill=DIM, font=self.font_meta, anchor="w")
        if bench:
            self._draw_champ_rows(bench, 14, top + 12, cell_w, per_row_h, cols)
        y_after_bench = top + 12 + bench_rows * per_row_h + 10

        self.canvas.create_text(16, y_after_bench, text="当前选择", fill=DIM, font=self.font_meta, anchor="w")
        if current:
            self._draw_champ_rows(current, 14, y_after_bench + 12, width - 28, per_row_h, 1)
        else:
            self.canvas.create_text(16, y_after_bench + 14, text="（没认出你当前选的英雄）", fill=DIM,
                                    font=self.font_meta, anchor="w")

        y_premade = y_after_bench + 12 + max(1, len(current)) * per_row_h + 14
        if premade_known:
            self.canvas.create_text(16, y_premade, text="开黑情况", fill=DIM, font=self.font_meta, anchor="w")
            if premade_lines:
                for index, group in enumerate(premade_lines):
                    y = y_premade + 22 + index * 26
                    size = len(group.get("names") or [])
                    side = {"ally": "我方", "enemy": "敌方"}.get(group.get("side") or "", "")
                    prefix = "疑似 " if group.get("inferred") else ""
                    label = (f"{side} {size} 人开黑：" if side else f"{size} 人开黑：")
                    self.canvas.create_text(22, y, text=prefix + label + " + ".join(group.get("names") or []),
                                            fill=GOLD, font=self.font_name, anchor="w")
            else:
                self.canvas.create_text(22, y_premade + 22, text="没有开黑小队（5 人各自单排）",
                                        fill=FG, font=self.font_name, anchor="w")

        note = "评级按 OP.GG 英雄排名（由胜率折算）"
        if not bench:
            note = "没读到顶部备选栏：请用「启动面板-管理员.cmd」重启（选人数据需要管理员权限）"
        self.canvas.create_text(16, height - 20, text=note, fill=DIM, font=self.font_meta, anchor="w")
        self.show_panel(30000, geometry)

    def load_config(self):
        path = os.path.join(self.data_dir, "config.json")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def save_config(self, data):
        path = os.path.join(self.data_dir, "config.json")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
        except Exception as error:
            log("config save failed", repr(error))

    def show_panel(self, auto_hide_ms=20000, geometry=None):
        """Full panel: used for a recommendation or a hint."""
        try:
            self.root.attributes("-transparentcolor", "")
        except tk.TclError:
            pass
        try:
            self.canvas.create_rectangle(1, 1, self.width - 2, self.height - 2, outline=GOLD)
        except tk.TclError:
            pass
        if geometry is None:
            self.width = self.panel_w
            self.height = self.full_height
            geometry = self.full_geometry
        try:
            self.root.geometry(geometry)
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._cancel_hide()
        if auto_hide_ms:
            self.hide_job = self.root.after(auto_hide_ms, self.show_pill)

    def _cancel_hide(self):
        if self.hide_job is not None:
            try:
                self.root.after_cancel(self.hide_job)
            except Exception:
                pass
            self.hide_job = None

    def _warm_ocr(self):
        started = time.time()
        ok = False
        try:
            ok = self.ocr.start()
        except Exception as error:
            log("ocr warm failed", repr(error))
        log("ocr warm %.2fs ok=%s" % (time.time() - started, ok))

    def _load_avatar(self):
        try:
            icon = os.path.join(self.data_dir, "champ-icons", "16.png")
            if not os.path.exists(icon):
                return
            from PIL import Image, ImageDraw, ImageTk

            paths = avatar.ensure_frames(icon, self.data_dir, 72)
            key = Image.new("RGB", (72, 72), BG)
            mask = Image.new("L", (72, 72), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 71, 71), fill=255)
            images = {}
            for state, path in paths.items():
                frame = Image.open(path).convert("RGB")
                # Composite onto the exact background colour: Tk's colour-key
                # transparency only matches flat pixels, not alpha-blended ones.
                key.paste(frame, (0, 0), mask)
                images[state] = ImageTk.PhotoImage(key.copy())
            self._avatar_images = images
            log("avatar loaded", len(self._avatar_images))
        except Exception as error:
            log("avatar load failed", repr(error))

    def _reapply_key(self):
        try:
            if self.width != self.pill_w:
                return
            self.root.attributes("-transparentcolor", BG)
            self.root.update_idletasks()
            log("reapplied key")
        except tk.TclError as error:
            log("reapply failed", repr(error))

    def _set_avatar(self, state):
        if not self._avatar_images or self._avatar_item is None:
            return
        try:
            if self.canvas.type(self._avatar_item) != "image":
                return
            self.canvas.itemconfigure(self._avatar_item, image=self._avatar_images[state])
            self._avatar_state = state
        except tk.TclError:
            pass

    def blink(self, done=None):
        """Two-frame blink (~210 ms): open -> half -> closed -> half -> open."""
        if self.width != self.pill_w or not self._avatar_images:
            if done:
                done()
            return
        self._blinking = True
        steps = ["half", "closed", "half", "open"]
        scale = float(os.environ.get("SORAKA_BLINK_SCALE", "1") or 1)
        delays = [int(55 * scale), int(85 * scale), int(55 * scale), 0]

        def finish():
            self._blinking = False
            self._set_avatar("open")
            if done:
                done()

        def step(index=0):
            if index >= len(steps):
                finish()
                return
            if self.width != self.pill_w:
                finish()
                return
            self._set_avatar(steps[index])
            if delays[index]:
                self.root.after(delays[index], lambda: step(index + 1))
            else:
                step(index + 1)

        step(0)

    def _idle_blink(self):
        try:
            if self.width == self.pill_w and not self._blinking:
                self.blink()
        except tk.TclError:
            return
        self.root.after(random.randint(8000, 14000), self._idle_blink)

    def _pill_click(self):
        # Blink first, then analyse: the panel replaces the pill, so the animation
        # would otherwise be cut off after the first frame.
        self.blink(done=self.recommend)

    def show_pill(self):
        """The Soraka avatar: click to analyse, x to quit."""
        self.hide_job = None
        self._champ_matches = None
        self._closing = False
        self._panel_closing = False
        self._panel_close_item = None
        self._panel_close_ring = None
        self.width = self.pill_w
        self.height = self.pill_h
        try:
            self.root.geometry(self.pill_geometry)
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.clear()
        self.button_rects = {}
        if self._avatar_images:
            try:
                self.root.attributes("-transparentcolor", BG)
                self.root.update_idletasks()
                log("transparentcolor", BG, "->", self.root.attributes("-transparentcolor"))
                self.root.after(150, self._reapply_key)
            except tk.TclError as error:
                log("transparentcolor failed", repr(error))
            self._avatar_item = self.canvas.create_image(self.pill_w // 2, self.pill_h // 2,
                                                         image=self._avatar_images["open"])
            self._avatar_state = "open"
            self.canvas.create_oval(1, 1, self.pill_w - 2, self.pill_h - 2, outline=GOLD, width=2)
            self._close_ring = self.canvas.create_oval(self.pill_w - 18, 2, self.pill_w - 2, 18,
                                                      fill="#12181f", outline=GOLD)
            self._close_item = self.canvas.create_text(self.pill_w - 10, 10, text="×", fill="#e8e8e8",
                                                       font=self.font_meta)
            self.button_rects["rec"] = ((0, 0, self.pill_w - 18, self.pill_h), self._pill_click)
            self.button_rects["quit"] = ((self.pill_w - 18, 0, self.pill_w, 18), self._quit_with_press)
        else:
            self._avatar_item = None
            self.canvas.create_text(14, self.pill_h // 2, text="识别", fill=GOLD, font=self.font_meta, anchor="w")
            self._close_item = self.canvas.create_text(self.pill_w - 16, self.pill_h // 2, text="×", fill=DIM,
                                                       font=self.font_name)
            self.button_rects["rec"] = ((0, 0, self.pill_w - 26, self.pill_h), self._pill_click)
            self.button_rects["quit"] = ((self.pill_w - 30, 0, self.pill_w, self.pill_h), self._quit_with_press)

    def show_idle(self, text, auto_hide_ms=12000):
        self._clear_badges()
        try:
            self.root.attributes("-transparentcolor", "")
        except tk.TclError:
            pass
        self.width = self.panel_w
        self.height = self.full_height
        try:
            self.root.geometry(self.full_geometry)
        except tk.TclError:
            pass
        self.clear()
        self.canvas.create_text(self.width // 2, 40, text="League Soraka", fill=GOLD, font=self.font_title)
        self.canvas.create_text(self.width // 2, 80, text=text, fill=FG, font=self.font_name, width=self.width - 40, justify="center")
        self.canvas.create_text(self.width // 2, self.height - 26, text="右键收起", fill=DIM, font=self.font_meta)
        self.show_panel(auto_hide_ms)

    # ---------- actions ----------

    def recommend(self, auto=False):
        if self.busy:
            if not auto and self._auto_request:
                self._manual_show_pending = True
                log("manual click queued behind auto scan")
            return
        if auto and not self.auto_augment:
            self._stop_augment_watch()
            return
        if auto and not self.args.image and not self.in_game():
            self._watch_base = None
            return
        # Manual analysis clears badges before capture; automatic scans keep them
        # visible so a quiet OCR retry does not make the ratings blink.
        if not auto:
            self._clear_badges()
            self._info_suppressed_until = 0.0
        if self.args.mock or self.args.demo:
            self.render(self.demo_result())
            return
        if not auto and not self.args.force_augment and not self.in_game():
            if self._show_prefetched():
                return
            if self._prefetching and self._prefetch_key:
                self._show_on_prefetch = True
                self.show_idle("选人分析中…", 8000)
                return
            phase = None
            try:
                phase = lcu.gameflow_phase()
            except Exception:
                pass
            if phase == "ChampSelect":
                self._start_champ_select(None)
            else:
                self.show_idle("未检测到对局", 8000)
            return
        icons = (self.layout or {}).get("icons")
        if not icons or len(icons) < 3:
            self.show_idle("还没有标定布局。\n进游戏，三张增幅卡出现时点「截图」，把文件发给助手标定。")
            return
        self.busy = True
        self._auto_request = auto
        if not auto:
            self.show_idle("识别中…", 8000)
        threading.Thread(target=self._recommend_worker, args=(auto,), daemon=True).start()

    def _start_champ_select(self, session=None):
        """Manual path: show the loading panel, then fetch + render."""
        if self.busy:
            return
        self.busy = True
        self.show_idle("选人分析中…", 8000)
        threading.Thread(target=self._champ_select_worker, args=(session,), daemon=True).start()

    def _show_prefetched(self):
        """Render the background snapshot without touching LCU/SGP."""
        if not self._prefetched_matches or not self._prefetch_key:
            return False
        if self._prefetch_at and time.time() - self._prefetch_at > 180:
            return False
        self._champ_matches = self._prefetched_matches
        self._premade = self._prefetched_premade
        self._teammates = self._prefetched_teammates
        log("show prefetched", self._prefetch_key)
        self.render_champions(self._champ_matches, self._premade, self._teammates)
        return True

    def _champ_rows(self, session):
        by_id = self.champions.get("by_id") or {}
        total = self.champions.get("total") or 0
        rows = []
        for cid in session.get("bench") or []:
            champ = by_id.get(cid)
            if not champ:
                continue
            rows.append({
                "id": cid,
                "name": champ.get("nameZh") or champ.get("nameEn"),
                "nameEn": champ.get("nameEn"),
                "rank": champ.get("rank"),
                "grade": champselect.grade_for(champ.get("rank"), total),
                "zone": "bench",
            })
        rows.sort(key=lambda r: (r.get("rank") or 999))
        current_id = session.get("current")
        if current_id and current_id in by_id:
            champ = by_id[current_id]
            rows.append({
                "id": current_id,
                "name": champ.get("nameZh") or champ.get("nameEn"),
                "nameEn": champ.get("nameEn"),
                "rank": champ.get("rank"),
                "grade": champselect.grade_for(champ.get("rank"), total),
                "zone": "current",
            })
        return rows

    def _prefetch_champ_select(self, session):
        """Fetch the champion-select panel data in the background, no UI."""
        if not self.prefetch_champ or self.busy or self._prefetching:
            return
        key = lcu.champ_select_key(session)
        if len(key[1]) < 5:
            return
        self._prefetch_key = key
        self._prefetched_matches = None
        self._prefetched_premade = None
        self._prefetched_teammates = None
        self._prefetch_at = 0.0
        self._prefetching = True
        self._prefetch_started = time.time()
        log("prefetch champ", key, "bench", bool(session.get("benchEnabled")))
        threading.Thread(target=self._champ_select_worker, args=(session, True), daemon=True).start()

    def _champ_live_worker(self):
        try:
            session = lcu.champ_select()
            if not session:
                self.pending.put({"champ_live_closed": True})
                return
            rows = self._champ_rows(session)
            key = lcu.champ_select_key(session)
            self.pending.put({"champ_live": {"session": session, "rows": rows, "key": key}})
        except Exception as error:
            log("champ live failed", repr(error))
            self.pending.put({"champ_live_closed": True})
        finally:
            self._champ_live_running = False

    def _champ_live_tick(self):
        if not self.prefetch_champ:
            return
        if not self._champ_live_running:
            self._champ_live_running = True
            threading.Thread(target=self._champ_live_worker, daemon=True).start()
        try:
            self.root.after(300, self._champ_live_tick)
        except tk.TclError:
            pass

    def _prefetch_champ_probe(self):
        phase = None
        session = None
        try:
            phase = lcu.gameflow_phase()
            if phase == "ChampSelect":
                session = lcu.champ_select()
        except Exception as error:
            log("prefetch probe failed", repr(error))
        finally:
            self._prefetch_probe_running = False
        self._champ_live_running = False
        self._champ_live_sig = None
        self._champ_live_missing = False
        self.pending.put({"prefetch_probe": {"phase": phase, "session": session}})

    def _prefetch_champ_tick(self):
        if not self.prefetch_champ:
            return
        if self._prefetching and self._prefetch_started and time.time() - self._prefetch_started > 30:
            log("prefetch timeout")
            self._prefetching = False
            self._prefetch_key = None
            self._prefetch_started = 0.0
        if not self._prefetch_probe_running:
            self._prefetch_probe_running = True
            threading.Thread(target=self._prefetch_champ_probe, daemon=True).start()
        try:
            self.root.after(1500, self._prefetch_champ_tick)
        except tk.TclError:
            pass

    def _handle_prefetch_champ(self, phase, session):
        if phase != "ChampSelect":
            if self._prefetch_key:
                self._prefetch_key = None
                self._prefetched_matches = None
                self._prefetched_premade = None
                self._prefetched_teammates = None
                self._prefetch_at = 0.0
                self._prefetching = False
                self._prefetch_started = 0.0
                self._show_on_prefetch = False
                if self._champ_matches is not None:
                    self.show_pill()
            return
        if not session or self.busy or self._prefetching or self._champ_matches is not None:
            return
        if not lcu.champ_select_ready(session):
            return
        if not self.prefetch_all_modes and not lcu.is_aram_champ_select(session):
            return
        key = lcu.champ_select_key(session)
        if key == self._prefetch_key:
            return
        self._prefetch_champ_select(session)

    def _card_boxes(self, icons, frame, offset=(0, 0)):
        fw, fh = frame
        size = float(icons[0].get("size", 0.078)) * fw
        name_band = (self.layout or {}).get("nameBand") or {"dy": 0.55, "h": 0.75}
        top = float(icons[0].get("y", 0.28)) * fh - 0.12 * size
        bottom = top + (float(name_band.get("dy", 0.55)) + float(name_band.get("h", 0.75)) + 0.12) * size
        boxes = []
        for point in icons[:3]:
            cx = float(point.get("x", 0.5)) * fw
            half = 0.56 * size
            boxes.append((int(cx - half - offset[0]), int(top - offset[1]),
                          int(cx + half - offset[0]), int(bottom - offset[1])))
        return boxes

    @staticmethod
    def _signature_from_boxes(image, boxes):
        parts = []
        for box in boxes:
            crop = image.crop(box).convert("L").resize((16, 16), Image.BILINEAR)
            parts.append(crop.tobytes())
        return b"".join(parts)

    def _shot_signature(self, shot, icons, frame, offset=(0, 0)):
        try:
            return self._signature_from_boxes(shot, self._card_boxes(icons, frame, offset))
        except Exception as error:
            log("signature shot failed", repr(error))
            return None

    def _screen_signature(self):
        try:
            rect = gamewindow.find_client_rect()
            if not rect:
                return None
            fx, fy, fw, fh = rect
            icons = (self.layout or {}).get("icons")
            if not icons or len(icons) < 3:
                return None
            boxes = self._card_boxes(icons, (fw, fh))
            left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
            right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
            shot = ImageGrab.grab(bbox=(fx + left, fy + top, fx + right, fy + bottom))
            relative = [(b[0] - left, b[1] - top, b[2] - left, b[3] - top) for b in boxes]
            return self._signature_from_boxes(shot, relative), (fw, fh)
        except Exception as error:
            log("signature screen failed", repr(error))
            return None

    def _start_augment_watch(self, signature=None):
        if not self.auto_augment:
            return
        # The watcher is continuous while the game is running. After a result we
        # intentionally clear the baseline and let the next 300ms tick capture the
        # new stable frame, which avoids a shot-vs-screen signature mismatch.
        self._watch_base = bytes(signature) if signature else None
        self._watch_candidate = None
        self._watch_until = time.time() + 365 * 86400
        self._auto_empty_retries = 0
        self._auto_hold_until = 0.0
        if not self._watch_job:
            self._watch_job = True
            try:
                self.root.after(300, self._augment_watch_tick)
            except tk.TclError:
                self._watch_job = False

    def _stop_augment_watch(self):
        self._watch_until = 0.0
        self._watch_base = None
        self._watch_candidate = None

    def _augment_watch_tick(self):
        self._watch_job = False
        try:
            if not self.auto_augment:
                return
            if self.busy or time.time() < self._auto_hold_until:
                return
            now = time.time()
            if not self.in_game():
                if self._badges:
                    self._clear_badges()
                if (self._live_teammates is not None or self._live_party is not None
                        or self._live_started_for is not None):
                    self._live_generation += 1
                    self._live_teammates = None
                    self._live_party = None
                    self._live_started_for = None
                if self.width != self.pill_w and self._champ_matches is None:
                    self.show_pill()
                return

            # Augment offers are granted around a death/respawn, and the choice
            # window is opened manually. Scan during death, shortly after
            # respawn, or while we already have badges on screen (the player may
            # open the window after respawning). Never clear badges merely
            # because isDead is false: the choice window can still be open.
            if not (self.is_dead or now <= self._post_death_until or self._badges):
                return
            if self.level < 3 and not self._badges:
                return

            foreground = gamewindow.is_foreground() or gamewindow.is_overlay_foreground()
            if not foreground:
                if now - self._last_fg_log > 5:
                    self._last_fg_log = now
                    log("auto watch paused: game not foreground", gamewindow.foreground_info())
                return

            if now - self._last_auto_scan >= 1.0:
                self._last_auto_scan = now
                log("auto scan while dead", "level", self.level)
                self.recommend(auto=True)
            if now - self._last_alive > 30:
                self._last_alive = now
                log("auto watch alive", "dead", True, "level", self.level,
                    "fg", gamewindow.foreground_info())
        finally:
            self._schedule_watch()

    def _schedule_watch(self):
        if not self.auto_augment or self._watch_job:
            return
        self._watch_job = True
        try:
            self.root.after(300, self._augment_watch_tick)
        except tk.TclError:
            self._watch_job = False

    def _maybe_start_builds(self):
        slug = (self.champion or "").lower()
        if not slug or slug in self._builds_running or self._builds_for == slug:
            return
        if time.time() - self._builds_failed.get(slug, 0) < 60:
            return
        self._builds_running.add(slug)
        threading.Thread(target=self._builds_worker, args=(slug,), daemon=True).start()

    def _builds_worker(self, slug):
        try:
            data = opgg_builds.fetch(self.data_dir, slug)
            if data:
                item_icons.prepare(data, self.data_dir)
                groups = opgg_builds.build_groups(data)
                icon_map = item_icons.icon_map(data)
                for group in groups:
                    group["icons"] = [icon_map.get(int(item)) for item in group.get("items") or []]
                data["groups"] = groups
            log("builds loaded", slug, bool(data))
            self.pending.put({"builds": slug, "data": data})
        except Exception as error:
            self._builds_failed[slug] = time.time()
            log("builds failed", slug, repr(error))
        finally:
            self._builds_running.discard(slug)

    def _maybe_start_live_context(self):
        if not self.live_players or not self.live_self_team:
            return
        key = (self._live_generation, self.live_players[0].get("puuid"), self.live_self_team)
        if self._live_started_for == key:
            return
        self._live_started_for = key
        threading.Thread(target=self._live_team_worker, args=(self._live_generation,), daemon=True).start()

    def _live_team_worker(self, generation=None):
        try:
            players = list(self.live_players)
            self_team = self.live_self_team
            self_puuid = self.live_self_puuid or premade.self_puuid()
            log("live team start", "players", len(players), "team", self_team, "self", bool(self_puuid),
                "teams", sorted({str(p.get("team")) for p in players}))

            def align_self_team():
                nonlocal self_puuid, self_team
                if not self_puuid:
                    self_puuid = premade.self_puuid()
                if self_puuid:
                    own = next((p for p in players if p.get("puuid") == self_puuid), None)
                    if own and own.get("team") is not None:
                        self_team = _team_key(own.get("team"))
                return self_team

            def allies_from(rows):
                found = []
                for player in rows:
                    if _team_key(player.get("team")) != _team_key(self_team):
                        continue
                    puuid = player.get("puuid")
                    if puuid and puuid in (self_puuid, premade.self_puuid()):
                        continue
                    found.append({
                        "puuid": puuid,
                        "championId": player.get("championId"),
                        "team": player.get("team"),
                    })
                return found

            # Live Client Data does not always expose complete puuids/teams; the
            # LCU gameflow payload is a fallback and must be retried if it is
            # incomplete when the game has just started.
            if not any(p.get("puuid") for p in players) or len(players) < 5:
                info = premade.detect(context_champions=[p.get("championId") for p in players
                                                         if p.get("championId")])
                if info:
                    players = [
                        {"puuid": puuid, "name": meta.get("name"), "team": _team_key(meta.get("team")),
                         "championId": meta.get("championId")}
                        for puuid, meta in (info.get("players") or {}).items()
                    ]
                    self_puuid = self_puuid or premade.self_puuid()
                    if info.get("selfTeam") is not None:
                        self_team = _team_key(info.get("selfTeam"))
                    if self_puuid:
                        meta = (info.get("players") or {}).get(self_puuid) or {}
                        if meta.get("team") is not None:
                            self_team = _team_key(meta.get("team"))
            align_self_team()
            allies = allies_from(players)
            if len(allies) < 4:
                info = premade.detect(context_puuids=[p.get("puuid") for p in players if p.get("puuid")],
                                      context_champions=[p.get("championId") for p in players
                                                          if p.get("championId")])
                if info:
                    players = [
                        {"puuid": puuid, "name": meta.get("name"), "team": _team_key(meta.get("team")),
                         "championId": meta.get("championId")}
                        for puuid, meta in (info.get("players") or {}).items()
                    ]
                    self_puuid = self_puuid or premade.self_puuid()
                    if info.get("selfTeam") is not None:
                        self_team = _team_key(info.get("selfTeam"))
                    if self_puuid:
                        meta = (info.get("players") or {}).get(self_puuid) or {}
                        if meta.get("team") is not None:
                            self_team = _team_key(meta.get("team"))
                    align_self_team()
                    allies = allies_from(players)
            log("live team allies", len(allies), "team", self_team)
            rows = teammates.analyze_team(allies, timeout=30.0) if allies else []
            current_generation = self._live_generation if generation is None else generation
            self.pending.put({"live_team": rows, "generation": current_generation})
            log("live team ready", len(rows))
            puuids = [p.get("puuid") for p in players if p.get("puuid")]
            champions = [p.get("championId") for p in players if p.get("championId")]
            party = self._premade_groups(context_puuids=puuids, champions=champions) if puuids else None
            self.pending.put({"live_party": party, "generation": current_generation})
            log("live party ready", bool(party))
            # A 0-row result usually means the live payload was still incomplete.
            # Reset the guard so the next 5s poll retries instead of staying empty
            # for the rest of the game.
            timed_out = any("timeout" in str(row.get("note") or "").lower() for row in rows)
            if len(rows) < 4 or timed_out:
                self._live_started_for = None
        except Exception as error:
            self._live_started_for = None
            log("live team failed", repr(error))

    def _recommend_worker(self, auto=False):
        started = time.time()
        try:
            self.poll_champion()
            icons = (self.layout or {}).get("icons")
            tmp_dir = os.path.join(self.data_dir, "tmp")
            os.makedirs(tmp_dir, exist_ok=True)

            rect = None if self.args.image else gamewindow.find_client_rect()
            if auto and not rect:
                raise RuntimeError("game window not found during auto refresh")
            if (auto and not self.args.image
                    and not (gamewindow.is_foreground() or gamewindow.is_overlay_foreground())):
                raise RuntimeError("game is not foreground during auto refresh")
            if rect:
                fx, fy, fw, fh = rect
            else:
                fx, fy, fw, fh = 0, 0, self.screen_w, self.screen_h
            log("frame", fx, fy, fw, fh, "rect" if rect else "fallback")

            size = float(icons[0].get("size", 0.08)) * fw
            left = max(0, int(min(p.get("x", 0.5) for p in icons[:3]) * fw - 1.5 * size))
            right = min(fw, int(max(p.get("x", 0.5) for p in icons[:3]) * fw + 1.5 * size))
            top = max(0, int(float(icons[0].get("y", 0.28)) * fh - 1.1 * size))
            # Tall enough to include the description under each card, which is the
            # fallback key when OP.GG's Chinese name differs from the live client.
            bottom = min(fh, int(float(icons[0].get("y", 0.28)) * fh + 3.6 * size))
            if self.args.image:
                full = Image.open(self.args.image).convert("RGB")
                shot = full.crop((left, top, right, bottom))
            else:
                shot = ImageGrab.grab(bbox=(fx + left, fy + top, fx + right, fy + bottom))
            offset = (left, top)
            signature = self._shot_signature(shot, icons, (fw, fh), offset)
            captured = time.time()
            crops = []
            for index, point in enumerate(icons[:3]):
                cx = float(point.get("x", 0.5)) * fw - left
                cy = float(point.get("y", 0.62)) * fh - top
                box = (int(cx - size / 2), int(cy - size / 2), int(cx + size / 2), int(cy + size / 2))
                path = os.path.join(tmp_dir, f"crop-{index}.png")
                shot.crop(box).save(path)
                crops.append(path)
            names, descriptions = self.ocr_names(shot, icons, tmp_dir, offset, (fw, fh))
            ocr_done = time.time()
            log("ocr names", names, "desc", descriptions)
            result = self.run_classifier(crops, names, descriptions, self.level)
            result["_signature"] = signature
            result["_auto"] = bool(auto)
            result["_builds"] = self.builds if self._builds_for == (self.champion or "").lower() else None
            result["_anchors"] = [
                {
                    "x": fx + float(point.get("x", 0.5)) * fw,
                    "y": fy + float(point.get("y", 0.28)) * fh,
                    "size": float(point.get("size", 0.08)) * fw,
                }
                for point in icons[:3]
            ]
            result["_teammates"] = self._live_teammates
            # Never query SGP/LCU party data synchronously here: that can take
            # seconds and used to keep the panel stuck on "识别中…" long after
            # the cards were already recognized. The background live-team worker
            # supplies both teammates and party later.
            result["_premade"] = self._live_party
            log(
                "timings capture=%.2f ocr=%.2f classify=%.2f" %
                (captured - started, ocr_done - captured, time.time() - ocr_done)
            )
            self.pending.put({"result": result, "auto": bool(auto)})
        except Exception as error:
            log("recommend failed", repr(error))
            self.pending.put({"error": str(error), "auto": bool(auto)})
        finally:
            self.busy = False

    def _champ_select_worker(self, session=None, prefetch=False):
        try:
            if session is None and not self.args.image:
                session = lcu.champ_select()
            log("lcu champ select", session, "prefetch" if prefetch else "")
            if session is not None:
                rows = self._champ_rows(session)
                key = lcu.champ_select_key(session)
                if self.args.champ_demo:
                    demo = []
                    for index, name in enumerate(["迅捷斥候", "九尾妖狐", "暴走萝莉", "德玛西亚之力", "光辉女郎"]):
                        demo.append({"id": index, "name": name, "rank": index * 10 + 1,
                                     "grade": "SABCD"[min(index, 4)],
                                     "zone": "bench" if index else "current"})
                    if prefetch:
                        self.pending.put({"prefetch_champions": demo, "key": key})
                    else:
                        self.pending.put({"champions": demo, "premade": self._premade_groups(),
                                          "teammates": teammates.demo_rows()})
                    return
                team = session.get("team") or []
                if prefetch:
                    self.pending.put({"prefetch_champions": rows, "key": key})
                else:
                    self._premade = None
                    self._teammates = None
                    self.pending.put({"champions": rows, "premade": None, "premade_pending": True})
                threading.Thread(target=self._premade_worker, args=(team, prefetch, key), daemon=True).start()
                return

            if prefetch:
                self.pending.put({"prefetch_error": "no-session", "key": self._prefetch_key})
                return

            # Fallback: no LCU access (not elevated) -> OCR only the centre name.
            if self.args.image:
                shot = Image.open(self.args.image).convert("RGB")
            else:
                shot = ImageGrab.grab(bbox=(0, 0, self.screen_w, self.screen_h))
            tmp_dir = os.path.join(self.data_dir, "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            path = os.path.join(tmp_dir, "champselect.png")
            shot.save(path)
            data = self.ocr_json(path, os.path.join(tmp_dir, "champselect.json"))
            lines = data.get("lines") or []
            log("champ select ocr lines", len(lines))
            matches = champselect.match_lines(lines, self.champions, self.screen_h, self.screen_w)
            log("champ select matches", [(m["name"], m["rank"], m["zone"]) for m in matches])
            if self.args.champ_demo:
                matches = [{"id": index, "name": name, "rank": index * 10 + 1,
                            "grade": "SABCD"[min(index, 4)],
                            "zone": "bench" if index else "current"}
                           for index, name in enumerate(["迅捷斥候", "九尾妖狐", "暴走萝莉", "德玛西亚之力", "光辉女郎"])]
            self.pending.put({"champions": matches, "ocr_only": True,
                              "premade": self._premade_groups(), "teammates": []})
        except Exception as error:
            log("champ select failed", repr(error))
            if prefetch:
                self.pending.put({"prefetch_error": str(error), "key": self._prefetch_key})
            else:
                self.pending.put({"error": str(error)})
        finally:
            if not prefetch:
                self.busy = False

    def poll_results(self):
        while True:
            try:
                item = self.pending.get_nowait()
            except queue.Empty:
                break
            if "prefetch_probe" in item:
                payload = item["prefetch_probe"]
                self._handle_prefetch_champ(payload.get("phase"), payload.get("session"))
            elif "prefetch_champions" in item:
                if item.get("key") != self._prefetch_key:
                    continue
                self._prefetched_matches = item.get("prefetch_champions") or []
                self._prefetch_at = time.time()
                self._prefetching = False
                self._prefetch_started = 0.0
                log("prefetch ready", len(self._prefetched_matches))
                if self._show_on_prefetch:
                    self._show_on_prefetch = False
                    if self._champ_matches is None:
                        self._show_prefetched()
            elif "prefetch_error" in item:
                if item.get("key") != self._prefetch_key:
                    continue
                self._prefetching = False
                self._prefetch_started = 0.0
                self._prefetch_key = None
                self._prefetched_matches = None
                self._prefetched_premade = None
                self._prefetched_teammates = None
                self._prefetch_at = 0.0
                log("prefetch failed", item.get("prefetch_error"))
                if self._show_on_prefetch:
                    self._show_on_prefetch = False
                    self.show_idle("选人数据暂不可用，请稍后再点", 8000)
            elif "error" in item:
                if item.get("auto"):
                    log("auto recommend failed", item.get("error"))
                    self._watch_base = None
                    self._watch_candidate = None
                    self._auto_hold_until = time.time() + 1.0
                    self._clear_badges()
                    if self._manual_show_pending and self.in_game():
                        self._manual_show_pending = False
                        self.show_game_info()
                else:
                    self.show_idle(f"识别失败：{item['error']}")
            elif "live_reset" in item:
                self._live_teammates = None
                self._live_party = None
                self._live_started_for = None
                if self.result:
                    self.result["_teammates"] = None
                    self.result["_premade"] = None
                if self.width != self.pill_w:
                    if self.in_game():
                        self.show_game_info()
                    else:
                        self.show_pill()
            elif "live_team" in item:
                if item.get("generation") is not None and item.get("generation") != self._live_generation:
                    log("discard stale live team", item.get("generation"), self._live_generation)
                else:
                    self._live_teammates = item.get("live_team") or []
                    if "live_party" in item:
                        self._live_party = item.get("live_party")
                    if self.result:
                        self.result["_teammates"] = self._live_teammates
                        self.result["_premade"] = self._live_party
                        if self.width != self.pill_w:
                            hide_ms = 0 if not self.result.get("cards") else 20000
                            self.render(self.result, preserve_badges=True, auto_hide_ms=hide_ms)
            elif "live_party" in item:
                if item.get("generation") is None or item.get("generation") == self._live_generation:
                    self._live_party = item.get("live_party")
                    if self.result:
                        self.result["_premade"] = self._live_party
                        if self.width != self.pill_w:
                            hide_ms = 0 if not self.result.get("cards") else 20000
                            self.render(self.result, preserve_badges=True, auto_hide_ms=hide_ms)
            elif "builds" in item:
                slug = item.get("builds")
                self._builds_for = slug
                self.builds = item.get("data")
                log("builds ready", slug, len((self.builds or {}).get("core") or []))
                if self.result:
                    self.result["_builds"] = self.builds
                    if not self.result.get("cards") and self.champion:
                        self.result["champion"] = self.champion
                    if self.width != self.pill_w:
                        hide_ms = 0 if not self.result.get("cards") else 20000
                        self.render(self.result, preserve_badges=True, auto_hide_ms=hide_ms)
            elif "champ_live" in item:
                live = item.get("champ_live") or {}
                rows = live.get("rows") or []
                key = live.get("key")
                if rows and key:
                    self._prefetch_key = key
                    self._prefetched_matches = rows
                    self._prefetch_at = time.time()
                    self._prefetching = False
                    self._prefetch_started = 0.0
                    self._champ_live_missing = False
                    sig = (key, tuple((row.get("id"), row.get("zone"), row.get("grade")) for row in rows))
                    if self._champ_matches is not None and sig != self._champ_live_sig:
                        self._champ_matches = rows
                        self._champ_live_sig = sig
                        self.render_champions(rows, self._premade)
            elif "champ_live_closed" in item:
                if not self._champ_live_missing:
                    self._champ_live_missing = True
                    self._champ_live_sig = None
                    self._prefetch_key = None
                    self._prefetched_matches = None
                    self._prefetch_at = 0.0
                    self._prefetching = False
                    if self._champ_matches is not None:
                        self.show_pill()
            elif "premade_update" in item:
                self._premade = item["premade_update"]
                if item.get("prefetch") and item.get("key") == self._prefetch_key:
                    self._prefetched_premade = item["premade_update"]
                if self._champ_matches is not None:
                    self.render_champions(self._champ_matches, self._premade, self._teammates)
            elif "teammates_update" in item:
                self._teammates = item["teammates_update"]
                if item.get("prefetch") and item.get("key") == self._prefetch_key:
                    self._prefetched_teammates = item["teammates_update"]
                if self._champ_matches is not None:
                    self.render_champions(self._champ_matches, self._premade, self._teammates)
            elif "teammates_error" in item:
                self._teammates = []
                if self._champ_matches is not None:
                    self.render_champions(self._champ_matches, self._premade, self._teammates)
            elif "champions" in item:
                self._champ_matches = item["champions"]
                self._premade = item.get("premade")
                self._teammates = item.get("teammates")
                self.render_champions(item["champions"], self._premade, self._teammates)
            else:
                result = item.get("result") or {}
                auto = bool(item.get("auto") or result.get("_auto"))
                recognized = [c for c in (result.get("cards") or []) if c.get("augmentId") is not None]
                if auto and len(recognized) < 2:
                    if self._auto_empty_retries < 1:
                        self._auto_empty_retries += 1
                        log("auto recommend found too few cards; retrying", self._auto_empty_retries)
                        self._auto_hold_until = time.time() + 1.0
                        self._watch_candidate = None

                        def retry_auto():
                            if self.busy or not self.in_game():
                                return
                            if not (gamewindow.is_foreground() or gamewindow.is_overlay_foreground()):
                                return
                            self.recommend(auto=True)

                        self.root.after(300, retry_auto)
                    else:
                        log("auto recommend still empty; clearing badges")
                        self._auto_empty_retries = 0
                        self._watch_base = None
                        self._watch_candidate = None
                        self._auto_hold_until = time.time() + 1.0
                        self._clear_badges()
                        if self._manual_show_pending and self.in_game():
                            self._manual_show_pending = False
                            self.show_game_info()
                elif auto and self.result and self._card_key(self.result) == self._card_key(result):
                    log("auto recommend same cards; no visual change")
                    self._auto_hold_until = time.time() + 1.0
                    self._start_augment_watch(None)
                    if self._manual_show_pending:
                        self._manual_show_pending = False
                        self.render(self.result, auto_hide_ms=20000)
                elif auto:
                    # Keep the left window manual: auto recognition updates the
                    # badges under the cards, but only refreshes the panel if it
                    # was already open. It never pops the panel by itself.
                    was_open = self.width != self.pill_w or self._manual_show_pending
                    self._manual_show_pending = False
                    self.result = result
                    self._show_badges(result)
                    self._augment_badge_level = self.level
                    if was_open:
                        self.render(result, preserve_badges=True, auto_hide_ms=20000)
                    self._auto_hold_until = time.time() + 1.2
                else:
                    hide_ms = 0 if len(recognized) < 2 else 20000
                    self.render(result, auto_hide_ms=hide_ms)
        self.root.after(80, self.poll_results)

    def ocr_json(self, image_path, out_path):
        """Warm worker first; fall back to a one-shot PowerShell call."""
        started = time.time()
        data = self.ocr.recognize(image_path, out_path)
        if data is None:
            script = os.path.join(ROOT, "scripts", "ocr-win.ps1")
            try:
                os.remove(out_path)
            except OSError:
                pass
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
                     "-Path", image_path, "-Language", "zh-Hans-CN", "-OutFile", out_path],
                    capture_output=True, timeout=25, creationflags=0x08000000,
                )
            except Exception as error:
                log("ocr one-shot failed", repr(error))
            try:
                with open(out_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                data = None
            log("ocr fallback %.2fs" % (time.time() - started))
        return data or {}

    def ocr_names(self, shot, icons, tmp_dir, offset=(0, 0), frame=None):
        """Read each card's title and description in one OCR pass.

        The title is the strongest key, but OP.GG's Chinese translation can differ
        from the live client; the description is kept for a fallback match.
        """
        name_band = (self.layout or {}).get("nameBand") or {"dy": 0.55, "h": 0.75, "w": 2.4}
        desc_band = (self.layout or {}).get("descBand") or {"dy": 1.15, "h": 2.1, "w": 2.4}
        width, height = frame if frame else (self.screen_w, self.screen_h)
        size = float(icons[0].get("size", 0.078)) * width
        icon_xs = [float(p.get("x", 0.5)) * width for p in icons[:3]]
        icon_y = float(icons[0].get("y", 0.28)) * height

        top = int(icon_y + float(name_band.get("dy", 0.55)) * size) - offset[1]
        bottom = int(icon_y + (float(desc_band.get("dy", 1.15)) + float(desc_band.get("h", 2.1))) * size) - offset[1]
        band_w = max(float(name_band.get("w", 2.4)), float(desc_band.get("w", 2.4)))
        left = int(min(icon_xs) - band_w * size / 2) - offset[0]
        right = int(max(icon_xs) + band_w * size / 2) - offset[0]
        path = os.path.join(tmp_dir, "names.png")
        shot.crop((left, top, right, bottom)).save(path)

        result_path = os.path.join(tmp_dir, "names.json")
        data = self.ocr_json(path, result_path)
        slots = [[], [], []]
        for line in data.get("lines") or []:
            text = (line.get("text") or "").replace(" ", "").replace("\u3000", "")
            if len(text) < 2:
                continue
            center = left + offset[0] + float(line.get("x", 0)) + float(line.get("w", 0)) / 2
            slot = min(range(3), key=lambda i: abs(icon_xs[i] - center))
            slots[slot].append((float(line.get("y", 0)), text))
        names = ["", "", ""]
        descriptions = ["", "", ""]
        for slot, lines in enumerate(slots):
            lines.sort(key=lambda item: item[0])
            if lines:
                names[slot] = lines[0][1]
                # Keep every line: if OCR misses the title, the description still
                # has enough text for the OP.GG descZh fallback.
                descriptions[slot] = " ".join(text for _y, text in lines)
        return names, descriptions

    def run_classifier(self, crops, names=None, descriptions=None, level=None):
        names = list(names or [])
        descriptions = list(descriptions or [])
        return classify_local.classify(self.data_dir, self.champion, names, crops,
                                       self.args.policy, descriptions, level)

    def _run_classifier_cli(self, request):
        return classify_local.classify(self.data_dir, request.get("champion"), request.get("names"),
                                       request.get("crops"), request.get("policy", "balanced"),
                                       request.get("descriptions"), request.get("level"))

    def demo_result(self):
        return {
            "champion": self.champion or "Annie",
            "patch": "16.22",
            "cards": [
                {"slot": 0, "name": "High Roller", "grade": "S", "score": 93.1},
                {"slot": 1, "name": "Witchful Thinking", "grade": "B", "score": 81.4},
                {"slot": 2, "name": "Homeguard", "grade": "C", "score": 76.2},
            ],
            "recommendation": {"keepSlot": 0, "rerollSlots": [1, 2], "chaseKeep": False,
                               "reason": "保留 1 号卡「High Roller」（S 93.1）；重置 2 号卡、3 号卡。"},
            "warnings": [],
        }

    def save_screenshot(self):
        restored = False
        try:
            self._clear_badges()
            # Hide the panel first, otherwise it ends up in its own screenshot.
            self.root.withdraw()
            self.root.update_idletasks()
            time.sleep(0.25)
            path = os.path.join(self.data_dir, "screenshots", f"capture-{int(time.time())}.png")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            ImageGrab.grab(bbox=(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())).save(path)
            self.show_idle(f"截图已保存：\n{os.path.basename(path)}\n在 data/screenshots 里")
            restored = True
            log("screenshot", path)
        except Exception as error:
            self.show_idle(f"截图失败：{error}")
            restored = True
        finally:
            if not restored:
                self.show_pill()

    # ---------- dragging ----------

    def quit_app(self):
        log("quit from pill")
        self._clear_badges()
        self.root.destroy()

    def on_right_click(self, _event):
        log("right click: collapse to pill")
        self._info_suppressed_until = time.time() + 10.0
        self.show_pill()

    def on_drag_start(self, event):
        self.drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def on_drag_move(self, event):
        if not self.drag_origin:
            return
        self.root.geometry(f"+{event.x_root - self.drag_origin[0]}+{event.y_root - self.drag_origin[1]}")

    def on_ready(self):
        log("ready", "mock" if self.args.mock else "real")
        if self.args.selftest:
            self.root.after(int(self.args.selftest * 1000), self.inject_selftest)
        if self.args.once:
            self.root.after(200, self.recommend)
            self.root.after(7000, self.root.destroy)
            return
        if self.args.demo:
            self.render(self.demo_result())
            self.root.after(int(self.args.demo * 1000), self.root.destroy)
            return
        if self.args.mock:
            self.render(self.demo_result())
            return
        if self.args.image:
            # Offline test: analyse the saved screenshot and keep it on screen.
            self.root.after(300, self.recommend)
            return
        if not self.layout:
            log("no layout.json: click 截图 in the panel to calibrate")
        else:
            log("ready, waiting for a click")
        self.show_pill()
        try:
            log("source mtime", int(os.path.getmtime(__file__)))
        except OSError:
            pass
        if self.auto_augment:
            log("auto watch started (300ms)")
            self._schedule_watch()
        # Keep champion/level/team data warm even when no augment screen is open;
        # this is also what makes the normal in-game info panel useful.
        self._periodic_champion()
        self.root.after(9000, self._idle_blink)
        threading.Thread(target=self._background_update, daemon=True).start()
        if self.prefetch_champ and not self.args.champ_demo:
            self.root.after(600, self._champ_live_tick)

    def inject_selftest(self):
        log("selftest: injecting F9")
        user32 = ctypes.windll.user32
        user32.keybd_event(VK["F9"], 0, 0, 0)
        self.root.after(150, lambda: user32.keybd_event(VK["F9"], 0, 2, 0))

    def run(self):
        try:
            self.root.mainloop()
        finally:
            try:
                self.ocr.stop()
            except Exception:
                pass
            log("exit")


def main():
    args = parse_args()
    log("start", " ".join(sys.argv[1:]))
    # Single instance: a second panel would be confusing.
    if not (args.image or args.once or args.demo or args.mock or args.no_mutex):
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "league-soraka-overlay")
        if kernel32.GetLastError() == 183:
            log("another overlay instance is already running; exiting")
            return
    import traceback

    def hook(exc_type, exc_value, exc_tb):
        log("uncaught", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

    sys.excepthook = hook
    try:
        Overlay(args).run()
    except Exception:
        log("crash", traceback.format_exc())
        raise


if __name__ == "__main__":
    main()

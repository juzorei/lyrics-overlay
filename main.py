"""Lyrics Overlay — точка входа.

Опрашивает плеер, тянет тексты из LRCLIB, рисует оверлей.
Запуск: python main.py [--demo] [--mode compact|karaoke]
"""

from __future__ import annotations

import argparse
import re
import signal
import sys
import threading
from pathlib import Path

import toml

from PySide6.QtCore import QObject, QTimer, Signal

from lyrics.cache import LyricsCache
from lyrics.genius import GeniusProvider
from lyrics.lrclib import LrclibProvider
from lyrics.netease import NeteaseProvider
from lyrics.parser import parse_lrc
from lyrics.provider import LyricsService
from players.base import Track
from players import create_monitor
from ui import LyricOverlay


class UiBridge(QObject):
    """Сигнальный мост: рабочие потоки -> GUI-поток.

    QTimer.singleShot из чужого потока небезопасен; сигнал с авто-
    (queued) коннекцией гарантирует выполнение слота в GUI-потоке.
    """

    not_found = Signal()
    synced_ready = Signal(list)
    plain_ready = Signal(str)
    mode_toggled = Signal()      # от pynput-хоткея
    clickthrough_toggled = Signal()
    visibility_toggled = Signal()


def load_config(path: Path, overrides: dict) -> dict:
    cfg = toml.load(path)
    # токены и личные настройки — в token.local.toml рядом с конфигом (в .gitignore)
    local = path.parent / "token.local.toml"
    if local.exists():
        for section, kv in toml.load(local).items():
            cfg.setdefault(section, {}).update(kv)
    for section, kv in overrides.items():
        cfg.setdefault(section, {}).update(kv)
    return cfg


class App:
    def __init__(self, cfg: dict, bridge: UiBridge, demo: bool):
        self._cfg = cfg
        self._bridge = bridge
        self._monitor = create_monitor(demo=demo)
        timeout = cfg["lyrics"].get("request_timeout", 10)
        cache = LyricsCache(Path(cfg["lyrics"].get("cache_dir", "cache")))
        providers = [
            LrclibProvider(timeout=timeout),
            NeteaseProvider(timeout=timeout),
        ]
        genius_token = cfg["lyrics"].get("genius_token", "")
        if genius_token:
            providers.append(GeniusProvider(access_token=genius_token, timeout=timeout))
        self._lyrics = LyricsService(
            cache,
            providers=providers,
            show_plain_when_unsynced=cfg["lyrics"].get("show_plain_when_unsynced", True),
        )
        self._current_key: str | None = None
        self._fetch_gen = 0  # защита от устаревших ответов после смены/остановки трека
        self._fetch_lock = threading.Lock()

    def poll_once(self, overlay: LyricOverlay) -> None:
        """Один цикл: трек из монитора → текст → UI. Вызывается QTimer'ом."""
        track = self._monitor.current_track()
        if track is None or track.is_empty:
            overlay.set_no_media()
            self._current_key = None
            self._fetch_gen += 1  # отменяем недоставленные результаты загрузки
            return
        overlay.update_track(track.title, track.artist, track.artwork)
        overlay.set_position(track.position_s, track.playing, track.duration_s)
        if track.key != self._current_key:
            self._current_key = track.key
            self._fetch_gen += 1
            gen = self._fetch_gen
            # загрузка текста — в фоне, чтобы UI не подвисал
            threading.Thread(target=self._fetch_lyrics, args=(track, gen), daemon=True).start()

    def _fetch_lyrics(self, track: Track, gen: int) -> None:
        with self._fetch_lock:
            result = self._lyrics.get_lyrics(track.title, track.artist, track.duration_s)
        if gen != self._fetch_gen:
            return  # трек уже сменился/остановился — результат неактуален
        # доставка в GUI-поток через queued-сигнал
        if result is None:
            self._bridge.not_found.emit()
        elif result.synced:
            self._bridge.synced_ready.emit(parse_lrc(result.synced))
        elif result.plain:
            self._bridge.plain_ready.emit(result.plain)


def main() -> int:
    parser = argparse.ArgumentParser(description="Universal lyrics overlay")
    parser.add_argument("--demo", action="store_true", help="использовать демо-монитор")
    parser.add_argument("--mode", choices=["compact", "karaoke"])
    parser.add_argument("--config", default=str(Path(__file__).parent / "config.toml"))
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 1
    cfg = load_config(cfg_path, {} if args.mode is None else {"ui": {"mode": args.mode}})

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    ui_cfg = dict(cfg.get("ui", {}))
    ui_cfg["sync_offset_s"] = cfg.get("lyrics", {}).get("sync_offset_s", 0.0)
    overlay = LyricOverlay(ui_cfg)
    overlay.show()

    # перетащили мышью → запомнить позицию в config.toml
    def persist_position(x: int, y: int) -> None:
        try:
            text = cfg_path.read_text(encoding="utf-8")
            text = re.sub(r"(compact_x\s*=\s*)\S+", rf"\g<1>{x}", text)
            text = re.sub(r"(compact_y\s*=\s*)\S+", rf"\g<1>{y}", text)
            cfg_path.write_text(text, encoding="utf-8")
        except OSError:
            pass  # не сохранили позицию — не критично, виджет продолжает работать

    overlay.on_drag_finished = persist_position

    # изменили настройку в контекстном меню → сохранить в config.toml
    def persist_setting(key: str, value) -> None:
        try:
            text = cfg_path.read_text(encoding="utf-8")
            # ini-подобный TOML: key = value (bool пишем как true/false)
            if isinstance(value, bool):
                v = "true" if value else "false"
            else:
                v = str(value)
            pat = rf"(?m)^(\s*{re.escape(key)}\s*=\s*)\S+.*$"
            if re.search(pat, text):
                text = re.sub(pat, rf"\g<1>{v}", text)
            else:  # ключа нет — добавляем в [ui]
                text = text.replace("[ui]", f"[ui]\n{key} = {v}", 1)
            cfg_path.write_text(text, encoding="utf-8")
        except OSError:
            pass

    overlay.on_setting_changed = persist_setting

    bridge = UiBridge()
    bridge.not_found.connect(overlay.set_not_found)
    bridge.synced_ready.connect(overlay.set_synced_lines)
    bridge.plain_ready.connect(overlay.set_plain_text)

    application = App(cfg, bridge, demo=args.demo)

    poll = QTimer()
    poll.timeout.connect(lambda: application.poll_once(overlay))
    poll.start(int(cfg.get("monitor", {}).get("poll_interval", 0.5) * 1000))

    # горячие клавиши (pynput)
    hotkeys = cfg.get("hotkeys", {})
    listeners = setup_hotkeys(hotkeys, overlay, app, bridge)

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(app.exec())


def setup_hotkeys(hotkeys: dict, overlay: LyricOverlay, app, bridge: UiBridge) -> list:
    from pynput import keyboard

    made = []

    def to_pynput(hotkey: str) -> str:
        # "ctrl+alt+l" → "<ctrl>+<alt>+l"; одиночные символы — как есть
        parts = []
        for p in hotkey.split("+"):
            p = p.strip()
            parts.append(f"<{p}>" if len(p) > 1 else p)
        return "+".join(parts)

    def on(hotkey: str, fn):
        try:
            listener = keyboard.GlobalHotKeys({to_pynput(hotkey): fn})
            listener.start()
            made.append(listener)
        except Exception:
            print(f"hotkey failed: {hotkey}", file=sys.stderr)

    # pynput зовёт колбэки из своего потока — эмитим Qt-сигнал,
    # который queued-коннекцией доставит вызов в GUI-поток
    bridge.mode_toggled.connect(overlay.toggle_mode)
    bridge.clickthrough_toggled.connect(overlay.toggle_click_through)

    def toggle_vis():
        overlay.setVisible(not overlay.isVisible())

    bridge.visibility_toggled.connect(toggle_vis)

    if h := hotkeys.get("toggle_mode"):
        on(h, bridge.mode_toggled.emit)
    if h := hotkeys.get("toggle_clickthrough"):
        on(h, bridge.clickthrough_toggled.emit)
    if h := hotkeys.get("toggle_visible"):
        on(h, bridge.visibility_toggled.emit)
    return made


if __name__ == "__main__":
    main()

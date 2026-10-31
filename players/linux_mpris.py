"""Монитор MPRIS (D-Bus) для Linux.

Опрашивает интерфейс org.mpris.MediaPlayer2.* — стандарт, который
поддерживают почти все Linux-плееры (Spotify, VLC, Rhythmbox, mpv,
браузеры и т.д.).
"""

from __future__ import annotations

import asyncio
import sys
import threading
import traceback
from typing import Optional

from players.base import Track


def _unwrap(v):
    """dbus-next отдаёт Variant — снимаем обёртку, сколько бы её ни было."""
    while hasattr(v, "value"):
        v = v.value
    return v


def _artwork_bytes(url: str) -> bytes:
    """Скачивает/читает обложку по mpris:artUrl (http(s) или file://)."""
    if not url:
        return b""
    try:
        from urllib.parse import urlparse
        from urllib.request import url2pathname, urlopen

        parsed = urlparse(url)
        if parsed.scheme == "file":
            from pathlib import Path

            return Path(url2pathname(parsed.path)).read_bytes()
        if parsed.scheme in ("http", "https"):
            req = urllib.request.Request(url, headers={"User-Agent": "lyrics-overlay/0.1"})
            with urlopen(req, timeout=5) as resp:
                return resp.read(2_000_000)  # обложки больше 2 МБ не бывают
    except Exception:
        return b""  # обложка не критична
    return b""


class MprisMonitor:
    """Опрашивает MPRIS-плееры в фоновом event-loop потоке (dbus-next)."""

    def __init__(self, poll_interval: float = 0.5):
        self._poll_interval = poll_interval
        self._latest: Optional[Track] = None
        self._preferred_name: Optional[str] = None  # липкость к источнику трека
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._poll_loop())
        finally:
            loop.close()

    async def _poll_loop(self) -> None:
        from dbus_next.aio import MessageBus

        try:
            bus = await MessageBus().connect()
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return
        while not self._stop.is_set():
            try:
                self._latest = await self._query(bus)
            except Exception:
                # сессия плеера умерла /player рестартует — логируем, переживаем
                traceback.print_exc(file=sys.stderr)
                self._latest = None
            await asyncio.sleep(self._poll_interval)

    @staticmethod
    async def _query(bus) -> Optional[Track]:
        import dbus_next

        reply = await bus.call(
            dbus_next.Message(
                destination="org.freedesktop.DBus",
                interface="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                member="ListNames",
            )
        )
        mpris_names = [n for n in reply.body[0] if n.startswith("org.mpris.MediaPlayer2.")]
        if not mpris_names:
            return None

        # статусы всех плееров (дёшево), затем липкий выбор
        statuses = {}
        for name in mpris_names:
            try:
                introspection = await bus.introspect(name, "/org/mpris/MediaPlayer2")
                obj = bus.get_object(name, "/org/mpris/MediaPlayer2")
                props = obj.get_interface("org.freedesktop.DBus.Properties")
                statuses[name] = str(_unwrap(
                    await props.call_get("org.mpris.MediaPlayer2.Player", "PlaybackStatus")
                ))
            except Exception:
                continue

        chosen = None
        if MprisMonitor._preferred_name and MprisMonitor._preferred_name in statuses:
            if statuses[MprisMonitor._preferred_name] == "Playing":
                chosen = MprisMonitor._preferred_name
            elif not any(st == "Playing" for st in statuses.values()):
                chosen = MprisMonitor._preferred_name  # наш на паузе, другие молчат
        if chosen is None:
            for name, st in statuses.items():
                if st == "Playing":
                    chosen = name
                    break
        if chosen is None:
            return None
        MprisMonitor._preferred_name = chosen

        name = chosen
        obj = bus.get_object(name, "/org/mpris/MediaPlayer2")
        props = obj.get_interface("org.freedesktop.DBus.Properties")

        meta_raw = await props.call_get("org.mpris.MediaPlayer2.Player", "Metadata")
        playback = _unwrap(await props.call_get("org.mpris.MediaPlayer2.Player", "PlaybackStatus"))
        position = _unwrap(await props.call_get("org.mpris.MediaPlayer2.Player", "Position"))

        metadata = _unwrap(meta_raw)
        if not isinstance(metadata, dict) or "mpris:title" not in metadata:
            return None

        title = str(_unwrap(metadata["mpris:title"])).strip()
        if not title:
            return None

        artist = ""
        if "mpris:artist" in metadata:
            arts = _unwrap(metadata["mpris:artist"])
            artist = (", ".join(str(a) for a in arts) if isinstance(arts, list) else str(arts)).strip()

        length = int(_unwrap(metadata["mpris:length"])) if "mpris:length" in metadata else 0
        art_url = str(_unwrap(metadata["mpris:artUrl"])) if "mpris:artUrl" in metadata else ""

        return Track(
            title=title,
            artist=artist,
            position_us=int(position),
            duration_us=length,
            playing=str(playback) == "Playing",
            source=name.replace("org.mpris.MediaPlayer2.", ""),
            artwork=_artwork_bytes(art_url),
        )

    def current_track(self) -> Optional[Track]:
        return self._latest

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

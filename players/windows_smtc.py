"""Монитор SMTC (System Media Transport Controls) для Windows.

Читает GlobalSystemMediaTransportControlsSessionManager — единый реестр
медиа-сессий всех приложений. Каждый плеер, поддерживающий медиаклавиши
(Spotify, браузеры, AIMP, VK, Yandex Music и т.д.), публикует там
название трека, исполнителя и позицию воспроизведения.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from typing import Optional

from players.base import Track


class SmtcMonitor:
    """Опрашивает SMTC в фоновом event-loop потоке.

    winsdk — async-first API, поэтому держим собственный loop;
    current_track() синхронно отдаёт последний известный Track.
    """

    def __init__(self, poll_interval: float = 0.5):
        self._poll_interval = poll_interval
        self._latest: Optional[Track] = None
        self._preferred_source: Optional[str] = None  # липкость к источнику трека
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
        import winsdk.windows.media.control as wmc

        manager = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
        while not self._stop.is_set():
            try:
                items = []
                for s in manager.get_sessions():
                    try:
                        playing = (
                            s.get_playback_info().playback_status
                            == wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
                        )
                    except Exception:
                        continue
                    items.append((s, playing))
                session = self._pick_session(items)
                self._latest = (
                    await self._track_from_session(session) if session else None
                )
            except Exception:
                # SMTC иногда отдаёт RPC-ошибки при смерти сессии плеера — переживаем.
                self._latest = None
            await asyncio.sleep(self._poll_interval)

    def _pick_session(self, items) -> Optional[object]:
        """Липкий выбор сессии: пока выбранный источник играет, слушаем только
        его — иначе браузер/видео перебивает трек у виджета."""
        if self._preferred_source:
            for s, playing in items:
                if s.source_app_user_model_id == self._preferred_source:
                    if playing:
                        return s
                    if not any(pl for _, pl in items):
                        return s  # наш источник на паузе, других играющих нет — держим его
                    break  # наш на паузе, но что-то другое играет — переключаемся
        for s, playing in items:
            if playing:
                self._preferred_source = s.source_app_user_model_id
                return s
        return None

    @staticmethod
    def _as_us(ticks) -> int:
        """TimelinePosition приходит как datetime.timedelta (полные микросекунды)
        либо как int-тики по 100 нс в иных версиях — обрабатываем оба случая."""
        if not ticks:
            return 0
        if isinstance(ticks, timedelta):
            return int(ticks / timedelta(microseconds=1))
        return int(ticks) * 10  # 100-наносекундные тики → микросекунды

    @staticmethod
    async def _track_from_session(session) -> Optional[Track]:
        import winsdk.windows.media.control as wmc

        media = await session.try_get_media_properties_async()
        timeline = session.get_timeline_properties()
        playback = session.get_playback_info()
        title = (media.title or "").strip()
        if not title:
            return None
        playing = playback.playback_status == wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
        artwork = await SmtcMonitor._artwork_bytes(media)
        return Track(
            title=title,
            artist=(media.artist or "").strip(),
            album=(media.album_title or "").strip(),
            duration_us=SmtcMonitor._as_us(getattr(timeline, "end_time", None)),
            position_us=SmtcMonitor._as_us(getattr(timeline, "position", None)),
            playing=bool(playing),
            source=(session.source_app_user_model_id or "").split("!")[0].split(".exe")[0],
            artwork=artwork,
        )

    @staticmethod
    async def _artwork_bytes(media) -> bytes:
        """Обложка из SMTC-миниатюры (обычно PNG/JPEG 300x300)."""
        try:
            thumb = getattr(media, "thumbnail", None)
            if thumb is None:
                return b""
            stream = await thumb.open_read_async()
            size = stream.size
            if not size:
                return b""
            from winsdk.windows.storage.streams import DataReader

            reader = DataReader(stream)
            await reader.load_async(size)
            return bytes(reader.read_buffer(size))
        except Exception:
            return b""  # обложка не критична — плеер может её отозвать в любой момент

    def current_track(self) -> Optional[Track]:
        return self._latest

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

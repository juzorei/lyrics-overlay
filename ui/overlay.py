"""Оверлей на PySide6: компакт-панель и karaoke-режим, кастомная отрисовка.

Дизайн: обложка слева (из SMTC/MPRIS), справа исполнитель/название и
прогресс-бар трека с акцентным цветом, ниже — текст песни. Всё рисуется
в paintEvent: длинные строки переносятся с лимитом визуальных строк и не
наезжают. Перерисовка только при изменениях; смена строки анимируется
кроссфейдом со сдвигом.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QAbstractAnimation, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from lyrics.parser import LyricLine, active_line_index

PADDING = 16
ART_SIZE_COMPACT = 64
ART_SIZE_KARAOKE = 96
# Сэмплы позиции, отстающие от экстраполяции меньше чем на это, — мусор:
# многие плееры (Spotify, MPRIS) шлют «замёрзшее» значение позиции.
# Принимаем только сэмплы «вперёд» и явные перемотки назад.
STALE_SAMPLE_MAX_S = 3.0
# plain-текст без известной длительности: строка меняется раз в N секунд
PLAIN_LINE_INTERVAL_S = 24.0


def _accent_color(settings: dict) -> QColor:
    return QColor(settings.get("accent", "#22d3ee"))


class LyricOverlay(QWidget):
    """Всегда-поверх-всех виджет с обложкой и текстом текущего трека."""

    def __init__(self, settings: dict, parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self._settings = settings
        self._mode = settings.get("mode", "compact")
        self._lines: list[LyricLine] = []
        self._synced = False
        self._message = "♪ ничего не играет"
        self._title = ""
        self._artist = ""

        self._position_s = 0.0
        self._duration_s: Optional[float] = None
        self._pos_ts = 0.0
        self._playing = False
        self._last_sample_pos: Optional[float] = None  # последний сырой сэмпл плеера
        self._active_idx = -10
        self._text_alpha = 1.0

        self._art_key = None
        self._art_pixmaps: dict[tuple, QPixmap] = {}  # ключ трека -> QPixmap
        self._art_placeholder = self._make_placeholder(ART_SIZE_COMPACT)

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(280)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim_progress = 1.0

        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._on_tick)
        self._ui_timer.start(120)

        self._click_through = False
        self._drag_offset = None
        self.on_drag_finished = None  # колбэк main.py: сохранить позицию в конфиг
        self.on_setting_changed = None  # колбэк main.py: (key, value) -> сохранить
        self._update_geometry()

    # --- мышь: перетаскивание + контекстное меню ---------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.unsetCursor()
            if self.on_drag_finished is not None:
                self.on_drag_finished(self.x(), self.y())
            event.accept()

    def contextMenuEvent(self, event) -> None:
        """Правая кнопка: все настройки виджета, без правки config.toml."""
        m = QMenu(self)
        m.setStyleSheet(
            "QMenu { background: #16161c; color: #eee; border: 1px solid #3a3a44;"
            " border-radius: 8px; padding: 6px; }"
            "QMenu::item { padding: 6px 22px; border-radius: 4px; }"
            "QMenu::item:selected { background: #2c2c38; }"
        )

        mode_action = m.addAction("Режим: " + ("караоке" if self._mode == "compact" else "компакт"))
        mode_action.triggered.connect(self.toggle_mode)

        sync_menu = m.addMenu("Сдвиг текста")
        for delta, label in [(0.0, "Сбросить"), (-1.0, "−1 c (текст раньше)"),
                             (-0.5, "−0.5 c"), (0.5, "+0.5 c"), (1.0, "+1 c (текст позже)")]:
            act = sync_menu.addAction(("✓ " if float(self._settings.get("sync_offset_s", 0.0)) == delta else "") + label)
            act.triggered.connect(lambda checked=False, d=delta: self._change_setting("sync_offset_s", d))

        size_menu = m.addMenu("Размер шрифта")
        for size in (16, 18, 22, 26, 30):
            act = size_menu.addAction(("✓ " if int(self._settings.get("font_size", 22)) == size else "") + f"{size} pt")
            act.triggered.connect(lambda checked=False, s=size: self._change_setting("font_size", s))

        art_action = m.addAction(("✓ " if self._settings.get("show_artwork", True) else "") + "Показывать обложку")
        art_action.triggered.connect(lambda: self._change_setting(
            "show_artwork", not self._settings.get("show_artwork", True)))

        ct_action = m.addAction(("✓ " if self._click_through else "") + "Клик-сквозь")
        ct_action.triggered.connect(lambda: self.set_click_through(not self._click_through))

        m.addSeparator()
        quit_action = m.addAction("Выход")
        quit_action.triggered.connect(QApplication.instance().quit)
        m.exec(event.globalPos())

    def _change_setting(self, key: str, value) -> None:
        """Меняем настройку, перерисовываемся, уведомляем main (persist)."""
        self._settings[key] = value
        if key == "font_size":
            self.update()
        self.update()
        if self.on_setting_changed is not None:
            self.on_setting_changed(key, value)

    # --- публичный API ----------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode not in ("compact", "karaoke") or mode == self._mode:
            return
        self._mode = mode
        self._active_idx = -10
        self._update_geometry()
        self.update()

    def toggle_mode(self) -> None:
        self.set_mode("karaoke" if self._mode == "compact" else "compact")

    def set_click_through(self, on: bool) -> bool:
        self._click_through = on
        self.setAttribute(Qt.WA_TransparentForMouseEvents, on)
        self.setWindowFlag(Qt.WindowTransparentForInput, on)
        self.show()
        return True

    def toggle_click_through(self) -> None:
        self.set_click_through(not self._click_through)

    def update_track(self, title: str, artist: str, artwork: bytes = b"") -> None:
        if (title, artist) != (self._title, self._artist) or artwork:
            self._title, self._artist = title, artist
            if artwork:
                key = (title.lower(), artist.lower(), len(artwork))
                if key != self._art_key:
                    self._art_key = key
                    pix = QPixmap()
                    if pix.loadFromData(artwork):
                        self._art_pixmaps[key] = pix
                        # кэш не разрастается: держим последние 8 обложек
                        while len(self._art_pixmaps) > 8:
                            self._art_pixmaps.pop(next(iter(self._art_pixmaps)))
            self.update()

    def set_synced_lines(self, lines: list[LyricLine]) -> None:
        self._lines = lines
        self._synced = bool(lines) and lines[0].time_s is not None
        self._active_idx = -10
        self._message = ""
        self.update()

    def set_plain_text(self, text: str) -> None:
        self._lines = [LyricLine(time_s=None, text=t) for t in text.splitlines() if t.strip()]
        self._synced = False
        self._active_idx = -10
        self._message = ""
        self.update()

    def set_not_found(self) -> None:
        self._lines = []
        self._synced = False
        self._message = "♪ текст не найден"
        self.update()

    def set_no_media(self) -> None:
        self._title = self._artist = ""
        self._lines = []
        self._message = "♪ ничего не играет"
        self.update()

    def set_position(self, position_s: float, playing: bool,
                     duration_s: Optional[float] = None) -> None:
        """Обновление от монитора.

        Плееры нередко «замораживают» позицию (присылают одно и то же
        значение, пока трек играет) или запаздывают. Если такое принимать,
        позиция пилит назад и текст стоит/мерцает. Поэтому:
        - сэмпл, совпадающий с предыдущим, игнорируем всегда — экстраполяция
          едет от последнего живого значения хоть весь трек;
        - отстающий (но изменившийся) сэмпл в пределах STALE_SAMPLE_MAX_S —
          тоже игнорируем;
        - большой скачок назад на новое значение = перемотка, принимаем;
        - вперёд — принимаем всегда.
        """
        now = time.monotonic()
        frozen = self._last_sample_pos is not None and position_s == self._last_sample_pos
        self._last_sample_pos = position_s
        if self._playing and playing:
            est = self._position_s + (now - self._pos_ts)
            if position_s < est - 0.3:
                if frozen or est - position_s <= STALE_SAMPLE_MAX_S:
                    # игнор: экстраполяция продолжает ехать от прежнего базового
                    # сэмпла — НИЧЕГО не перезаписываем, иначе часы откатываются
                    return
        self._position_s = max(0.0, position_s)
        self._pos_ts = now
        self._playing = playing
        if duration_s is not None:
            self._duration_s = duration_s

    def active_text(self) -> str:
        """Текст активной строки (для тестов)."""
        if self._message:
            return self._message
        if not self._lines:
            return ""
        idx = self._compute_active_index()
        return self._lines[max(idx, 0)].text

    # --- внутреннее -------------------------------------------------------

    def _current_position(self) -> float:
        pos = self._position_s
        if self._playing:
            pos += time.monotonic() - self._pos_ts
            if self._duration_s:
                pos = min(pos, self._duration_s)
        return pos

    def _compute_active_index(self) -> int:
        if not self._lines:
            return -1
        # sync_offset_s — ручная подстройка, если текст систематически
        # опережает/отстаёт от звука (у LRC-таймингов бывает сдвиг)
        pos = self._current_position() + float(self._settings.get("sync_offset_s", 0.0))
        if self._synced:
            return max(active_line_index(self._lines, pos), 0)
        if self._duration_s and self._duration_s > 0:
            frac = min(pos / self._duration_s, 1.0)
            return min(int(frac * len(self._lines)), len(self._lines) - 1)
        return min(int(pos / PLAIN_LINE_INTERVAL_S), len(self._lines) - 1)

    def _on_anim_value(self, v) -> None:
        self._anim_progress = float(v)
        self.update()

    def _on_tick(self) -> None:
        if self._anim.state() == QAbstractAnimation.Running:
            return
        target = 0.45 if (not self._playing and self._lines) else 1.0
        if abs(self._text_alpha - target) > 0.02:
            self._text_alpha += (target - self._text_alpha) * 0.3
            self.update()
            return
        if not self._lines or self._message:
            return
        idx = self._compute_active_index()
        if idx != self._active_idx:
            self._prev_idx = self._active_idx
            self._active_idx = idx
            self._anim_progress = 0.0
            self._anim.start()
        elif self._playing and not self._synced and self._duration_s:
            # plain-режим: прогресс-бар должен ехать между сменами строк
            self.update()

    def _make_placeholder(self, size: int) -> QPixmap:
        """Заглушка-обложка: градиент + нота."""
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0, QColor(50, 50, 66))
        grad.setColorAt(1, QColor(28, 28, 38))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(0, 0, size, size, 10, 10)
        p.setPen(QColor(255, 255, 255, 90))
        f = QFont()
        f.setPixelSize(int(size * 0.42))
        p.setFont(f)
        p.drawText(pix.rect(), Qt.AlignCenter, "♪")
        p.end()
        return pix

    def _art_pixmap(self) -> QPixmap:
        size = ART_SIZE_KARAOKE if self._mode == "karaoke" else ART_SIZE_COMPACT
        if self._art_key and self._art_key in self._art_pixmaps:
            return self._art_pixmaps[self._art_key]
        return self._art_placeholder

    # --- отрисовка ----------------------------------------------------------

    def _wrapped(self, painter: QPainter, text: str, font: QFont,
                 width: int, max_lines: int) -> list[str]:
        metrics = QFontMetrics(font)
        words = text.split()
        if not words:
            return [""]
        lines, cur = [], words[0]
        for w in words[1:]:
            trial = f"{cur} {w}"
            if metrics.horizontalAdvance(trial) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = w
                if len(lines) == max_lines:
                    break
        else:
            lines.append(cur)
        while len(lines) > max_lines:
            lines[-1] = metrics.elidedText(lines[-1], Qt.ElideRight, width)
            break
        return lines

    def _draw_panel(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(26, 26, 36, 215))
        grad.setColorAt(1, QColor(10, 10, 14, 205))
        p.setPen(QPen(QColor(255, 255, 255, 24), 1))
        p.setBrush(grad)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 14, 14)
        p.drawPath(path)

    def _draw_art(self, p: QPainter, x: float, y: float, size: int) -> None:
        pix = self._art_pixmap()
        scaled = pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.save()
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, size, size), 10, 10)
        p.setClipPath(path)
        p.drawPixmap(int(x - (scaled.width() - size) / 2), int(y - (scaled.height() - size) / 2), scaled)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x + 0.5, y + 0.5, size - 1, size - 1), 10, 10)

    def _draw_header(self, p: QPainter, x: float, y: float, w: float) -> float:
        """Исполнитель + название + прогресс-бар; возвращает нижний Y."""
        font_px = int(self._settings.get("font_size", 22))
        accent = _accent_color(self._settings)

        f_artist = QFont()
        f_artist.setPixelSize(max(int(font_px * 0.42), 11))
        f_title = QFont()
        f_title.setPixelSize(int(font_px * 0.6))
        f_title.setBold(True)

        if self._artist:
            p.setFont(f_artist)
            p.setPen(QColor(255, 255, 255, 135))
            for ln in self._wrapped(p, self._artist, f_artist, int(w), 1):
                p.drawText(QRectF(x, y, w, font_px * 0.55), Qt.AlignLeft | Qt.AlignVCenter, ln)
                y += font_px * 0.55
            y += 2
        p.setFont(f_title)
        p.setPen(QColor(255, 255, 255, 240))
        for ln in self._wrapped(p, self._title, f_title, int(w), 1):
            p.drawText(QRectF(x, y, w, font_px * 0.75), Qt.AlignLeft | Qt.AlignVCenter, ln)
            y += font_px * 0.75

        # прогресс-бар
        if self._duration_s and self._duration_s > 0:
            y += 6
            frac = max(0.0, min(self._current_position() / self._duration_s, 1.0))
            bar_h = 4.0
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 32))
            p.drawRoundedRect(QRectF(x, y, w, bar_h), 2, 2)
            if frac > 0:
                p.setBrush(accent)
                p.drawRoundedRect(QRectF(x, y, max(w * frac, bar_h), bar_h), 2, 2)
            y += bar_h
        return y

    def _entries(self) -> list[tuple[str, int, float, bool]]:
        font_px = int(self._settings.get("font_size", 22))
        dim_px = max(int(font_px * 0.62), 11)
        if self._message:
            return [(self._message, dim_px, 0.8, False)]
        if not self._lines:
            return []
        idx = self._active_idx if self._active_idx >= 0 else 0
        if self._mode == "compact":
            window = [(idx - 1, False), (idx, True), (idx + 1, False)]
        else:
            window = [(i, i == idx) for i in range(max(0, idx - 2), min(len(self._lines), idx + 4))]
        entries = []
        for i, is_active in window:
            if not (0 <= i < len(self._lines)):
                continue
            text = self._lines[i].text or "♪"
            if is_active:
                entries.append((text, font_px, 1.0, True))
            else:
                entries.append((text, dim_px, 0.55, False))
        return entries

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)

        self._draw_panel(p)

        w = self.width()
        h = self.height()
        font_px = int(self._settings.get("font_size", 22))
        show_art = self._settings.get("show_artwork", True) and bool(self._title)
        art_size = ART_SIZE_KARAOKE if self._mode == "karaoke" else ART_SIZE_COMPACT

        # верхний блок: обложка + заголовок/прогресс
        header_x = PADDING
        header_w = w - PADDING * 2
        if show_art:
            self._draw_art(p, PADDING, PADDING, art_size)
            header_x = PADDING + art_size + 14
            header_w = w - header_x - PADDING
        header_bottom = self._draw_header(p, header_x, PADDING + 2, header_w)

        # блок текста — ниже обложки/заголовка
        lyrics_top = max(header_bottom + 8, PADDING + art_size + 8) if show_art else header_bottom + 8

        entries = self._entries()
        if not entries:
            return
        f_active = QFont()
        f_active.setPixelSize(font_px)
        f_active.setBold(True)
        f_dim = QFont()
        f_dim.setPixelSize(max(int(font_px * 0.62), 11))
        inner_w = w - PADDING * 2

        heights = []
        total = 0
        for text, px, alpha, bold in entries:
            f = f_active if bold else f_dim
            n = len(self._wrapped(p, text, f, inner_w, 2)) if bold else 1
            line_h = int(px * 1.32)
            heights.append((n, f, line_h))
            total += n * line_h + 4

        area_h = h - lyrics_top - PADDING
        block_y = lyrics_top + max(int((area_h - total) / 2), 0)

        prog = self._anim_progress
        animating = prog < 1.0
        for (text, px, alpha, bold), (n_lines, f, line_h) in zip(entries, heights):
            is_active = bold
            slide = int((1.0 - prog) * 10) if animating and is_active else 0
            a = alpha * (max(prog, 0.15) if is_active else 1.0) * self._text_alpha
            if is_active and animating and getattr(self, "_prev_idx", -10) in range(len(self._lines)):
                old = self._lines[self._prev_idx].text or "♪"
                p.setFont(f)
                p.setPen(QColor(255, 255, 255, int(235 * (1 - prog) * self._text_alpha)))
                yy = block_y - int(prog * 10)
                for ln in self._wrapped(p, old, f, inner_w, 2):
                    p.drawText(QRectF(PADDING, yy, inner_w, line_h),
                               Qt.AlignHCenter | Qt.AlignVCenter, ln)
                    yy += line_h
            p.setFont(f)
            if is_active:
                accent = _accent_color(self._settings)
                color = QColor(
                    int(255 * (1 - 0.35) + accent.red() * 0.35),
                    int(255 * (1 - 0.35) + accent.green() * 0.35),
                    int(255 * (1 - 0.35) + accent.blue() * 0.35),
                )
                color.setAlpha(int(255 * a))
            else:
                color = QColor(255, 255, 255)
                color.setAlpha(int(255 * a))
            p.setPen(color)
            yy = block_y + slide
            for ln in self._wrapped(p, text, f, inner_w, 2 if is_active else 1):
                p.drawText(QRectF(PADDING, yy, inner_w, line_h),
                           Qt.AlignHCenter | Qt.AlignVCenter, ln)
                yy += line_h
            block_y += n_lines * line_h + 4

    def _apply_mode_geometry(self) -> None:
        if self._mode == "compact":
            self.resize(
                int(self._settings.get("compact_width", 560)),
                int(self._settings.get("compact_height", 210)),
            )
            self.move(
                int(self._settings.get("compact_x", 100)),
                int(self._settings.get("compact_y", 100)),
            )
        else:
            w = int(self._settings.get("karaoke_width", 900))
            h = int(self._settings.get("karaoke_height", 600))
            screen = self.screen() or _primary_screen()
            if screen is not None:
                g = screen.availableGeometry()
                self.resize(w, min(h, g.height() - 80))
                self.move(g.center().x() - w // 2, g.bottom() - self.height())
            else:
                self.resize(w, h)

    def _update_geometry(self) -> None:
        self._apply_mode_geometry()


def _primary_screen():
    try:
        return QApplication.primaryScreen()
    except Exception:
        return None

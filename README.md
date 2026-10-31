# Lyrics Overlay

Универсальный виджет с текстом текущего трека поверх всех окон — для любого плеера,
на Windows и Linux. Python + PySide6.

![features](https://img.shields.io/badge/lyrics-LRCLIB%20%2B%20NetEase%20%2B%20Genius-22d3ee)

## Возможности

- **Любой плеер**: Windows — SMTC (Spotify, браузеры, AIMP, VK, Yandex Music…),
  Linux — MPRIS (почти все плееры)
- **Три источника текстов**: LRCLIB и NetEase (синхронизированные, караоке) →
  Genius (обычные, по вашему токену) — с дисковым кэшем
- **Обложка альбома** прямо из плеера, прогресс-бар трека
- **Два режима**: компакт-панель и караоке (Ctrl+Alt+L)
- **Умная синхронизация**: позиция экстраполируется между опросами плеера,
  замороженные/запаздывающие сэмплы фильтруются, ручной сдвиг — из меню
- **Управление мышью**: тащить панель за любую точку (позиция запоминается),
  правая кнопка — меню всех настроек (сдвиг текста, шрифт, обложка, клик-сквозь)
- **Аккуратные анимации**: кроссфейд смены строк, затухание на паузе

## Управление

| Действие                    | Как                             |
|-----------------------------|---------------------------------|
| Переместить панель          | тащить левой кнопкой (позиция сохраняется) |
| Настройки                   | правая кнопка по панели         |
| Режим компакт ↔ караоке     | Ctrl+Alt+L (или в меню)          |
| Клик-сквозь вкл/выкл        | Ctrl+Alt+K (или в меню)          |
| Показать/скрыть             | Ctrl+Alt+J                      |
| Сдвиг текста ±              | правая кнопка → «Сдвиг текста»  |

## Установка и запуск

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux
pip install -r requirements.txt
python main.py                # или python main.py --demo (без плеера)
```

Требуется Python 3.10+.

## Настройка

Всё — в `config.toml` (правой кнопкой по панели половина меняется на лету).

Личные токены держите в `token.local.toml` (в `.gitignore`):

```toml
[lyrics]
genius_token = "ваш_токен"
```

Без токена Genius не используется; LRCLIB и NetEase работают без ключей.

### Genius-токен (2 минуты)

1. [genius.com/api-clients](https://genius.com/api-clients) → New API Client
2. Generate Access Token → скопируйте в `token.local.toml`

## Как работает

- **Windows** — читает SMTC (System Media Transport Controls): любой плеер,
  поддерживающий медиаклавиши (Spotify, браузеры, AIMP, VK, Yandex Music...),
  публикует там название/исполнителя/позицию.
- **Linux** — MPRIS через D-Bus (тот же набор плееров).
- **Тексты** — цепочка источников (кэшируются на диск):
  1. [LRCLIB](https://lrclib.net) — открытый API без ключей, synced (караоке) + plain;
  2. [NetEase Cloud Music](https://music.163.com) — публичный API, synced LRC;
  3. [Genius](https://genius.com) — **plain-текст без таймкодов**, нужен бесплатный токен.

### Genius-токен (необязательно, 2 минуты)

1. Зарегистрируйтесь на [genius.com](https://genius.com).
2. Перейдите на [genius.com/api-clients](https://genius.com/api-clients) →
   «New API Client»: заполните имя (любое) и URL (подойдёт `http://localhost`).
3. Нажмите «Generate access token» рядом с клиентом — это **client access token**.
4. Впишите его в `config.toml`:

```toml
[lyrics]
genius_token = "вставьте_токен_сюда"
```

Без токена Genius просто не используется; LRCLIB и NetEase работают без ключей.
- **UI** — PySide6: always-on-top полупрозрачный оверлей, два режима
  (компакт-панель / полный караоке), плавное затухание на паузе.

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # Linux
pip install -r requirements.txt
```

> Linux: для глобальных горячих клавиш pynput нужен X11 или `python3-xlib`.

## Запуск

```bash
python main.py            # обычный запуск
python main.py --demo     # демо-трек, без плеера (для проверки UI)
python main.py --mode karaoke
```

## Горячие клавиши (настройка в `config.toml`)

| Комбинация   | Действие                          |
|--------------|-----------------------------------|
| Ctrl+Alt+L   | переключить compact ↔ karaoke     |
| Ctrl+Alt+K   | клик-сквозь вкл/выкл              |
| Ctrl+Alt+J   | показать/скрыть виджет            |

Настройки (позиция, размер, шрифт, прозрачность, интервал опроса) — в `config.toml`.

## Структура

```
main.py               точка входа, цикл опроса, горячие клавиши
config.toml           настройки
players/              мониторы плееров (SMTC / MPRIS / demo)
lyrics/               LRC-парсер, кэш, клиент LRCLIB
ui/                   оверлей (PySide6)
tests/                pytest: парсер, кэш
```

## Тесты

```bash
pytest tests/ -v
```

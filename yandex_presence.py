import subprocess
import sys
import os

def bootstrap_dependencies():
    """Проверяет и устанавливает необходимые зависимости"""
    required = [
        "requests", "pypresence", "rich", "pystray",
        "Pillow", "winsdk"
    ]
    missing = []
    for package in required:
        try:
            if package == "Pillow":
                import PIL
            elif package == "winsdk":
                import winsdk
            else:
                __import__(package)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"--- Установка недостающих компонентов: {', '.join(missing)} ---")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
            print("--- Все компоненты успешно установлены! ---")
        except Exception as e:
            print(f"Ошибка при установке: {e}")
            sys.exit(1)

bootstrap_dependencies()

import time
import asyncio
import requests
import datetime
import re
import difflib
import os
import sys
import ctypes
import threading
from PIL import Image
import pystray
from pystray import MenuItem as item
from urllib.parse import quote
from pypresence import AioPresence
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SessionManager
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.prompt import Confirm

# --- Configuration ---
DISCORD_CLIENT_ID = "1503812613052694658"
CURRENT_COMMIT = "86739a031ac27765ccff08a9a1538882d681f550"
REPO_URL = "Peaostrel/VEINYMusic"

console = Console()

def print_glitch_header():
    """Печатает стилизованный заголовок в духе VEIN"""
    header = """
[magenta]------------------------------------------------------------[/magenta]
[bold white]  VEINYMusic[/bold white] [dim]- |ч| ! |я| = |А| ! ! [/dim]
[magenta]------------------------------------------------------------[/magenta]
"""
    console.print(header)

# Глобальные переменные для управления окном и выходом
is_console_visible = True
tray_icon = None

def toggle_console(icon, item):
    """Переключает видимость окна консоли"""
    global is_console_visible
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if is_console_visible:
        ctypes.windll.user32.ShowWindow(hwnd, 0)
        is_console_visible = False
    else:
        ctypes.windll.user32.ShowWindow(hwnd, 5)
        is_console_visible = True

def on_exit(icon, item):
    """Завершает работу скрипта"""
    icon.stop()
    os._exit(0)

def setup_tray():
    """Запускает иконку в трее в отдельном потоке"""
    global tray_icon
    icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
    if os.path.exists(icon_path):
        image = Image.open(icon_path)
    else:
        image = Image.new('RGB', (64, 64), color=(147, 112, 219))

    menu = pystray.Menu(
        item('Показать/Скрыть консоль', toggle_console),
        item('Выход', on_exit)
    )
    tray_icon = pystray.Icon("VEINYMusic", image, "VEINYMusic", menu)
    threading.Thread(target=tray_icon.run, daemon=True).start()

def check_updates():
    """Проверяет обновления через GitHub API без использования Git"""
    try:
        api_url = f"https://api.github.com/repos/{REPO_URL}/commits/main"
        response = requests.get(api_url, timeout=5)
        if response.status_code == 200:
            latest_commit = response.json().get("sha")
            if latest_commit and latest_commit != CURRENT_COMMIT:
                console.print(Panel(
                    f"[bold yellow]Доступно обновление![/bold yellow]\n"
                    f"[dim]Текущая версия: {CURRENT_COMMIT[:7]}\n"
                    f"Новая версия: {latest_commit[:7]}[/dim]\n\n"
                    "Хотите обновиться сейчас? (скрипт скачает новую версию и перезапустится)",
                    title="[bold cyan]Update Check[/bold cyan]",
                    border_style="cyan"
                ))
                if Confirm.ask("Обновиться?", default=True):
                    raw_url = f"https://raw.githubusercontent.com/{REPO_URL}/main/yandex_presence.py"
                    new_code = requests.get(raw_url, timeout=10).text
                    if "import" in new_code and "asyncio" in new_code:
                        new_code = re.sub(
                            r'CURRENT_COMMIT = ".*?"',
                            f'CURRENT_COMMIT = "{latest_commit}"',
                            new_code
                        )
                        with open(__file__, "w", encoding="utf-8") as f:
                            f.write(new_code)
                        console.print("[bold green]Обновление успешно! Перезапуск...[/bold green]")
                        time.sleep(1)
                        os.execv(sys.executable, [sys.executable] + sys.argv)
                    else:
                        console.print("[bold red]Ошибка: Скачанный файл кажется поврежденным.[/bold red]")
    except Exception:
        pass

def clean_text(text):
    """Очищает текст от мусора, сохраняя кириллицу"""
    if not text: return ""
    text = text.lower()
    text = re.sub(r'[\(\[\{].*?[\)\]\}]', '', text)
    text = text.replace('explicit', '').replace('lyrics', '')
    return " ".join(text.split()).strip()

# Глобальная сессия для ускорения сетевых запросов
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

def get_track_meta(title, artist):
    """
    Ищет обложку и метаданные трека.
    Источники (по приоритету): Deezer → iTunes.
    Оба API публичные и не требуют авторизации.
    """
    q_artist = clean_text(artist)
    q_title = clean_text(title)

    def similarity(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio()

    def best_match(items, get_title, get_artist):
        """Выбирает лучший результат из списка по схожести с оригиналом."""
        t_clean = clean_text(title)
        a_clean = clean_text(artist)
        best, best_score = None, 0.0
        for item in items:
            i_title = clean_text(get_title(item))
            i_artist = clean_text(get_artist(item))
            title_sim = similarity(t_clean, i_title)
            
            if a_clean:
                artist_sim = similarity(a_clean, i_artist)
                artist_match = (a_clean in i_artist or i_artist in a_clean)
                artist_score = max(artist_sim, 0.8 if artist_match else 0.0)
                
                # Если исполнитель вообще не похож, отсекаем этот вариант
                if artist_score < 0.25:
                    continue
                score = title_sim * 0.6 + artist_score * 0.4
            else:
                score = title_sim
                
            if score > best_score:
                best_score = score
                best = item
        return best if best_score >= 0.6 else None

    def try_deezer(query):
        """Deezer Search API — публичный, без ключа, хорошо знает и западную, и русскую музыку."""
        try:
            url = f"https://api.deezer.com/search?q={quote(query)}&limit=10&output=json"
            r = session.get(url, timeout=4)
            if r.status_code != 200:
                return None
            items = r.json().get("data", [])
            if not items:
                return None

            found = best_match(
                items,
                get_title=lambda x: x.get("title", ""),
                get_artist=lambda x: x.get("artist", {}).get("name", "")
            )
            if not found:
                return None

            album = found.get("album", {})
            # Deezer отдаёт обложку в нескольких размерах — берём самую большую
            cover = (
                album.get("cover_xl") or
                album.get("cover_big") or
                album.get("cover_medium") or
                album.get("cover") or
                "logo"
            )
            return {
                "id": found.get("id"),
                "title": found.get("title") or title,
                "artist": found.get("artist", {}).get("name", artist),
                "album": album.get("title", "Deezer"),
                "album_id": album.get("id"),
                "cover": cover,
                "_track_link": found.get("link", f"https://www.deezer.com/track/{found.get('id')}"),
                "_source": "deezer",
            }
        except Exception:
            return None

    def try_itunes(query):
        """iTunes Search API — публичный фолбек, особенно хорош для западных треков."""
        try:
            url = f"https://itunes.apple.com/search?term={quote(query)}&media=music&entity=song&limit=10"
            r = session.get(url, timeout=4)
            if r.status_code != 200:
                return None
            items = r.json().get("results", [])
            if not items:
                return None

            found = best_match(
                items,
                get_title=lambda x: x.get("trackName", ""),
                get_artist=lambda x: x.get("artistName", "")
            )
            if not found:
                return None

            # iTunes даёт 100x100, меняем на 600x600
            cover = found.get("artworkUrl100", "logo").replace("100x100bb", "600x600bb")
            return {
                "id": found.get("trackId"),
                "title": found.get("trackName") or title,
                "artist": found.get("artistName", artist),
                "album": found.get("collectionName", "iTunes"),
                "album_id": found.get("collectionId"),
                "cover": cover,
                "_track_link": found.get("trackViewUrl", ""),
                "_source": "itunes",
            }
        except Exception:
            return None

    # Пробуем запросы от точного к широкому
    queries = [
        f"{q_title} {q_artist}",
        f"{q_artist} {q_title}",
        q_title,
    ]

    for query in queries:
        if not query.strip():
            continue
        result = try_deezer(query)
        if result:
            return result
        result = try_itunes(query)
        if result:
            return result

    return None

meta_cache = {}
rejected_tracks = set()

async def get_raw_system_media():
    """Сканирует все сессии и выбирает лучшую (Playing > Paused, затем по времени)"""
    try:
        manager = await SessionManager.request_async()
        sessions = manager.get_sessions()
        candidates = []

        for session in sessions:
            info = await session.try_get_media_properties_async()
            if not info.title: continue

            track_id = f"{info.artist}-{info.title}"
            if track_id in rejected_tracks: continue

            meta = meta_cache.get(track_id)
            if not meta and track_id not in meta_cache:
                meta = get_track_meta(info.title, info.artist)
                if meta:
                    meta_cache[track_id] = meta
                else:
                    app_id = session.source_app_user_model_id.lower() if session.source_app_user_model_id else ""
                    if "yandex" in app_id or "308046b0af4a39cb" in app_id:
                        meta_cache[track_id] = None
                    else:
                        rejected_tracks.add(track_id)
                        continue

            playback = session.get_playback_info()
            timeline = session.get_timeline_properties()
            pos = timeline.position.total_seconds()

            if playback.playback_status == 4:  # Playing
                now = datetime.datetime.now(datetime.timezone.utc)
                diff = (now - timeline.last_updated_time).total_seconds()
                pos += diff

            candidates.append({
                "title": info.title,
                "artist": info.artist or "Unknown",
                "duration": timeline.end_time.total_seconds(),
                "position": pos,
                "status": playback.playback_status,
                "updated": timeline.last_updated_time,
                "meta": meta
            })

        if not candidates: return None
        candidates.sort(key=lambda x: (x['status'] == 4, x['updated']), reverse=True)
        return candidates[0]

    except Exception:
        pass
    return None

def create_ui(raw, meta, debug_info=None):
    """Создает компактный и надежный интерфейс"""
    if not raw:
        msg = "[bold yellow]Ожидание запуска плеера...[/bold yellow]\n[dim]Включи музыку в Яндекс Музыке в браузере[/dim]"
        if debug_info:
            msg += f"\n\n[dim italic]Система видит: {debug_info}[/dim italic]"
        return Panel(
            msg,
            title="[bold magenta]VEINYMusic[/bold magenta]",
            border_style="magenta",
            padding=(1, 2)
        )

    d_art = meta['artist'] if meta else raw['artist']
    d_tit = meta['title'] if meta else raw['title']
    d_alb = meta['album'] if meta else "Yandex Music"

    status_icon = "▶" if raw['status'] == 4 else "⏸"
    status_color = "green" if raw['status'] == 4 else "yellow"

    info_table = Table.grid(padding=(0, 2))
    info_table.add_column(style="cyan", justify="right")
    info_table.add_column(style="white bold")

    import unicodedata
    def sanitize(t):
        if not t: return ""
        t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
        return t

    album_text = Text(sanitize(d_alb), style="white", no_wrap=True, overflow="ellipsis")
    if meta and meta.get('_source'):
        album_text.append(f" ({meta['_source']})", style="dim")

    info_table.add_row("ТРЕК",   Text(sanitize(d_tit), style="bold magenta", no_wrap=True, overflow="ellipsis"))
    info_table.add_row("АРТИСТ", Text(sanitize(d_art), style="cyan",         no_wrap=True, overflow="ellipsis"))
    info_table.add_row("АЛЬБОМ", album_text)

    def format_time(seconds):
        m, s = divmod(int(max(0, seconds)), 60)
        return f"{m:02d}:{s:02d}"

    progress = Progress(
        TextColumn("[bold blue]{task.fields[current]}"),
        BarColumn(bar_width=None, complete_style="magenta", finished_style="green"),
        TextColumn("[bold blue]{task.fields[total_time]}"),
        expand=True
    )
    progress.add_task(
        "music",
        total=max(1, raw['duration']),
        completed=raw['position'],
        current=format_time(raw['position']),
        total_time=format_time(raw['duration'])
    )

    ui_group = Group("", info_table, "", progress, "")
    return Panel(
        ui_group,
        title=f"[bold {status_color}]{status_icon} {raw['status'] == 4 and 'ИГРАЕТ' or 'ПАУЗА'}[/bold {status_color}]",
        subtitle="[dim]Синхронизация с Discord активна[/dim]",
        border_style="magenta" if raw['status'] == 4 else "yellow",
        padding=(0, 2)
    )

async def main():
    print_glitch_header()
    check_updates()
    setup_tray()

    rpc = AioPresence(DISCORD_CLIENT_ID)
    try: await rpc.connect()
    except: pass

    last_track_id = ""
    last_status = None
    last_start_ts = 0
    last_debug = None

    with Live(auto_refresh=False, console=console) as live:
        while True:
            raw = await get_raw_system_media()
            now = time.time()

            if not raw:
                try:
                    manager = await SessionManager.request_async()
                    sessions = manager.get_sessions()
                    if sessions:
                        s = sessions[0]
                        info = await s.try_get_media_properties_async()
                        if info.title:
                            last_debug = f"{info.artist} - {info.title}"
                            q_art = clean_text(info.artist)
                            q_tit = clean_text(info.title)
                            last_debug += f" (Search: {q_art} {q_tit})"
                except: pass

                live.update(create_ui(None, None, last_debug), refresh=True)
                if last_status != "off":
                    try:
                        await rpc.clear()
                    except:
                        pass
                    last_status = "off"; last_track_id = ""
                await asyncio.sleep(1)
                continue

            track_id = f"{raw['artist']}-{raw['title']}"
            meta = raw['meta']

            is_new_track     = track_id != last_track_id
            is_status_changed = raw['status'] != last_status
            current_start_ts = int(now - raw['position'])
            is_seeked        = abs(current_start_ts - last_start_ts) > 2

            if is_new_track or is_status_changed or is_seeked:
                try:
                    if raw['status'] == 4:  # PLAYING
                        end_ts = int(current_start_ts + raw['duration']) if raw['duration'] > 0 else None

                        await rpc.update(
                            details=meta['title'] if meta else raw['title'],
                            state=f"{meta['artist']} — {meta['album']}" if meta else raw['artist'],
                            large_image=meta['cover'] if meta else "logo",
                            large_text=f"Трек: {meta['title'] if meta else raw['title']}",
                            small_image="logo",
                            small_text="VEINYMusic",
                            start=current_start_ts, end=end_ts,
                            activity_type=2
                        )
                    else:  # PAUSED
                        await rpc.update(
                            details=f"⏸ {meta['title'] if meta else raw['title']}",
                            state=meta['artist'] if meta else raw['artist'],
                            large_image=meta['cover'] if meta else "logo",
                            large_text="На паузе",
                            activity_type=2
                        )
                except Exception as e:
                    err_name = type(e).__name__
                    import traceback
                    with open("rpc_error.log", "a", encoding="utf-8") as f:
                        f.write(f"RPC Update Error ({err_name}) at {datetime.datetime.now()}:\n{traceback.format_exc()}\n")
                    
                    # Если это сетевая ошибка или таймаут, переподключаемся
                    if err_name in ("ConnectionResetError", "BrokenPipeError", "TimeoutError", "ResponseTimeout", "InvalidID", "ConnectionClosed"):
                        try:
                            rpc.close()
                        except:
                            pass
                        rpc = AioPresence(DISCORD_CLIENT_ID)
                        try:
                            await rpc.connect()
                        except:
                            pass

                last_track_id = track_id
                last_status   = raw['status']
                last_start_ts = current_start_ts

            live.update(create_ui(raw, meta), refresh=True)
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
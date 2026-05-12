import time
import asyncio
import requests
import datetime
from pypresence import AioPresence
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SessionManager
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.live import Live
from rich.table import Table

# --- Configuration ---
DISCORD_CLIENT_ID = "1503812613052694658" 
console = Console()

def get_track_meta(title, artist):
    """Ищет обложку и инфо о треке через публичный поиск Яндекса"""
    query = f"{artist} {title}".strip()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        search_url = f"https://music.yandex.ru/handlers/music-search.jsx?text={query}&type=all"
        response = requests.get(search_url, headers=headers, timeout=3)
        if response.status_code == 200:
            resp = response.json()
            if resp.get("tracks") and resp["tracks"].get("items"):
                track = resp["tracks"]["items"][0]
                album = track.get("albums", [{}])[0]
                cover = track.get("coverUri", "")
                return {
                    "id": track.get("id"),
                    "title": track.get("title"),
                    "artist": ", ".join([a["name"] for a in track.get("artists", [])]),
                    "album": album.get("title", "Yandex Music"),
                    "album_id": album.get("id"),
                    "cover": "https://" + cover.replace("%%", "400x400") if cover else "logo"
                }
    except: pass
    return None

meta_cache = {}
rejected_tracks = set()

async def get_raw_system_media():
    """Сканирует ВСЕ сессии в системе и находит ту, что из Яндекс Музыки"""
    try:
        manager = await SessionManager.request_async()
        sessions = manager.get_sessions()
        
        for session in sessions:
            info = await session.try_get_media_properties_async()
            if not info.title: continue
                
            track_id = f"{info.artist}-{info.title}"
            
            # Быстрая проверка кэша
            if track_id in rejected_tracks:
                continue
                
            meta = meta_cache.get(track_id)
            if not meta:
                meta = get_track_meta(info.title, info.artist)
                if meta:
                    meta_cache[track_id] = meta
                else:
                    rejected_tracks.add(track_id)
                    continue

            # Если мы дошли сюда, значит эта сессия — точно Яндекс Музыка!
            playback = session.get_playback_info()
            timeline = session.get_timeline_properties()
            
            pos = timeline.position.total_seconds()
            if playback.playback_status == 4: # Играет
                now = datetime.datetime.now(datetime.timezone.utc)
                diff = (now - timeline.last_updated_time).total_seconds()
                pos += diff
                
            return {
                "title": info.title,
                "artist": info.artist or "Unknown",
                "duration": timeline.end_time.total_seconds(),
                "position": pos,
                "status": playback.playback_status,
                "meta": meta
            }
    except: pass
    return None

def create_ui(raw, meta):
    """Создает компактный и надежный интерфейс"""
    if not raw:
        return Panel(
            "[bold yellow]Ожидание запуска плеера...[/bold yellow]\n[dim]Включи музыку в Яндекс Музыке в браузере[/dim]",
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
    info_table.add_row("ТРЕК", d_tit)
    info_table.add_row("АРТИСТ", d_art)
    info_table.add_row("АЛЬБОМ", f"[dim]{d_alb}[/dim]")
    
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
    rpc = AioPresence(DISCORD_CLIENT_ID)
    try: await rpc.connect()
    except: pass

    last_track_id = ""
    last_status = None
    last_start_ts = 0
    
    with Live(auto_refresh=False, console=console) as live:
        while True:
            raw = await get_raw_system_media()
            now = time.time()
            
            if not raw:
                live.update(create_ui(None, None), refresh=True)
                if last_status != "off":
                    await rpc.clear()
                last_status = "off"; last_track_id = ""
                await asyncio.sleep(1); continue

            track_id = f"{raw['artist']}-{raw['title']}"
            meta = raw['meta']
            
            is_new_track = track_id != last_track_id
            is_status_changed = raw['status'] != last_status
            
            current_start_ts = int(now - raw['position'])
            is_seeked = abs(current_start_ts - last_start_ts) > 2

            if is_new_track or is_status_changed or is_seeked:
                try:
                    if raw['status'] == 4: # PLAYING
                        end_ts = int(current_start_ts + raw['duration']) if raw['duration'] > 0 else None
                        
                        btns = []
                        if meta:
                            btns.append({"label": "Слушать", "url": f"https://music.yandex.ru/track/{meta['id']}"})
                            if meta.get('album_id'):
                                btns.append({"label": "Альбом", "url": f"https://music.yandex.ru/album/{meta['album_id']}"})

                        await rpc.update(
                            details=meta['artist'],
                            state=meta['title'],
                            large_image=meta['cover'],
                            large_text=f"Альбом: {meta['album']}",
                            small_image="logo",
                            start=current_start_ts, end=end_ts, activity_type=2,
                            buttons=btns
                        )
                    else: # PAUSED
                        await rpc.update(
                            details=f"⏸ {meta['artist']}",
                            state=meta['title'],
                            large_image=meta['cover'],
                            large_text="На паузе",
                            activity_type=2
                        )
                except Exception:
                    # Если Discord был закрыт или упал, пытаемся переподключиться тихо
                    try:
                        await rpc.connect()
                    except:
                        pass
                
                last_track_id = track_id
                last_status = raw['status']
                last_start_ts = current_start_ts
            
            # Обновляем UI
            live.update(create_ui(raw, meta), refresh=True)
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass

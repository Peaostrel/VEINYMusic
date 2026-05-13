import time
import asyncio
import requests
import datetime
import re
import difflib
from urllib.parse import quote
from pypresence import AioPresence
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SessionManager
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.live import Live
from rich.table import Table
from rich.text import Text

# --- Configuration ---
DISCORD_CLIENT_ID = "1503812613052694658" 
console = Console()

def print_glitch_header():
    """Печатает стилизованный заголовок в духе VEIN"""
    header = """
    [magenta]------------------------------------------------------------[/magenta]
    [bold white]  VEINYMusic[/bold white] [dim]- |ч| ! |я| = |А| ! ! [/dim]
    [magenta]------------------------------------------------------------[/magenta]
    """
    console.print(header)

def clean_text(text):
    """Очищает текст от мусора, сохраняя кириллицу"""
    if not text: return ""
    text = text.lower()
    text = re.sub(r'[\(\[\{].*?[\)\]\}]', '', text) # Удаляем текст в скобках
    text = text.replace('explicit', '').replace('lyrics', '')
    return " ".join(text.split()).strip()

def get_track_meta(title, artist):
    """Ищет обложку и инфо о треке через публичный поиск Яндекса"""
    q_artist = clean_text(artist)
    q_title = clean_text(title)
    
    # Сначала ищем Название + Артист (так точнее для Яндекса), потом наоборот
    queries = [f"{q_title} {q_artist}", f"{q_artist} {q_title}", q_title]
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    def format_item(item, original_title):
        if "albums" in item and item["albums"]:
            album_title = item["albums"][0].get("title", "Yandex Music")
            album_id = item["albums"][0].get("id")
        else:
            album_title = item.get("title", "Yandex Music")
            album_id = item.get("id")
            
        cover = item.get("coverUri", "")
        # Если оригинальный тайтл есть, используем его, чтобы избежать битых символов из API
        final_title = item.get("title")
        if not final_title or len(final_title) < 2:
            final_title = original_title
            
        return {
            "id": item.get("id"),
            "title": final_title,
            "artist": ", ".join([a["name"] for a in item.get("artists", [])]),
            "album": album_title,
            "album_id": album_id,
            "cover": "https://" + cover.replace("%%", "400x400") if cover else "logo"
        }
        
    best_candidate = None
    best_similarity = 0.0
    
    for query in queries:
        if not query.strip(): continue
        try:
            encoded_query = quote(query)
            search_url = f"https://music.yandex.ru/handlers/music-search.jsx?text={encoded_query}&type=all"
            response = requests.get(search_url, headers=headers, timeout=3)
            if response.status_code == 200:
                resp = response.json()
                
                items = []
                if resp.get("best") and resp["best"].get("item"):
                    items.append(resp["best"]["item"])
                if resp.get("tracks") and resp["tracks"].get("items"):
                    items.extend(resp["tracks"]["items"][:5])
                if resp.get("albums") and resp["albums"].get("items"):
                    items.extend(resp["albums"]["items"][:5])
                    
                t_clean = clean_text(title)
                a_clean = clean_text(artist)
                
                for item in items:
                    i_title = clean_text(item.get("title", ""))
                    i_artists = [clean_text(a["name"]) for a in item.get("artists", [])]
                    
                    matcher = difflib.SequenceMatcher(None, t_clean, i_title)
                    title_similarity = matcher.ratio()
                    
                    artist_match = any(a_clean in a or a in a_clean for a in i_artists)
                    
                    if artist_match:
                        # Если 100% совпадение, возвращаем сразу
                        if title_similarity >= 0.99:
                            return format_item(item, title)
                        # Иначе сохраняем кандидата с наивысшим баллом
                        if title_similarity > 0.5 and title_similarity > best_similarity:
                            best_similarity = title_similarity
                            best_candidate = item
                            
                    elif (not artist or artist.lower() == "unknown"):
                        # Если артист неизвестен, ищем только по названию с высоким порогом
                        if title_similarity > 0.8 and title_similarity > best_similarity:
                            best_similarity = title_similarity
                            best_candidate = item
        except: pass
        
    if best_candidate:
        return format_item(best_candidate, title)
        
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
            if not meta:
                meta = get_track_meta(info.title, info.artist)
                if meta:
                    meta_cache[track_id] = meta
                else:
                    rejected_tracks.add(track_id)
                    continue

            playback = session.get_playback_info()
            timeline = session.get_timeline_properties()
            
            pos = timeline.position.total_seconds()
            if playback.playback_status == 4: # Playing
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
        
        # Сортировка: статус 4 (Playing) в начало, затем по новизне обновления
        candidates.sort(key=lambda x: (x['status'] == 4, x['updated']), reverse=True)
        return candidates[0]
        
    except Exception: pass
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
    print_glitch_header()
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
                # Попробуем найти ХОТЬ ЧТО-ТО для отладки
                try:
                    manager = await SessionManager.request_async()
                    sessions = manager.get_sessions()
                    if sessions:
                        s = sessions[0]
                        info = await s.try_get_media_properties_async()
                        if info.title:
                            last_debug = f"{info.artist} - {info.title}"
                            # Добавляем в лог инфу о попытке поиска
                            q_art = clean_text(info.artist)
                            q_tit = clean_text(info.title)
                            last_debug += f" (Search: {q_art} {q_tit})"
                except: pass
                
                live.update(create_ui(None, None, last_debug), refresh=True)
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
                            details=meta['title'],
                            state=f"{meta['artist']} — {meta['album']}",
                            large_image=meta['cover'],
                            large_text=f"Трек: {meta['title']}",
                            small_image="logo",
                            small_text="VEINYMusic",
                            start=current_start_ts, end=end_ts, activity_type=2,
                            buttons=btns
                        )
                    else: # PAUSED
                        await rpc.update(
                            details=f"⏸ {meta['title']}",
                            state=meta['artist'],
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

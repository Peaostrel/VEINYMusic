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
import json
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
CURRENT_COMMIT = "084d53269a654d47940f10c24b3c1ea554674f34"
REPO_URL = "Peaostrel/VEINYMusic"

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
OLD_TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord_token.txt")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpc_error.log")

DEFAULT_CONFIG = {
    "discord_token": None,
    "yandex_token": None,
    "lyrics_enabled": False,
    "lyrics_offset": 0.8,
    "startup_enabled": False
}
CONFIG = DEFAULT_CONFIG.copy()

def load_config():
    global CONFIG
    if os.path.exists(OLD_TOKEN_PATH):
        try:
            with open(OLD_TOKEN_PATH, "r", encoding="utf-8") as f:
                token = f.read().strip()
                if token:
                    CONFIG["discord_token"] = token
            os.remove(OLD_TOKEN_PATH)
        except Exception:
            pass
            
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                CONFIG.update(json.load(f))
        except Exception:
            pass
    save_config()

def save_config():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=4)
    except Exception:
        pass

load_config()

console = Console()
status_manager = None
shutdown_requested = False
_ctrl_handler = None

# --- Discord Custom Status & Lyrics Support ---
import urllib.parse


class DiscordStatusManager:
    def __init__(self, token):
        self.token = token
        self.enabled = bool(token)
        self.original_status = None
        self.current_status_text = None
        self.has_backed_up = False
        self.rate_limit_until = 0.0
        self.headers = {
            "Authorization": self.token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
    async def backup_status(self):
        """Получает текущий кастомный статус пользователя, чтобы сохранить его"""
        if not self.enabled or self.has_backed_up:
            return
        
        now = time.time()
        if now < self.rate_limit_until:
            return

        try:
            r = await asyncio.to_thread(requests.get, "https://discord.com/api/v9/users/@me/settings", headers=self.headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                self.original_status = data.get("custom_status")
                
                # Защита от "грязного" бэкапа: если прошлый запуск не завершился корректно
                # и оставил слова песни в статусе, мы не должны бэкапить их как оригинал!
                if self.original_status and self.original_status.get("emoji_name") == "🎵":
                    self.original_status = None
                    
                self.has_backed_up = True
                if self.original_status:
                    self.current_status_text = self.original_status.get("text")
                else:
                    self.current_status_text = None
            elif r.status_code == 429:
                data = r.json()
                retry_after = data.get("retry_after", 5.0)
                self.rate_limit_until = now + retry_after
            elif r.status_code == 401:
                self.enabled = False
        except Exception as e:
            self.rate_limit_until = now + 5.0  # Cooldown on network error
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"Discord Status Backup Error at {datetime.datetime.now()}: {e}\n")

    async def update_status(self, text, emoji_name="🎵"):
        """Обновляет статус, если он изменился, с учетом rate limit"""
        if not self.enabled:
            return
        
        if text == self.current_status_text:
            return
            
        now = time.time()
        if now < self.rate_limit_until:
            return
            
        if not self.has_backed_up:
            await self.backup_status()
            if not self.has_backed_up:
                return  # If backup failed (e.g. rate limit or network), don't update!

        payload = {
            "custom_status": {
                "text": text[:128] if text else "",
            }
        }
        if emoji_name:
            payload["custom_status"]["emoji_name"] = emoji_name
            payload["custom_status"]["emoji_id"] = None
        else:
            payload["custom_status"]["emoji_name"] = None
            payload["custom_status"]["emoji_id"] = None

        if not text:
            payload = {"custom_status": None}
            
        try:
            r = await asyncio.to_thread(requests.patch, "https://discord.com/api/v9/users/@me/settings", headers=self.headers, json=payload, timeout=15)
            if r.status_code == 200:
                self.current_status_text = text
            elif r.status_code == 429:
                data = r.json()
                retry_after = data.get("retry_after", 5.0)
                self.rate_limit_until = now + retry_after
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(f"Discord Status Rate Limit: retry after {retry_after}s at {datetime.datetime.now()}\n")
            elif r.status_code == 401:
                self.enabled = False
            else:
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(f"Discord Status Update Error ({r.status_code}) at {datetime.datetime.now()}: {r.text}\n")
        except Exception as e:
            self.rate_limit_until = now + 5.0  # Cooldown on network error
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"Discord Status Update Error at {datetime.datetime.now()}: {e}\n")

    async def restore_status(self, force_wait=False):
        """Восстанавливает оригинальный статус"""
        if not self.enabled or not self.has_backed_up:
            return
            
        now = time.time()
        if now < self.rate_limit_until:
            wait_time = self.rate_limit_until - now
            if force_wait and wait_time < 5.0:
                await asyncio.sleep(wait_time)
            else:
                return
        
        payload = {
            "custom_status": self.original_status
        }
        try:
            r = await asyncio.to_thread(requests.patch, "https://discord.com/api/v9/users/@me/settings", headers=self.headers, json=payload, timeout=15)
            if r.status_code == 200:
                self.has_backed_up = False
                self.current_status_text = self.original_status.get("text") if self.original_status else None
            elif r.status_code == 429:
                data = r.json()
                retry_after = data.get("retry_after", 5.0)
                self.rate_limit_until = now + retry_after
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(f"Discord Status Restore Rate Limit: retry after {retry_after}s at {datetime.datetime.now()}\n")
            elif r.status_code == 401:
                self.enabled = False
        except Exception as e:
            self.rate_limit_until = now + 5.0  # Cooldown on network error
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"Discord Status Restore Error at {datetime.datetime.now()}: {e}\n")

    def restore_status_sync(self, force_wait=False):
        """Синхронная версия restore_status для вызова при выходе из программы"""
        if not self.enabled or not self.has_backed_up:
            return
            
        now = time.time()
        if now < self.rate_limit_until:
            wait_time = self.rate_limit_until - now
            if force_wait and wait_time < 5.0:
                time.sleep(wait_time)
            else:
                return
        
        payload = {
            "custom_status": self.original_status
        }
        try:
            r = requests.patch("https://discord.com/api/v9/users/@me/settings", headers=self.headers, json=payload, timeout=15)
            if r.status_code == 200:
                self.has_backed_up = False
                self.current_status_text = self.original_status.get("text") if self.original_status else None
        except Exception as e:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"Discord Status Sync Restore Error at {datetime.datetime.now()}: {e}\n")

def parse_lrc(lrc_text):
    if not lrc_text:
        return []
    lines = []
    time_pattern = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\]')
    for line in lrc_text.splitlines():
        line = line.strip()
        matches = time_pattern.findall(line)
        if not matches:
            continue
        text = time_pattern.sub('', line).strip()
        for min_str, sec_str in matches:
            minutes = int(min_str)
            seconds = float(sec_str)
            timestamp = minutes * 60 + seconds
            lines.append((timestamp, text))
    lines.sort(key=lambda x: x[0])
    return lines

def fuzzy_match_artist(requested, returned):
    if not requested or not returned:
        return False
    req = requested.lower()
    ret = returned.lower()
    if req in ret or ret in req:
        return True
    
    req_words = set(re.findall(r'\b\w{3,}\b', req))
    ret_words = set(re.findall(r'\b\w{3,}\b', ret))
    
    if req_words and ret_words:
        return bool(req_words & ret_words)
    return False

fetching_lyrics = set()
lyrics_cache = {}

def async_fetch_lyrics(track_id, title, artist):
    fetching_lyrics.add(track_id)
    def run():
        try:
            q_artist = clean_text(artist)
            q_title = clean_text(title)
            url = f"https://lrclib.net/api/get?artist_name={quote(q_artist)}&track_name={quote(q_title)}"
            r = session.get(url, timeout=15)
            data = None
            if r.status_code == 200:
                data = r.json()
                if not data.get('syncedLyrics'):
                    data = None  # Ищем другую версию, если тут нет синхронизированных слов
            
            if not data:
                # Fallback: поиск по разным вариантам запроса
                search_queries = [
                    f"{q_artist} {q_title}",
                    q_title
                ]
                for q in search_queries:
                    search_url = f"https://lrclib.net/api/search?q={quote(q)}"
                    r_search = session.get(search_url, timeout=15)
                    if r_search.status_code == 200:
                        results = r_search.json()
                        # Ищем ПЕРВЫЙ результат, у которого ЕСТЬ syncedLyrics
                        for res in results:
                            if res.get('syncedLyrics'):
                                # Если ищем только по названию, проверяем совпадение артиста
                                if q == q_title:
                                    res_artist = res.get('artistName', '')
                                    if not fuzzy_match_artist(q_artist, res_artist):
                                        continue
                                data = res
                                break
                    if data:
                        break
            
            if data and data.get('syncedLyrics'):
                parsed = parse_lrc(data['syncedLyrics'])
                lyrics_cache[track_id] = parsed
            else:
                lyrics_cache[track_id] = []
        except Exception:
            lyrics_cache[track_id] = []
        finally:
            if track_id in fetching_lyrics:
                fetching_lyrics.remove(track_id)
    
    threading.Thread(target=run, daemon=True).start()

def get_current_lyric_line(lyrics, position):
    if not lyrics:
        return None
    
    current_line = None
    line_start_time = 0
    next_line_start_time = None
    current_index = -1
    
    for i, (ts, txt) in enumerate(lyrics):
        if ts <= position:
            current_line = txt
            line_start_time = ts
            next_line_start_time = lyrics[i+1][0] if i + 1 < len(lyrics) else None
            current_index = i
        else:
            break
            
    if current_line is not None:
        # Если есть следующая строчка, она ограничивает длительность показа текущей.
        # Если между строчками гигантская пауза (длинный проигрыш), скрываем слова.
        if next_line_start_time is not None:
            limit = min(next_line_start_time - line_start_time, 10.0)
            if position > line_start_time + limit:
                return None
        else:
            # Для последней строчки песни ограничиваем ее показ 8 секундами, чтобы сбросить статус во время аутро
            if position > line_start_time + 8.0:
                return None
                
    return current_line



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
    global shutdown_requested
    shutdown_requested = True
    icon.stop()

STARTUP_LNK_PATH = os.path.join(
    os.environ["APPDATA"],
    "Microsoft\\Windows\\Start Menu\\Programs\\Startup",
    "VEINYMusic.lnk"
)

def is_startup_enabled():
    return os.path.exists(STARTUP_LNK_PATH)

def toggle_startup(icon, item):
    if is_startup_enabled():
        try:
            os.remove(STARTUP_LNK_PATH)
        except Exception:
            pass
    else:
        try:
            bat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RUN_ME.bat")
            working_dir = os.path.dirname(os.path.abspath(__file__))
            
            target = bat_path if os.path.exists(bat_path) else sys.executable
            arguments = "" if os.path.exists(bat_path) else f'"{os.path.abspath(__file__)}"'
            
            import subprocess
            cmd = (
                f"$WshShell = New-Object -ComObject WScript.Shell; "
                f"$Shortcut = $WshShell.CreateShortcut('{STARTUP_LNK_PATH}'); "
                f"$Shortcut.TargetPath = '{target}'; "
                f"$Shortcut.Arguments = '{arguments}'; "
                f"$Shortcut.WorkingDirectory = '{working_dir}'; "
                f"$Shortcut.Save()"
            )
            subprocess.run(["powershell", "-Command", cmd], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass

def prompt_for_token():
    try:
        import tkinter as tk
        
        token = None
        
        def submit():
            nonlocal token
            token = entry.get()
            root.destroy()
            
        def on_closing():
            root.destroy()
            
        def paste_from_clipboard():
            try:
                clip = root.clipboard_get()
                entry.delete(0, tk.END)
                entry.insert(0, clip)
            except Exception:
                pass

        root = tk.Tk()
        root.title("VEINYMusic - Настройка Discord")
        root.geometry("550x330")
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", on_closing)
        
        msg = (
            "Для трансляции слов песен необходим ваш токен авторизации Discord.\n\n"
            "Как получить токен:\n"
            "1. Откройте Discord в браузере (discord.com/app)\n"
            "2. Нажмите F12 (Инструменты разработчика)\n"
            "3. Перейдите во вкладку 'Network' (Сеть)\n"
            "4. Отправьте любое сообщение в любой чат\n"
            "5. В появившемся списке кликните на 'messages'\n"
            "6. Справа прокрутите вниз до раздела 'Request Headers'\n"
            "7. Найдите строку 'Authorization' и скопируйте ее значение.\n"
        )
        
        tk.Label(root, text=msg, justify=tk.LEFT, font=("Arial", 10)).pack(padx=20, pady=10)
        
        frame = tk.Frame(root)
        frame.pack(padx=20, pady=5, fill=tk.X)
        
        tk.Label(frame, text="Ваш токен:", font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        entry = tk.Entry(frame, width=35)
        entry.pack(side=tk.LEFT, padx=10)
        
        btn_paste = tk.Button(frame, text="Вставить", command=paste_from_clipboard)
        btn_paste.pack(side=tk.LEFT)
        
        btn_ok = tk.Button(root, text="Сохранить и Включить", command=submit, bg="#4CAF50", fg="white", font=("Arial", 10, "bold"))
        btn_ok.pack(pady=15)
        
        # Для русской раскладки надежнее проверять keycode 86 (клавиша V/М на Windows)
        def on_keypress(event):
            if event.state & 0x0004 and event.keycode == 86: # 0x0004 - это флаг зажатого Control
                paste_from_clipboard()
                return "break"
                
        entry.bind("<KeyPress>", on_keypress)
        entry.bind("<Control-v>", lambda e: paste_from_clipboard() or "break")
        entry.bind("<Control-V>", lambda e: paste_from_clipboard() or "break")
        
        # Центрируем окно
        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry('{}x{}+{}+{}'.format(width, height, x, y))
        
        root.mainloop()
        return token
    except Exception as e:
        console.print(f"[bold red]Ошибка при вызове окна ввода токена: {e}[/bold red]")
        return None

def prompt_for_yandex_token():
    try:
        import tkinter as tk
        import webbrowser
        import requests
        import threading
        import time

        import base64

        client_id = base64.b64decode("MjNjYWJiYmRjNmNkNDE4YWJiNGIzOWMzMmM0MTE5NWQ=").decode("utf-8")
        client_secret = base64.b64decode("NTNiYzc1MjM4ZjBjNGQwOGExMThlNTFmZTkyMDMzMDA=").decode("utf-8")

        # 1. Запрос кода устройства
        try:
            r = requests.post("https://oauth.yandex.ru/device/code", data={
                "client_id": client_id
            }, timeout=10)
            if r.status_code != 200:
                console.print(f"[bold red]Ошибка при получении кода устройства: {r.text}[/bold red]")
                return None
            data = r.json()
            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_url = data["verification_url"]
            interval = data.get("interval", 5)
        except Exception as e:
            console.print(f"[bold red]Сетевая ошибка при запросе кода Yandex OAuth: {e}[/bold red]")
            return None

        token_result = [None]
        flow_success = threading.Event()
        stop_event = threading.Event()

        root = tk.Tk()
        root.title("Авторизация в Яндекс.Музыке")
        root.geometry("520x280")
        root.attributes("-topmost", True)

        msg = (
            "Мы открываем страницу подтверждения в вашем браузере.\n"
            "Пожалуйста, введите код активации ниже, чтобы предоставить доступ:\n\n"
            "Код также автоматически скопирован в буфер обмена."
        )
        tk.Label(root, text=msg, justify=tk.CENTER, font=("Arial", 10)).pack(padx=20, pady=15)

        # Крупное отображение кода
        code_text = user_code.lower()
        label_code = tk.Label(root, text=code_text, font=("Arial", 26, "bold"), fg="#e02d5c")
        label_code.pack(pady=10)

        label_status = tk.Label(root, text="Ожидание подтверждения в браузере...", font=("Arial", 9, "italic"), fg="gray")
        label_status.pack(pady=10)

        # Копируем в буфер
        root.clipboard_clear()
        root.clipboard_append(code_text)

        # Открываем браузер
        webbrowser.open(verification_url)

        def poll_token():
            while not stop_event.is_set():
                try:
                    r_poll = requests.post("https://oauth.yandex.ru/token", data={
                        "grant_type": "device_code",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": device_code
                    }, timeout=10)
                    
                    res = r_poll.json()
                    if r_poll.status_code == 200 and "access_token" in res:
                        token_result[0] = res["access_token"]
                        flow_success.set()
                        root.after(0, root.destroy)
                        break
                    elif res.get("error") == "authorization_pending":
                        pass
                    elif res.get("error") == "slow_down":
                        time.sleep(5)
                    elif res.get("error") in ("expired_token", "invalid_grant"):
                        break
                except Exception:
                    pass
                
                for _ in range(interval):
                    if stop_event.is_set():
                        break
                    time.sleep(1)

        poll_thread = threading.Thread(target=poll_token, daemon=True)
        poll_thread.start()

        def on_closing():
            stop_event.set()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_closing)
        
        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry('{}x{}+{}+{}'.format(width, height, x, y))

        root.mainloop()
        return token_result[0]
    except Exception as e:
        console.print(f"[bold red]Ошибка во время авторизации Yandex: {e}[/bold red]")
        return None

def get_yandex_status_text(item):
    if CONFIG.get("yandex_token"):
        return "Токен Яндекс.Музыки: Установлен"
    return "Токен Яндекс.Музыки: Не задан"

def configure_yandex_token(icon, item):
    import tkinter as tk
    
    current_token = CONFIG.get("yandex_token")
    if current_token:
        choice = None
        
        def on_delete():
            nonlocal choice
            choice = "delete"
            root.destroy()
            
        def on_reauth():
            nonlocal choice
            choice = "reauth"
            root.destroy()
            
        def on_cancel():
            root.destroy()

        root = tk.Tk()
        root.title("VEINYMusic - Управление токеном")
        root.geometry("420x160")
        root.attributes("-topmost", True)
        
        tk.Label(root, text="Токен Яндекс.Музыки уже сохранен в настройках.", font=("Arial", 10, "bold")).pack(pady=20)
        
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=10)
        
        tk.Button(btn_frame, text="Авторизоваться заново", command=on_reauth, bg="#4CAF50", fg="white", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="Удалить токен", command=on_delete, bg="#f44336", fg="white", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="Отмена", command=on_cancel, font=("Arial", 9)).pack(side=tk.LEFT, padx=10)
        
        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry('{}x{}+{}+{}'.format(width, height, x, y))

        root.mainloop()
        
        if choice == "delete":
            CONFIG["yandex_token"] = None
            save_config()
            meta_cache.clear()
            return
        elif choice != "reauth":
            return
            
    token = prompt_for_yandex_token()
    if token:
        CONFIG["yandex_token"] = token
        save_config()
        meta_cache.clear()

def is_lyrics_enabled(item):
    return CONFIG.get("lyrics_enabled", False)

def toggle_lyrics(icon, item):
    global status_manager
    is_enabled = not CONFIG.get("lyrics_enabled", False)
    
    if is_enabled:
        token = CONFIG.get("discord_token")
        if not token:
            token = prompt_for_token()
            if token:
                token = token.strip()
                CONFIG["discord_token"] = token
                save_config()
            else:
                return  # Пользователь отменил ввод, не включаем слова
                
        CONFIG["lyrics_enabled"] = True
        save_config()
        if not status_manager:
            status_manager = DiscordStatusManager(CONFIG["discord_token"])
        status_manager.enabled = True
    else:
        CONFIG["lyrics_enabled"] = False
        save_config()
        if status_manager and status_manager.enabled:
            status_manager.restore_status_sync()
            status_manager.enabled = False

def change_offset(delta):
    current = CONFIG.get("lyrics_offset", 0.8)
    CONFIG["lyrics_offset"] = round(current + delta, 1)
    save_config()

def increase_offset(icon, item):
    change_offset(0.1)

def decrease_offset(icon, item):
    change_offset(-0.1)

def get_offset_text(item):
    return f"Задержка слов: {CONFIG.get('lyrics_offset', 0.8)}с"

def setup_tray():
    """Запускает иконку в трее в отдельном потоке"""
    global tray_icon
    icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
    if os.path.exists(icon_path):
        image = Image.open(icon_path)
    else:
        image = Image.new('RGB', (64, 64), color=(147, 112, 219))

    offset_menu = pystray.Menu(
        item('Увеличить (+0.1с)', increase_offset),
        item('Уменьшить (-0.1с)', decrease_offset)
    )

    menu = pystray.Menu(
        item('Показать/Скрыть консоль', toggle_console),
        item('Слова песен в статусе Discord', toggle_lyrics, checked=is_lyrics_enabled),
        item(get_offset_text, offset_menu),
        item(get_yandex_status_text, configure_yandex_token),
        item('Запуск при старте системы', toggle_startup, checked=lambda item: is_startup_enabled()),
        item('Выход', on_exit)
    )
    tray_icon = pystray.Icon("VEINYMusic", image, "VEINYMusic", menu)
    threading.Thread(target=tray_icon.run, daemon=True).start()

def check_updates():
    """Проверяет обновления через GitHub API без использования Git"""
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".git")):
        return
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
                    # Добавляем ?v=latest_commit чтобы обойти кэш GitHub raw CDN (5 минут)
                    raw_url = f"https://raw.githubusercontent.com/{REPO_URL}/main/yandex_presence.py?v={latest_commit}"
                    new_code = requests.get(raw_url, timeout=10).text
                    if "import" in new_code and "asyncio" in new_code:
                        new_code = re.sub(
                            r'CURRENT_COMMIT = "[a-f0-9]{40}"',
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

def get_track_meta(title, artist, album_hint=None):
    """
    Ищет обложку и метаданные трека.
    Источники (по приоритету): Deezer → iTunes.
    Оба API публичные и не требуют авторизации.
    """
    q_artist = clean_text(artist)
    q_title = clean_text(title)

    def transliterate(text):
        mapping = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
            'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
        }
        return "".join(mapping.get(c, c) for c in text.lower())

    def similarity(a, b):
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        ratio_t = difflib.SequenceMatcher(None, transliterate(a), transliterate(b)).ratio()
        return max(ratio, ratio_t)

    def best_match(items, get_title, get_artist):
        """Выбирает лучший результат из списка по схожести с оригиналом."""
        t_clean = clean_text(title)
        a_clean = clean_text(artist)
        best, best_score = None, 0.0
        for item in items:
            raw_title = get_title(item)
            i_title = clean_text(raw_title)
            i_artist = clean_text(get_artist(item))
            title_sim = similarity(t_clean, i_title)
            
            exact_title_bonus = 0.2 if title.lower() == raw_title.lower() else 0.0
            
            if a_clean:
                artist_sim = similarity(a_clean, i_artist)
                artist_match = (a_clean in i_artist or i_artist in a_clean)
                artist_score = max(artist_sim, 0.8 if artist_match else 0.0)
                
                if artist_score < 0.25:
                    continue
                score = title_sim * 0.6 + artist_score * 0.4 + exact_title_bonus
            else:
                score = title_sim + exact_title_bonus
                
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

    def try_yandex(query):
        yandex_token = CONFIG.get("yandex_token")
        if not yandex_token:
            return None
        try:
            url = f"https://api.music.yandex.net/search?text={quote(query)}&type=track&page=0"
            headers = {
                "Authorization": f"OAuth {yandex_token}",
                "User-Agent": "YandexMusicAndroid/24022571",
                "X-Yandex-Music-Client": "YandexMusicAndroid/24022571"
            }
            r = session.get(url, headers=headers, timeout=4)
            if r.status_code != 200:
                return None
            
            data = r.json()
            tracks_list = data.get("result", {}).get("tracks", {}).get("results", [])
            if not tracks_list:
                return None

            found = best_match(
                tracks_list,
                get_title=lambda x: x.get("title", ""),
                get_artist=lambda x: ", ".join(a.get("name", "") for a in x.get("artists", [])) if x.get("artists") else ""
            )
            if not found:
                return None

            track_id = found.get("id")
            album = found.get("albums", [{}])[0] if found.get("albums") else {}
            
            if track_id:
                try:
                    details_url = f"https://api.music.yandex.net/tracks?trackIds={track_id}"
                    r_details = session.get(details_url, headers=headers, timeout=4)
                    if r_details.status_code == 200:
                        details_data = r_details.json()
                        details_results = details_data.get("result", [])
                        if details_results:
                            details_track = details_results[0]
                            albums_list = details_track.get("albums", [])
                            if albums_list:
                                selected_album = None
                                if album_hint:
                                    best_album = None
                                    best_score = -1
                                    h_clean = clean_text(album_hint)
                                    for alb in albums_list:
                                        a_title = alb.get("title", "")
                                        score = similarity(h_clean, clean_text(a_title))
                                        if score > best_score:
                                            best_score = score
                                            best_album = alb
                                    if best_score >= 0.5:
                                        selected_album = best_album
                                
                                if not selected_album:
                                    selected_album = albums_list[0]
                                
                                album = selected_album
                except Exception:
                    pass

            cover_uri = album.get("coverUri") or found.get("coverUri")
            cover = "logo"
            if cover_uri:
                cover = cover_uri.replace("%%", "400x400")
                if not cover.startswith("http"):
                    cover = "https://" + cover

            return {
                "id": track_id,
                "title": found.get("title") or title,
                "artist": ", ".join(a.get("name", "") for a in found.get("artists", [])) if found.get("artists") else artist,
                "album": album.get("title", "Yandex Music"),
                "album_id": album.get("id"),
                "cover": cover,
                "_track_link": f"https://music.yandex.ru/track/{track_id}",
                "_source": "yandex",
            }
        except Exception:
            return None

    # Пробуем запросы от точного к широкому
    queries = [
        f"{q_title} {q_artist}",
        q_title,
    ]

    # Если есть токен Яндекса, сначала ищем в Яндекс.Музыке
    if CONFIG.get("yandex_token"):
        for query in queries:
            if not query.strip():
                continue
            result = try_yandex(query)
            if result:
                return result

    # Фолбек на Deezer / iTunes
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

def get_open_window_titles():
    """Возвращает список заголовков всех видимых окон в системе"""
    titles = []
    def enum_windows_proc(hwnd, lParam):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                if buff.value:
                    titles.append(buff.value)
        return True

    try:
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        cb_func = WNDENUMPROC(enum_windows_proc)
        ctypes.windll.user32.EnumWindows(cb_func, 0)
    except Exception:
        pass
    return titles

def is_yandex_music_session(session, info):
    """Проверяет, относится ли медиа-сессия к Яндекс Музыке"""
    app_id = (session.source_app_user_model_id or "").lower()
    title  = (info.title  or "").strip()
    artist = (info.artist or "").strip()
    album_title = (info.album_title or "").strip()

    # Яндекс Браузер и его App ID — принимаем, но с фильтрами
    is_yandex_browser = "yandex" in app_id
    # Обычные браузеры — Chrome, Firefox, Edge, Opera, Brave
    is_other_browser = any(b in app_id for b in ["chrome", "edge", "firefox", "opera", "brave", "browser"]) or "308046b0af4a39cb" in app_id

    if not is_yandex_browser and not is_other_browser:
        # Не браузер и не Яндекс — неизвестный источник, отклоняем
        return False

    # Блокируем сессии без исполнителя (фоновый звук сайтов, виджеты)
    if not artist:
        return False

    # Блокируем YouTube по ключевым словам в названии альбома (если задано)
    if album_title and "youtube" in album_title.lower():
        return False


    # Блокируем YouTube — у них artist = название канала, но заголовок содержит характерные паттерны
    title_low = title.lower()
    artist_low = artist.lower()
    youtube_signals = ["- youtube", "• youtube", "| youtube", "youtube music"]
    if any(s in title_low for s in youtube_signals):
        return False
    # YouTube часто ставит исполнителя как "- Topic" (официальные каналы)
    if artist_low.endswith(" - topic"):
        return False

    # Надёжный способ: YouTube ВСЕГДА добавляет "- YouTube" в заголовок окна/вкладки.
    # Проверяем — есть ли окно с названием трека И "youtube" в заголовке → это YouTube, блокируем.
    titles = get_open_window_titles()
    t_clean = clean_text(title)
    for wt in titles:
        wt_low = wt.lower()
        if "youtube" in wt_low and t_clean and t_clean in wt_low:
            return False

    return True

meta_cache = {}
rejected_tracks = set()

async def get_raw_system_media():
    """Сканирует все сессии и выбирает лучшую (только Яндекс Музыка)"""
    try:
        manager = await SessionManager.request_async()
        sessions = manager.get_sessions()
        candidates = []

        for session in sessions:
            info = await session.try_get_media_properties_async()
            if not info.title: continue

            # Строгая фильтрация: пропускаем всё, что не является Яндекс Музыкой
            if not is_yandex_music_session(session, info):
                continue

            # Пропускаем сессии без исполнителя — веб-виджеты и фоновое аудио сайтов
            # не устанавливают поле artist. Настоящие треки из приложения всегда его имеют.
            if not info.artist or not info.artist.strip():
                continue

            track_id = f"{info.artist}-{info.title}"
            if track_id in rejected_tracks: continue

            meta = meta_cache.get(track_id)

            # Если трек не найден в глобальной базе музыки, мы разрешаем его
            # только если в системе реально открыто окно Яндекс Музыки.
            if (meta is None or meta == "pending") and track_id in meta_cache:
                titles = await asyncio.to_thread(get_open_window_titles)
                has_ym_window = any(
                    "яндекс музыка" in t.lower() or "яндекс.музыка" in t.lower()
                    or "yandex music" in t.lower() or "yandex.music" in t.lower()
                    for t in titles
                )
                if not has_ym_window:
                    continue

            if meta == "pending":
                meta = None

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
                "album_title": info.album_title,
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

def create_ui(raw, meta, debug_info=None, current_lyric=None):
    """Создает компактный и надежный интерфейс"""
    header = Text.from_markup("""[magenta]------------------------------------------------------------[/magenta]
[bold white]  VEINYMusic[/bold white]
[magenta]------------------------------------------------------------[/magenta]
""")

    if not raw:
        msg = "[bold yellow]Ожидание запуска плеера...[/bold yellow]\n[dim]Включи музыку в Яндекс Музыке в браузере[/dim]"
        if debug_info:
            msg += f"\n\n[dim italic]Система видит: {debug_info}[/dim italic]"
        if status_manager and status_manager.enabled:
            msg += "\n\n[dim green]✓ Lyrics Status Sync: Активен (токен загружен)[/dim green]"
        else:
            msg += "\n\n[dim]💡 Lyrics Status: отключен. Вставь свой токен в файл [italic]config.json[/italic], чтобы транслировать слова песни в статус![/dim]"
        
        if CONFIG.get("yandex_token"):
            msg += "\n[dim green]✓ Поиск метаданных: Яндекс.Музыка (токен установлен)[/dim green]"
        else:
            msg += "\n[dim]💡 Поиск метаданных: Deezer / iTunes. Установите токен Яндекса для идеального совпадения треков![/dim]"
        panel = Panel(
            msg,
            title="[bold magenta]VEINYMusic[/bold magenta]",
            border_style="magenta",
            padding=(1, 2)
        )
        return Group(header, panel)

    d_art = raw['artist']
    d_tit = raw['title']
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

    if current_lyric:
        lyric_text = Text(f"♪ {current_lyric} ♪", style="italic magenta bold", justify="center", no_wrap=True, overflow="ellipsis")
    else:
        lyric_text = Text(" ", justify="center")

    ui_group = Group("", info_table, "", progress, "", lyric_text, "")

    panel = Panel(
        ui_group,
        title=f"[bold {status_color}]{status_icon} {raw['status'] == 4 and 'ИГРАЕТ' or 'ПАУЗА'}[/bold {status_color}]",
        subtitle="Синхронизация с Discord активна",
        border_style="magenta" if raw['status'] == 4 else "yellow",
        padding=(0, 2)
    )
    return Group(header, panel)

async def main():
    global status_manager, _ctrl_handler
    os.system("")  # Force enable VT100 ANSI processing in Windows CMD
    check_updates()
    setup_tray()

    # Перехватываем закрытие консоли (крестик)
    import ctypes
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
    def console_ctrl_handler(ctrl_type):
        global shutdown_requested
        if ctrl_type in (0, 2, 5, 6): # Ctrl+C, Close, Logoff, Shutdown
            shutdown_requested = True
            time.sleep(3) # Даем главному потоку время на очистку
            return True
        return False
    
    _ctrl_handler = console_ctrl_handler
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_handler, True)

    # Инициализируем менеджер кастомных статусов Discord
    token = CONFIG.get("discord_token")
    if token:
        status_manager = DiscordStatusManager(token)

    rpc = AioPresence(DISCORD_CLIENT_ID)
    try: await rpc.connect()
    except: pass

    last_debug = None

    # Variables for Discord RPC debouncing
    rpc_track_id = ""
    rpc_status = "off"
    rpc_start_ts = 0
    rpc_cover = None
    rpc_album = None
    rpc_cooldown_until = 0
    
    track_settle_time = 1.5 # seconds to wait before updating Discord RPC / status / fetching lyrics
    track_detected_time = 0
    current_detected_track_id = ""

    # Очищаем экран перед запуском интерфейса, чтобы избежать багов с прокруткой консоли Windows
    os.system("cls" if os.name == "nt" else "clear")
    
    last_ui_state = None
    with Live(auto_refresh=False, console=console) as live:
        while not shutdown_requested:
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

                current_ui_state = ("idle", last_debug, CONFIG.get("yandex_token"))
                if current_ui_state != last_ui_state:
                    live.update(create_ui(None, None, last_debug), refresh=True)
                    last_ui_state = current_ui_state
                
                # Восстанавливаем оригинальный кастомный статус, если плеер закрыт
                if status_manager and status_manager.enabled:
                    await status_manager.restore_status()

                if rpc_status != "off":
                    try:
                        await rpc.clear()
                    except:
                        pass
                    rpc_status = "off"
                    rpc_track_id = ""
                    rpc_cover = None
                    rpc_album = None
                    current_detected_track_id = ""
                await asyncio.sleep(1)
                continue

            track_id = f"{raw['artist']}-{raw['title']}"
            
            # Update the detected track tracking
            if track_id != current_detected_track_id:
                current_detected_track_id = track_id
                track_detected_time = time.time()
                
                # Immediately clear lyrics when skipping tracks to avoid lingering text
                if status_manager and status_manager.enabled:
                    await status_manager.restore_status()

            is_settled = (time.time() - track_detected_time) >= track_settle_time
            meta = raw['meta']

            # If settled, we trigger metadata fetch, lyrics fetch, and RPC update
            if is_settled:
                # 1. Fetch metadata if not in cache
                if not meta and track_id not in meta_cache:
                    if len(meta_cache) > 500:
                        meta_cache.clear()
                    meta_cache[track_id] = "pending"
                    
                    def fetch_and_cache(t_id, t_title, t_artist, t_album):
                        try:
                            res = get_track_meta(t_title, t_artist, t_album)
                            meta_cache[t_id] = res
                        except Exception:
                            meta_cache[t_id] = None
                    
                    threading.Thread(target=fetch_and_cache, args=(track_id, raw['title'], raw['artist'], raw['album_title']), daemon=True).start()
                
                # 2. Fetch lyrics if not in cache
                if track_id not in lyrics_cache and track_id not in fetching_lyrics:
                    if len(lyrics_cache) > 500:
                        lyrics_cache.clear()
                    async_fetch_lyrics(track_id, raw['title'], raw['artist'])

            current_lyric = None
            if is_settled:
                # Get lyrics and update custom status
                lyrics = lyrics_cache.get(track_id)
                offset = CONFIG.get("lyrics_offset", 0.8)
                current_lyric = get_current_lyric_line(lyrics, raw['position'] + offset) if lyrics else None

                if status_manager and status_manager.enabled:
                    if raw['status'] == 4 and CONFIG.get("lyrics_enabled", False) and current_lyric:
                        await status_manager.update_status(current_lyric, emoji_name="🎵")
                    else:
                        await status_manager.update_status(raw['artist'], emoji_name=None)
                
                # 3. Update Discord RPC
                cover = meta['cover'] if meta else None
                album_name = meta['album'] if meta else None
                
                is_new_rpc_track = track_id != rpc_track_id
                is_status_changed = raw['status'] != rpc_status
                current_start_ts = int(now - raw['position'])
                is_seeked = abs(current_start_ts - rpc_start_ts) > 2
                is_cover_updated = cover != rpc_cover
                is_album_updated = album_name != rpc_album

                if now >= rpc_cooldown_until:
                    if is_new_rpc_track or is_status_changed or is_seeked or is_cover_updated or is_album_updated:
                        try:
                            if raw['status'] == 4:  # PLAYING
                                 end_ts = int(current_start_ts + raw['duration']) if raw['duration'] > 0 else None

                                 details = raw['title']
                                 state = f"{raw['artist']} — {meta['album']}" if meta else raw['artist']

                                 if len(details) > 128:
                                     details = details[:125] + "..."
                                 if len(state) > 128:
                                     state = state[:125] + "..."

                                 if len(details) < 2:
                                     details = details.ljust(2)
                                 if len(state) < 2:
                                     state = state.ljust(2)

                                 await rpc.update(
                                     details=details,
                                     state=state,
                                     large_image=meta['cover'] if meta else "logo",
                                     small_image="logo",
                                     small_text="VEINYMusic",
                                     start=current_start_ts, end=end_ts,
                                     activity_type=2
                                 )
                            else:  # PAUSED
                                 details = f"⏸ {raw['title']}"
                                 state = raw['artist']

                                 if len(details) > 128:
                                     details = details[:125] + "..."
                                 if len(state) > 128:
                                     state = state[:125] + "..."

                                 if len(details) < 2:
                                     details = details.ljust(2)
                                 if len(state) < 2:
                                     state = state.ljust(2)

                                 await rpc.update(
                                     details=details,
                                     state=state,
                                     large_image=meta['cover'] if meta else "logo",
                                     activity_type=2
                                 )
                            
                            # Update RPC state variables
                            rpc_track_id = track_id
                            rpc_status = raw['status']
                            rpc_start_ts = current_start_ts
                            rpc_cover = cover
                            rpc_album = album_name
                            
                        except Exception as e:
                            err_name = type(e).__name__
                            import traceback
                            with open(LOG_PATH, "a", encoding="utf-8") as f:
                                f.write(f"RPC Update Error ({err_name}) at {datetime.datetime.now()}:\n{traceback.format_exc()}\n")
                            
                            rpc_cooldown_until = now + 10.0
                            
                            if err_name != "ServerError":
                                try:
                                    rpc.close()
                                except:
                                    pass
                                rpc = AioPresence(DISCORD_CLIENT_ID)
                                try:
                                    await rpc.connect()
                                except:
                                    pass

            current_ui_state = (
                track_id,
                raw['status'],
                int(raw['position']),
                current_lyric,
                meta['title'] if meta else None,
                meta['cover'] if meta else None,
                CONFIG.get("yandex_token")
            )
            if current_ui_state != last_ui_state:
                live.update(create_ui(raw, meta, current_lyric=current_lyric), refresh=True)
                last_ui_state = current_ui_state
            await asyncio.sleep(0.1)

    console.print("\n[bold yellow]Очистка статуса... Пожалуйста, подождите.[/bold yellow]")
    try:
        await rpc.clear()
        rpc.close()
    except:
        pass
        
    if status_manager:
        await status_manager.restore_status(force_wait=True)
        await asyncio.sleep(0.5)
        
    console.print("[bold green]Статус успешно очищен! Скрипт закрывается...[/bold green]")
    await asyncio.sleep(1)
    os._exit(0)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        if status_manager:
            status_manager.restore_status_sync(force_wait=True)
            time.sleep(0.5)
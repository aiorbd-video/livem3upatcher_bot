import hashlib
import time
import re
import json
from datetime import timedelta
from config import START_TIME

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

USER_LIMIT = {}
LIMIT_SECONDS = 3
admin_state = {}

def allow_user(user_id: int) -> bool:
    now = time.time()
    last = USER_LIMIT.get(user_id)
    if last is not None and (now - last) < LIMIT_SECONDS:
        return False
    USER_LIMIT[user_id] = now
    return True

def get_sys_status() -> str:
    uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
    if HAS_PSUTIL:
        try:
            ram = psutil.virtual_memory().percent
            cpu = psutil.cpu_percent(interval=None)
            return f"⏱ <b>Uptime:</b> {uptime}\n💽 <b>RAM:</b> {ram}%\n⚙️ <b>CPU:</b> {cpu}%"
        except Exception: pass
    return f"⏱ <b>Uptime:</b> {uptime}\n⚠️ <i>Install 'psutil' for CPU/RAM stats</i>"

def make_stream_hash(stream_url: str) -> str:
    return hashlib.md5(stream_url.encode()).hexdigest()

def parse_m3u_playlist(content: str):
    streams = []
    clean = re.sub(r"#TOTAL-VS-MATCHES:[^\n#]*", "", content)
    clean = re.sub(r"#LAST-UPDATED:[^\n#]*", "", clean)
    blocks = clean.split("#EXTINF")

    for idx, block in enumerate(blocks):
        if not block.strip() or block.startswith("#EXTM3U"): continue
        stream = {"title": f"Live Stream {idx}", "group": "লাইভ টিভি", "logo": "", "referer": "", "origin": "", "cookie": "", "user_agent": "", "url": ""}
        
        extinf_line = block.strip().splitlines()[0] if block.strip() else ""
        if g_match := re.search(r'group-title="([^"]+)"', extinf_line, re.IGNORECASE): stream["group"] = g_match.group(1).strip()
        if l_match := re.search(r'tvg-logo="([^"]+)"', extinf_line, re.IGNORECASE): stream["logo"] = l_match.group(1).strip()
        
        if "," in extinf_line: stream["title"] = extinf_line.split(",", 1)[-1].strip()

        if ref_m := re.search(r"#EXTVLCOPT:http-referrer=([^#\n]+)", block, re.IGNORECASE): stream["referer"] = ref_m.group(1).strip()
        if orig_m := re.search(r"#EXTVLCOPT:http-origin=([^#\n]+)", block, re.IGNORECASE): stream["origin"] = orig_m.group(1).strip()
        if cookie_m := re.search(r"#EXTVLCOPT:http-cookie=([^#\n]+)", block, re.IGNORECASE): stream["cookie"] = cookie_m.group(1).strip()
        if ua_m := re.search(r"#EXTVLCOPT:http-user-agent=([^#\n]+)", block, re.IGNORECASE): stream["user_agent"] = ua_m.group(1).strip()

        urls = re.findall(r"(https?://[^\s#]+)", block.replace(stream["logo"], ""))
        if urls:
            playback_url = urls[-1].strip()
            stream["url"] = playback_url.split("|", 1)[0].strip() if "|" in playback_url else playback_url
            streams.append(stream)

    return streams

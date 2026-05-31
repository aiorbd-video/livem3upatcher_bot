import hashlib
import time
import re
from datetime import timedelta
import aiohttp
from config import START_TIME, HEADERS

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

async def fetch_m3u_content(url: str):
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=20) as response:
                if response.status == 200:
                    return await response.text()
    except Exception as e:
        print(f"Fetch error: {e}")
    return None

def parse_m3u_playlist(content: str):
    streams = []
    # হ্যাং হওয়া থেকে বাঁচতে সেফ রিপ্লেস
    blocks = content.split("#EXTINF")

    for idx, block in enumerate(blocks):
        if not block.strip() or block.startswith("#EXTM3U"): continue
        stream = {"title": f"Live Stream {idx}", "group": "লাইভ টিভি", "logo": "", "referer": "", "origin": "", "cookie": "", "user_agent": "", "url": ""}
        
        lines = block.strip().splitlines()
        if not lines: continue
        
        extinf_line = lines[0]
        
        # নিরাপদ লোগো এবং গ্রুপ পার্সিং
        if g_match := re.search(r'group-title="([^"]+)"', extinf_line, re.IGNORECASE): 
            stream["group"] = g_match.group(1).strip()
        if l_match := re.search(r'tvg-logo="([^"]+)"', extinf_line, re.IGNORECASE): 
            stream["logo"] = l_match.group(1).strip()
        
        # 🎯 সেফ টাইটেল এক্সট্রাকশন (NO HANG)
        parts = extinf_line.split(',')
        raw_title = parts[-1].strip()
        raw_title = re.sub(r'https?://[^\s]+', '', raw_title).strip() # ক্লিনিং
        stream["title"] = raw_title if raw_title else f"Live Stream {idx}"

        # হেডার পার্সিং
        if ref_m := re.search(r"#EXTVLCOPT:http-referrer=([^#\n]+)", block, re.IGNORECASE): stream["referer"] = ref_m.group(1).strip()
        if orig_m := re.search(r"#EXTVLCOPT:http-origin=([^#\n]+)", block, re.IGNORECASE): stream["origin"] = orig_m.group(1).strip()
        if cookie_m := re.search(r"#EXTVLCOPT:http-cookie=([^#\n]+)", block, re.IGNORECASE): stream["cookie"] = cookie_m.group(1).strip()
        if ua_m := re.search(r"#EXTVLCOPT:http-user-agent=([^#\n]+)", block, re.IGNORECASE): stream["user_agent"] = ua_m.group(1).strip()

        # নিখুঁত লিংক পার্সিং
        for line in lines[1:]:
            line = line.strip()
            if line and not line.startswith('#'):
                playback_url = line
                if "|" in playback_url:
                    parts = playback_url.split("|", 1)
                    playback_url, h_part = parts[0].strip(), parts[1]
                    if p_ref := re.search(r"Referer=([^&]+)", h_part, re.IGNORECASE): stream["referer"] = p_ref.group(1).strip()
                    if p_orig := re.search(r"Origin=([^&]+)", h_part, re.IGNORECASE): stream["origin"] = p_orig.group(1).strip()
                    if p_cookie := re.search(r"Cookie=([^&]+)", h_part, re.IGNORECASE): stream["cookie"] = p_cookie.group(1).strip()
                    if p_ua := re.search(r"User-Agent=([^&]+)", h_part, re.IGNORECASE): stream["user_agent"] = p_ua.group(1).strip()

                stream["url"] = playback_url
                break 

        if stream["url"]:
            streams.append(stream)

    return streams

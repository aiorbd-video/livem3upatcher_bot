import hashlib
import time
import re
import json
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

async def fetch_m3u_content(url: str):
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=20) as response:
                if response.status == 200:
                    return await response.text()
    except Exception as e:
        print(f"Fetch Error: {e}")
    return None

def parse_m3u_playlist(content: str):
    streams = []
    content = content.replace("#EXTM3U", "")
    blocks = content.split("#EXTINF")

    for idx, block in enumerate(blocks):
        if not block.strip(): continue
        stream = {"title": f"Live Stream {idx}", "group": "লাইভ টিভি", "logo": "", "referer": "", "origin": "", "cookie": "", "user_agent": "", "url": ""}
        
        if 'group-title="' in block:
            stream["group"] = block.split('group-title="')[1].split('"')[0]
        if 'tvg-logo="' in block:
            stream["logo"] = block.split('tvg-logo="')[1].split('"')[0]

        if ',' in block:
            after_comma = block.split(',', 1)[1]
            match = re.search(r'(#EXTVLCOPT|#EXTHTTP|https?://)', after_comma)
            if match:
                stream["title"] = after_comma[:match.start()].strip()
                rest = after_comma[match.start():]
            else:
                stream["title"] = after_comma.strip()
                rest = ""
            
            stream["title"] = re.sub(r'https?://[^\s]+', '', stream["title"]).strip()
            
            # হেডার এক্সট্রাকশন
            if "#EXTVLCOPT:http-referrer=" in rest: stream["referer"] = rest.split("#EXTVLCOPT:http-referrer=")[1].split("#")[0].split("http")[0].strip()
            if "#EXTVLCOPT:http-origin=" in rest: stream["origin"] = rest.split("#EXTVLCOPT:http-origin=")[1].split("#")[0].split("http")[0].strip()
            if "#EXTVLCOPT:http-cookie=" in rest: stream["cookie"] = rest.split("#EXTVLCOPT:http-cookie=")[1].split("#")[0].split("http")[0].strip()
            if "#EXTVLCOPT:http-user-agent=" in rest: stream["user_agent"] = rest.split("#EXTVLCOPT:http-user-agent=")[1].split("#")[0].split("http")[0].strip()

            if "#EXTHTTP:{" in rest:
                try:
                    j_part = rest.split("#EXTHTTP:")[1]
                    if "}http" in j_part:
                        j_str = j_part.split("}http")[0] + "}"
                        h_data = {k.lower(): v for k, v in json.loads(j_str).items()}
                        stream["cookie"] = str(h_data.get("cookie", stream["cookie"]))
                        stream["referer"] = str(h_data.get("referer", stream["referer"]))
                        stream["origin"] = str(h_data.get("origin", stream["origin"]))
                        stream["user_agent"] = str(h_data.get("user-agent", stream["user_agent"]))
                except Exception: pass

            url_matches = re.findall(r'(https?://[^\s#|]+)', rest)
            if url_matches:
                stream["url"] = url_matches[-1].strip()

            if "|" in rest:
                h_part = rest.split("|", 1)[1]
                if "Referer=" in h_part: stream["referer"] = h_part.split("Referer=")[1].split("&")[0].split("#")[0].strip()
                if "Origin=" in h_part: stream["origin"] = h_part.split("Origin=")[1].split("&")[0].split("#")[0].strip()
                if "Cookie=" in h_part: stream["cookie"] = h_part.split("Cookie=")[1].split("&")[0].split("#")[0].strip()
                if "User-Agent=" in h_part: stream["user_agent"] = h_part.split("User-Agent=")[1].split("&")[0].split("#")[0].strip()
        
        if stream["url"]:
            streams.append(stream)
            
    return streams

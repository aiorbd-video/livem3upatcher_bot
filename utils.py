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

# 🎯 ফিক্স: এই লাইনগুলো মুছে গিয়েছিল, এগুলো আবার অ্যাড করা হয়েছে
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
    """সার্ভারের র‍্যাম এবং সিপিইউ দেখার ফাংশন"""
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
        print(f"Fetch Error: {e}")
    return None

def parse_m3u_playlist(content: str):
    """
    Universal Enterprise M3U Parser
    যেকোনো বিকৃত, জোড়া লাগানো বা কাস্টম হেডার যুক্ত M3U ফাইল পার্স করতে সক্ষম
    """
    streams = []
    
    content = content.replace("#TOTAL-VS-MATCHES:", "\n")
    content = content.replace("#LAST-UPDATED:", "\n")
    content = content.replace("#EXTM3U", "")
    
    blocks = content.split("#EXTINF")
    
    for idx, block in enumerate(blocks):
        if not block.strip():
            continue
            
        stream = {
            "title": f"Unknown Stream {idx}", "group": "লাইভ টিভি", "logo": "",
            "referer": "", "origin": "", "cookie": "", "user_agent": "", "url": ""
        }
        
        boundary_match = re.search(r'(#EXTVLCOPT:|#EXTHTTP:|https?://)', block)
        if boundary_match:
            meta_part = block[:boundary_match.start()]
            rest_part = block[boundary_match.start():]
        else:
            meta_part = block
            rest_part = ""
            
        # 1. 메টাডেটা পার্সিং (লোগো, গ্রুপ, টাইটেল)
        if 'group-title="' in meta_part: stream["group"] = meta_part.split('group-title="')[1].split('"')[0].strip()
        elif 'group-title=' in meta_part: stream["group"] = meta_part.split('group-title=')[1].split()[0].split(',')[0].strip()
            
        if 'tvg-logo="' in meta_part: stream["logo"] = meta_part.split('tvg-logo="')[1].split('"')[0].strip()
            
        if ',' in meta_part:
            if '",' in meta_part: raw_title = meta_part.split('",')[-1].strip()
            else: raw_title = meta_part.split(',', 1)[-1].strip()
            
            raw_title = re.sub(r'^[:\- \t]+', '', raw_title)
            if raw_title: stream["title"] = raw_title

        # 2. হেডার এবং URL আন-গ্লুয়িং (Un-gluing)
        rest_part = rest_part.replace("#EXTVLCOPT:", "\n#EXTVLCOPT:")
        rest_part = rest_part.replace("#EXTHTTP:", "\n#EXTHTTP:")
        rest_part = re.sub(r'([^\s])(https?://)', r'\1\n\2', rest_part)
        
        rest_lines = [line.strip() for line in rest_part.splitlines() if line.strip()]
        
        # 3. হেডার এবং টোকেন এক্সট্রাকশন
        for line in rest_lines:
            if line.startswith("#EXTVLCOPT:"):
                opt = line.split(":", 1)[1]
                if "=" in opt:
                    key, val = opt.split("=", 1)
                    key, val = key.strip().lower(), val.strip()
                    if key == "http-referrer": stream["referer"] = val
                    elif key == "http-origin": stream["origin"] = val
                    elif key == "http-cookie": stream["cookie"] = val
                    elif key == "http-user-agent": stream["user_agent"] = val
                    
            elif line.startswith("#EXTHTTP:"):
                try:
                    j_str = line.split("#EXTHTTP:")[1]
                    h_data = {k.lower(): v for k, v in json.loads(j_str).items()}
                    if "cookie" in h_data: stream["cookie"] = str(h_data["cookie"]).strip()
                    if "referer" in h_data: stream["referer"] = str(h_data["referer"]).strip()
                    if "origin" in h_data: stream["origin"] = str(h_data["origin"]).strip()
                    if "user-agent" in h_data: stream["user_agent"] = str(h_data["user-agent"]).strip()
                except Exception: pass
                    
            elif line.startswith("http"):
                raw_url = line
                if "|" in raw_url:
                    u_part, h_part = raw_url.split("|", 1)
                    raw_url = u_part.strip()
                    
                    if p_ref := re.search(r'Referer=([^&]+)', h_part, re.IGNORECASE): stream["referer"] = p_ref.group(1).strip()
                    if p_orig := re.search(r'Origin=([^&]+)', h_part, re.IGNORECASE): stream["origin"] = p_orig.group(1).strip()
                    if p_cookie := re.search(r'Cookie=([^&]+)', h_part, re.IGNORECASE): stream["cookie"] = p_cookie.group(1).strip()
                    if p_ua := re.search(r'User-Agent=([^&]+)', h_part, re.IGNORECASE): stream["user_agent"] = p_ua.group(1).strip()
                
                stream["url"] = raw_url
                
        if stream["url"]:
            streams.append(stream)
            
    return streams

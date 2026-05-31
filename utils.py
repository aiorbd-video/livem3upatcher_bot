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

def get_vlc_opt(string: str, opt_name: str) -> str:
    """জোড়া লাগানো লাইন থেকে সেফলি VLC অপশন বের করার ফাংশন"""
    if opt_name in string:
        val = string.split(opt_name)[1]
        # অপশনটি পরবর্তী ট্যাগ বা লিংকের আগ পর্যন্ত কাটবে
        match = re.search(r'(#|http://|https://)', val)
        if match:
            return val[:match.start()].strip()
        return val.strip()
    return ""

def parse_m3u_playlist(content: str):
    streams = []
    
    # 🎯 হ্যাং হওয়া এড়াতে অপ্রয়োজনীয় ডাটা ক্লিন করে সরাসরি #EXTINF দিয়ে স্প্লিট
    content = content.replace("#TOTAL-VS-MATCHES:", " ")
    content = content.replace("#LAST-UPDATED:", " ")
    content = content.replace("#EXTM3U", "")
    
    blocks = content.split("#EXTINF")

    for idx, block in enumerate(blocks):
        if not block.strip(): 
            continue
            
        stream = {"title": f"Live Stream {idx}", "group": "লাইভ টিভি", "logo": "", "referer": "", "origin": "", "cookie": "", "user_agent": "", "url": ""}
        
        # 1. গ্রুপ ও লোগো এক্সট্রাকশন
        if 'group-title="' in block:
            stream["group"] = block.split('group-title="')[1].split('"')[0]
        if 'tvg-logo="' in block:
            stream["logo"] = block.split('tvg-logo="')[1].split('"')[0]

        # 2. টাইটেল এবং লিংকের বাউন্ডারি বের করা
        if ',' in block:
            after_comma = block.split(',', 1)[1]
            
            # টাইটেল কোথায় শেষ আর হেডার/লিংক কোথায় শুরু তা খোঁজা
            boundary_match = re.search(r'(#EXTVLCOPT|#EXTHTTP|http://|https://)', after_comma)
            
            if boundary_match:
                boundary_idx = boundary_match.start()
                stream["title"] = after_comma[:boundary_idx].strip()
                rest_of_string = after_comma[boundary_idx:]
                
                # 3. হেডার ও কুকি এক্সট্রাকশন
                stream["referer"] = get_vlc_opt(rest_of_string, "#EXTVLCOPT:http-referrer=")
                stream["origin"] = get_vlc_opt(rest_of_string, "#EXTVLCOPT:http-origin=")
                stream["cookie"] = get_vlc_opt(rest_of_string, "#EXTVLCOPT:http-cookie=")
                stream["user_agent"] = get_vlc_opt(rest_of_string, "#EXTVLCOPT:http-user-agent=")

                # JSON কুকি হ্যান্ডেলিং (#EXTHTTP)
                if "#EXTHTTP:{" in rest_of_string:
                    try:
                        j_part = rest_of_string.split("#EXTHTTP:")[1]
                        if "}http" in j_part:
                            j_str = j_part.split("}http")[0] + "}"
                            h_data = {k.lower(): v for k, v in json.loads(j_str).items()}
                            if "cookie" in h_data: stream["cookie"] = str(h_data["cookie"])
                            if "referer" in h_data: stream["referer"] = str(h_data["referer"])
                            if "origin" in h_data: stream["origin"] = str(h_data["origin"])
                            if "user-agent" in h_data: stream["user_agent"] = str(h_data["user-agent"])
                    except Exception as e:
                        print(f"JSON Parsing failed: {e}")

                # 4. ফাইনাল প্লেব্যাক লিংক বের করা
                last_http_idx = max(rest_of_string.rfind("http://"), rest_of_string.rfind("https://"))
                if last_http_idx != -1:
                    raw_url = rest_of_string[last_http_idx:].strip()
                    
                    # পাইপ (|) অপশন থাকলে ক্লিন করা
                    if "|" in raw_url:
                        parts = raw_url.split("|", 1)
                        raw_url = parts[0].strip()
                        h_part = parts[1]
                        if "Referer=" in h_part: stream["referer"] = h_part.split("Referer=")[1].split("&")[0].strip()
                        if "Origin=" in h_part: stream["origin"] = h_part.split("Origin=")[1].split("&")[0].strip()
                        if "Cookie=" in h_part: stream["cookie"] = h_part.split("Cookie=")[1].split("&")[0].strip()
                        if "User-Agent=" in h_part: stream["user_agent"] = h_part.split("User-Agent=")[1].split("&")[0].strip()
                    
                    stream["url"] = raw_url

        if stream["url"]:
            streams.append(stream)

    return streams

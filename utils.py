# ফাইল: utils.py
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
        
        # 🎯 ফিক্স ১: কোটেশন (" ") সহ এবং ছাড়া সব ধরনের গ্রুপ ও লোগো ধরবে
        if g_match := re.search(r'group-title=(?:"([^"]+)"|([^\s,]+))', extinf_line, re.IGNORECASE): 
            stream["group"] = (g_match.group(1) or g_match.group(2)).strip()
            
        if l_match := re.search(r'tvg-logo=(?:"([^"]+)"|([^\s,]+))', extinf_line, re.IGNORECASE): 
            stream["logo"] = (l_match.group(1) or l_match.group(2)).strip()
        
        # 🎯 ফিক্স ২: টাইটেল থেকে সব হাবিজাবি ট্যাগ পুরোপুরি মুছে ফেলা
        clean_title = re.sub(r'[a-zA-Z0-9\-]+=(?:"[^"]*"|[^\s,]+)', '', extinf_line) # সব tvg- ট্যাগ মুছবে
        clean_title = re.sub(r'^:[-0-9\s]+', '', clean_title) # শুরুর :-1 মুছবে
        clean_title = clean_title.lstrip(', ') # শুরুর কমা মুছবে
        stream["title"] = clean_title.strip() if clean_title.strip() else f"Live Stream {idx}"

        # রেগুলার অপশন এক্সট্রাক্ট
        if ref_m := re.search(r"#EXTVLCOPT:http-referrer=([^#\n]+)", block, re.IGNORECASE): stream["referer"] = ref_m.group(1).strip()
        if orig_m := re.search(r"#EXTVLCOPT:http-origin=([^#\n]+)", block, re.IGNORECASE): stream["origin"] = orig_m.group(1).strip()
        if cookie_m := re.search(r"#EXTVLCOPT:http-cookie=([^#\n]+)", block, re.IGNORECASE): stream["cookie"] = cookie_m.group(1).strip()
        if ua_m := re.search(r"#EXTVLCOPT:http-user-agent=([^#\n]+)", block, re.IGNORECASE): stream["user_agent"] = ua_m.group(1).strip()

        # JSON ফরম্যাট থেকে এক্সট্রাক্ট
        json_m = re.search(r"#EXTHTTP:(\{.*?\})", block, re.IGNORECASE)
        if json_m:
            try:
                j_data = {k.lower(): v for k, v in json.loads(json_m.group(1)).items()}
                if "cookie" in j_data: stream["cookie"] = str(j_data["cookie"]).strip()
                if "referer" in j_data: stream["referer"] = str(j_data["referer"]).strip()
                if "origin" in j_data: stream["origin"] = str(j_data["origin"]).strip()
                if "user-agent" in j_data: stream["user_agent"] = str(j_data["user-agent"]).strip()
            except Exception: pass

        # URL এক্সট্রাক্ট
        urls = re.findall(r"(https?://[^\s#]+)", block.replace(stream["logo"], ""))
        if urls:
            playback_url = urls[-1].strip()
            if "|" in playback_url:
                parts = playback_url.split("|", 1)
                playback_url, h_part = parts[0].strip(), parts[1]
                if p_ref := re.search(r"Referer=([^&]+)", h_part, re.IGNORECASE): stream["referer"] = p_ref.group(1).strip()
                if p_orig := re.search(r"Origin=([^&]+)", h_part, re.IGNORECASE): stream["origin"] = p_orig.group(1).strip()
                if p_cookie := re.search(r"Cookie=([^&]+)", h_part, re.IGNORECASE): stream["cookie"] = p_cookie.group(1).strip()
                if p_ua := re.search(r"User-Agent=([^&]+)", h_part, re.IGNORECASE): stream["user_agent"] = p_ua.group(1).strip()

            stream["url"] = playback_url
            streams.append(stream)

    return streams

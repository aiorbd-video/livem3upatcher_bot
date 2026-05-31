import hashlib
import time
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

def parse_m3u_playlist(content: str):
    streams = []
    
    # 🎯 হ্যাং হওয়া এড়াতে সব ভারী রেগেক্স বাদ! শুধু স্প্লিট ব্যবহার করা হয়েছে
    content = content.replace("#EXTINF", "\n#EXTINF")
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    
    current_stream = None

    for line in lines:
        if line.startswith("#EXTM3U") or line.startswith("#TOTAL") or line.startswith("#LAST"):
            continue
            
        if line.startswith("#EXTINF"):
            # আগের স্ট্রিম সেভ করা
            if current_stream and current_stream.get("url"):
                streams.append(current_stream)
                
            current_stream = {"title": "Unknown", "group": "লাইভ টিভি", "logo": "", "referer": "", "origin": "", "cookie": "", "user_agent": "", "url": ""}
            
            # সেফ গ্রুপ এবং লোগো এক্সট্রাকশন (No Hang Guarantee)
            if 'group-title="' in line:
                current_stream["group"] = line.split('group-title="')[1].split('"')[0]
            elif "group-title=" in line:
                current_stream["group"] = line.split('group-title=')[1].split()[0].split(',')[0]
                
            if 'tvg-logo="' in line:
                current_stream["logo"] = line.split('tvg-logo="')[1].split('"')[0]
            elif "tvg-logo=" in line:
                current_stream["logo"] = line.split('tvg-logo=')[1].split()[0].split(',')[0]

            # সেফ টাইটেল এক্সট্রাকশন (লোগোর লিংকের কমা বাইপাস)
            if '",' in line:
                raw_title = line.split('",')[-1].strip()
            else:
                raw_title = line.split(',', 1)[-1].strip() if ',' in line else "Live Stream"
            
            current_stream["title"] = raw_title
            
        elif current_stream: # যদি EXTINF ব্লকের ভেতরে থাকি
            if line.startswith("#EXTVLCOPT:http-referrer="):
                current_stream["referer"] = line.split("=", 1)[1].strip()
            elif line.startswith("#EXTVLCOPT:http-origin="):
                current_stream["origin"] = line.split("=", 1)[1].strip()
            elif line.startswith("#EXTVLCOPT:http-cookie="):
                current_stream["cookie"] = line.split("=", 1)[1].strip()
            elif line.startswith("#EXTVLCOPT:http-user-agent="):
                current_stream["user_agent"] = line.split("=", 1)[1].strip()
            
            # Toffee / SonyLiv JSON HTTP Header Handle (Fast string search)
            elif line.startswith("#EXTHTTP:"):
                try:
                    json_str = line.split("#EXTHTTP:")[1]
                    if "}http" in json_str:
                        j_part, url_part = json_str.split("}http", 1)
                        j_part += "}" 
                        url_part = "http" + url_part
                        
                        import json
                        h_data = {k.lower(): v for k, v in json.loads(j_part).items()}
                        if "cookie" in h_data: current_stream["cookie"] = str(h_data["cookie"])
                        if "referer" in h_data: current_stream["referer"] = str(h_data["referer"])
                        if "origin" in h_data: current_stream["origin"] = str(h_data["origin"])
                        if "user-agent" in h_data: current_stream["user_agent"] = str(h_data["user-agent"])
                        
                        current_stream["url"] = url_part.strip()
                except Exception as e:
                    print(f"JSON Parse Error: {e}")
                    
            elif line.startswith("http"):
                playback_url = line
                if "|" in playback_url:
                    parts = playback_url.split("|", 1)
                    playback_url = parts[0].strip()
                    h_part = parts[1]
                    
                    if "Referer=" in h_part: current_stream["referer"] = h_part.split("Referer=")[1].split("&")[0].strip()
                    if "Origin=" in h_part: current_stream["origin"] = h_part.split("Origin=")[1].split("&")[0].strip()
                    if "Cookie=" in h_part: current_stream["cookie"] = h_part.split("Cookie=")[1].split("&")[0].strip()
                    if "User-Agent=" in h_part: current_stream["user_agent"] = h_part.split("User-Agent=")[1].split("&")[0].strip()
                    
                current_stream["url"] = playback_url

    # শেষের স্ট্রিমটি যুক্ত করা
    if current_stream and current_stream.get("url"):
        streams.append(current_stream)

    return streams

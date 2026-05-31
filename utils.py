import hashlib
import time
import json
from config import START_TIME, HEADERS

def parse_m3u_playlist(content: str):
    streams = []
    # কোনো রেগেক্স ছাড়াই একদম সরাসরি লাইন পার্সিং
    lines = content.splitlines()
    current_stream = None

    for line in lines:
        line = line.strip()
        if not line: continue
        
        if line.startswith("#EXTINF"):
            current_stream = {"title": "Unknown", "group": "লাইভ টিভি", "logo": "", "referer": "", "origin": "", "cookie": "", "user_agent": "", "url": ""}
            # গ্রুপ ও লোগো
            if 'group-title="' in line: current_stream["group"] = line.split('group-title="')[1].split('"')[0]
            if 'tvg-logo="' in line: current_stream["logo"] = line.split('tvg-logo="')[1].split('"')[0]
            # টাইটেল
            current_stream["title"] = line.split(',')[-1].split('http')[0].strip()
            
        elif current_stream:
            # হেডারগুলো খুঁজে বের করা
            if "#EXTVLCOPT:http-referrer=" in line: current_stream["referer"] = line.split("=", 1)[1].strip()
            elif "#EXTVLCOPT:http-origin=" in line: current_stream["origin"] = line.split("=", 1)[1].strip()
            elif "#EXTVLCOPT:http-cookie=" in line: current_stream["cookie"] = line.split("=", 1)[1].strip()
            elif "#EXTVLCOPT:http-user-agent=" in line: current_stream["user_agent"] = line.split("=", 1)[1].strip()
            
            # JSON Header Handling
            elif "#EXTHTTP:{" in line:
                try:
                    j = json.loads(line.split("#EXTHTTP:")[1])
                    current_stream["cookie"] = str(j.get("cookie", ""))
                    current_stream["referer"] = str(j.get("referer", ""))
                    current_stream["origin"] = str(j.get("origin", ""))
                    current_stream["user_agent"] = str(j.get("user-agent", ""))
                except: pass
            
            # URL এবং Pipe হ্যান্ডলিং
            elif line.startswith("http"):
                url_part = line.split("|")[0].strip()
                current_stream["url"] = url_part
                
                # পাইপলাইনের ভেতরের হেডার
                if "|" in line:
                    h_part = line.split("|", 1)[1]
                    for p in h_part.split("&"):
                        if "Referer=" in p: current_stream["referer"] = p.split("=")[1]
                        if "Cookie=" in p: current_stream["cookie"] = p.split("=")[1]
                        if "User-Agent=" in p: current_stream["user_agent"] = p.split("=")[1]
                
                streams.append(current_stream)
                current_stream = None
    return streams

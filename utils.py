import re
import json

def parse_m3u_playlist(content: str):
    """
    Universal Enterprise M3U Parser
    যেকোনো বিকৃত, জোড়া লাগানো বা কাস্টম হেডার যুক্ত M3U ফাইল পার্স করতে সক্ষম (No ReDoS, Zero Hang)
    """
    streams = []
    
    # ১. গারবেজ ক্লিনিং (অপ্রয়োজনীয় ট্যাগ মুছে ফেলা)
    content = content.replace("#TOTAL-VS-MATCHES:", "\n")
    content = content.replace("#LAST-UPDATED:", "\n")
    content = content.replace("#EXTM3U", "")
    
    # ২. ব্লক স্প্লিটিং (প্রতিটি স্ট্রিমকে আলাদা ব্লকে ভাগ করা)
    blocks = content.split("#EXTINF")
    
    for idx, block in enumerate(blocks):
        if not block.strip():
            continue
            
        stream = {
            "title": f"Unknown Stream {idx}",
            "group": "লাইভ টিভি",
            "logo": "",
            "referer": "",
            "origin": "",
            "cookie": "",
            "user_agent": "",
            "url": ""
        }
        
        # ৩. মেটাডেটা এবং প্লেব্যাক লিংকের বাউন্ডারি আলাদা করা
        # এটি লোগো বা টাইটেলকে URL বা হেডারের সাথে মিক্স হতে দেবে না
        boundary_match = re.search(r'(#EXTVLCOPT:|#EXTHTTP:|https?://)', block)
        
        if boundary_match:
            meta_part = block[:boundary_match.start()]
            rest_part = block[boundary_match.start():]
        else:
            meta_part = block
            rest_part = ""
            
        # ==========================================
        # 🟢 স্টেজ ১: মেটাডেটা পার্সিং (লোগো, গ্রুপ, টাইটেল)
        # ==========================================
        if 'group-title="' in meta_part:
            stream["group"] = meta_part.split('group-title="')[1].split('"')[0].strip()
        elif 'group-title=' in meta_part:
            stream["group"] = meta_part.split('group-title=')[1].split()[0].split(',')[0].strip()
            
        if 'tvg-logo="' in meta_part:
            stream["logo"] = meta_part.split('tvg-logo="')[1].split('"')[0].strip()
            
        if ',' in meta_part:
            # লোগোর ভেতর কমা থাকলে তা সেফলি বাইপাস করা
            if '",' in meta_part:
                raw_title = meta_part.split('",')[-1].strip()
            else:
                raw_title = meta_part.split(',', 1)[-1].strip()
            
            # টাইটেলের শুরুতে থাকা ফালতু ক্যারেক্টার ক্লিন করা
            raw_title = re.sub(r'^[:\- \t]+', '', raw_title)
            if raw_title:
                stream["title"] = raw_title

        # ==========================================
        # 🔵 স্টেজ ২: হেডার এবং URL আন-গ্লুয়িং (Un-gluing)
        # ==========================================
        # জোড়া লাগানো ফাইলকে প্রসেসিংয়ের জন্য সোজা করা হচ্ছে
        rest_part = rest_part.replace("#EXTVLCOPT:", "\n#EXTVLCOPT:")
        rest_part = rest_part.replace("#EXTHTTP:", "\n#EXTHTTP:")
        
        # যদি URL অন্য কোনো লেখার সাথে জোড়া লাগানো থাকে (যেমন: JSON এর '}' বা টাইটেল), তবে তা আলাদা করা
        rest_part = re.sub(r'([^\s])(https?://)', r'\1\n\2', rest_part)
        
        rest_lines = [line.strip() for line in rest_part.splitlines() if line.strip()]
        
        # ==========================================
        # 🟠 স্টেজ ৩: হেডার এবং টোকেন এক্সট্রাকশন
        # ==========================================
        for line in rest_lines:
            if line.startswith("#EXTVLCOPT:"):
                opt = line.split(":", 1)[1]
                if "=" in opt:
                    key, val = opt.split("=", 1)
                    key = key.strip().lower()
                    val = val.strip()
                    if key == "http-referrer": stream["referer"] = val
                    elif key == "http-origin": stream["origin"] = val
                    elif key == "http-cookie": stream["cookie"] = val
                    elif key == "http-user-agent": stream["user_agent"] = val
                    
            elif line.startswith("#EXTHTTP:"):
                try:
                    j_str = line.split("#EXTHTTP:")[1]
                    # JSON ডিকশনারি কেস-ইনসেনসিটিভ করা হচ্ছে
                    h_data = {k.lower(): v for k, v in json.loads(j_str).items()}
                    if "cookie" in h_data: stream["cookie"] = str(h_data["cookie"]).strip()
                    if "referer" in h_data: stream["referer"] = str(h_data["referer"]).strip()
                    if "origin" in h_data: stream["origin"] = str(h_data["origin"]).strip()
                    if "user-agent" in h_data: stream["user_agent"] = str(h_data["user-agent"]).strip()
                except Exception:
                    pass
                    
            elif line.startswith("http"):
                raw_url = line
                # পাইপ (|) সিনট্যাক্স সাপোর্ট
                if "|" in raw_url:
                    u_part, h_part = raw_url.split("|", 1)
                    raw_url = u_part.strip()
                    
                    if p_ref := re.search(r'Referer=([^&]+)', h_part, re.IGNORECASE): stream["referer"] = p_ref.group(1).strip()
                    if p_orig := re.search(r'Origin=([^&]+)', h_part, re.IGNORECASE): stream["origin"] = p_orig.group(1).strip()
                    if p_cookie := re.search(r'Cookie=([^&]+)', h_part, re.IGNORECASE): stream["cookie"] = p_cookie.group(1).strip()
                    if p_ua := re.search(r'User-Agent=([^&]+)', h_part, re.IGNORECASE): stream["user_agent"] = p_ua.group(1).strip()
                
                stream["url"] = raw_url
                
        # ফাইনাল চেক: লিংক থাকলেই কেবল সিস্টেমে যুক্ত হবে
        if stream["url"]:
            streams.append(stream)
            
    return streams

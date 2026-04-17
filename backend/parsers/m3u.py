import re
from typing import List

def parse_m3u_content(content: str) -> List[dict]:
    """Parse M3U/M3U8 content into a list of track dicts."""
    tracks = []
    lines = content.splitlines()
    current_info = None

    for line in lines:
        line = line.strip()
        if not line or line == "#EXTM3U":
            continue

        if line.startswith("#EXTINF:"):
            # #EXTINF:-1,Artist - Title
            match = re.match(r"#EXTINF:[^,]*,(.+)", line)
            if match:
                info = match.group(1).strip()
                current_info = parse_extinf_title(info)
        elif not line.startswith("#"):
            # This is the file path / URL
            if current_info:
                current_info["path"] = line
                tracks.append(current_info)
                current_info = None
            else:
                # No EXTINF, try to parse from filename
                tracks.append({"artist": None, "title": line, "album": None, "path": line})

    return tracks

def parse_extinf_title(title: str) -> dict:
    """Try to parse 'Artist - Title' format from EXTINF title."""
    if " - " in title:
        parts = title.split(" - ", 1)
        return {"artist": parts[0].strip(), "title": parts[1].strip(), "album": None}
    return {"artist": None, "title": title.strip(), "album": None}


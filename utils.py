# /Users/roman/work/itter/utils.py
import re
import hashlib
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict
from config import ITTER_DEBUG_MODE, IP_HASH_SALT

# --- ANSI Escape Codes ---
# Reset
RESET = "\033[0m"
# Styles
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m" # Might not work everywhere
UNDERLINE = "\033[4m"
BLINK = "\033[5m"
# Colors (Foreground)
FG_BLACK = "\033[30m"
FG_RED = "\033[31m"
FG_GREEN = "\033[32m"
FG_YELLOW = "\033[33m"
FG_BLUE = "\033[34m"
FG_MAGENTA = "\033[35m"
FG_CYAN = "\033[36m"
FG_WHITE = "\033[37m"
FG_BRIGHT_BLACK = "\033[90m" # Often used for Grey/Dim
FG_BRIGHT_RED = "\033[91m"
FG_BRIGHT_GREEN = "\033[92m"
FG_BRIGHT_YELLOW = "\033[93m"
FG_BRIGHT_BLUE = "\033[94m"
FG_BRIGHT_MAGENTA = "\033[95m"
FG_BRIGHT_CYAN = "\033[96m"
FG_BRIGHT_WHITE = "\033[97m"

# --- Logging ---
def debug_log(msg: str) -> None:
    if ITTER_DEBUG_MODE:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{timestamp} DEBUG] {msg}")

# --- Time Formatting ---
def time_ago(iso_str: Optional[str]) -> str:
    # ... (keep existing time_ago function) ...
    if not iso_str: return "some time ago"
    parsed_dt = None
    if isinstance(iso_str, datetime): parsed_dt = iso_str
    else:
        try:
            iso_str_cleaned = iso_str.split(".")[0].replace("Z", "+00:00")
            if "+" not in iso_str_cleaned: iso_str_cleaned += "+00:00"
            parsed_dt = datetime.fromisoformat(iso_str_cleaned)
        except ValueError: debug_log(f"time_ago parse error for: {iso_str}"); return "a while ago"
    if not parsed_dt.tzinfo: parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
    diff = datetime.now(timezone.utc) - parsed_dt; seconds = int(diff.total_seconds())
    if seconds < 10: return "just now";
    if seconds < 60: return f"{seconds}s ago";
    minutes = seconds // 60;
    if minutes < 60: return f"{minutes}m ago";
    hours = minutes // 60;
    if hours < 24: return f"{hours}h ago";
    days = hours // 24;
    if days < 7: return f"{days}d ago";
    weeks = days // 7;
    if weeks < 5: return f"{weeks}w ago";
    months = days // 30;
    if months < 12: return f"{months}mo ago";
    years = days // 365; return f"{years}y ago"


# --- Input Parsing ---
CMD_SPLIT_RE = re.compile(r"^\s*(\S+)(?:\s+(.*))?$")
HASHTAG_RE = re.compile(r"(?<!\w)#(\w(?:[\w-]*\w)?)")
USER_RE = re.compile(r"(?<!\w)@(\w{3,20})")

def parse_input_line(line: str) -> Tuple[Optional[str], str, List[str], List[str]]:
    # ... (keep existing parse_input_line function) ...
    m = CMD_SPLIT_RE.match(line.strip());
    if not m: return None, "", [], [];
    cmd = m.group(1).lower(); raw_text = m.group(2) or "";
    hashtags = list(set(HASHTAG_RE.findall(raw_text.lower()))); user_refs = list(set(USER_RE.findall(raw_text)));
    return cmd, raw_text, hashtags, user_refs

def parse_target_filter(raw_text: str) -> Dict[str, Optional[str]]:
    # ... (keep existing parse_target_filter function from v13) ...
    text = raw_text.strip().lower();
    if not text or text == "all": return {"type": "all", "value": None};
    if text == "mine": return {"type": "mine", "value": None};
    if text.startswith("#"):
        channel_name = text[1:];
        if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]*$", channel_name): return {"type": "channel", "value": channel_name};
        else: debug_log(f"Invalid channel format '{text}', defaulting to 'all'"); return {"type": "all", "value": None};
    debug_log(f"Unrecognized filter '{text}', defaulting to 'all'"); return {"type": "all", "value": None}


# --- Eet Content Formatting ---
def format_eet_content(content: str) -> str:
    """Applies ANSI color codes to all hashtags and mentions in eet content."""

    # Define the combined regex pattern string directly inside the function.
    # This pattern will find either a hashtag or a username.
    # Group 1: '#' (if hashtag)
    # Group 2: hashtag text (if hashtag)
    # Group 3: '@' (if username)
    # Group 4: username text (if username)
    combined_pattern_str = r"(?<!\w)(?:(#)(\w(?:[\w-]*\w)?)|(@)(\w{3,20}))"

    # Replacer that decides what to do with each match.
    def replacer(match_obj: re.Match) -> str:
        if match_obj.group(1) == "#":  # It's a hashtag
            hashtag_char = match_obj.group(1)  # The '#'
            hashtag_text = match_obj.group(2)  # The tag content
            # FG_MAGENTA and RESET are from your global definitions
            return f"{FG_MAGENTA}{hashtag_char}{hashtag_text}{RESET}"
        elif match_obj.group(3) == "@":  # It's a username
            mention_char = match_obj.group(3)  # The '@'
            mention_text = match_obj.group(4)  # The username
            # FG_CYAN and RESET are from your global definitions
            return f"{FG_CYAN}{mention_char}{mention_text}{RESET}"
        # This fallback should ideally not be reached if the pattern is correct
        # and only matches what we intend for hashtags or usernames.
        return match_obj.group(0)

    # Perform the substitution using the combined pattern and the local replacer.
    # This replaces your two sequential .sub() calls.
    highlighted_content = re.sub(combined_pattern_str, replacer, content)

    return highlighted_content


# --- IP Hashing ---
def hash_ip(ip_address: str) -> Optional[str]:
    if not IP_HASH_SALT:
        debug_log("IP_HASH_SALT is not set. Cannot hash IP.")
        return None
    if not ip_address:
        debug_log("No IP address provided to hash.")
        return None
    try:
        salted_ip = IP_HASH_SALT + ip_address
        hashed_ip = hashlib.sha256(salted_ip.encode("utf-8")).hexdigest()
        return hashed_ip
    except Exception as e:
        debug_log(f"Error hashing IP address {ip_address}: {e}")
        return None

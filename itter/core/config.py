# /config.py
import os
import sys
from dotenv import load_dotenv
import importlib.resources

# --- Resources ---
BANNER_FILE = importlib.resources.files("itter.resources").joinpath("itter_banner.txt")

# --- Constants ---
EET_MAX_LENGTH = 180
SSH_HOST_KEY_PATH = "./ssh_host_key"
MIN_TIMELINE_PAGE_SIZE = 1
MAX_TIMELINE_PAGE_SIZE = 30
DEFAULT_TIMELINE_PAGE_SIZE = 10
WATCH_REFRESH_INTERVAL_SECONDS = 15
SIDEBAR_WIDTH = 25
SIDEBAR_SCROLL_STEP = 3

# --- Environment Loading ---
load_dotenv(override=True)

ITTER_DEBUG_MODE = os.getenv("ITTER_DEBUG_MODE", "False").lower() == "true"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # Should be SERVICE_ROLE key
SUPABASE_WSURL = os.getenv("SUPABASE_WSURL")
SSH_HOST = os.getenv("SSH_HOST", "0.0.0.0")
SSH_PORT = int(os.getenv("SSH_PORT", "8022"))
IP_HASH_SALT = os.getenv("IP_HASH_SALT")


# --- Validation ---
def validate_config():
    missing_env = []
    for var, name in [
        (SUPABASE_URL, "SUPABASE_URL"),
        (SUPABASE_KEY, "SUPABASE_KEY"),
        (SUPABASE_WSURL, "SUPABASE_WSURL"),
        (IP_HASH_SALT, "IP_HASH_SALT"),
    ]:
        if not var:
            missing_env.append(name)

    if missing_env:
        sys.stderr.write(
            f"[FATAL ERROR] Missing environment variables: {', '.join(missing_env)}\n"
        )
        sys.exit(1)

    if not os.path.exists(SSH_HOST_KEY_PATH):
        sys.stderr.write(
            f"[FATAL ERROR] SSH host key not found at {SSH_HOST_KEY_PATH}\n"
        )
        sys.stderr.write(
            f'Generate it using: ssh-keygen -t ed25519 -f {SSH_HOST_KEY_PATH} -N ""\n'
        )
        sys.exit(1)

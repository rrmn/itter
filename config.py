# /Users/roman/work/itter/config.py
import os
import sys
from dotenv import load_dotenv

# --- Constants ---
BANNER_FILE = "itter_banner.txt"
EET_MAX_LENGTH = 180
SSH_HOST_KEY_PATH = "./ssh_host_key"
DEFAULT_TIMELINE_PAGE_SIZE = 10
WATCH_REFRESH_INTERVAL_SECONDS = 15  # How often watch mode refreshes

# --- Environment Loading ---
load_dotenv(override=True)

ITTER_DEBUG_MODE = os.getenv("ITTER_DEBUG_MODE", "False").lower() == "true"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # Should be SERVICE_ROLE key
SUPABASE_WSURL = os.getenv("SUPABASE_WSURL")
SSH_HOST = os.getenv("SSH_HOST", "0.0.0.0")
SSH_PORT = int(os.getenv("SSH_PORT", "8022"))


# --- Validation ---
def validate_config():
    missing_env = []
    for var, name in [
        (SUPABASE_URL, "SUPABASE_URL"),
        (SUPABASE_KEY, "SUPABASE_KEY"),
        (SUPABASE_WSURL, "SUPABASE_WSURL"),
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

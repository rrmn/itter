import logging

from dotenv import load_dotenv
from pydantic import ValidationError
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


# --- Environment Loading ---
class Config(BaseSettings):
    """Main config class."""

    banner_file: str = "itter_banner.txt"
    eet_max_length: int = 180
    ssh_host_key_path: str = "./ssh_host_key"
    min_timeline_page_size: int = 1
    max_timeline_page_size: int = 30
    default_timeline_page_size: int = 10
    watch_refresh_interval_seconds: int = 15  # How often watch mode refreshes
    itter_debug_mode: bool
    supabase_url: str
    supabase_key: str
    supabase_wsurl: str
    ssh_host: str = "0.0.0.0"
    ssh_port: str = "8022"
    ip_hash_salt: str


_ = load_dotenv(override=True)


# --- Validation ---
def validate_config() -> None:
    try:
        config: Config = Config()
    except ValidationError:
        logger.exception("[FATAL ERROR] Missing environment variables")
        raise

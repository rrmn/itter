from contextvars import ContextVar
from pathlib import Path

from dotenv import load_dotenv
from realtime import AsyncRealtimeClient
from supabase import Client

from itter.config import Config

_ = load_dotenv(Path(__file__).parent.parent.parent.joinpath(".env"))
config: Config = Config()
db_client_ctx: ContextVar[Client] = ContextVar("db_client")
rt_client_ctx: ContextVar[AsyncRealtimeClient] = ContextVar("rt_client")

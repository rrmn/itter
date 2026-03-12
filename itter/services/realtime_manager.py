# /realtime_manager.py
import asyncio
import sys
from typing import Dict, Any, Optional, TYPE_CHECKING, cast
from realtime import RealtimeSubscribeStates
from realtime._async.client import AsyncRealtimeClient

from realtime.types import PostgresChangesPayload, RealtimePostgresChangesListenEvent

from itter.core.utils import debug_log

if TYPE_CHECKING:
    from itter.ssh.shell import ItterShell

rt_client: Optional[AsyncRealtimeClient] = None
active_sessions_ref: Optional[Dict[str, "ItterShell"]] = None


def init_realtime(
    client: AsyncRealtimeClient, sessions_dict: Dict[str, "ItterShell"]
) -> None:
    global rt_client, active_sessions_ref
    rt_client = client
    active_sessions_ref = sessions_dict
    debug_log("Realtime manager module initialized.")


def handle_global_new_post_event(payload: PostgresChangesPayload) -> None:
    if not active_sessions_ref or not isinstance(payload, dict):
        return

    event_type = payload.get("type") or payload.get("eventType")
    table_name = payload.get("table")

    if event_type == "INSERT" and table_name == "posts":
        new_post_record = payload.get("new") or payload.get("record")

        if not new_post_record:
            debug_log(
                "Realtime global: Event received, but no record data found in payload."
            )
            return

        try:
            loop = asyncio.get_running_loop()
            for username, session_instance in list(active_sessions_ref.items()):
                if getattr(session_instance, "_is_watching_timeline", False):
                    loop.create_task(
                        session_instance.handle_new_post_realtime(new_post_record)
                    )
        except RuntimeError as e:
            debug_log(f"Realtime event loop error: {e}")


async def start_realtime() -> None:
    if not rt_client:
        raise RuntimeError("Realtime client not initialized")

    try:
        await rt_client.connect()
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] Realtime connect failed: {e}\n")
        sys.exit(1)

    realtime_posts_channel = rt_client.channel("itter:posts_feed")

    event_type = cast(RealtimePostgresChangesListenEvent, "INSERT")

    realtime_posts_channel.on_postgres_changes(
        event=event_type,
        schema="public",
        table="posts",
        callback=handle_global_new_post_event,
    )

    def rt_subscribe_callback(status: RealtimeSubscribeStates, err: Any = None) -> None:
        if status == RealtimeSubscribeStates.SUBSCRIBED:
            debug_log("Successfully subscribed to new post events.")
        elif status in [
            RealtimeSubscribeStates.CHANNEL_ERROR,
            RealtimeSubscribeStates.TIMED_OUT,
        ]:
            sys.stderr.write(f"[ERROR] Realtime subscription failed: {status}, {err}\n")

    try:
        await realtime_posts_channel.subscribe(rt_subscribe_callback)
    except Exception as e:
        sys.stderr.write(f"[ERROR] Realtime channel subscribe error: {e}\n")

    async def _listen_task() -> None:
        if rt_client:
            await rt_client.listen()

    try:
        asyncio.create_task(_listen_task())
        debug_log("Realtime listener started in background task.")
    except Exception as ex:
        debug_log(f"Realtime listen error: {ex}. Realtime features might be affected.")

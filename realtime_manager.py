# /realtime_manager.py
import asyncio
import sys
from typing import Dict, Any, Optional, TYPE_CHECKING

from realtime import AsyncRealtimeClient, RealtimeSubscribeStates
from utils import debug_log
if TYPE_CHECKING:
    from ssh.shell import ItterShell

# Placeholder for the client - will be initialized in main.py
rt_client: Optional[AsyncRealtimeClient] = None

active_sessions_ref: Optional[Dict[str, "ItterShell"]] = None

def init_realtime(client: AsyncRealtimeClient, sessions_dict: Dict[str, "ItterShell"]):
    """Initializes the realtime module with the client and session reference."""
    global rt_client, active_sessions_ref
    rt_client = client
    active_sessions_ref = sessions_dict
    debug_log("Realtime manager module initialized.")


async def handle_global_new_post_event(payload: Dict[str, Any]):
    """Callback for new post inserts from Supabase Realtime."""
    if not active_sessions_ref:
        debug_log("Realtime handler called but active_sessions_ref is None.")
        return

    debug_log(f"Realtime global: New post event payload: {payload}")
    if payload.get("type") == "INSERT" and payload.get("table") == "posts":
        new_post_record = payload.get("new")
        if not new_post_record:
            debug_log("Realtime global: No 'new' record data found in INSERT payload.")
            return

        # Iterate safely over a copy of the session keys/values
        for username, session_instance in list(active_sessions_ref.items()):
            # Check if the session object has the method and is watching
            # Use getattr for safer access
            is_watching = getattr(session_instance, "_is_watching_timeline", False)
            handler_method = getattr(session_instance, "handle_new_post_realtime", None)

            if is_watching and callable(handler_method):
                # Let the session instance handle relevance checking and rendering
                asyncio.create_task(handler_method(new_post_record))
            elif not is_watching:
                pass  # User is not watching, no action needed
            elif not callable(handler_method):
                debug_log(
                    f"Session object for {username} lacks callable handle_new_post_realtime method"
                )

    else:
        debug_log(
            f"Realtime global: Received non-INSERT event or event for different table: {payload.get('type')}, table: {payload.get('table')}"
        )


async def start_realtime():
    """Connects to Realtime, sets up subscriptions, and starts listening."""
    if not rt_client:
        raise RuntimeError("Realtime client not initialized")

    debug_log("Connecting to Supabase Realtime...")
    try:
        await rt_client.connect()
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] Realtime connect failed: {e}\n")
        sys.exit(1)  # Exit if connection fails

    # Use a specific channel name (can be anything descriptive)
    realtime_posts_channel = rt_client.channel("itter:posts_feed")

    realtime_posts_channel.on_postgres_changes(
        event="INSERT",
        schema="public",
        table="posts",
        callback=handle_global_new_post_event,  # Use the handler defined above
    )

    def rt_subscribe_callback(status: RealtimeSubscribeStates, err=None):
        debug_log(
            f"[Realtime Posts Channel] Subscription status: {status}, error: {err}"
        )
        if status == RealtimeSubscribeStates.SUBSCRIBED:
            debug_log("Successfully subscribed to new post events.")
        elif status in [
            RealtimeSubscribeStates.CHANNEL_ERROR,
            RealtimeSubscribeStates.TIMED_OUT,
        ]:
            sys.stderr.write(f"[ERROR] Realtime subscription failed: {status}, {err}\n")

    try:
        debug_log("Subscribing to Realtime posts channel...")
        await realtime_posts_channel.subscribe(rt_subscribe_callback)
    except Exception as e:
        sys.stderr.write(
            f"[ERROR] Realtime channel subscribe error: {e}\n"
        )  # Changed to ERROR level
        debug_log(f"Realtime subscription failed, live updates may be impaired: {e}")

    # Start listening in the background
    try:
        asyncio.create_task(rt_client.listen())
        debug_log("Realtime listener started in background task.")
    except Exception as ex:
        debug_log(f"Realtime listen error: {ex}. Realtime features might be affected.")

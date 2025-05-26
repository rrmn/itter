# /realtime_manager.py
import asyncio
import logging
import sys
from typing import Dict, Any, Optional

# Import from the actual library now that the name conflict is resolved
from realtime import AsyncRealtimeClient, RealtimeSubscribeStates
from itter.utils import debug_log
from itter.context import rt_client_ctx
# Import the specific type hint for the shell if needed for type checking within this file
# from ssh_server import ItterShell # <--- Uncomment if you need detailed type checking

# Placeholder for active sessions - will be passed in (use Any for now to avoid circular import if ItterShell not imported)
active_sessions_ref: Optional[Dict[str, Any]] = None

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s] - %(asctime)s - %(name)s - %(funcName)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


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
    if not rt_client_ctx:
        raise RuntimeError("Realtime client not initialized")

    logger.debug("Connecting to Supabase Realtime")
    try:
        await rt_client_ctx.get().connect()
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] Realtime connect failed: {e}\n")
        sys.exit(1)  # Exit if connection fails

    # Use a specific channel name (can be anything descriptive)
    realtime_posts_channel = rt_client_ctx.get().channel("itter:posts_feed")

    realtime_posts_channel.on_postgres_changes(
        event="INSERT",
        schema="public",
        table="posts",
        callback=handle_global_new_post_event,  # Use the handler defined above
    )

    def rt_subscribe_callback(status: RealtimeSubscribeStates, err=None):
        logger.debug(
            "[Realtime Posts Channel] Subscription status: %s, %s", status, err
        )
        if status == RealtimeSubscribeStates.SUBSCRIBED:
            logger.debug("Successfully subscribed to new post events.")
        elif status in [
            RealtimeSubscribeStates.CHANNEL_ERROR,
            RealtimeSubscribeStates.TIMED_OUT,
        ]:
            sys.stderr.write(f"[ERROR] Realtime subscription failed: {status}, {err}\n")

    try:
        logger.debug("Subscribing to Realtime posts channel...")
        await realtime_posts_channel.subscribe(rt_subscribe_callback)
    except Exception as e:
        sys.stderr.write(
            f"[ERROR] Realtime channel subscribe error: {e}\n"
        )  # Changed to ERROR level
        logger.exception("Realtime subscription failed, live updates may be impaired.")

    # Start listening in the background
    try:
        asyncio.create_task(rt_client_ctx.get().listen())
        logger.debug("Realtime listener started in background task.")
    except Exception as ex:
        logger.exception("Realtime listen error; realtime features might be affected.")

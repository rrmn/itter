import asyncio
import logging
import sys
import traceback

import typer
from realtime import AsyncRealtimeClient  # Need this for type hint
from supabase import Client, create_client  # Need Client for type hint

from itter import database, realtime_manager, ssh_server

# Import our refactored modules
from itter.context import config, db_client_ctx, rt_client_ctx

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s] - %(asctime)s - %(name)s - %(funcName)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
# --- Global State ---
# Use forward reference for ItterShell type hint
active_sessions: dict[str, ssh_server.ItterShell] = {}


# --- Initialization ---
def initialize_clients() -> None:
    """Initialize Supabase and Realtime clients."""
    try:
        logger.debug("Creating Supabase client...")
        supabase_client: Client = create_client(
            supabase_url=config.supabase_url,
            supabase_key=config.supabase_key,
        )
        db_client_ctx.set(supabase_client)
    except Exception:  # TODO: exception handling
        logger.exception("Unable to create Supabase client")
        sys.exit(1)

    try:
        logger.debug("Creating Realtime client...")
        # Ensure realtime client is created correctly
        rt_client: AsyncRealtimeClient = AsyncRealtimeClient(
            config.supabase_wsurl,
            config.supabase_key,
        )
        rt_client_ctx.set(rt_client)
        logger.debug("Realtime client created and Realtime manager module intialized.")
    except Exception:
        logger.exception("Unable to create Realtime client")
        sys.exit(1)


# --- Main Server Loop ---
async def main_server_loop():
    """Initializes and runs the main application components."""
    logger.debug("Starting main server loop...")
    # Start Realtime listener (connects, subscribes, and listens in background)
    await realtime_manager.start_realtime()  # Use renamed module

    # Start SSH server (listens for connections)
    await ssh_server.start_ssh_server(active_sessions)  # Pass sessions dict

    # Keep the main loop alive (Realtime listen runs in background)
    while True:
        await asyncio.sleep(3600)
        logger.debug("Hourly keep-alive tick.")


# --- CLI Handling ---
cli_app = typer.Typer()


@cli_app.command()
def create_user(username: str, public_key_file: typer.FileText):
    """Manually create a user (e.g., for admin purposes)."""
    typer.echo(f"Attempting to create user '{username}'...")
    logger.debug("Attempting to create user: %s", username)
    key_content = public_key_file.read().strip()
    if not key_content:
        typer.echo("Error: Public key file is empty.", err=True)
        raise typer.Exit(code=1)
    try:

        async def _create() -> None:
            result = await database.db_create_user(username, key_content)
            logger.debug(result)

        asyncio.run(_create())
        typer.echo(f"User '{username}' created successfully!")
    except Exception as e:
        typer.echo(f"Error creating user: {e}", err=True)
        raise typer.Exit(code=1) from e


# --- Entry Point ---
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        logger.debug("Running in CLI mode")
        initialize_clients()
        cli_app_args = sys.argv[2:]
        if not cli_app_args:
            cli_app_args = ["--help"]
        cli_app(args=cli_app_args)
    else:
        logger.debug("Running in Server mode.")
        initialize_clients()  # Initialize Supabase & Realtime
        try:
            asyncio.run(main_server_loop())
        except KeyboardInterrupt:
            logger.info("itter.sh server shutting down... Did we have fun?")
        except Exception:
            logger.exception("Unhandled top-level exception")
            traceback.print_exc()
        finally:
            # Check the client exists on the manager module before trying to close
            if rt_client_ctx and rt_client_ctx.get().is_connected:
                logger.debug("Closing realtime connection...")
                asyncio.run(rt_client_ctx.get().close())
            logger.debug("itter.sh has exited.")

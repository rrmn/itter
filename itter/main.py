# /main.py
import asyncio
import sys
import traceback
import typer
from typing import Dict, Optional
from supabase import create_client, Client
from realtime import AsyncRealtimeClient

import itter.core.config as config
import itter.core.utils as utils
import itter.data.database as database
import itter.services.realtime_manager as realtime_manager
from itter.ssh import ssh_server
from itter.ssh.shell import ItterShell

# --- Global State ---
# Use forward reference for ItterShell type hint
active_sessions: Dict[str, "ItterShell"] = {}

# Use forward reference for type hint to avoid circular import if needed later
active_sessions_ref: Optional[Dict[str, "ItterShell"]] = None

# --- Initialization ---
def initialize_clients():
    """Initialize Supabase and Realtime clients."""
    supabase_client: Optional[Client] = None
    rt_client: Optional[AsyncRealtimeClient] = None
    try:
        utils.debug_log("Creating Supabase client...")
        supabase_client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        database.init_db(supabase_client)  # Pass client to DB module
        utils.debug_log("Supabase client created and DB module initialized.")
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] Unable to create Supabase client: {e}\n")
        sys.exit(1)

    try:
        utils.debug_log("Creating Realtime client...")
        # Ensure realtime client is created correctly
        rt_client = AsyncRealtimeClient(config.SUPABASE_WSURL, config.SUPABASE_KEY)
        # Pass client and sessions dict
        realtime_manager.init_realtime(
            rt_client, active_sessions
        )
        utils.debug_log(
            "Realtime client created and Realtime manager module initialized."
        )
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] Unable to create Realtime client: {e}\n")
        sys.exit(1)


# --- Main Server Loop ---
async def main_server_loop():
    """Initializes and runs the main application components."""
    utils.debug_log("Starting main server loop...")
    # Start Realtime listener (connects, subscribes, and listens in background)
    await realtime_manager.start_realtime()
    
    # Start SSH server (listens for connections)
    await ssh_server.start_ssh_server(active_sessions)

    # Keep the main loop alive (Realtime listen runs in background)
    while True:
        await asyncio.sleep(3600)
        utils.debug_log("Hourly keep-alive tick.")


# --- CLI Handling ---
cli_app = typer.Typer()


@cli_app.command()
def create_user(username: str, public_key_file: typer.FileText):
    """Manually create a user (e.g., for admin purposes)."""
    supabase_cli_client: Optional[Client] = None
    try:
        supabase_cli_client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        database.init_db(supabase_cli_client)
    except Exception as e:
        typer.echo(f"Error initializing DB for CLI: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Attempting to create user '{username}'...")
    key_content = public_key_file.read().strip()
    if not key_content:
        typer.echo("Error: Public key file is empty.", err=True)
        raise typer.Exit(code=1)
    try:

        async def _create():
            await database.db_create_user(username, key_content)

        asyncio.run(_create())
        typer.echo(f"User '{username}' created successfully!")
    except Exception as e:
        typer.echo(f"Error creating user: {e}", err=True)
        raise typer.Exit(code=1)


# --- Entry Point ---
if __name__ == "__main__":
    config.validate_config()  # Validate essential config first

    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        utils.debug_log("Running in CLI mode.")
        cli_app_args = sys.argv[2:]
        if not cli_app_args:
            cli_app_args = ["--help"]
        cli_app(args=cli_app_args)
    else:
        utils.debug_log("Running in Server mode.")
        initialize_clients()  # Initialize Supabase & Realtime
        try:
            asyncio.run(main_server_loop())
        except KeyboardInterrupt:
            print("\n[INFO] itter.sh server shutting down... Did we have fun?")
        except Exception as top_level_ex:
            sys.stderr.write(
                f"[FATAL CRASH] Unhandled top-level exception: {top_level_ex}\n"
            )
            traceback.print_exc()
        finally:
            # Check the client exists on the manager module before trying to close
            if realtime_manager.rt_client and realtime_manager.rt_client.is_connected:
                utils.debug_log("Closing Realtime connection...")
                asyncio.run(realtime_manager.rt_client.close())
            utils.debug_log("itter.sh has exited.")

# /main.py
import asyncio
import sys
import traceback
import typer
from typing import Dict, Optional
from supabase import create_async_client, AsyncClient
from realtime._async.client import AsyncRealtimeClient

import itter.core.config as config
import itter.core.utils as utils
import itter.data.database as database
import itter.services.realtime_manager as realtime_manager
from itter.ssh import ssh_server
from itter.ssh.shell import ItterShell

active_sessions: Dict[str, "ItterShell"] = {}
active_sessions_ref: Optional[Dict[str, "ItterShell"]] = None


async def initialize_clients() -> None:
    supabase_client: Optional[AsyncClient] = None
    rt_client: Optional[AsyncRealtimeClient] = None

    try:
        utils.debug_log("Creating Supabase Async client...")
        supabase_client = await create_async_client(
            str(config.SUPABASE_URL), str(config.SUPABASE_KEY)
        )
        database.init_db(supabase_client)
        utils.debug_log("Supabase client created and DB module initialized.")
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] Unable to create Supabase client: {e}\n")
        sys.exit(1)

    try:
        utils.debug_log("Creating Realtime client...")
        rt_client = AsyncRealtimeClient(
            str(config.SUPABASE_WSURL), str(config.SUPABASE_KEY)
        )
        realtime_manager.init_realtime(rt_client, active_sessions)
        utils.debug_log(
            "Realtime client created and Realtime manager module initialized."
        )
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] Unable to create Realtime client: {e}\n")
        sys.exit(1)


async def main_server_loop() -> None:
    utils.debug_log("Starting main server loop...")
    await initialize_clients()
    await realtime_manager.start_realtime()
    await ssh_server.start_ssh_server(active_sessions)
    while True:
        await asyncio.sleep(3600)
        utils.debug_log("Hourly keep-alive tick.")


cli_app = typer.Typer()


@cli_app.command()
def create_user(username: str, public_key_file: typer.FileText) -> None:
    key_content: str = public_key_file.read().strip()
    if not key_content:
        typer.echo("Error: Public key file is empty.", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Attempting to create user '{username}'...")

    async def _create() -> None:
        try:
            supabase_cli_client: AsyncClient = await create_async_client(
                str(config.SUPABASE_URL), str(config.SUPABASE_KEY)
            )
            database.init_db(supabase_cli_client)
            await database.db_create_user(username, key_content)
            typer.echo(f"User '{username}' created successfully!")
        except Exception as e:
            typer.echo(f"Error creating user: {e}", err=True)
            raise typer.Exit(code=1)

    asyncio.run(_create())


if __name__ == "__main__":
    config.validate_config()
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        utils.debug_log("Running in CLI mode.")
        cli_app_args = sys.argv[2:]
        if not cli_app_args:
            cli_app_args = ["--help"]
        cli_app(args=cli_app_args)
    else:
        utils.debug_log("Running in Server mode.")
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
            if realtime_manager.rt_client and realtime_manager.rt_client.is_connected:
                utils.debug_log("Closing Realtime connection...")
                asyncio.run(realtime_manager.rt_client.close())
            utils.debug_log("itter.sh has exited.")

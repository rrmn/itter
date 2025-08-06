# /itter/ssh_server.py
import asyncssh
import sys
from typing import Dict

from itter.ssh.server import ItterSSHServer, init_ssh
from itter.ssh.shell import ItterShell
import itter.core.utils as utils
import itter.core.config as config

# --- SSH Server Start Function ---
async def start_ssh_server(
    sessions_dict: Dict[str, ItterShell]
):
    """Starts the AsyncSSH server."""
    init_ssh(sessions_dict)
    utils.debug_log(f"Starting SSH server on {config.SSH_HOST}:{config.SSH_PORT}")
    try:
        await asyncssh.create_server(
            ItterSSHServer,
            config.SSH_HOST,
            config.SSH_PORT,
            server_host_keys=[config.SSH_HOST_KEY_PATH],
            line_editor=False,
        )
        print(f"itter.sh server humming on ssh://{config.SSH_HOST}:{config.SSH_PORT}")
        print("Ctrl+C to stop.")
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] SSH server failed to start: {e}\n")
        sys.exit(1)

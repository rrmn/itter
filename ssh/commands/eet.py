# /Users/roman/work/itter/ssh/commands/eet.py
from typing import TYPE_CHECKING, List
import database as db
import utils
import config

if TYPE_CHECKING:
    from ssh.shell import ItterShell


async def handle_eet(
    shell: "ItterShell",
    content: str,
    hashtags: List[str],
    mentions: List[str],
):
    """Handles the 'eet' command."""
    if not shell.username:
        return
    content = content.strip()
    if not content:
        shell._write_to_channel("Usage: eet <text>")
    elif len(content) > config.EET_MAX_LENGTH:
        shell._write_to_channel(f"ERROR: Eet too long! Max {config.EET_MAX_LENGTH}.")
    else:
        await db.db_post_eet(
            shell.username,
            content,
            hashtags,
            mentions,
            shell._client_ip,
        )
        shell._write_to_channel("Eet posted!")
        if shell._is_watching_timeline:
            utils.debug_log(
                "Eet posted while watching, triggering immediate timeline refresh."
            )
            # We need to import timeline inside the function to avoid circular dependency
            from . import timeline as timeline_cmd

            await timeline_cmd.refresh_watch_display(shell, timeline_page_to_fetch=1)

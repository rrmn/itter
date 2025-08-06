# /itter/ssh/commands/settings.py
from typing import TYPE_CHECKING
import itter.core.config as config

from itter.core.utils import BOLD, RESET, FG_BRIGHT_BLACK
if TYPE_CHECKING:
    from itter.ssh.shell import ItterShell


async def handle_settings(shell: "ItterShell", raw_text: str):
    parts = raw_text.lower().split()
    if not parts:
        shell._write_to_channel(
            f"\r\nCurrent settings:\r\n  Eets per page: {BOLD}{shell._timeline_page_size}{RESET}\r\n  {FG_BRIGHT_BLACK}Usage:{RESET} settings pagesize <{config.MIN_TIMELINE_PAGE_SIZE}-{config.MAX_TIMELINE_PAGE_SIZE}>"
        )
    elif len(parts) == 2 and parts[0] == "pagesize":
        try:
            new_size = int(parts[1])
            if (
                config.MIN_TIMELINE_PAGE_SIZE
                <= new_size
                <= config.MAX_TIMELINE_PAGE_SIZE
            ):
                shell._timeline_page_size = new_size
                shell._write_to_channel(
                    f"All right! You will now see {new_size} eets per page."
                )
            else:
                shell._write_to_channel(
                    f"Error: Page size must be between {config.MIN_TIMELINE_PAGE_SIZE} and {config.MAX_TIMELINE_PAGE_SIZE}."
                )
        except ValueError:
            shell._write_to_channel("That... was not a number.")
    else:
        shell._write_to_channel(
            f"{FG_BRIGHT_BLACK}Usage:{RESET} settings pagesize <{config.MIN_TIMELINE_PAGE_SIZE}-{config.MAX_TIMELINE_PAGE_SIZE}>"
        )

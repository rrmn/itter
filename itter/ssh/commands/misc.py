# /itter/ssh/commands/misc.py
from typing import TYPE_CHECKING

import itter.core.config as config
from itter.core.utils import BOLD, RESET, FG_BRIGHT_BLACK
if TYPE_CHECKING:
    from itter.ssh.shell import ItterShell


def display_welcome_banner(shell: "ItterShell"):
    shell._clear_screen()
    banner_lines = shell._banner_text.splitlines()
    for line in banner_lines:
        shell._write_to_channel(line)
    shell._write_to_channel()


def show_help(shell: "ItterShell"):
    help_text = (
        f"\r\nitter.sh Commands:\r\n"
        f"  {BOLD}e{RESET}et {FG_BRIGHT_BLACK}<text>{RESET}                     - Post an eet (max {config.EET_MAX_LENGTH} chars).\r\n"
        f"  {BOLD}w{RESET}atch {FG_BRIGHT_BLACK}[mine|all|#chan|@user]{RESET}   - Live timeline view (Default: all).\r\n"
        f"  {BOLD}t{RESET}ime{BOLD}l{RESET}ine {FG_BRIGHT_BLACK}[mine|all|#chan|@user] [<page>]{RESET} - Show eets (Default: all, 1).\r\n"
        f"  {BOLD}f{RESET}ollow {FG_BRIGHT_BLACK}[#chan|@user] --list{RESET}    - Follow a user or channel, list follows.\r\n"
        f"  {BOLD}u{RESET}n{BOLD}f{RESET}ollow {FG_BRIGHT_BLACK}[#chan|@user]{RESET}         - Unfollow a user or channel.\r\n"
        f"  {BOLD}i{RESET}gnore {FG_BRIGHT_BLACK}@<user> --list{RESET}          - Ignore a user, list ignores.\r\n"
        f"  {BOLD}u{RESET}n{BOLD}i{RESET}gnore {FG_BRIGHT_BLACK}@<user>{RESET}               - Unignore a user.\r\n"
        f"  {BOLD}p{RESET}rofile {FG_BRIGHT_BLACK}[@<user>]{RESET}              - View user profile (yours or another's).\r\n"
        f"  {BOLD}p{RESET}rofile {BOLD}e{RESET}dit {FG_BRIGHT_BLACK}-name <Name> -email <Email> --reset{RESET} - Edit profile (or reset it).\r\n"
        f"  {BOLD}s{RESET}ettings {FG_BRIGHT_BLACK}[pagesize|key]{RESET}        - View or change settings (e.g., public keys).\r\n"
        f"  {BOLD}h{RESET}elp                           - Show this help message.\r\n"
        f"  {BOLD}c{RESET}lear                          - Clear the screen.\r\n"
        f"  e{BOLD}x{RESET}it                           - Exit watch mode or itter.sh.\r\n"
    )
    shell._write_to_channel(help_text)


async def handle_exit_command(shell: "ItterShell"):
    if shell._is_watching_timeline:
        shell._is_watching_timeline = False
        shell._sidebar_enabled = False  # Disable sidebar
        if (
            shell._timeline_auto_refresh_task
            and not shell._timeline_auto_refresh_task.done()
        ):
            shell._timeline_auto_refresh_task.cancel()
        # Restore normal screen after exiting watch mode
        shell._clear_screen()
        display_welcome_banner(shell)
        show_help(shell)
        shell._prompt()
    else:
        shell._write_to_channel("\nitter.sh says: Don't let the door hit you!")
        shell.close()


async def handle_help(shell: "ItterShell"):
    display_welcome_banner(shell)
    show_help(shell)


async def handle_clear(shell: "ItterShell"):
    shell._clear_screen()
    if shell._is_watching_timeline:
        # We need to import timeline inside the function to avoid circular dependency
        from . import timeline as timeline_cmd

        await timeline_cmd.refresh_watch_display(
            shell, timeline_page_to_fetch=shell._current_timeline_page
        )
    else:
        shell._prompt()

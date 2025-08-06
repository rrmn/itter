# /itter/ssh/commands/ignore.py
import re
from typing import TYPE_CHECKING

import itter.data.database as db
import itter.core.utils as utils
from itter.core.utils import BOLD, RESET, FG_MAGENTA
if TYPE_CHECKING:
    from itter.ssh.shell import ItterShell


async def display_ignore_list(shell: "ItterShell"):
    if not shell.username:
        return
    try:
        ignoring_list = await db.db_get_user_ignoring(shell.username)
    except Exception as e:
        shell._write_to_channel(f"Error fetching ignore list: {e}")
        return
    output_lines = []
    output_lines.append(
        f"\r\n{BOLD}--- You are ignoring ({len(ignoring_list)} users) ---{RESET}"
    )
    if not ignoring_list:
        output_lines.append(
            f"  Not ignoring anyone. What a saint! Use `{BOLD}ignore @user{RESET}` if needed."
        )
    else:
        for user_data in ignoring_list:
            display_name_part = (
                f" ({user_data['display_name']})"
                if user_data.get("display_name")
                else ""
            )
            time_part = f" - since {utils.time_ago(user_data.get('created_at'))}"
            output_lines.append(
                f"  {FG_MAGENTA}@{user_data['username']}{RESET}{display_name_part}{time_part}"
            )
    output_lines.append("\r\n")
    shell._write_to_channel("\r\n".join(output_lines))


async def handle_ignore(shell: "ItterShell", raw_text: str):
    if not shell.username:
        return
    target_text_ignore = raw_text.strip()
    if target_text_ignore.lower() == "--list":
        await display_ignore_list(shell)
    elif target_text_ignore.startswith("@"):
        target_user_to_ignore = target_text_ignore[1:]
        if not target_user_to_ignore or not re.match(
            r"^[a-zA-Z0-9_]{3,20}$", target_user_to_ignore
        ):
            shell._write_to_channel("Invalid username format: '@username'.")
        elif target_user_to_ignore == shell.username:
            shell._write_to_channel(
                "You cannot ignore yourself. (That's what my psychologist said)"
            )
        else:
            await db.db_ignore_user(shell.username, target_user_to_ignore)
            shell._write_to_channel(
                f"Okay, @{target_user_to_ignore} will now be ignored. Their posts won't appear in your timelines. Phew."
            )
    else:
        shell._write_to_channel(
            f"Usage: {BOLD}ignore @<user>{RESET} OR {BOLD}ignore --list{RESET}"
        )


async def handle_unignore(shell: "ItterShell", raw_text: str):
    if not shell.username:
        return
    target_text_unignore = raw_text.strip()
    if target_text_unignore.startswith("@"):
        target_user_to_unignore = target_text_unignore[1:]
        if not target_user_to_unignore or not re.match(
            r"^[a-zA-Z0-9_]{3,20}$", target_user_to_unignore
        ):
            shell._write_to_channel("Invalid username format: '@username'.")
        else:
            await db.db_unignore_user(shell.username, target_user_to_unignore)
            shell._write_to_channel(
                f"Okay, @{target_user_to_unignore} is forgiven and will no longer be ignored. You'll see their posts again."
            )
    else:
        shell._write_to_channel(f"Usage: {BOLD}unignore @<user>{RESET}")

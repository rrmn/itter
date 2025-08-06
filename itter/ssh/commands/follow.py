# /itter/ssh/commands/follow.py
import re
from typing import TYPE_CHECKING

import itter.data.database as db
import itter.core.utils as utils
from itter.core.utils import BOLD, RESET, FG_CYAN, FG_MAGENTA, FG_RED, FG_BRIGHT_BLACK

if TYPE_CHECKING:
    from itter.ssh.shell import ItterShell


async def display_follow_lists(shell: "ItterShell"):
    if not shell.username:
        return

    try:
        following_list = await db.db_get_user_following(shell.username)
        followers_list = await db.db_get_user_followers(shell.username)
        following_channels_list = await db.db_get_user_following_channels(
            shell.username
        )
    except Exception as e:
        shell._write_to_channel(
            f"{FG_RED}Error:{RESET} Could not fetch follow lists: {e}"
        )
        return

    output_lines = []
    output_lines.append(
        f"\r\n{BOLD}--- You are following ({len(following_list)} users) ---{RESET}"
    )
    if not following_list:
        output_lines.append(
            f"  {FG_BRIGHT_BLACK}Not following anyone yet. Use `{BOLD}follow @user{RESET}{FG_BRIGHT_BLACK}`.{RESET}"
        )
    else:
        for user_data in following_list:
            display_name_part = (
                f" ({user_data['display_name']})"
                if user_data.get("display_name")
                else ""
            )
            time_part = f" - since {utils.time_ago(user_data.get('created_at'))}"
            output_lines.append(
                f"  {FG_CYAN}@{user_data['username']}{RESET}{display_name_part}{time_part}"
            )

    output_lines.append(
        f"\r\n{BOLD}--- You are following ({len(following_channels_list)} channels) ---{RESET}"
    )
    if not following_channels_list:
        output_lines.append(
            f"  {FG_BRIGHT_BLACK}Not following any channels yet. Use `{BOLD}follow #channel{RESET}{FG_BRIGHT_BLACK}`.{RESET}"
        )
    else:
        for channel_data in following_channels_list:
            time_part = f" - since {utils.time_ago(channel_data.get('created_at'))}"
            output_lines.append(
                f"  {FG_MAGENTA}#{channel_data['channel_tag']}{RESET}{time_part}"
            )

    output_lines.append(
        f"\r\n{BOLD}--- Follows you ({len(followers_list)} users) ---{RESET}"
    )
    if not followers_list:
        output_lines.append(
            f"  {FG_BRIGHT_BLACK}No followers yet. Be more eet-eresting!{RESET}"
        )
    else:
        for user_data in followers_list:
            display_name_part = (
                f" ({user_data['display_name']})"
                if user_data.get("display_name")
                else ""
            )
            time_part = f" - since {utils.time_ago(user_data.get('created_at'))}"
            output_lines.append(
                f"  {FG_CYAN}@{user_data['username']}{RESET}{display_name_part}{time_part}"
            )

    output_lines.append("\r\n")
    shell._write_to_channel("\r\n".join(output_lines))


async def handle_follow(shell: "ItterShell", raw_text: str):
    if not shell.username:
        return
    target_text = raw_text.strip()
    if target_text.lower() == "--list":
        await display_follow_lists(shell)
    elif target_text.startswith("#"):
        channel_tag_to_follow = target_text[1:]
        if not channel_tag_to_follow or not re.match(
            r"^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$",
            channel_tag_to_follow,
        ):
            shell._write_to_channel(
                f"{FG_RED}Invalid channel name.{RESET} Use letters, numbers, and hyphens (but not at the start/end)."
            )
        else:
            await db.db_follow_channel(shell.username, channel_tag_to_follow)
            shell._write_to_channel(
                f"Now following {FG_MAGENTA}#{channel_tag_to_follow.lower()}{RESET}. Their eets will appear in your 'mine' feed."
            )
    elif target_text.startswith("@"):
        target_user_to_follow = target_text[1:]
        if not target_user_to_follow or not re.match(
            r"^[a-zA-Z0-9_]{3,20}$", target_user_to_follow
        ):
            shell._write_to_channel(
                f"{FG_RED}Invalid username.{RESET} Must be 3-20 characters (letters, numbers, underscores)."
            )
        else:
            await db.db_follow_user(shell.username, target_user_to_follow)
            shell._write_to_channel(
                f"Now following {FG_CYAN}@{target_user_to_follow}{RESET}. You will now see their eets in your 'mine' feed."
            )
    else:
        shell._write_to_channel(
            f"Usage: {BOLD}follow @user{RESET} or {BOLD}follow #channel{RESET} or {BOLD}follow --list{RESET}"
        )


async def handle_unfollow(shell: "ItterShell", raw_text: str):
    if not shell.username:
        return
    target_text = raw_text.strip()
    if target_text.startswith("#"):
        channel_tag_to_unfollow = target_text[1:]
        if not channel_tag_to_unfollow or not re.match(
            r"^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$",
            channel_tag_to_unfollow,
        ):
            shell._write_to_channel(
                f"{FG_RED}Invalid channel name format: {RESET} '#{channel_tag_to_unfollow}'."
            )
        else:
            await db.db_unfollow_channel(shell.username, channel_tag_to_unfollow)
            shell._write_to_channel(
                f"No longer following channel {FG_MAGENTA}#{channel_tag_to_unfollow.lower()}{RESET}."
            )
    elif target_text.startswith("@"):
        target_user_to_unfollow = target_text[1:]
        if not target_user_to_unfollow or not re.match(
            r"^[a-zA-Z0-9_]{3,20}$", target_user_to_unfollow
        ):
            shell._write_to_channel(f"{FG_RED}Invalid username format.{RESET} Try using '{BOLD}@username{RESET}'.")
        else:
            await db.db_unfollow_user(shell.username, target_user_to_unfollow)
            shell._write_to_channel(
                f"Unfollowed {FG_CYAN}@{target_user_to_unfollow}{RESET}. Their posts will no longer appear in your 'mine' feed."
            )
    else:
        shell._write_to_channel(
            f"Usage: {BOLD}unfollow @user{RESET} or {BOLD}unfollow #channel{RESET}"
        )

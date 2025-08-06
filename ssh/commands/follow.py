# /Users/roman/work/itter/ssh/commands/follow.py
import re
from typing import TYPE_CHECKING
import database as db
import utils
from utils import BOLD, RESET, FG_CYAN, FG_MAGENTA

if TYPE_CHECKING:
    from ssh.shell import ItterShell


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
        shell._write_to_channel(f"Error fetching follow lists: {e}")
        return

    output_lines = []
    output_lines.append(
        f"\r\n{BOLD}--- You are following ({len(following_list)} users) ---{RESET}"
    )
    if not following_list:
        output_lines.append(
            f"  Not following anyone yet. Use `{BOLD}follow @user{RESET}`."
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
            f"  Not following any channels yet. Use `{BOLD}follow #channel{RESET}`."
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
        output_lines.append("  No followers yet. Be more eet-eresting!")
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
                f"Invalid channel name format: '#{channel_tag_to_follow}'. Must be alphanumeric with hyphens, not starting/ending with hyphen."
            )
        else:
            await db.db_follow_channel(shell.username, channel_tag_to_follow)
            shell._write_to_channel(
                f"Now following channel {FG_MAGENTA}#{channel_tag_to_follow.lower()}{RESET}. Posts from this channel will appear in your 'mine' feed."
            )
    elif target_text.startswith("@"):
        target_user_to_follow = target_text[1:]
        if not target_user_to_follow or not re.match(
            r"^[a-zA-Z0-9_]{3,20}$", target_user_to_follow
        ):
            shell._write_to_channel(
                "Invalid username format: '@username' (3-20 alphanumeric/underscore)."
            )
        else:
            await db.db_follow_user(shell.username, target_user_to_follow)
            shell._write_to_channel(
                f"Following {FG_CYAN}@{target_user_to_follow}{RESET}. You will now see their posts on your 'mine' page."
            )
    else:
        shell._write_to_channel(
            f"Usage: {BOLD}follow @<user>{RESET} OR {BOLD}follow #<channel>{RESET} OR {BOLD}follow --list{RESET}"
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
                f"Invalid channel name format: '#{channel_tag_to_unfollow}'."
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
            shell._write_to_channel("Invalid username format: '@username'.")
        else:
            await db.db_unfollow_user(shell.username, target_user_to_unfollow)
            shell._write_to_channel(
                f"Unfollowed {FG_CYAN}@{target_user_to_unfollow}{RESET}. They won't show up on your 'mine' page anymore."
            )
    else:
        shell._write_to_channel(
            f"Usage: {BOLD}unfollow @<user>{RESET} OR {BOLD}unfollow #<channel>{RESET}"
        )

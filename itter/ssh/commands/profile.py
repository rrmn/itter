# /itter/ssh/commands/profile.py
from typing import TYPE_CHECKING, List
import itter.data.database as db
import itter.core.utils as utils

from itter.core.utils import RESET, FG_BRIGHT_BLACK, FG_RED

if TYPE_CHECKING:
    from itter.ssh.shell import ItterShell


async def handle_profile_command(
    shell: "ItterShell", raw_text: str, user_refs: List[str]
):
    if not shell.username:
        return
    args = raw_text.split()
    if args and (args[0].lower() == "edit" or args[0].lower() == "e"):
        new_display_name, new_email, reset_user = None, None, False
        try:
            idx = args.index("-name")
            new_display_name = (
                args[idx + 1]
                if len(args) > idx + 1 and not args[idx + 1].startswith("-")
                else None
            )
        except (ValueError, IndexError):
            pass
        try:
            idx = args.index("-email")
            new_email = (
                args[idx + 1]
                if len(args) > idx + 1 and not args[idx + 1].startswith("-")
                else None
            )
        except (ValueError, IndexError):
            pass
        try:
            if args.index("--reset") != -1:
                reset_user = True
                new_display_name = None
                new_email = None
        except (ValueError, IndexError):
            pass
        if new_display_name is None and new_email is None and not reset_user:
            shell._write_to_channel(
                f"{FG_BRIGHT_BLACK}Usage:{RESET} profile edit -name <Name> -email <Email> or --reset"
            )
        else:
            await db.db_update_profile(
                shell.username, new_display_name, new_email, reset_user
            )
            shell._write_to_channel("Profile updated. Looking fine!")
    else:
        profile_username = (
            user_refs[0]
            if user_refs
            else (raw_text.strip().lstrip("@") if raw_text.strip() else shell.username)
        )
        if profile_username.startswith("#"):
            shell._write_to_channel(
                f"{FG_RED}That's a channel, not a profile:{RESET} {profile_username}"
            )
            return
        try:
            stats = await db.db_get_profile_stats(profile_username)
            display_name = (
                stats.get("display_name") or f"{FG_BRIGHT_BLACK}none{RESET}"
            )
            email = stats.get("email") or f"{FG_BRIGHT_BLACK}none{RESET}"

            profile_output = (
                f"\r\n\r\n\r\n\r\n--- Profile: @{stats['username']} ---\r\n"
                + f"  Display Name: {display_name}\r\n"
                + f"  Email:        {email}\r\n"
                + f"  Joined:       {utils.time_ago(stats.get('joined_at'))}\r\n"
                + f"  Eets:         {stats['eet_count']}\r\n"
                + f"  Following:    {stats['following_count']}\r\n"
                + f"  Followers:    {stats['follower_count']}\r\n"
                + "---------------------------\r\n"
            )
            # We need to import misc inside the function to avoid circular dependency
            from itter.ssh.commands import misc as misc_cmd

            shell._clear_screen()
            misc_cmd.display_welcome_banner(shell)
            shell._write_to_channel(profile_output)
        except ValueError as ve:
            shell._write_to_channel(f"{FG_RED}Error:{RESET} {ve}")
        except Exception as e:
            utils.debug_log(f"Err profile {profile_username}: {e}")
            shell._write_to_channel(
                f"{FG_RED}Couldn't @{profile_username}.{RESET} Is this user a... ghost?"
            )

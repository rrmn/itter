# /itter/ssh/commands/settings.py
from typing import TYPE_CHECKING
import re
import itter.core.config as config
import itter.data.database as db
import itter.core.utils as utils

from itter.core.utils import BOLD, RESET, FG_BRIGHT_BLACK, FG_BRIGHT_YELLOW, FG_RED

if TYPE_CHECKING:
    from itter.ssh.shell import ItterShell


async def _handle_pagesize_setting(shell: "ItterShell", args: list[str]):
    if not args:
        shell._write_to_channel(
            f"Current eets per page: {BOLD}{shell._timeline_page_size}{RESET}"
        )
        return

    try:
        new_size = int(args[0])
        if config.MIN_TIMELINE_PAGE_SIZE <= new_size <= config.MAX_TIMELINE_PAGE_SIZE:
            shell._timeline_page_size = new_size
            shell._write_to_channel(
                f"All right! You will now see {new_size} eets per page."
            )
        else:
            shell._write_to_channel(
                f"Sorry... Page size must be between {config.MIN_TIMELINE_PAGE_SIZE} and {config.MAX_TIMELINE_PAGE_SIZE} (for now)."
            )
    except (ValueError, IndexError):
        shell._write_to_channel("That... was not a number.")


async def _handle_key_setting(shell: "ItterShell", args: list[str]):
    if not shell.username:
        return

    user_obj = await db.db_get_user_by_username(shell.username)
    if not user_obj:
        shell._write_to_channel(
            f"{FG_RED}Error:{RESET} Could not find your user record. That is... unusual."
        )
        return

    user_id = user_obj["id"]
    subcommand = args[0] if args else "list"

    if subcommand == "list":
        keys = await db.db_get_user_public_keys(user_id)
        if not keys:
            shell._write_to_channel(
                "You have no public keys registered. This is... unusual."
            )
            return

        output = [f"\r\n{BOLD}--- Your Public Keys ---{RESET}"]
        for key in keys:
            key_str = key.get("public_key", "")
            is_current = key_str == shell._authenticated_key
            current_marker = (
                f"{FG_BRIGHT_YELLOW} (current session){RESET}" if is_current else ""
            )

            # Show the key type and the start of the key body for easy identification
            key_parts = key_str.split()
            key_type = key_parts[0] if key_parts else "N/A"
            key_body_preview = (
                key_parts[1][:20] + "..." if len(key_parts) > 1 else "N/A"
            )

            created_ago = utils.time_ago(key.get("created_at"))
            output.append(
                f"  - {BOLD}{key['name']}{RESET}{current_marker}\r\n"
                f"    {FG_BRIGHT_BLACK}Added:{RESET} {created_ago}\r\n"
                f"    {FG_BRIGHT_BLACK}Key:{RESET}   {key_type} {key_body_preview}"
            )

        output.append(
            f"\r\n{FG_BRIGHT_BLACK}To add a key, copy the entire line from your .pub file:{RESET}"
        )
        output.append("  settings key add <name> ssh-ed25519 AAAA...")
        output.append(
            f"\r\n{FG_BRIGHT_BLACK}To remove a key:{RESET} settings key remove <name>"
        )
        shell._write_to_channel("\r\n".join(output))

    elif subcommand == "add":
        if len(args) < 3:
            shell._write_to_channel(f"{FG_RED}Sorry, that didn't work...{RESET} Let's try again?")
            shell._write_to_channel(
                f"  {FG_BRIGHT_BLACK}Command:{RESET} settings key add <key-name> <full-public-key-string>"
            )
            shell._write_to_channel(
                f"  {FG_BRIGHT_BLACK}Example:{RESET} settings key add my-macbook ssh-ed25519 AAAAC3... user@host"
            )
            return

        key_name = args[1]
        if not re.match(r"^[a-zA-Z0-9_-]+$", key_name):
            shell._write_to_channel(
                f"{FG_RED}Sorry, that was too fancy:{RESET} A key name can contain letters, numbers, dashes, underscores. No spaces, please."
            )
            return

        # The rest of the arguments are the public key. Join them back together.
        full_key_str = " ".join(args[2:]).strip()

        # A better validation: check that the key string starts appropriately.
        key_parts = full_key_str.split()
        if not (len(key_parts) >= 2 and key_parts[0].startswith("ssh-")):
            shell._write_to_channel(
                f"{FG_RED}Oops, that didn't work:{RESET} Your public key format looks off to the system."
            )
            shell._write_to_channel(
                f"Please try again and copy the {BOLD}entire line{RESET} from your public key file (e.g., `id_rsa.pub`)."
            )
            shell._write_to_channel(
                "It should start with `ssh-rsa`, `ssh-ed25519`, etc."
            )
            shell._write_to_channel(
                "\nIf it doesn't work: Can you ping us on github.com/rrrmn/itter.sh?"
            )
            return

        try:
            await db.db_add_user_public_key(user_id, key_name, full_key_str)
            shell._write_to_channel(
                f"Successfully added new public key named '{key_name}'."
            )
        except ValueError as ve:
            shell._write_to_channel(f"{FG_RED}Error:{RESET} {ve}")
        except Exception as e:
            utils.debug_log(f"Error adding key: {e}")
            shell._write_to_channel(
                f"{FG_RED}An unexpected error occurred while adding the key.{RESET}"
            )

    elif subcommand == "remove":
        if len(args) < 2:
            shell._write_to_channel(
                f"{FG_BRIGHT_BLACK}Usage:{RESET} settings key remove <name>"
            )
            return

        key_name_to_remove = args[1]
        keys = await db.db_get_user_public_keys(user_id)

        key_to_remove = next((k for k in keys if k["name"] == key_name_to_remove), None)

        if not key_to_remove:
            shell._write_to_channel(
                f"{FG_RED}Error:{RESET} Eh... I don't see a key with the name '{FG_BRIGHT_BLACK}{key_name_to_remove}{RESET}'. Do you?"
            )
            return

        if key_to_remove.get("public_key") == shell._authenticated_key:
            shell._write_to_channel(
                f"{FG_RED}Error:{RESET} Sorry, you cannot remove your current key."
            )
            shell._write_to_channel(
                "If you want to remove the key you are currently using, please do it while logged in with another key."
            )
            return

        if len(keys) <= 1:
            shell._write_to_channel(
                f"{FG_RED}Ehm... You cannot remove your last public key. This would lock you out of your account. You don't really want that, do you?{RESET}"
            )
            return

        try:
            await db.db_remove_user_public_key(user_id, key_name_to_remove)
            shell._write_to_channel(
                f"Poof, it's gone! Successfully removed public key '{FG_BRIGHT_BLACK}{key_name_to_remove}{RESET}'."
            )
        except Exception as e:
            utils.debug_log(f"Error removing key: {e}")
            shell._write_to_channel(
                f"{FG_RED}Sh... itter. Some weird error occurred while removing the key.{RESET}"
            )

    else:
        shell._write_to_channel(
            f"Ehm... '{FG_BRIGHT_BLACK}settings key {BOLD}{subcommand}{RESET}' doesn't look right. Options are: {FG_BRIGHT_BLACK}{BOLD}list{RESET}, {FG_BRIGHT_BLACK}{BOLD}add{RESET}, {FG_BRIGHT_BLACK}{BOLD}remove{RESET}."
        )


async def handle_settings(shell: "ItterShell", raw_text: str):
    parts = raw_text.lower().split()
    if not parts:
        # Default view: show pagesize and mention key management
        shell._write_to_channel(
            f"\r\nCurrent settings:\r\n  - Eets per page: {BOLD}{shell._timeline_page_size}{RESET}\r\n\r\n{FG_BRIGHT_BLACK}Usage:{RESET}\r\n  settings pagesize <num>\r\n  settings key [list|add|remove] ..."
        )
        return

    setting_area = parts[0]
    args = parts[1:]

    if setting_area == "pagesize":
        await _handle_pagesize_setting(shell, args)
    elif setting_area == "key":
        await _handle_key_setting(shell, args)
    else:
        shell._write_to_channel(
            f"Eh.. Not sure we have settings for '{FG_BRIGHT_BLACK}{setting_area}{RESET}'. Maybe try '{FG_BRIGHT_BLACK}pagesize{RESET}' or '{FG_BRIGHT_BLACK}key{RESET}'?"
        )

import asyncio
import asyncssh
import re
import textwrap
import sys
from typing import Optional, Dict, Any, List, Tuple

# Import from our modules
import database as db
import utils
import config
from command_history import CommandHistory

from utils import BOLD, FG_BRIGHT_BLACK, RESET, FG_CYAN, FG_MAGENTA, FG_BRIGHT_YELLOW

# Global reference - will be set by main.py
# Use forward reference for type hint to avoid circular import if needed later
active_sessions_ref: Optional[Dict[str, "ItterShell"]] = None


def init_ssh(sessions_dict: Dict[str, "ItterShell"]):  # <-- Update type hint
    """Initializes the SSH module with the active sessions reference."""
    global active_sessions_ref
    active_sessions_ref = sessions_dict
    utils.debug_log("SSH Server module initialized.")


class ItterSSHServer(asyncssh.SSHServer):
    def __init__(self):
        self.is_registration_attempt = False
        self.registration_username_candidate: Optional[str] = None
        self.submitted_public_key: Optional[str] = None
        self.current_username: Optional[str] = None
        super().__init__()

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        utils.debug_log(
            f"ItterSSHServer connection_made by {conn.get_extra_info('peername')}"
        )
        self._conn = conn

    def connection_lost(self, exc: Optional[Exception]) -> None:
        utils.debug_log(f"ItterSSHServer connection_lost: {exc}")
        if (
            self.current_username
            and active_sessions_ref is not None
            and self.current_username in active_sessions_ref
        ):
            utils.debug_log(
                f"Removing session for {self.current_username} due to connection loss."
            )
            # Use try-except in case the session was already removed somehow
            try:
                del active_sessions_ref[self.current_username]
            except KeyError:
                utils.debug_log(f"Session for {self.current_username} already removed.")
        self.current_username = None

    async def begin_auth(self, username: str) -> bool:
        # Reset state for this auth attempt
        self.is_registration_attempt = False
        self.registration_username_candidate = None

        utils.debug_log(
            f"begin_auth for '{username}'. Initial state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
        )

        if username.startswith("register:"):
            potential_username = username[9:]
            error_message = None

            if not re.match(r"^[a-zA-Z0-9_]{3,20}$", potential_username):
                utils.debug_log(
                    f"Invalid registration username format: '{potential_username}'"
                )
                error_message = "Registration failed: Invalid username format (3-20 alphanumeric/underscore)."
            else:
                conflicting_db_username = await db.db_username_exists_case_insensitive(
                    potential_username
                )
                if conflicting_db_username:
                    utils.debug_log(
                        f"Registration attempt for '{potential_username}' rejected. Case-insensitive conflict with existing username: '{conflicting_db_username}'"
                    )
                    error_message = f"Sorry, '{potential_username}' is already taken."

            if error_message:
                await self._send_auth_failure_message(error_message)
                self.is_registration_attempt = False
                self.current_username = None
                self.registration_username_candidate = None
                utils.debug_log(
                    f"begin_auth returning False. Reason: {error_message}. Final state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
                )
                return False

            self.is_registration_attempt = True
            self.registration_username_candidate = potential_username
            self.current_username = None
            utils.debug_log(
                f"Registration mode activated for: '{self.registration_username_candidate}'. State: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'. Returning True."
            )
            return True

        # Normal login
        self.is_registration_attempt = False
        self.registration_username_candidate = None

        user_data = await db.db_get_user_by_username(username)
        if not user_data:
            utils.debug_log(f"Login attempt for non-existent user: '{username}'")
            # For login failures, usually no special banner is needed.
            # The client will typically show "Permission denied".
            self.current_username = None
            utils.debug_log(
                f"begin_auth returning False for non-existent login user '{username}'. Final state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
            )
            return False

        self.current_username = username
        utils.debug_log(
            f"User '{self.current_username}' found for login. State: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'. Returning True."
        )
        return True

    async def _send_auth_failure_message(self, message: str):
        if hasattr(self, "_conn") and self._conn:
            try:
                banner_message = message
                if not banner_message.endswith("\r\n"):
                    banner_message += "\r\n"

                utils.debug_log(
                    f"Attempting to send auth banner: {banner_message.strip()}"
                )
                self._conn.send_auth_banner(banner_message)

                await asyncio.sleep(0.1)  # Brief pause for banner

                utils.debug_log(
                    f"Requesting client disconnect after sending auth banner for: {message.strip()}"
                )
                # If begin_auth returns False, asyncssh should handle the auth failure.
                # Calling disconnect() here is an explicit request to close the connection
                # if it hasn't already started closing due to auth failure.
                # The try/except will catch errors if it's already closing.
                self._conn.disconnect(14, "Authentication failed")

            except asyncssh.Error as e:  # Catch specific asyncssh errors
                utils.debug_log(
                    f"asyncssh error during _send_auth_failure_message (banner/disconnect): {e}"
                )
            except Exception as e:  # Catch other errors
                utils.debug_log(
                    f"Generic error during _send_auth_failure_message (banner/disconnect): {e}"
                )
        else:
            utils.debug_log(
                f"No connection object (_conn) available to send auth failure message: {message}"
            )

    def public_key_auth_supported(self) -> bool:
        return True

    async def validate_public_key(
        self, username_from_auth_begin: str, key: asyncssh.SSHKey
    ) -> bool:
        utils.debug_log(
            f"validate_public_key for '{username_from_auth_begin}'. Server state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
        )
        try:
            self.submitted_public_key = key.export_public_key().decode().strip()
        except Exception as e:
            utils.debug_log(f"Error exporting public key: {e}")
            return False

        if self.is_registration_attempt:
            # For registration, registration_username_candidate should be set
            if not self.registration_username_candidate:
                utils.debug_log(
                    f"[CRITICAL] validate_public_key: is_registration_attempt is True, but registration_username_candidate is None for '{username_from_auth_begin}'. This is an inconsistent state."
                )
                return False  # Safety break
            utils.debug_log(
                f"Public key captured for registration of '{self.registration_username_candidate}' (original user in auth: '{username_from_auth_begin}'). Returning True."
            )
            return True

        # Not a registration attempt (self.is_registration_attempt is False)
        if not self.current_username:
            utils.debug_log(
                f"Public key validation attempted for '{username_from_auth_begin}' but self.current_username is None and not a registration attempt. Returning False."
            )
            return False

        # This must be a login attempt, self.current_username should match username_from_auth_begin (or be derived)
        if self.current_username != username_from_auth_begin:
            utils.debug_log(
                f"[WARNING] validate_public_key: username_from_auth_begin ('{username_from_auth_begin}') differs from self.current_username ('{self.current_username}') in login flow."
            )
            # This might indicate an issue if they are expected to be same. For now, proceed with self.current_username.

        user_obj = await db.db_get_user_by_username(self.current_username)
        if not user_obj or "public_key" not in user_obj or not user_obj["public_key"]:
            utils.debug_log(
                f"User '{self.current_username}' (for '{username_from_auth_begin}') not found or has no public key. Returning False."
            )
            return False

        stored_key = user_obj["public_key"].strip()
        is_valid = stored_key == self.submitted_public_key
        utils.debug_log(
            f"Key validation for login user '{self.current_username}' (for '{username_from_auth_begin}'): {'Success' if is_valid else 'Failure'}. Returning {is_valid}."
        )
        return is_valid

    def session_requested(
        self,
    ) -> Optional["ItterShell"]:  # Return type can be Optional
        utils.debug_log(
            f"session_requested called. Server state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
        )

        shell_to_return: Optional[ItterShell] = None

        if self.is_registration_attempt:
            if self.registration_username_candidate and self.submitted_public_key:
                utils.debug_log(
                    f"Creating ItterShell for REGISTRATION of '{self.registration_username_candidate}'"
                )
                shell_to_return = ItterShell(
                    ssh_server_ref=self,
                    initial_username=None,
                    is_registration_flow=True,
                    registration_details=(
                        self.registration_username_candidate,
                        self.submitted_public_key,
                    ),
                )
            else:
                utils.debug_log(
                    f"[CRITICAL] session_requested: In registration flow but registration_username_candidate ('{self.registration_username_candidate}') or submitted_public_key is missing. Refusing session."
                )
                # self._conn.send_auth_banner("Registration process incomplete. Please try again.\r\n") # Optional
                # self._conn.disconnect(...) # Optional
                return None  # Refuse session

        elif (
            self.current_username
        ):  # This implies not a registration attempt, but a login
            utils.debug_log(
                f"Creating ItterShell for LOGIN of '{self.current_username}'"
            )
            shell_to_return = ItterShell(
                ssh_server_ref=self,
                initial_username=self.current_username,
                is_registration_flow=False,
                registration_details=None,
            )
        else:
            # This is the problematic state: no registration, no current_username.
            # This means begin_auth likely failed or didn't establish a user,
            # but other auth (e.g. public key without specific user context) passed.
            utils.debug_log(
                f"[CRITICAL] session_requested: No valid user context (not registration, no current_username). Refusing session."
            )
            # self._conn.send_auth_banner("Authentication failed to establish user context. Please try again.\r\n") # Optional
            # self._conn.disconnect(...) # Optional
            return None  # Refuse session

        if shell_to_return and active_sessions_ref is not None:
            shell_to_return.set_active_sessions_ref(active_sessions_ref)
        elif shell_to_return and active_sessions_ref is None:
            utils.debug_log(
                "WARNING: active_sessions_ref is None when creating ItterShell! Shell will be created but may lack full functionality."
            )

        return shell_to_return


class ItterShell(asyncssh.SSHServerSession):
    def __init__(
        self,
        ssh_server_ref: ItterSSHServer,
        initial_username: Optional[str],
        is_registration_flow: bool,
        registration_details: Optional[Tuple[str, str]],
    ):
        self._ssh_server = ssh_server_ref
        self.username: Optional[str] = initial_username
        self._is_registration_flow = is_registration_flow
        if self._is_registration_flow and registration_details:
            self._reg_username_candidate, self._reg_public_key = registration_details
        else:
            self._reg_username_candidate, self._reg_public_key = None, None

        self._chan: Optional[asyncssh.SSHServerChannel] = None
        self._current_target_filter: Dict[str, Optional[str]] = {
            "type": "all",
            "value": None,
        }
        self._is_watching_timeline = False
        self._timeline_auto_refresh_task: Optional[asyncio.Task] = None
        self._current_timeline_page = 1
        self._term_width = 80
        self._term_height = 24
        self._input_buffer = ""
        self._command_history = CommandHistory()
        self._active_sessions: Optional[Dict[str, "ItterShell"]] = (
            None  # Use forward reference
        )
        self._client_ip: Optional[str] = None

        self._timeline_page_size = config.DEFAULT_TIMELINE_PAGE_SIZE
        self._last_timeline_eets_count: Optional[int] = None  # For PgUp/PgDn context

        try:
            with open(config.BANNER_FILE, "r") as f:
                self._banner_text = f.read()
        except FileNotFoundError:
            self._banner_text = "Welcome to itter.sh!\n(Banner file not found)"

        super().__init__()

    # Method to receive the active sessions reference from the factory
    def set_active_sessions_ref(self, sessions_dict: Dict[str, "ItterShell"]):
        self._active_sessions = sessions_dict

    def _write_to_channel(self, message: str = "", newline: bool = True):
        if self._chan:
            try:
                processed_message = message.replace("\r\n", "\n").replace("\n", "\r\n")
                if newline:
                    if not processed_message.endswith("\r\n"):
                        processed_message += "\r\n"
                else:
                    if processed_message.endswith("\r\n"):
                        processed_message = processed_message[:-2]
                self._chan.write(processed_message)
            except (
                OSError,
                asyncssh.Error,
                ConnectionResetError,
                BrokenPipeError,
            ) as e:
                utils.debug_log(f"Failed to write basic content: {e}")

    def _get_prompt_text(self):
        return f"({self.username})itter> "

    def _prompt(self):
        if self.username:
            prompt_text = self._get_prompt_text()
            self._write_to_channel(prompt_text, newline=False)

    def _redraw_prompt_and_buffer(self):
        if self._chan and self.username:
            self._prompt()
            if self._input_buffer:
                self._write_to_channel(self._input_buffer, newline=False)

    def _redraw_input_line(self):
        # Move cursor to the beginning of the line
        self._write_to_channel("\r", newline=False)
        # Get prompt text to calculate length accurately
        prompt_text = self._get_prompt_text()
        # Calculate total length of prompt + current buffer
        total_len_to_clear = len(prompt_text) + len(self._input_buffer)
        # Overwrite existing input with spaces to clear the line visually
        self._write_to_channel(" " * total_len_to_clear, newline=False)
        # Move cursor back to the beginning of the line again
        self._write_to_channel("\r", newline=False)

    def connection_made(self, chan: asyncssh.SSHServerChannel) -> None:
        utils.debug_log(
            f"ItterShell connection_made for {'REGISTRATION' if self._is_registration_flow else self.username}"
        )
        self._chan = chan

        # --- Capture client IP ---
        if self._chan:
            peername = self._chan.get_extra_info("peername")
            if peername and isinstance(peername, tuple) and len(peername) > 0:
                self._client_ip = peername[0]
                utils.debug_log("Client IP captured.")
            else:
                self._client_ip = (
                    None  # Should ideally not happen if connection is established
                )
                utils.debug_log("Could not determine client IP for session.")

        if self._is_registration_flow:
            asyncio.create_task(self._handle_registration_flow())
        else:
            if self.username and self._active_sessions is not None:
                self._active_sessions[self.username] = self  # Add self to shared dict
                self._display_welcome_banner()
                self._show_help()
                self._prompt()
            elif not self.username:
                self._write_to_channel("ERROR: Login session started without username.")
                self.close()
            else:  # _active_sessions is None
                self._write_to_channel(
                    "ERROR: Server state error (active_sessions not set)."
                )
                utils.debug_log(
                    "CRITICAL: _active_sessions is None in ItterShell connection_made"
                )
                self.close()

    async def _handle_registration_flow(self):
        # ... (rest of method is likely okay, uses _write_to_channel) ...
        utils.debug_log(f"Finalizing registration for '{self._reg_username_candidate}'")
        if not self._reg_username_candidate or not self._reg_public_key:
            self._write_to_channel(
                "ERROR: Registration Error: Missing username or public key."
            )
            self.close()
            return
        try:
            await db.db_create_user(self._reg_username_candidate, self._reg_public_key)
            success_msg = (
                f"\r\nRegistration successful as user '{self._reg_username_candidate}'!\r\n"
                f"You can now log in via:\r\n"
                f"\r\n"
                f"  > {BOLD}ssh {self._reg_username_candidate}@app.itter.sh{RESET}\r\n"  # Placeholder URL
                f"\r\n"
                f"or {BOLD}ssh{RESET} {FG_BRIGHT_BLACK}-i /path/to/your/private_key{RESET} {BOLD}{self._reg_username_candidate}@app.itter.sh{RESET}"  # Placeholder URL
                f"\r\n"
                f"Have fun & see you on the other side!\r\n"
                f"\r\n"
            )
            self._write_to_channel(success_msg)
            utils.debug_log(
                f"User '{self._reg_username_candidate}' registered successfully."
            )
        except Exception as e:
            utils.debug_log(
                f"[DB ERROR] Registration failed for '{self._reg_username_candidate}': {e}"
            )
            self._write_to_channel(f"ERROR: Registration failed. Details: {e}")
        finally:
            self.close()

    def _display_welcome_banner(self):
        self._clear_screen()
        banner_lines = self._banner_text.splitlines()
        for line in banner_lines:
            self._write_to_channel(line)
        self._write_to_channel()

    def _show_help(self):
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
            f"  {BOLD}s{RESET}ettings                       - View or change settings.\r\n"
            f"  {BOLD}h{RESET}elp                           - Show this help message.\r\n"
            f"  {BOLD}c{RESET}lear                          - Clear the screen.\r\n"
            f"  e{BOLD}x{RESET}it                           - Exit watch mode or itter.sh.\r\n"
        )
        self._write_to_channel(help_text)

    def pty_requested(self, term_type: str, term_size: tuple, term_modes: dict) -> bool:
        cols = 80
        rows = 24
        pixwidth = 0
        pixheight = 0
        try:
            if isinstance(term_size, tuple) and len(term_size) >= 2:
                cols = int(term_size[0]) if term_size[0] > 0 else 80
                rows = int(term_size[1]) if term_size[1] > 0 else 24
            if isinstance(term_size, tuple) and len(term_size) >= 4:
                pixwidth = int(term_size[2]) if term_size[2] else 0
                pixheight = int(term_size[3]) if term_size[3] else 0
        except Exception as e:
            utils.debug_log(f"Error parsing term_size tuple {term_size}: {e}")
        utils.debug_log(
            f"PTY requested: term={term_type}, size={cols}x{rows}, pix={pixwidth}x{pixheight}"
        )
        self._term_width = cols
        self._term_height = rows
        return True

    def shell_requested(self) -> bool:
        utils.debug_log("Shell requested by client.")
        return True

    def data_received(self, data: str, datatype: asyncssh.DataType) -> None:
        if not self._chan:
            return
        utils.debug_log(f"Data received: {data!r} (datatype: {datatype})")

        # Handle escape sequences
        if data.startswith("\x1b"):
            if data == "\x1b[A":  # Up arrow
                self._redraw_input_line()
                command = self._command_history.scroll_up()
                self._input_buffer = command  # Update buffer before writing
                self._prompt()
                self._write_to_channel(self._input_buffer, newline=False)
                return
            elif data == "\x1b[B":  # Down arrow
                self._redraw_input_line()
                command = self._command_history.scroll_down()
                self._input_buffer = command  # Update buffer before writing
                self._prompt()
                self._write_to_channel(self._input_buffer, newline=False)
                return
            # Page Up: \x1b[5~ , Page Down: \x1b[6~
            elif data == "\x1b[5~":  # Page Up
                if (
                    self._is_watching_timeline
                    or self._last_timeline_eets_count is not None
                ):
                    utils.debug_log("Page Up received")
                    if self._current_timeline_page > 1:
                        self._current_timeline_page -= 1
                        is_live = self._is_watching_timeline

                        async def _pg_up_scroll():
                            await self._render_and_display_timeline(
                                page=self._current_timeline_page, is_live_update=is_live
                            )
                            if not is_live:
                                self._prompt()  # Manually prompt after static render if not live

                        asyncio.create_task(_pg_up_scroll())
                    else:
                        self._write_to_channel(
                            "\r\nAlready at the first page.", newline=True
                        )
                        self._redraw_prompt_and_buffer()  # Redraw prompt if at first page
                    return
            elif data == "\x1b[6~":  # Page Down
                if (
                    self._is_watching_timeline
                    or self._last_timeline_eets_count is not None
                ):
                    utils.debug_log("Page Down received")
                    # Only allow page down if we previously got a full page or are watching
                    can_page_down = self._is_watching_timeline or (
                        self._last_timeline_eets_count is not None
                        and self._last_timeline_eets_count >= self._timeline_page_size
                    )
                    if can_page_down:
                        self._current_timeline_page += 1
                        is_live = self._is_watching_timeline

                        async def _pg_down_scroll():
                            await self._render_and_display_timeline(
                                page=self._current_timeline_page, is_live_update=is_live
                            )
                            if not is_live:
                                self._prompt()  # Manually prompt after static render if not live

                        asyncio.create_task(_pg_down_scroll())
                    else:
                        self._write_to_channel(
                            "\r\nAlready at the last page or no more items.",
                            newline=True,
                        )
                        self._redraw_prompt_and_buffer()  # Redraw prompt if at last page
                    return
            # Other escape sequences (like Home, End, Delete) can be added here if needed.
            # Example: \x1b[H (Home), \x1b[F (End) or \x1b[4~ (End), \x1b[3~ (Delete)
            # For now, just log them.
            else:
                utils.debug_log(f"Unhandled escape sequence: {data!r}")
                # Potentially echo back or ignore. For now, ignore.
                return  # Important to return to prevent processing as normal chars

        # Handle normal character input (no longer inside the if data.startswith("\x1b") block)
        for char in data:
            if char in ("\r", "\n"):
                self._write_to_channel()
                line_to_process = self._input_buffer
                self._input_buffer = ""
                if line_to_process:
                    utils.debug_log(f"Processing command line: '{line_to_process}'")
                    asyncio.create_task(self._handle_command_line(line_to_process))
                else:
                    self._prompt()
            elif char == "\x7f":  # Backspace
                if self._input_buffer:
                    self._input_buffer = self._input_buffer[:-1]
                    self._write_to_channel("\b \b", newline=False)
            elif char == "\x03":  # Ctrl+C
                self._write_to_channel("^C\r\n", newline=False)
                self.close()
            elif char == "\x04":  # Ctrl+D
                self._write_to_channel("^D\r\n", newline=False)
                self.close()
            elif char == "\x15":  # Ctrl+U (kill line)
                if self._input_buffer:
                    self._redraw_input_line()  # Clears the line
                    self._input_buffer = ""
                    self._prompt()  # Redraw prompt
            elif char == "\x17":  # Ctrl+W (kill word)
                if self._input_buffer:
                    old_buffer = self._input_buffer
                    self._redraw_input_line()  # Clears current line visually

                    # Find the start of the last word
                    i = len(old_buffer) - 1
                    while i >= 0 and old_buffer[i].isspace():  # Skip trailing spaces
                        i -= 1
                    j = i
                    while j >= 0 and not old_buffer[j].isspace():  # Go to start of word
                        j -= 1

                    self._input_buffer = old_buffer[
                        : j + 1
                    ]  # Keep part before the word
                    self._prompt()  # Redraw prompt
                    self._write_to_channel(
                        self._input_buffer, newline=False
                    )  # Redraw new buffer
            elif char.isprintable():
                self._input_buffer += char
                self._write_to_channel(char, newline=False)
            else:
                utils.debug_log(f"Ignoring unhandled character: {char!r}")

    def _clear_screen(self):
        if self._chan:
            self._chan.write("\033[2J\033[H")

    async def _display_follow_lists(self):
        if not self.username:
            return

        try:
            following_list = await db.db_get_user_following(self.username)
            followers_list = await db.db_get_user_followers(self.username)
            following_channels_list = await db.db_get_user_following_channels(
                self.username
            )
            following_channels_list = await db.db_get_user_following_channels(
                self.username
            )
        except Exception as e:
            self._write_to_channel(f"Error fetching follow lists: {e}")
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

        output_lines.append("\r\n")  # Extra newline for spacing before prompt
        self._write_to_channel("\r\n".join(output_lines))

    async def _display_ignore_list(self):
        if not self.username:
            return

        try:
            ignoring_list = await db.db_get_user_ignoring(self.username)
        except Exception as e:
            self._write_to_channel(f"Error fetching ignore list: {e}")
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

        output_lines.append("\r\n")  # Extra newline for spacing before prompt
        self._write_to_channel("\r\n".join(output_lines))

    async def _handle_command_line(self, line: str):
        if not self.username and not self._is_registration_flow:
            self._write_to_channel("ERROR: Critical error: No user context.")
            self.close()
            return

        cmd, raw_text_full, hashtags_in_full_line, user_refs_in_full_line = (
            utils.parse_input_line(line)
        )
        # For follow/unfollow, hashtags_in_full_line and user_refs_in_full_line are not reliable
        # for the target itself if the target is the *only* argument.
        # We will parse the target from raw_text_full.

        cmd, raw_text_full, hashtags_in_full_line, user_refs_in_full_line = (
            utils.parse_input_line(line)
        )

        utils.debug_log(
            f"Parsed command: cmd='{cmd}', raw_text_full='{raw_text_full}', hashtags_in_full_line={hashtags_in_full_line}, user_refs_in_full_line={user_refs_in_full_line}"
        )

        if not cmd:
            self._prompt()
            return

        try:
            # Add command to history
            self._command_history.add((cmd + " " + raw_text_full.strip()).strip())
            self._last_timeline_eets_count = (
                None  # Reset timeline context for non-timeline commands
            )

            if cmd == "eet" or cmd == "e":
                content = raw_text_full.strip()
                if not content:
                    self._write_to_channel("Usage: eet <text>")
                elif len(content) > config.EET_MAX_LENGTH:
                    self._write_to_channel(
                        f"ERROR: Eet too long! Max {config.EET_MAX_LENGTH}."
                    )
                else:
                    await db.db_post_eet(
                        self.username,
                        content,
                        hashtags_in_full_line,
                        user_refs_in_full_line,
                        self._client_ip,
                    )
                    self._write_to_channel("Eet posted!")
                    if self._is_watching_timeline:
                        utils.debug_log(
                            "Eet posted while watching, triggering immediate timeline refresh."
                        )
                        await self._render_and_display_timeline(
                            page=1, is_live_update=True
                        )
            elif cmd == "timeline" or cmd == "tl" or cmd == "watch" or cmd == "w":
                self._current_timeline_page = 1
                target_specifier_text = (
                    raw_text_full  # Use raw_text_full here for parsing page and target
                )
                parts = raw_text_full.split()
                page_from_input = None
                self._current_timeline_page = (
                    1  # Default to page 1 for new timeline/watch command
                )
                target_specifier_text = raw_text_full
                parts = raw_text_full.split()
                page_from_input = None
                if parts and parts[-1].isdigit():
                    page_from_input = int(parts[-1])
                    target_specifier_text = " ".join(parts[:-1]).strip()

                # Re-evaluate target type based on target_specifier_text
                # (which now excludes the page number if it was present)

                # Quick check for specific target types first
                if target_specifier_text.startswith("@"):
                    user_match = re.match(r"^@(\w{3,20})$", target_specifier_text)
                    if user_match:
                        self._current_target_filter = {
                            "type": "user",
                            "value": user_match.group(1),
                        }
                    else:  # Invalid user format after @
                        self._write_to_channel(
                            f"Invalid user format: '{target_specifier_text}'. Defaulting to 'all'."
                        )
                        self._current_target_filter = {"type": "all", "value": None}
                elif target_specifier_text.startswith("#"):
                    channel_match = re.match(
                        r"^#(\w(?:[\w-]*\w)?)$", target_specifier_text
                    )  # More robust channel regex
                    if channel_match:
                        self._current_target_filter = {
                            "type": "channel",
                            "value": channel_match.group(1),
                        }
                    else:  # Invalid channel format after #
                        self._write_to_channel(
                            f"Invalid channel format: '{target_specifier_text}'. Defaulting to 'all'."
                        )
                        self._current_target_filter = {"type": "all", "value": None}
                else:  # Not starting with @ or #, use general parser
                    self._current_target_filter = utils.parse_target_filter(
                        target_specifier_text
                    )

                if page_from_input is not None:
                    self._current_timeline_page = page_from_input

                utils.debug_log(
                    f"Timeline/Watch target set to: {self._current_target_filter}, page: {self._current_timeline_page}"
                )
                if cmd == "watch" or cmd == "w":
                    self._is_watching_timeline = True
                    await (
                        self._start_live_timeline_view()
                    )  # Will use current page (likely 1 for watch start)
                    return
                else:  # timeline or tl
                    self._is_watching_timeline = False
                    await self._render_and_display_timeline(
                        page=self._current_timeline_page
                    )
            elif cmd == "follow" or cmd == "f":
                target_text = raw_text_full.strip()
                if target_text.lower() == "--list":
                    await self._display_follow_lists()
                elif target_text.startswith("#"):
                    channel_tag_to_follow = target_text[1:]
                    if not channel_tag_to_follow or not re.match(
                        r"^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$",
                        channel_tag_to_follow,
                    ):
                        self._write_to_channel(
                            f"Invalid channel name format: '#{channel_tag_to_follow}'. Must be alphanumeric with hyphens, not starting/ending with hyphen."
                        )
                    else:
                        await db.db_follow_channel(self.username, channel_tag_to_follow)
                        self._write_to_channel(
                            f"Now following channel {FG_MAGENTA}#{channel_tag_to_follow.lower()}{RESET}. Posts from this channel will appear in your 'mine' feed."
                        )
                elif target_text.startswith("@"):
                    target_user_to_follow = target_text[1:]
                    if not target_user_to_follow or not re.match(
                        r"^[a-zA-Z0-9_]{3,20}$", target_user_to_follow
                    ):
                        self._write_to_channel(
                            "Invalid username format: '@username' (3-20 alphanumeric/underscore)."
                        )
                    else:
                        await db.db_follow_user(self.username, target_user_to_follow)
                        self._write_to_channel(
                            f"Following {FG_CYAN}@{target_user_to_follow}{RESET}. You will now see their posts on your 'mine' page."
                        )
                else:
                    self._write_to_channel(
                        f"Usage: {BOLD}follow @<user>{RESET} OR {BOLD}follow #<channel>{RESET} OR {BOLD}follow --list{RESET}"
                    )

            elif cmd == "unfollow" or cmd == "uf":
                target_text = raw_text_full.strip()
                if target_text.startswith("#"):
                    channel_tag_to_unfollow = target_text[1:]
                    if not channel_tag_to_unfollow or not re.match(
                        r"^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$",
                        channel_tag_to_unfollow,
                    ):
                        self._write_to_channel(
                            f"Invalid channel name format: '#{channel_tag_to_unfollow}'."
                        )
                    else:
                        await db.db_unfollow_channel(
                            self.username, channel_tag_to_unfollow
                        )
                        self._write_to_channel(
                            f"No longer following channel {FG_MAGENTA}#{channel_tag_to_unfollow.lower()}{RESET}."
                        )
                elif target_text.startswith("@"):
                    target_user_to_unfollow = target_text[1:]
                    if not target_user_to_unfollow or not re.match(
                        r"^[a-zA-Z0-9_]{3,20}$", target_user_to_unfollow
                    ):
                        self._write_to_channel("Invalid username format: '@username'.")
                    else:
                        await db.db_unfollow_user(
                            self.username, target_user_to_unfollow
                        )
                        self._write_to_channel(
                            f"Unfollowed {FG_CYAN}@{target_user_to_unfollow}{RESET}. They won't show up on your 'mine' page anymore."
                        )
                else:
                    self._write_to_channel(
                        f"Usage: {BOLD}unfollow @<user>{RESET} OR {BOLD}unfollow #<channel>{RESET}"
                    )
            elif cmd == "ignore" or cmd == "i":
                target_text_ignore = raw_text_full.strip()
                if target_text_ignore.lower() == "--list":
                    await self._display_ignore_list()
                elif target_text_ignore.startswith("@"):
                    target_user_to_ignore = target_text_ignore[1:]
                    if not target_user_to_ignore or not re.match(
                        r"^[a-zA-Z0-9_]{3,20}$", target_user_to_ignore
                    ):
                        self._write_to_channel("Invalid username format: '@username'.")
                    elif target_user_to_ignore == self.username:
                        self._write_to_channel(
                            "You cannot ignore yourself. (That's what my psychologist said)"
                        )
                    else:
                        await db.db_ignore_user(self.username, target_user_to_ignore)
                        self._write_to_channel(
                            f"Okay, @{target_user_to_ignore} will now be ignored. Their posts won't appear in your timelines. Phew."
                        )
                else:
                    self._write_to_channel(
                        f"Usage: {BOLD}ignore @<user>{RESET} OR {BOLD}ignore --list{RESET}"
                    )

            elif cmd == "unignore" or cmd == "ui":
                target_text_unignore = raw_text_full.strip()
                if target_text_unignore.startswith("@"):
                    target_user_to_unignore = target_text_unignore[1:]
                    if not target_user_to_unignore or not re.match(
                        r"^[a-zA-Z0-9_]{3,20}$", target_user_to_unignore
                    ):
                        self._write_to_channel("Invalid username format: '@username'.")
                    else:
                        await db.db_unignore_user(
                            self.username, target_user_to_unignore
                        )
                        self._write_to_channel(
                            f"Okay, @{target_user_to_unignore} is forgiven and will no longer be ignored. You'll see their posts again."
                        )
                else:
                    self._write_to_channel(f"Usage: {BOLD}unignore @<user>{RESET}")
            elif cmd == "profile" or cmd == "p":
                await self._handle_profile_command(
                    raw_text_full, user_refs_in_full_line
                )
            elif cmd == "settings" or cmd == "s":
                parts = raw_text_full.lower().split()
                if not parts:  # Just "settings"
                    self._write_to_channel(
                        f"\r\nCurrent settings:\r\n"
                        f"  Eets per page: {BOLD}{self._timeline_page_size}{RESET}\r\n"
                        f"  {FG_BRIGHT_BLACK}Usage:{RESET} settings pagesize <{config.MIN_TIMELINE_PAGE_SIZE}-{config.MAX_TIMELINE_PAGE_SIZE}>"
                    )
                elif len(parts) == 2 and parts[0] == "pagesize":
                    try:
                        new_size = int(parts[1])
                        if (
                            config.MIN_TIMELINE_PAGE_SIZE
                            <= new_size
                            <= config.MAX_TIMELINE_PAGE_SIZE
                        ):
                            self._timeline_page_size = new_size
                            self._write_to_channel(
                                f"All right! You will now see {new_size} eets per page."
                            )
                        else:
                            self._write_to_channel(
                                f"Error: Page size must be between {config.MIN_TIMELINE_PAGE_SIZE} and {config.MAX_TIMELINE_PAGE_SIZE}."
                            )
                    except ValueError:
                        self._write_to_channel("That... was not a number.")
                else:
                    self._write_to_channel(
                        f"{FG_BRIGHT_BLACK}Usage:{RESET} settings pagesize <{config.MIN_TIMELINE_PAGE_SIZE}-{config.MAX_TIMELINE_PAGE_SIZE}>"
                    )
            elif cmd == "help" or cmd == "h":
                self._display_welcome_banner()
                self._show_help()
            elif cmd == "clear" or cmd == "c":
                self._clear_screen()
                if self._is_watching_timeline:
                    await self._render_and_display_timeline(
                        page=self._current_timeline_page, is_live_update=True
                    )
                else:  # If not watching, after clear, just show prompt
                    self._prompt()
                return
            elif cmd == "exit" or cmd == "x":
                await self._handle_exit_command()
                return
            else:
                self._write_to_channel(f"Unknown command: '{cmd}'. Type 'help'.")
        except ValueError as ve:
            self._write_to_channel(f"Error: {ve}")
        except Exception as e:
            utils.debug_log(f"Error handling command '{cmd}': {e}")
            self._write_to_channel(f"An unexpected server error occurred.")
            if config.ITTER_DEBUG_MODE:
                import traceback

                self._write_to_channel(traceback.format_exc())

        if self._chan and not self._is_watching_timeline:
            self._prompt()

    async def _handle_profile_command(self, raw_text: str, user_refs: List[str]):
        args = raw_text.split()
        if args and (args[0].lower() == "edit" or args[0].lower() == "e"):
            new_display_name = None
            new_email = None
            reset_user = False
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
            if new_display_name is None and new_email is None and reset_user is False:
                self._write_to_channel(
                    f"{FG_BRIGHT_BLACK}Usage:{RESET} profile edit -name <Name> -email <Email>  (use --reset to delete all fields)"
                )
            else:
                await db.db_update_profile(
                    self.username, new_display_name, new_email, reset_user
                )
                self._write_to_channel("Profile updated.")
        else:
            profile_username = (
                user_refs[0]
                if user_refs
                else (
                    raw_text.strip().lstrip("@") if raw_text.strip() else self.username
                )
            )
            if profile_username.startswith("#"):  # Cannot view profile of a channel
                self._write_to_channel(
                    f"That's a channel, not a profile: {profile_username}"
                )
                return

            try:
                stats = await db.db_get_profile_stats(profile_username)
                profile_output = (
                    "\r\n"
                    + "\r\n"
                    + "\r\n"
                    + f"\r\n--- Profile: @{stats['username']} ---\r\n"
                    + f"  Display Name: {stats.get('display_name', 'N/A')}\r\n"
                    + f"  Email:        {stats.get('email', 'N/A')}\r\n"
                    + f"  Joined:       {utils.time_ago(stats.get('joined_at'))}\r\n"
                    + f"  Eets:         {stats['eet_count']}\r\n"
                    + f"  Following:    {stats['following_count']}\r\n"
                    + f"  Followers:    {stats['follower_count']}\r\n"
                    + f"---------------------------\r\n"
                )
                self._clear_screen()
                self._display_welcome_banner()
                self._write_to_channel(profile_output)
            except ValueError as ve:
                self._write_to_channel(f"Error: {ve}")
            except Exception as e:
                utils.debug_log(f"Err profile {profile_username}: {e}")
                self._write_to_channel(
                    f"Error fetching profile for @{profile_username}."
                )

    async def _handle_exit_command(self):
        if self._is_watching_timeline:
            self._is_watching_timeline = False
            if (
                self._timeline_auto_refresh_task
                and not self._timeline_auto_refresh_task.done()
            ):
                self._timeline_auto_refresh_task.cancel()
            self._write_to_channel("\nExited live timeline view.")
            self._prompt()
        else:
            self._write_to_channel("\nitter.sh says: Don't let the door hit you!")
            self.close()

    async def _start_live_timeline_view(self):
        self._clear_screen()
        target_type_display = self._current_target_filter["type"]
        target_value_display = self._current_target_filter["value"]
        if target_type_display == "channel" and target_value_display:
            target_display = f"#{target_value_display}"
        elif target_type_display == "user" and target_value_display:
            target_display = f"@{target_value_display}"
        else:
            target_display = target_type_display  # 'all' or 'mine'

        self._write_to_channel(f"Entering live view for {target_display}.")
        self._write_to_channel(
            "(Type 'exit' or Ctrl+C to stop; PgUp/PgDn to scroll recent history)\n"
        )
        # Start watch on page 1
        self._current_timeline_page = 1
        await self._render_and_display_timeline(
            page=self._current_timeline_page, is_live_update=True
        )
        if (
            self._timeline_auto_refresh_task
            and not self._timeline_auto_refresh_task.done()
        ):
            self._timeline_auto_refresh_task.cancel()
        self._timeline_auto_refresh_task = asyncio.create_task(
            self._timeline_refresh_loop()
        )

    async def _timeline_refresh_loop(self):
        try:
            while self._is_watching_timeline:
                await asyncio.sleep(config.WATCH_REFRESH_INTERVAL_SECONDS)
                if self._is_watching_timeline:
                    utils.debug_log("Live timeline auto-refresh triggered.")
                    # Live refresh always fetches page 1 of the current filter
                    await self._render_and_display_timeline(page=1, is_live_update=True)
        except asyncio.CancelledError:
            utils.debug_log("Timeline refresh loop cancelled.")
        except Exception as e:
            utils.debug_log(f"Error in timeline refresh loop: {e}")
            if self._is_watching_timeline and self._chan:
                self._clear_screen()  # Clear screen before showing error and prompt
                self._write_to_channel(f"ERROR: Live timeline update error: {e}\r\n")
                self._redraw_prompt_and_buffer()

    async def _render_and_display_timeline(
        self, page: int, is_live_update: bool = False
    ):
        if not self.username:
            return
        try:
            self._current_timeline_page = page  # Update current page
            eets = await db.db_get_filtered_timeline_posts(
                self.username,
                self._current_target_filter,
                page=self._current_timeline_page,
                page_size=self._timeline_page_size,
            )
        except Exception as e:
            error_message = f"Timeline Error: {e}"
            if is_live_update and self._chan:
                self._clear_screen()
                self._write_to_channel(error_message + "\r\n")
                self._redraw_prompt_and_buffer()
            elif self._chan:
                self._write_to_channel(error_message)
            return

        self._last_timeline_eets_count = len(eets)  # Update for PgUp/PgDn context

        formatted_output = await asyncio.to_thread(
            self._format_timeline_output, eets, self._current_timeline_page
        )

        self._clear_screen()
        self._write_to_channel(formatted_output, newline=True)

        if is_live_update:
            self._redraw_prompt_and_buffer()
        # For non-live (manual timeline command), the prompt will be added by _handle_command_line
        # *unless* this was called from a PgUp/PgDn handler, in which case prompt is handled there.

    def _format_timeline_output(self, eets: List[Dict[str, Any]], page: int) -> str:
        time_w = 12
        user_w = 20  # Max visual width for user column
        sep_w = 3
        # Calculate eet_w based on visual width used by other columns
        eet_w = max(
            10, self._term_width - time_w - user_w - (sep_w * 2) - 2
        )  # -2 for final margins

        target_type_display = self._current_target_filter["type"]
        target_value_display = self._current_target_filter["value"]

        if target_type_display == "channel" and target_value_display:
            timeline_title = f"#{target_value_display}"
        elif target_type_display == "user" and target_value_display:
            timeline_title = f"@{target_value_display}"
        elif target_type_display == "mine":
            timeline_title = "Your 'Mine' Feed"
        else:
            timeline_title = "All Eets"

        header_line = (
            f"--- {timeline_title} (Page {page}, {self._timeline_page_size} items) ---"
        )

        output_lines = [f"{BOLD}{header_line}{RESET}"]
        output_lines.append(
            f"{'Time':<{time_w}}   {'User':<{user_w}}   {'Eet':<{eet_w}}"
        )
        output_lines.append(
            f"{FG_BRIGHT_BLACK}"
            + "-" * min(self._term_width, time_w + user_w + eet_w + (sep_w * 2))
            + f"{RESET}"
        )

        if not eets:
            output_lines.append(
                " No eets found."
                if page == 1
                else f" End of timeline for {timeline_title}."
            )
        else:
            for eet in eets:
                time_str = utils.time_ago(eet.get("created_at"))

                # User column formatting
                author_username = eet.get("username", "ghost")
                author_display_name = eet.get("display_name")
                user_display_str_raw = f"@{author_username}"

                user_final_display_name = (
                    f"{author_display_name} ({user_display_str_raw})"
                    if author_display_name
                    else user_display_str_raw
                )

                # Truncate based on visual width
                user_final_display_name_truncated = utils.truncate_str_with_wcwidth(
                    user_final_display_name, user_w
                )

                user_column_str_colored = user_final_display_name_truncated
                if (
                    author_username.lower() == self.username.lower()
                ):  # Current user's post
                    user_column_str_colored = f"{utils.FG_BRIGHT_YELLOW}{user_final_display_name_truncated}{utils.RESET}"
                # No special color for other users in this column. Mentions are handled in content.

                # Pad the user column string correctly, considering ANSI and wcwidth
                user_col_visual_width_after_truncate = utils.wcswidth(
                    user_final_display_name_truncated
                )
                user_col_padding_spaces = user_w - user_col_visual_width_after_truncate
                user_col_padded_final = (
                    f"{user_column_str_colored}{' ' * max(0, user_col_padding_spaces)}"
                )

                # Eet content formatting
                raw_content = (
                    eet.get("content", "").replace("\r", "").replace("\n", " ")
                )

                # Wrap raw content first using a TextWrapper that is NOT wcwidth aware for wrapping itself
                # but we use wcwidth for display calculations line by line.
                # This is a compromise as TextWrapper doesn't handle wcwidth + ANSI.
                wrapper = textwrap.TextWrapper(
                    width=eet_w,
                    subsequent_indent="  ",
                    break_long_words=True,
                    break_on_hyphens=True,
                    replace_whitespace=False,
                    drop_whitespace=True,
                )  # Standard textwrap

                content_lines_raw = wrapper.wrap(
                    text=utils.strip_ansi(raw_content)
                )  # Wrap the plain text

                if not content_lines_raw:  # Should not happen if raw_content exists
                    output_lines.append(
                        f"{time_str:<{time_w}} | {user_col_padded_final} | "
                    )
                else:
                    # Format the first line (which was already wrapped from plain text)
                    first_line_formatted = utils.format_eet_content(
                        content_lines_raw[0], self.username, utils.FG_BRIGHT_YELLOW
                    )
                    output_lines.append(
                        f"{time_str:<{time_w}} "
                        + " "
                        + f" {user_col_padded_final} "
                        + " "
                        + f" {first_line_formatted}"
                    )

                    # Calculate indent based on visual widths of static parts
                    indent_visual_width = time_w + sep_w + user_w + sep_w
                    indent_str = " " * indent_visual_width
                    for i in range(1, len(content_lines_raw)):
                        # Format subsequent lines
                        line_formatted = utils.format_eet_content(
                            content_lines_raw[i], self.username, utils.FG_BRIGHT_YELLOW
                        )
                        output_lines.append(f"{indent_str}{line_formatted}")

        footer_lines = []
        if self._is_watching_timeline:
            status = f"Live updating... {FG_BRIGHT_BLACK}(exit to stop, (Shift +) PgUp/PgDn to scroll){RESET}"
            footer_lines.append(status)
        else:  # Static timeline view (not watching)
            footer = ""
            base_command_parts = ["timeline"]  # or "tl"
            if (
                self._current_target_filter["type"] == "user"
                and self._current_target_filter["value"]
            ):
                base_command_parts.append(f"@{self._current_target_filter['value']}")
            elif (
                self._current_target_filter["type"] == "channel"
                and self._current_target_filter["value"]
            ):
                base_command_parts.append(f"#{self._current_target_filter['value']}")
            elif self._current_target_filter["type"] != "all":  # e.g. 'mine'
                base_command_parts.append(self._current_target_filter["type"])

            base_command_str = " ".join(base_command_parts)

            if (
                not eets and page > 1
            ):  # No eets on this page, but it's not the first page
                footer = f"No more eets on page {page}. Type `{base_command_str} {page - 1}` for previous."
            elif len(eets) >= self._timeline_page_size:  # Full page, more might exist
                footer = f"Type `{base_command_str} {page + 1}` for more, or `{base_command_str} {page - 1}` for previous (if page > 1)."
            elif (
                eets
            ):  # Some eets, but less than page size (i.e., last page of results)
                footer = f"End of results on page {page}."
                if page > 1:
                    footer += f" Type `{base_command_str} {page - 1}` for previous."

            if footer:
                footer_lines.append(footer)

        if footer_lines:
            output_lines.append("\r\n" + "\r\n".join(footer_lines))

        return "\r\n".join(output_lines)

    async def handle_new_post_realtime(self, post_record: Dict[str, Any]):
        if not self._is_watching_timeline or not self.username:
            return
        utils.debug_log(f"RT check for {self.username}: Post {post_record.get('id')}")

        # In watch mode, we always refresh if _is_watching_timeline is true,
        # and the underlying RPC (`get_timeline`, `get_all_posts_timeline`, etc.)
        # is responsible for filtering based on self._current_target_filter.
        # The RPCs also handle ignored users.

        # The key is that handle_new_post_realtime is only called if a new post is inserted globally.
        # The _render_and_display_timeline will then re-fetch based on the current filter.

        # For "mine" feed, this means the get_timeline RPC must be correctly
        # filtering by followed users AND followed channels.
        # For "channel" feed, get_channel_timeline RPC filters by that channel.
        # For "user" feed, get_user_posts_timeline RPC filters by that user.
        # For "all" feed, get_all_posts_timeline fetches all relevant posts.

        # So, if the user is watching, we just refresh their current view to page 1.
        utils.debug_log(
            f"RT relevant for {self.username} (is watching), refreshing to page 1."
        )
        await self._render_and_display_timeline(
            page=1, is_live_update=True
        )  # Refresh to page 1

    def connection_lost(self, exc: Optional[Exception]) -> None:
        utils.debug_log(
            f"ItterShell connection_lost for {self.username or 'REGISTRATION'}: {exc}"
        )
        if (
            self.username
            and self._active_sessions
            and self.username in self._active_sessions
        ):
            try:
                del self._active_sessions[self.username]
            except KeyError:
                pass  # Already removed
        if (
            self._timeline_auto_refresh_task
            and not self._timeline_auto_refresh_task.done()
        ):
            self._timeline_auto_refresh_task.cancel()
        self._chan = None

    def close(self) -> None:
        if self._chan:
            self._chan.close()


# --- SSH Server Start Function ---
async def start_ssh_server(
    sessions_dict: Dict[str, ItterShell],
):  # Use correct type hint
    """Starts the AsyncSSH server."""
    init_ssh(sessions_dict)
    utils.debug_log(f"Starting SSH server on {config.SSH_HOST}:{config.SSH_PORT}")
    try:
        await asyncssh.create_server(
            ItterSSHServer,
            config.SSH_HOST,
            config.SSH_PORT,
            server_host_keys=[config.SSH_HOST_KEY_PATH],
            line_editor=False,  # We handle line editing
            # term_type='xterm-256color' # Can suggest a term type to client
        )
        print(f"itter.sh server humming on ssh://{config.SSH_HOST}:{config.SSH_PORT}")
        print("Ctrl+C to stop.")
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] SSH server failed to start: {e}\n")
        sys.exit(1)

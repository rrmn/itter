# /Users/roman/work/itter/ssh_server.py
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

from utils import (
    BOLD,
    FG_BRIGHT_BLACK,
    RESET,
    FG_CYAN,
    FG_MAGENTA,
    FG_BRIGHT_YELLOW,
    FG_GREEN,
)

# Global reference - will be set by main.py
# Use forward reference for type hint to avoid circular import if needed later
active_sessions_ref: Optional[Dict[str, "ItterShell"]] = None


def init_ssh(sessions_dict: Dict[str, "ItterShell"]):
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
                
                await asyncio.sleep(0.1) # Brief pause for banner

                utils.debug_log(f"Requesting client disconnect after sending auth banner for: {message.strip()}")
                # If begin_auth returns False, asyncssh should handle the auth failure.
                # Calling disconnect() here is an explicit request to close the connection
                # if it hasn't already started closing due to auth failure.
                # The try/except will catch errors if it's already closing.
                self._conn.disconnect(
                    14, "Authentication failed"
                )

            except asyncssh.Error as e:

                await asyncio.sleep(0.1)
                utils.debug_log(
                    f"Requesting client disconnect after sending auth banner for: {message.strip()} (Error: {e})"
                )
                self._conn.disconnect(14, "Authentication failed")
            except asyncssh.Error as e:
                utils.debug_log(
                    f"asyncssh error during _send_auth_failure_message (banner/disconnect): {e}"
                )
            except Exception as e:
                utils.debug_log(
                    f"Generic error during _send_auth_failure_message (banner/disconnect): {e}"
                )
        else:
            utils.debug_log(f"No connection object (_conn) available to send auth failure message: {message}")

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
                return False
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

    def session_requested(self) -> Optional["ItterShell"]:
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
                utils.debug_log(f"[CRITICAL] session_requested: In registration flow but registration_username_candidate ('{self.registration_username_candidate}') or submitted_public_key is missing. Refusing session.")
                
                self._conn.send_auth_banner("Registration process incomplete. Please try again.\r\n") # Optional
                self._conn.disconnect(...) # Optional
                return None  # Refuse session
                
                return None
        elif self.current_username:
            utils.debug_log(f"Creating ItterShell for LOGIN of '{self.current_username}'")
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
            utils.debug_log(f"[CRITICAL] session_requested: No valid user context (not registration, no current_username). Refusing session.")
            return None
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
        self._active_sessions: Optional[Dict[str, "ItterShell"]] = None
        self._client_ip: Optional[str] = None
        self._timeline_page_size = config.DEFAULT_TIMELINE_PAGE_SIZE
        # For PgUp/PgDn context
        self._last_timeline_eets_count: Optional[int] = None

        # Sidebar related state
        self._sidebar_enabled: bool = False
        self._sidebar_scroll_offset: int = 0
        self._sidebar_full_user_list: List[str] = []

        try:
            with open(config.BANNER_FILE, "r") as f:
                self._banner_text = f.read()
        except FileNotFoundError:
            self._banner_text = "Welcome to itter.sh!\n(Banner file not found)"
        super().__init__()

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
        # Get prompt text to calculate length accurately
        self._write_to_channel("\r", newline=False)
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
                # Should ideally not happen if connection is established
                self._client_ip = (
                    None
                )
                utils.debug_log("Could not determine client IP for session.")
        if self._is_registration_flow:
            asyncio.create_task(self._handle_registration_flow())
        else:
            if self.username and self._active_sessions is not None:
                self._active_sessions[self.username] = self
                self._display_welcome_banner()
                self._show_help()
                self._prompt()
            elif not self.username:
                self._write_to_channel("ERROR: Login session started without username.")
                self.close()
            else:
                self._write_to_channel(
                    "ERROR: Server state error (active_sessions not set)."
                )
                utils.debug_log(
                    "CRITICAL: _active_sessions is None in ItterShell connection_made"
                )
                self.close()

    async def _handle_registration_flow(self):
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
                f"  > {BOLD}ssh {self._reg_username_candidate}@app.itter.sh{RESET}\r\n"
                f"\r\n"
                f"or {BOLD}ssh{RESET} {FG_BRIGHT_BLACK}-i /path/to/your/private_key{RESET} {BOLD}{self._reg_username_candidate}@app.itter.sh{RESET}"
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
                self._input_buffer = command
                self._prompt()
                self._write_to_channel(self._input_buffer, newline=False)
                return
            elif data == "\x1b[B":  # Down arrow
                self._redraw_input_line()
                command = self._command_history.scroll_down()
                self._input_buffer = command
                self._prompt()
                self._write_to_channel(self._input_buffer, newline=False)
                return
            # Page Up: \x1b[5~ , Page Down: \x1b[6~
            elif data == "\x1b[5~": # Page Up (timeline scroll)
                if self._is_watching_timeline:
                    if self._current_timeline_page > 1:
                        self._current_timeline_page -= 1
                        asyncio.create_task(
                            self._refresh_watch_display(
                                timeline_page_to_fetch=self._current_timeline_page
                            )
                        )
                    else:  # Already at first page of timeline
                        self._write_to_channel(
                            "\r\nAlready at the first page of timeline.", newline=True
                        )
                        self._redraw_prompt_and_buffer()
                    return
                elif (
                    self._last_timeline_eets_count is not None
                ):  # Static timeline with previous results
                    if self._current_timeline_page > 1:
                        self._current_timeline_page -= 1
                        asyncio.create_task(
                            self._render_and_display_timeline(
                                page=self._current_timeline_page, is_live_update=False
                            )
                        )  # is_live_update is False
                    else:
                        self._write_to_channel(
                            "\r\nAlready at the first page.", newline=True
                        )
                        self._prompt()  # Redraw prompt for static timeline
                    return
            elif data == "\x1b[6~":  # Page Down (timeline scroll)
                if self._is_watching_timeline:
                    can_page_down_live = (
                        self._last_timeline_eets_count is not None
                        and self._last_timeline_eets_count >= self._timeline_page_size
                    )
                    if can_page_down_live:  # For live, assume more can come unless last fetch was < page_size
                        self._current_timeline_page += 1
                        asyncio.create_task(
                            self._refresh_watch_display(
                                timeline_page_to_fetch=self._current_timeline_page
                            )
                        )
                    else:
                        self._write_to_channel(
                            "\r\nAlready at the last page of timeline or no more items.",
                            newline=True,
                        )
                        self._redraw_prompt_and_buffer()
                    return
                elif self._last_timeline_eets_count is not None:  # Static timeline
                    can_page_down_static = (
                        self._last_timeline_eets_count >= self._timeline_page_size
                    )
                    if can_page_down_static:
                        self._current_timeline_page += 1
                        asyncio.create_task(
                            self._render_and_display_timeline(
                                page=self._current_timeline_page, is_live_update=False
                            )
                        )
                    else:
                        self._write_to_channel(
                            "\r\nAlready at the last page or no more items.",
                            newline=True,
                        )
                        self._prompt()
                    return
            # Sidebar scroll handlers
            elif data == "\x1b[5;5~":  # Ctrl+PageUp
                if self._sidebar_enabled:  # Only if sidebar is active
                    self._sidebar_scroll_offset = max(
                        0, self._sidebar_scroll_offset - config.SIDEBAR_SCROLL_STEP
                    )
                    utils.debug_log(
                        f"Sidebar scroll up. New offset: {self._sidebar_scroll_offset}"
                    )
                    asyncio.create_task(
                        self._refresh_watch_display(
                            timeline_page_to_fetch=self._current_timeline_page
                        )
                    )  # Redraw with current timeline page
                    return
            elif data == "\x1b[6;5~":  # Ctrl+PageDown
                if self._sidebar_enabled:  # Only if sidebar is active
                    # Calculate max scroll offset for sidebar dynamically
                    num_header_rows = 3
                    num_footer_rows = 1
                    prompt_line_height = 1
                    scrollable_body_height = (
                        self._term_height
                        - num_header_rows
                        - num_footer_rows
                        - prompt_line_height
                    )
                    scrollable_body_height = max(
                        1, scrollable_body_height
                    )  # Ensure at least 1 line

                    max_scroll = max(
                        0, len(self._sidebar_full_user_list) - scrollable_body_height
                    )
                    self._sidebar_scroll_offset = min(
                        max_scroll,
                        self._sidebar_scroll_offset + config.SIDEBAR_SCROLL_STEP,
                    )
                    utils.debug_log(
                        f"Sidebar scroll down. New offset: {self._sidebar_scroll_offset}, Max scroll: {max_scroll}"
                    )
                    asyncio.create_task(
                        self._refresh_watch_display(
                            timeline_page_to_fetch=self._current_timeline_page
                        )
                    )  # Redraw with current timeline page
                    return
            else:
                utils.debug_log(f"Unhandled escape sequence: {data!r}")
                return

        # Handle normal character input
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
            elif char == "\x7f" or char == "\x08":  # Backspace
                if self._input_buffer:
                    self._input_buffer = self._input_buffer[:-1]
                    self._write_to_channel("\b \b", newline=False)
            elif char == "\x03":  # Ctrl+C
                self._write_to_channel("^C\r\n", newline=False)
                self.close()
            elif char == "\x04":  # Ctrl+D
                self._write_to_channel("^D\r\n", newline=False)
                self.close()
            elif char == "\x15":  # Ctrl+U
                if self._input_buffer:
                    self._redraw_input_line()
                    self._input_buffer = ""
                    self._prompt()
            elif char == "\x17":  # Ctrl+W
                if self._input_buffer:
                    old_buffer = self._input_buffer
                    self._redraw_input_line()
                    # Find the start of the last word
                    i = len(old_buffer) - 1
                    while i >= 0 and old_buffer[i].isspace():
                        i -= 1
                    j = i
                    while j >= 0 and not old_buffer[j].isspace():
                        j -= 1
                    self._input_buffer = old_buffer[: j + 1]
                    self._prompt()
                    self._write_to_channel(self._input_buffer, newline=False)
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
        output_lines.append("\r\n")
        self._write_to_channel("\r\n".join(output_lines))

    async def _handle_command_line(self, line: str):
        if not self.username and not self._is_registration_flow:
            self._write_to_channel("ERROR: Critical error: No user context.")
            self.close()
            return
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
            self._command_history.add((cmd + " " + raw_text_full.strip()).strip())
            if cmd not in ["timeline", "tl", "watch", "w"]:
                self._last_timeline_eets_count = None
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
                        await self._refresh_watch_display(timeline_page_to_fetch=1)
            elif cmd == "timeline" or cmd == "tl" or cmd == "watch" or cmd == "w":
                self._current_timeline_page = 1
                target_specifier_text = raw_text_full
                parts = raw_text_full.split()
                page_from_input = None
                if parts and parts[-1].isdigit():
                    page_from_input = int(parts[-1])
                    target_specifier_text = " ".join(parts[:-1]).strip()
                if target_specifier_text.startswith("@"):
                    user_match = re.match(r"^@(\w{3,20})$", target_specifier_text)
                    if user_match:
                        self._current_target_filter = {
                            "type": "user",
                            "value": user_match.group(1),
                        }
                    else:
                        self._write_to_channel(
                            f"Invalid user format: '{target_specifier_text}'. Defaulting to 'all'."
                        )
                        self._current_target_filter = {"type": "all", "value": None}
                elif target_specifier_text.startswith("#"):
                    channel_match = re.match(
                        r"^#(\w(?:[\w-]*\w)?)$", target_specifier_text
                    )
                    if channel_match:
                        self._current_target_filter = {
                            "type": "channel",
                            "value": channel_match.group(1),
                        }
                    else:
                        self._write_to_channel(
                            f"Invalid channel format: '{target_specifier_text}'. Defaulting to 'all'."
                        )
                        self._current_target_filter = {"type": "all", "value": None}
                else:
                    self._current_target_filter = utils.parse_target_filter(
                        target_specifier_text
                    )
                if page_from_input is not None:
                    self._current_timeline_page = page_from_input
                utils.debug_log(
                    f"Timeline/Watch target set to: {self._current_target_filter}, page: {self._current_timeline_page}"
                )

                is_watch_command = cmd == "watch" or cmd == "w"
                self._sidebar_enabled = (
                    is_watch_command  # Enable sidebar only for watch
                )
                self._is_watching_timeline = is_watch_command

                if is_watch_command:
                    self._sidebar_scroll_offset = 0  # Reset scroll on new watch
                    await (
                        self._start_live_timeline_view()
                    )  # Calls _refresh_watch_display
                    return  # Loop is handled by _timeline_refresh_loop
                else:  # Static timeline command
                    await self._render_and_display_timeline(
                        page=self._current_timeline_page, is_live_update=False
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
                if not parts:
                    self._write_to_channel(
                        f"\r\nCurrent settings:\r\n  Eets per page: {BOLD}{self._timeline_page_size}{RESET}\r\n  {FG_BRIGHT_BLACK}Usage:{RESET} settings pagesize <{config.MIN_TIMELINE_PAGE_SIZE}-{config.MAX_TIMELINE_PAGE_SIZE}>"
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
                    await self._refresh_watch_display(
                        timeline_page_to_fetch=self._current_timeline_page
                    )
                else:
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
                self._write_to_channel(
                    f"{FG_BRIGHT_BLACK}Usage:{RESET} profile edit -name <Name> -email <Email> --reset"
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
            if profile_username.startswith("#"):
                self._write_to_channel(
                    f"That's a channel, not a profile: {profile_username}"
                )
                return
            try:
                stats = await db.db_get_profile_stats(profile_username)
                profile_output = (
                    f"\r\n\r\n\r\n\r\n--- Profile: @{stats['username']} ---\r\n"
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

    async def _handle_exit_command(self):  # MODIFIED: Handle sidebar state
        if self._is_watching_timeline:
            self._is_watching_timeline = False
            self._sidebar_enabled = False  # Disable sidebar
            if (
                self._timeline_auto_refresh_task
                and not self._timeline_auto_refresh_task.done()
            ):
                self._timeline_auto_refresh_task.cancel()
            # Restore normal screen after exiting watch mode
            self._clear_screen()
            self._display_welcome_banner()
            self._show_help()
            self._prompt()
        else:
            self._write_to_channel("\nitter.sh says: Don't let the door hit you!")
            self.close()

    async def _start_live_timeline_view(
        self,
    ):  
        # This method is called when 'watch' command starts
        self._sidebar_enabled = True
        self._sidebar_scroll_offset = 0
        self._current_timeline_page = 1

        # Initial display
        await self._refresh_watch_display(timeline_page_to_fetch=1)

        # Start background refresh loop if not already running or if it was cancelled
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
                    await self._refresh_watch_display(
                        timeline_page_to_fetch=1
                    )  # Refresh to page 1, sidebar scroll maintained
        except asyncio.CancelledError:
            utils.debug_log("Timeline refresh loop cancelled.")
        except Exception as e:
            utils.debug_log(f"Error in timeline refresh loop: {e}")
            if self._is_watching_timeline and self._chan:
                self._clear_screen()
                self._write_to_channel(f"ERROR: Live timeline update error: {e}\r\n")
                self._redraw_prompt_and_buffer()

    async def _render_and_display_timeline(
        self, page: int, is_live_update: bool = False
    ):
        """
        Renders timeline. If is_live_update is True (watch mode), it uses _refresh_watch_display.
        Otherwise, it uses the original static timeline formatting.
        """
        if not self.username:
            return

        if (
            is_live_update and self._is_watching_timeline
        ):
            # This path is for watch mode updates (initial, scroll, auto-refresh)
            await self._refresh_watch_display(timeline_page_to_fetch=page)
        else:
            # This path is for the static 'timeline' command
            self._sidebar_enabled = False  # Ensure sidebar is off for static timeline
            try:
                self._current_timeline_page = page
                eets = await db.db_get_filtered_timeline_posts(
                    self.username,
                    self._current_target_filter,
                    page=self._current_timeline_page,
                    page_size=self._timeline_page_size,
                )
                self._last_timeline_eets_count = len(eets)
            except Exception as e:
                error_message = f"Timeline Error: {e}"
                if self._chan:
                    self._write_to_channel(error_message)
                return

            formatted_output = await asyncio.to_thread(
                self._format_timeline_output,
                eets,
                self._current_timeline_page,
            )
            self._clear_screen()
            self._write_to_channel(formatted_output, newline=True)
            self._prompt()

    # This method is ONLY for static timeline display (no sidebar)
    def _format_timeline_output(
        self, eets: List[Dict[str, Any]], page: int
    ) -> str:
        time_w = 12
        user_w = 20
        sep_w = 3
        eet_w = max(10, self._term_width - time_w - user_w - (sep_w * 2) - 2)
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
                author_username = eet.get("username", "ghost")
                author_display_name = eet.get("display_name")
                user_display_str_raw = f"@{author_username}"
                user_final_display_name = (
                    f"{author_display_name} ({user_display_str_raw})"
                    if author_display_name
                    else user_display_str_raw
                )
                user_final_display_name_truncated = utils.truncate_str_with_wcwidth(
                    user_final_display_name, user_w
                )
                user_column_str_colored = user_final_display_name_truncated
                if author_username.lower() == self.username.lower():
                    user_column_str_colored = f"{utils.FG_BRIGHT_YELLOW}{user_final_display_name_truncated}{utils.RESET}"
                user_col_visual_width_after_truncate = utils.wcswidth(
                    utils.strip_ansi(user_final_display_name_truncated)
                )  # Use strip_ansi for wcswidth
                user_col_padding_spaces = user_w - user_col_visual_width_after_truncate
                user_col_padded_final = (
                    f"{user_column_str_colored}{' ' * max(0, user_col_padding_spaces)}"
                )
                raw_content = (
                    eet.get("content", "").replace("\r", "").replace("\n", " ")
                )
                wrapper = textwrap.TextWrapper(
                    width=eet_w,
                    subsequent_indent="  ",
                    break_long_words=True,
                    break_on_hyphens=True,
                    replace_whitespace=False,
                    drop_whitespace=True,
                )
                content_lines_raw = wrapper.wrap(text=utils.strip_ansi(raw_content))
                if not content_lines_raw:
                    output_lines.append(
                        f"{time_str:<{time_w}} | {user_col_padded_final} | "
                    )
                else:
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
                    indent_visual_width = time_w + sep_w + user_w + sep_w
                    indent_str = " " * indent_visual_width
                    for i in range(1, len(content_lines_raw)):
                        line_formatted = utils.format_eet_content(
                            content_lines_raw[i], self.username, utils.FG_BRIGHT_YELLOW
                        )
                        output_lines.append(f"{indent_str}{line_formatted}")
        footer_lines = []
        # Footer logic for static timeline
        base_command_parts = ["timeline"]
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
        elif self._current_target_filter["type"] != "all":
            base_command_parts.append(self._current_target_filter["type"])
        base_command_str = " ".join(base_command_parts)
        footer = ""
        if not eets and page > 1:
            footer = f"No more eets on page {page}. Type `{base_command_str} {page - 1}` for previous."
        elif len(eets) >= self._timeline_page_size:
            footer = f"Type `{base_command_str} {page + 1}` for more, or `{base_command_str} {page - 1}` for previous (if page > 1)."
        elif eets:
            footer = f"End of results on page {page}." + (
                f" Type `{base_command_str} {page - 1}` for previous."
                if page > 1
                else ""
            )
        if footer:
            footer_lines.append(footer)
        if footer_lines:
            output_lines.append("\r\n" + "\r\n".join(footer_lines))
        return "\r\n".join(output_lines)

    # --- Methods for watch mode with sidebar ---
    async def _refresh_watch_display(self, timeline_page_to_fetch: int):
        """Refreshes the entire watch mode screen, including timeline and sidebar."""
        if not self.username or not self._is_watching_timeline:
            return

        self._current_timeline_page = timeline_page_to_fetch
        try:
            eets = await db.db_get_filtered_timeline_posts(
                self.username,
                self._current_target_filter,
                page=self._current_timeline_page,
                page_size=self._timeline_page_size,
            )
            self._last_timeline_eets_count = len(eets)

            if (
                self._sidebar_enabled
            ):  # Should always be true if _is_watching_timeline is true
                await self._update_sidebar_full_user_list()

            screen_output = self._build_watch_screen_output(eets)
            self._clear_screen()
            self._write_to_channel(screen_output, newline=True)
            self._redraw_prompt_and_buffer()

        except Exception as e:
            error_message = f"Timeline Refresh Error: {e}"
            if self._chan:  # Ensure channel exists before trying to write
                self._clear_screen()
                self._write_to_channel(error_message + "\r\n")
                self._redraw_prompt_and_buffer()

    async def _update_sidebar_full_user_list(self):
        """Fetches and formats the list of users for the sidebar."""
        if not self.username or not self._active_sessions:
            self._sidebar_full_user_list = []
            return

        online_usernames = sorted(
            list(self._active_sessions.keys()), key=lambda u: u.lower()
        )

        followed_users_data = await db.db_get_user_following(self.username)  # RPC call
        followed_usernames_set = {
            user_data["username"].lower() for user_data in followed_users_data
        }

        def sort_key(u_name: str):
            is_self = u_name.lower() == self.username.lower()
            is_followed = u_name.lower() in followed_usernames_set
            return (not is_self, not is_followed, u_name.lower())

        sorted_online_users = sorted(online_usernames, key=sort_key)

        formatted_list = []
        for user in sorted_online_users:
            prefix = (
                f"{FG_GREEN}*{RESET} "
                if user.lower() in followed_usernames_set
                and user.lower() != self.username.lower()
                else "  "
            )
            user_display_str = f"{prefix}@{user}"
            if user.lower() == self.username.lower():
                user_display_str = f"  {FG_BRIGHT_YELLOW}@{user}{RESET}"

            truncated_user_str = utils.truncate_str_with_wcwidth(
                user_display_str, config.SIDEBAR_WIDTH - 1
            )
            formatted_list.append(truncated_user_str)
        self._sidebar_full_user_list = formatted_list

    def _get_timeline_body_lines_for_watch(
        self,
        eets: List[Dict[str, Any]],
        timeline_content_width: int,
        num_lines_available: int,
    ) -> List[str]:
        """Generates formatted and padded lines for the timeline body in watch mode."""
        if not self.username:
            return [" " * timeline_content_width] * num_lines_available

        time_w, user_w_max, sep_chars_count = 10, 18, 4  # "  " + "  "
        eet_content_text_width = max(
            10, timeline_content_width - time_w - user_w_max - sep_chars_count
        )

        output_lines = []

        if not eets:
            for _ in range(num_lines_available):
                output_lines.append(" " * timeline_content_width)
            return output_lines[:num_lines_available]  # Ensure exact length

        for eet_idx, eet in enumerate(eets):
            if len(output_lines) >= num_lines_available:
                break

            time_str = utils.time_ago(eet.get("created_at"))
            author_username = eet.get("username", "ghost")
            author_display_name = eet.get("display_name")

            user_display_raw = f"@{author_username}"
            if author_display_name:
                user_display_raw = f"{author_display_name} ({user_display_raw})"

            # Truncate the non-ANSI version for width calculation before coloring
            user_display_truncated_plain = utils.truncate_str_with_wcwidth(
                user_display_raw, user_w_max
            )

            user_col_str_colored = user_display_truncated_plain  # Base for coloring
            if author_username.lower() == self.username.lower():
                user_col_str_colored = (
                    f"{FG_BRIGHT_YELLOW}{user_display_truncated_plain}{RESET}"
                )

            user_col_padding = user_w_max - utils.wcswidth(user_display_truncated_plain)
            user_col_final_padded = (
                f"{user_col_str_colored}{' ' * max(0, user_col_padding)}"
            )

            raw_content = eet.get("content", "").replace("\r", "").replace("\n", " ")
            wrapper = textwrap.TextWrapper(
                width=eet_content_text_width,
                subsequent_indent="  ",
                break_long_words=True,
                break_on_hyphens=True,
                replace_whitespace=False,
                drop_whitespace=True,
            )
            content_lines_wrapped_raw = wrapper.wrap(text=utils.strip_ansi(raw_content))
            if not content_lines_wrapped_raw:
                content_lines_wrapped_raw = [""]

            for i, line_content_raw in enumerate(content_lines_wrapped_raw):
                if len(output_lines) >= num_lines_available:
                    break

                formatted_eet_line_content_colored = utils.format_eet_content(
                    line_content_raw, self.username
                )

                line_prefix_str = ""
                if i == 0:
                    line_prefix_str = f"{time_str:<{time_w}}  {user_col_final_padded}  "
                else:
                    indent_spaces = time_w + 2 + user_w_max + 2
                    line_prefix_str = " " * indent_spaces

                full_line_unpadded = (
                    f"{line_prefix_str}{formatted_eet_line_content_colored}"
                )

                line_padding = timeline_content_width - utils.wcswidth(
                    utils.strip_ansi(full_line_unpadded)
                )
                output_lines.append(f"{full_line_unpadded}{' ' * max(0, line_padding)}")

            if eet_idx < len(eets) - 1 and len(output_lines) < num_lines_available:
                output_lines.append(" " * timeline_content_width)

        while len(output_lines) < num_lines_available:
            output_lines.append(" " * timeline_content_width)

        return output_lines[:num_lines_available]

    def _get_sidebar_visible_content_lines(self, num_lines_available: int) -> List[str]:
        """Generates formatted and padded lines for the visible portion of the sidebar."""
        if not self._sidebar_enabled or not self._sidebar_full_user_list:
            return [" " * config.SIDEBAR_WIDTH] * num_lines_available

        start_idx = self._sidebar_scroll_offset
        end_idx = self._sidebar_scroll_offset + num_lines_available

        visible_user_strings_already_truncated = self._sidebar_full_user_list[
            start_idx:end_idx
        ]

        output_lines = []
        for user_str_truncated_colored in visible_user_strings_already_truncated:
            padding = config.SIDEBAR_WIDTH - utils.wcswidth(
                utils.strip_ansi(user_str_truncated_colored)
            )
            output_lines.append(f"{user_str_truncated_colored}{' ' * max(0, padding)}")

        while len(output_lines) < num_lines_available:
            output_lines.append(" " * config.SIDEBAR_WIDTH)
        return output_lines[:num_lines_available]

    def _build_watch_screen_output(self, eets: List[Dict[str, Any]]) -> str:
        """Builds the complete screen output string for watch mode with sidebar."""
        if not self.username:
            return ""

        sidebar_w = config.SIDEBAR_WIDTH if self._sidebar_enabled else 0
        separator_str = f" {FG_BRIGHT_BLACK}|{RESET} " if self._sidebar_enabled else ""
        separator_visual_w = (
            utils.wcswidth(utils.strip_ansi(separator_str))
            if self._sidebar_enabled
            else 0
        )

        timeline_w = self._term_width - sidebar_w - separator_visual_w
        timeline_w = max(20, timeline_w)

        output_buffer = []

        # --- Calculate Scrollable Body Height (same for timeline and sidebar body) ---
        num_header_lines = (3)
        num_footer_lines = 1
        prompt_line_allowance = 1
        scrollable_body_height = (
            self._term_height
            - num_header_lines
            - num_footer_lines
            - prompt_line_allowance
        )
        scrollable_body_height = max(1, scrollable_body_height)

        # --- Prepare Timeline Header Content (Padded) ---
        target_type = self._current_target_filter["type"]
        target_val = self._current_target_filter["value"]
        tl_title_text_content = (
            f"#{target_val}"
            if target_type == "channel" and target_val
            else f"@{target_val}"
            if target_type == "user" and target_val
            else "Your 'Mine' Feed"
            if target_type == "mine"
            else "All Eets"
        )
        _tl_title_raw = (
            BOLD
            + utils.truncate_str_with_wcwidth(
                f"--- {tl_title_text_content} (Page {self._current_timeline_page}, {self._timeline_page_size} per page) ---",
                timeline_w,
            )
            + RESET
        )
        tl_title_padded = f"{_tl_title_raw}{' ' * max(0, timeline_w - utils.wcswidth(utils.strip_ansi(_tl_title_raw)))}"

        time_cw, user_cw_max, tl_col_seps = 10, 18, 2 * 2  # 2 spaces for 2 seps "  "
        eet_cw = max(10, timeline_w - time_cw - user_cw_max - tl_col_seps)
        _tl_cols_raw = utils.truncate_str_with_wcwidth(
            f"{'Time':<{time_cw}}  {'User':<{user_cw_max}}  {'Eet':<{eet_cw}}",
            timeline_w,
        )
        tl_cols_padded = f"{_tl_cols_raw}{' ' * max(0, timeline_w - utils.wcswidth(utils.strip_ansi(_tl_cols_raw)))}"

        _tl_sep_raw = FG_BRIGHT_BLACK + "-" * timeline_w + RESET
        tl_sep_padded = f"{_tl_sep_raw}{' ' * max(0, timeline_w - utils.wcswidth(utils.strip_ansi(_tl_sep_raw)))}"

        # --- Prepare Sidebar Header Content (Padded) ---
        sb_title_padded = " " * sidebar_w
        sb_sep_padded = " " * sidebar_w
        if self._sidebar_enabled:
            _sb_title_raw = f"{BOLD}Souls Connected ({len(self._sidebar_full_user_list)}){RESET}"
            _sb_title_trunc = utils.truncate_str_with_wcwidth(_sb_title_raw, sidebar_w)
            sb_title_padded = f"{_sb_title_trunc}{' ' * max(0, sidebar_w - utils.wcswidth(utils.strip_ansi(_sb_title_trunc)))}"

            _sb_sep_raw = FG_BRIGHT_BLACK + "-" * sidebar_w + RESET
            sb_sep_padded = f"{_sb_sep_raw}{' ' * max(0, sidebar_w - utils.wcswidth(utils.strip_ansi(_sb_sep_raw)))}"

        # --- Assemble Header Section (3 lines) ---
        output_buffer.append(f"{tl_title_padded}{separator_str}{sb_title_padded}")
        output_buffer.append(f"{tl_cols_padded}{separator_str}{sb_sep_padded}")
        output_buffer.append(
            f"{tl_sep_padded}{separator_str}{' ' * sidebar_w if self._sidebar_enabled else ''}"
        )

        # --- Get Body Content Lines (Padded by their respective functions) ---
        timeline_body_content = self._get_timeline_body_lines_for_watch(
            eets, timeline_w, scrollable_body_height
        )
        sidebar_body_content = self._get_sidebar_visible_content_lines(
            scrollable_body_height
        )  # Height matches timeline body

        # --- Assemble Body Section ---
        for i in range(scrollable_body_height):
            timeline_line = timeline_body_content[i]  # Already padded to timeline_w
            sidebar_line = (
                sidebar_body_content[i] if self._sidebar_enabled else ""
            )  # Already padded to sidebar_w
            output_buffer.append(f"{timeline_line}{separator_str}{sidebar_line}")

        # --- Assemble Footer (Status Line) ---
        status_footer_raw = f"Live updating {tl_title_text_content}... {FG_BRIGHT_BLACK}(PgUp/PgDn to scroll. 'exit' to stop){RESET}"
        status_footer_padded = utils.truncate_str_with_wcwidth(
            status_footer_raw, self._term_width
        )  # Full terminal width
        output_buffer.append(status_footer_padded)

        return "\r\n".join(output_buffer)

    async def handle_new_post_realtime(self, post_record: Dict[str, Any]):  # PRESERVED
        if not self._is_watching_timeline or not self.username:
            return
        utils.debug_log(f"RT check for {self.username}: Post {post_record.get('id')}")
        utils.debug_log(
            f"RT relevant for {self.username} (is watching), refreshing to page 1."
        )
        await self._refresh_watch_display(timeline_page_to_fetch=1)

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
                pass
        if (
            self._timeline_auto_refresh_task
            and not self._timeline_auto_refresh_task.done()
        ):
            self._timeline_auto_refresh_task.cancel()
        self._sidebar_enabled = False
        self._chan = None

    def close(self) -> None:
        if self._chan:
            self._chan.close()

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

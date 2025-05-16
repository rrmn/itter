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
import command_history

from utils import BOLD, FG_BRIGHT_BLACK, RESET, FG_CYAN, FG_MAGENTA

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
                if not banner_message.endswith('\r\n'):
                    banner_message += '\r\n'
                
                utils.debug_log(f"Attempting to send auth banner: {banner_message.strip()}")
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

            except asyncssh.Error as e:  # Catch specific asyncssh errors
                utils.debug_log(
                    f"asyncssh error during _send_auth_failure_message (banner/disconnect): {e}"
                )
            except Exception as e:  # Catch other errors
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

    def session_requested(self) -> Optional["ItterShell"]:  # Return type can be Optional
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

        elif self.current_username:  # This implies not a registration attempt, but a login
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
        self._command_history = command_history.CommandHistory()
        self._active_sessions: Optional[Dict[str, "ItterShell"]] = (
            None  # Use forward reference
        )
        self._client_ip: Optional[str] = None

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
        prompt_text = self._get_prompt_text()
        self._write_to_channel("\r", newline=False)
        # Overwrite existing input with spaces to clear the line
        self._write_to_channel(" " * (len(prompt_text) + len(self._input_buffer)), newline=False)
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
                f"  > {BOLD}ssh {self._reg_username_candidate}@app.itter.sh{RESET}\r\n" # Placeholder URL
                f"\r\n"
                f"or {BOLD}ssh{RESET} {FG_BRIGHT_BLACK}-i /path/to/your/private_key{RESET} {BOLD}{self._reg_username_candidate}@app.itter.sh{RESET}" # Placeholder URL
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
            f"  {BOLD}f{RESET}ollow {FG_BRIGHT_BLACK}@<user>{RESET}                  - Follow a user.\r\n"
            f"  {BOLD}f{RESET}ollow {FG_BRIGHT_BLACK}--list{RESET}                    - List your follows and followers.\r\n"
            f"  {BOLD}u{RESET}n{BOLD}f{RESET}ollow {FG_BRIGHT_BLACK}@<user>{RESET}                - Unfollow a user.\r\n"
            f"  {BOLD}i{RESET}gnore {FG_BRIGHT_BLACK}@<user>{RESET}                  - Ignore a user.\r\n"
            f"  {BOLD}i{RESET}gnore {FG_BRIGHT_BLACK}--list{RESET}                    - List users you ignore.\r\n"
            f"  {BOLD}u{RESET}n{BOLD}i{RESET}gnore {FG_BRIGHT_BLACK}@<user>{RESET}                - Unignore a user.\r\n"
            f"  {BOLD}p{RESET}rofile {FG_BRIGHT_BLACK}[@<user>]{RESET}              - View user profile (yours or another's).\r\n"
            f"  {BOLD}p{RESET}rofile {BOLD}e{RESET}dit {FG_BRIGHT_BLACK}-name <Name> -email <Email>{RESET} - Edit your profile.\r\n"
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
            if data == "\x1b[A":
                self._redraw_input_line()
                command = self._command_history.scroll_up()
                self._prompt()
                self._write_to_channel(command, newline=False)
                self._input_buffer = command
                return
            elif data == "\x1b[B":
                self._redraw_input_line()
                command = self._command_history.scroll_down()
                self._prompt()
                self._write_to_channel(command, newline=False)
                self._input_buffer = command
                return
            else:
                utils.debug_log(f"unknown escape sequence: {data!r}")
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
            elif char == "\x7f":
                if self._input_buffer:
                    self._input_buffer = self._input_buffer[:-1]
                    self._write_to_channel("\b \b", newline=False)
            elif char == "\x03":
                self._write_to_channel("^C\r\n", newline=False)
                self.close()
            elif char == "\x04":
                self._write_to_channel("^D\r\n", newline=False)
                self.close()
            elif char == "\x15":  # Ctrl+U
                if self._input_buffer:
                    current_prompt_len = len(self._get_prompt_text())
                    old_input_buffer_len = len(self._input_buffer)
                    self._write_to_channel("\r", newline=False)
                    self._write_to_channel(
                        " " * (current_prompt_len + old_input_buffer_len), newline=False
                    )
                    self._write_to_channel("\r", newline=False)
                    self._input_buffer = ""
                    self._prompt()
            elif char == "\x17":  # Ctrl+W
                if self._input_buffer:
                    old_input_buffer_len = len(self._input_buffer)

                    i = len(self._input_buffer) - 1
                    while i >= 0 and self._input_buffer[i].isspace():
                        i -= 1
                    j = i
                    while j >= 0 and not self._input_buffer[j].isspace():
                        j -= 1

                    self._input_buffer = self._input_buffer[: j + 1]

                    current_prompt_len = len(self._get_prompt_text())
                    self._write_to_channel("\r", newline=False)
                    self._write_to_channel(
                        " " * (current_prompt_len + old_input_buffer_len), newline=False
                    )
                    self._write_to_channel("\r", newline=False)
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
        if not self.username: return

        try:
            following_list = await db.db_get_user_following(self.username)
            followers_list = await db.db_get_user_followers(self.username)
        except Exception as e:
            self._write_to_channel(f"Error fetching follow lists: {e}")
            return

        output_lines = []
        output_lines.append(f"\r\n{BOLD}--- You are following ({len(following_list)} users) ---{RESET}")
        if not following_list:
            output_lines.append(f"  Not following anyone yet. Use `{BOLD}follow @user{RESET}`.")
        else:
            for user_data in following_list:
                display_name_part = f" ({user_data['display_name']})" if user_data.get('display_name') else ""
                time_part = f" - since {utils.time_ago(user_data.get('created_at'))}"
                output_lines.append(f"  {FG_CYAN}@{user_data['username']}{RESET}{display_name_part}{time_part}")

        output_lines.append(f"\r\n{BOLD}--- Follows you ({len(followers_list)} users) ---{RESET}")
        if not followers_list:
            output_lines.append("  No followers yet. Be more eet-eresting!")
        else:
            for user_data in followers_list:
                display_name_part = f" ({user_data['display_name']})" if user_data.get('display_name') else ""
                time_part = f" - since {utils.time_ago(user_data.get('created_at'))}"
                output_lines.append(f"  {FG_CYAN}@{user_data['username']}{RESET}{display_name_part}{time_part}")
        
        output_lines.append("\r\n") # Extra newline for spacing before prompt
        self._write_to_channel("\r\n".join(output_lines))

    async def _display_ignore_list(self):
        if not self.username: return

        try:
            ignoring_list = await db.db_get_user_ignoring(self.username)
        except Exception as e:
            self._write_to_channel(f"Error fetching ignore list: {e}")
            return

        output_lines = []
        output_lines.append(f"\r\n{BOLD}--- You are ignoring ({len(ignoring_list)} users) ---{RESET}")
        if not ignoring_list:
            output_lines.append(f"  Not ignoring anyone. What a saint! Use `{BOLD}ignore @user{RESET}` if needed.")
        else:
            for user_data in ignoring_list:
                display_name_part = f" ({user_data['display_name']})" if user_data.get('display_name') else ""
                time_part = f" - since {utils.time_ago(user_data.get('created_at'))}"
                output_lines.append(f"  {FG_MAGENTA}@{user_data['username']}{RESET}{display_name_part}{time_part}")
        
        output_lines.append("\r\n") # Extra newline for spacing before prompt
        self._write_to_channel("\r\n".join(output_lines))

    async def _handle_command_line(self, line: str):
        if not self.username and not self._is_registration_flow:
            self._write_to_channel("ERROR: Critical error: No user context.")
            self.close()
            return

        cmd, raw_text, hashtags, user_refs = utils.parse_input_line(line)
        utils.debug_log(
            f"Parsed command: cmd='{cmd}', raw_text='{raw_text}', hashtags={hashtags}, user_refs={user_refs}"
        )

        if not cmd:
            self._prompt()
            return

        try:
            # Add command to history
            self._command_history.add((cmd + " " + raw_text.strip()).strip())

            if cmd == "eet" or cmd == "e":
                content = raw_text.strip()
                if not content:
                    self._write_to_channel("Usage: eet <text>")
                elif len(content) > config.EET_MAX_LENGTH:
                    self._write_to_channel(
                        f"ERROR: Eet too long! Max {config.EET_MAX_LENGTH}."
                    )
                else:
                    await db.db_post_eet(
                        self.username, content, hashtags, user_refs, self._client_ip
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
                target_specifier_text = raw_text
                parts = raw_text.split()
                if parts and parts[-1].isdigit():
                    self._current_timeline_page = int(parts[-1])
                    target_specifier_text = " ".join(parts[:-1])

                if user_refs:
                    self._current_target_filter = {
                        "type": "user",
                        "value": user_refs[0],
                    }
                elif target_specifier_text.strip().startswith("#"):
                    channel_name = target_specifier_text.strip()[1:]
                    if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]*$", channel_name):
                        self._current_target_filter = {
                            "type": "channel",
                            "value": channel_name,
                        }
                    else:
                        self._write_to_channel(f"Invalid channel: '{channel_name}'.")
                        self._current_target_filter = {"type": "all", "value": None}
                else:
                    self._current_target_filter = utils.parse_target_filter(
                        target_specifier_text
                    )

                utils.debug_log(
                    f"Timeline/Watch target set to: {self._current_target_filter}"
                )
                if cmd == "watch" or cmd == "w":
                    self._is_watching_timeline = True
                    await self._start_live_timeline_view()
                    return
                else:
                    self._is_watching_timeline = False
                    await self._render_and_display_timeline(
                        page=self._current_timeline_page
                    )
            elif cmd == "follow" or cmd == "f":
                if raw_text.strip().lower() == "--list":
                    await self._display_follow_lists()
                else:
                    target_user = (
                        user_refs[0] if user_refs else raw_text.strip().lstrip("@")
                    )
                    if not target_user:
                        self._write_to_channel("Usage: follow @<username> OR follow --list")
                    else:
                        await db.db_follow_user(self.username, target_user)
                        self._write_to_channel(f"Following @{target_user}. You will now see their posts on your 'mine' page.")
            elif cmd == "unfollow" or cmd == "uf":
                target_user = (
                    user_refs[0] if user_refs else raw_text.strip().lstrip("@")
                )
                if not target_user:
                    self._write_to_channel("Usage: unfollow @<username>")
                else:
                    await db.db_unfollow_user(self.username, target_user)
                    self._write_to_channel(f"Unfollowed @{target_user}. They won't show up on your 'mine' page anymore.")
            elif cmd == "ignore" or cmd == "i":
                if raw_text.strip().lower() == "--list":
                    await self._display_ignore_list()
                else:
                    target_user_to_ignore = (
                        user_refs[0] if user_refs else raw_text.strip().lstrip("@")
                    )
                    if not target_user_to_ignore:
                        self._write_to_channel("Usage: ignore @<username> OR ignore --list")
                    elif target_user_to_ignore == self.username:
                        self._write_to_channel("You cannot ignore yourself. (That's what my psychologist said)")
                    else:
                        await db.db_ignore_user(self.username, target_user_to_ignore)
                        self._write_to_channel(
                            f"Okay, @{target_user_to_ignore} will now be ignored. Their posts won't appear in your timelines. Phew."
                        )
            elif cmd == "unignore" or cmd == "ui":
                target_user_to_unignore = (
                    user_refs[0] if user_refs else raw_text.strip().lstrip("@")
                )
                if not target_user_to_unignore:
                    self._write_to_channel("Usage: unignore @<username>")
                else:
                    await db.db_unignore_user(self.username, target_user_to_unignore)
                    self._write_to_channel(
                        f"Okay, @{target_user_to_unignore} is forgiven and will no longer be ignored. You'll see their posts again."
                    )
            elif cmd == "profile" or cmd == "p":
                await self._handle_profile_command(raw_text, user_refs)
            elif cmd == "help" or cmd == "h":
                self._display_welcome_banner()
                self._show_help()
            elif cmd == "clear" or cmd == "c":
                self._clear_screen()
                if self._is_watching_timeline:
                    await self._render_and_display_timeline(page=1, is_live_update=True)
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
            new_display_name = None
            new_email = None
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
            if new_display_name is None and new_email is None:
                self._write_to_channel(
                    "Usage: profile edit -name <Name> -email <Email>"
                )
            else:
                await db.db_update_profile(self.username, new_display_name, new_email)
                self._write_to_channel("Profile updated.")
        else:
            profile_username = (
                user_refs[0]
                if user_refs
                else (
                    raw_text.strip().lstrip("@") if raw_text.strip() else self.username
                )
            )
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
                self._write_to_channel(f"Error fetching profile.")

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
        self._write_to_channel(
            f"Entering live view for {self._current_target_filter['type']}='{self._current_target_filter['value'] or 'all'}'."
        )
        self._write_to_channel("(Type 'exit' or Ctrl+C to stop)\n")
        await self._render_and_display_timeline(
            page=1, is_live_update=True
        )  # Initial render clears screen
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
                    await self._render_and_display_timeline(page=1, is_live_update=True)
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
        if not self.username:
            return
        try:
            eets = await db.db_get_filtered_timeline_posts(
                self.username, self._current_target_filter, page=page
            )
        except Exception as e:
            if self._is_watching_timeline and self._chan:
                self._clear_screen()
                self._write_to_channel(f"Timeline Error: {e}\r\n")
                self._redraw_prompt_and_buffer()
            elif self._chan:
                self._write_to_channel(f"Timeline Error: {e}")
            return

        formatted_output = await asyncio.to_thread(
            self._format_timeline_output, eets, page
        )

        if is_live_update:
            self._clear_screen()
            self._write_to_channel(formatted_output, newline=True)
            self._redraw_prompt_and_buffer()
        else:
            self._clear_screen()
            self._write_to_channel(formatted_output, newline=True)


    def _format_timeline_output(self, eets: List[Dict[str, Any]], page: int) -> str:
        time_w = 12
        user_w = 25
        sep_w = 3
        eet_w = max(10, self._term_width - time_w - user_w - (sep_w * 2))

        output_lines = [f"{'Time':<{time_w}} | {'User':<{user_w}} | {'Eet':<{eet_w}}"]
        output_lines.append(
            "-" * min(self._term_width, time_w + user_w + eet_w + (sep_w * 2))
        )

        if not eets:
            output_lines.append(" No eets found." if page == 1 else " End of timeline.")
        else:
            for eet in eets:
                t = utils.time_ago(eet.get("created_at"))
                u_raw = f"@{eet.get('username', 'ghost')}"
                disp = eet.get("display_name")
                u = f"{disp} ({u_raw})" if disp else u_raw
                if len(u) > user_w:
                    u = u[: user_w - 3] + "..."

                cont = eet.get("content", "").replace("\r", "").replace("\n", " ")

                wrapper = textwrap.TextWrapper(width=eet_w, subsequent_indent="  ")
                cont_lines_plain = wrapper.wrap(text=cont)

                first_line_content = cont_lines_plain[0] if cont_lines_plain else ""
                output_lines.append(
                    f"{t:<{time_w}} | {u:<{user_w}} | {utils.format_eet_content(first_line_content):<{eet_w}}"
                )

                indent = " " * (time_w + sep_w + user_w + sep_w)
                for i in range(1, len(cont_lines_plain)):
                    output_lines.append(
                        f"{indent}{utils.format_eet_content(cont_lines_plain[i]):<{eet_w}}"
                    )

        footer_lines = []
        if self._is_watching_timeline:
            status = f"Live updating... Target: {self._current_target_filter['type']}='{self._current_target_filter['value'] or 'all'}'. (exit to stop)"
            footer_lines.append(status)
        else:
            footer = ""
            if not eets and page > 1:
                footer = f"Page {page}. No more."
            elif len(eets) >= config.DEFAULT_TIMELINE_PAGE_SIZE:
                footer = f"Page {page}. `timeline ... {page + 1}` for more."
            elif eets:
                footer = f"Page {page}. End of results."
            if footer:
                footer_lines.append(footer)

        if footer_lines:
            output_lines.append("\r\n" + "\r\n".join(footer_lines))

        return "\r\n".join(output_lines)

    async def handle_new_post_realtime(self, post_record: Dict[str, Any]):
        if not self._is_watching_timeline or not self.username:
            return
        utils.debug_log(f"RT check for {self.username}: Post {post_record.get('id')}")
        post_author_id = post_record.get("user_id")
        post_tags = post_record.get("tags", []) or []
        target_type = self._current_target_filter.get("type")
        target_value = self._current_target_filter.get("value")
        refresh = False
        if target_type == "all":
            refresh = True
        elif target_type == "mine":
            details = await db.db_get_user_by_id(post_author_id)
            if details:
                current_user_obj = await db.db_get_user_by_username(self.username)
                if current_user_obj and post_author_id == current_user_obj["id"]:
                    refresh = True
                elif details and await db.db_is_following(
                    self.username, details["username"]
                ):
                    refresh = True
        elif target_type == "user":
            details = await db.db_get_user_by_id(post_author_id)
            if details and details["username"] == target_value:
                refresh = True
        elif target_type == "channel":
            if target_value and target_value in [tag.lower() for tag in post_tags]:
                refresh = True
        if refresh:
            utils.debug_log(f"RT relevant for {self.username}, refreshing.")
            await self._render_and_display_timeline(page=1, is_live_update=True)

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
            line_editor=False,
        )
        print(f"itter.sh server humming on ssh://{config.SSH_HOST}:{config.SSH_PORT}")
        print("Ctrl+C to stop.")
    except Exception as e:
        sys.stderr.write(f"[FATAL ERROR] SSH server failed to start: {e}\n")
        sys.exit(1)
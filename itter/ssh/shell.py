import asyncio
import asyncssh
from typing import Optional, Dict, Any, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .server import ItterSSHServer

import itter.data.database as db
import itter.core.utils as utils
import itter.core.config as config
from itter.core.command_history import CommandHistory

from itter.ssh.commands import (
    eet as eet_cmd,
    timeline as timeline_cmd,
    follow as follow_cmd,
    ignore as ignore_cmd,
    profile as profile_cmd,
    settings as settings_cmd,
    misc as misc_cmd,
)
from itter.core.utils import BOLD, RESET, FG_BRIGHT_BLACK


class ItterShell(asyncssh.SSHServerSession):
    def __init__(
        self,
        ssh_server_ref: "ItterSSHServer",
        initial_username: Optional[str],
        authenticated_key: Optional[str],
        is_registration_flow: bool,
        registration_details: Optional[Tuple[str, str]],
    ):
        self._ssh_server = ssh_server_ref
        self.username: Optional[str] = initial_username
        self._authenticated_key = authenticated_key
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
        self._cursor_pos = 0  # Cursor position within the input buffer
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

    def _redraw_line_and_cursor(self):
        """
        Redraws the entire input line, including the prompt and buffer,
        clears any old characters, and positions the cursor correctly.
        """
        if not self._chan:
            return

        # 1. Go to the beginning of the line
        self._write_to_channel("\r", newline=False)

        # 2. Write the new content (prompt + buffer)
        prompt_text = self._get_prompt_text()
        self._write_to_channel(prompt_text + self._input_buffer, newline=False)

        # 3. Clear any characters from the old, longer line
        self._write_to_channel(
            "\033[K", newline=False
        )  # Erase from cursor to end of line

        # 4. Move cursor back to the correct position
        suffix = self._input_buffer[self._cursor_pos :]
        move_left_count = utils.wcswidth(suffix)
        if move_left_count > 0:
            self._write_to_channel(f"\033[{move_left_count}D", newline=False)

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
                self._client_ip = None
                utils.debug_log("Could not determine client IP for session.")
        if self._is_registration_flow:
            asyncio.create_task(self._handle_registration_flow())
        else:
            if self.username and self._active_sessions is not None:
                self._active_sessions[self.username] = self
                misc_cmd.display_welcome_banner(self)
                misc_cmd.show_help(self)
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
                command = self._command_history.scroll_up()
                self._input_buffer = command
                self._cursor_pos = len(self._input_buffer)
                self._redraw_line_and_cursor()
                return
            elif data == "\x1b[B":  # Down arrow
                command = self._command_history.scroll_down()
                self._input_buffer = command
                self._cursor_pos = len(self._input_buffer)
                self._redraw_line_and_cursor()
                return
            elif data == "\x1b[D":  # Left arrow
                if self._cursor_pos > 0:
                    self._cursor_pos -= 1
                    self._write_to_channel(data, newline=False)
                return
            elif data == "\x1b[C":  # Right arrow
                if self._cursor_pos < len(self._input_buffer):
                    self._cursor_pos += 1
                    self._write_to_channel(data, newline=False)
                return
            # Page Up: \x1b[5~ , Page Down: \x1b[6~
            elif data == "\x1b[5~":  # Page Up (timeline scroll)
                if self._is_watching_timeline:
                    if self._current_timeline_page > 1:
                        self._current_timeline_page -= 1
                        asyncio.create_task(
                            timeline_cmd.refresh_watch_display(
                                self, timeline_page_to_fetch=self._current_timeline_page
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
                            timeline_cmd.render_and_display_timeline(
                                self,
                                page=self._current_timeline_page,
                                is_live_update=False,
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
                            timeline_cmd.refresh_watch_display(
                                self, timeline_page_to_fetch=self._current_timeline_page
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
                            timeline_cmd.render_and_display_timeline(
                                self,
                                page=self._current_timeline_page,
                                is_live_update=False,
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
                        timeline_cmd.refresh_watch_display(
                            self, timeline_page_to_fetch=self._current_timeline_page
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
                        timeline_cmd.refresh_watch_display(
                            self, timeline_page_to_fetch=self._current_timeline_page
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
                    self._cursor_pos = 0
                    asyncio.create_task(self._handle_command_line(line_to_process))
                else:
                    self._prompt()
            elif char == "\x7f" or char == "\x08":  # Backspace
                if self._cursor_pos > 0:
                    self._input_buffer = (
                        self._input_buffer[: self._cursor_pos - 1]
                        + self._input_buffer[self._cursor_pos :]
                    )
                    self._cursor_pos -= 1
                    self._redraw_line_and_cursor()
            elif char == "\x03":  # Ctrl+C
                self._write_to_channel("^C\r\n", newline=False)
                self.close()
            elif char == "\x04":  # Ctrl+D
                self._write_to_channel("^D\r\n", newline=False)
                self.close()
            elif char == "\x15":  # Ctrl+U
                if self._input_buffer:
                    self._input_buffer = ""
                    self._cursor_pos = 0
                    self._redraw_line_and_cursor()
            elif char == "\x17":  # Ctrl+W
                if self._cursor_pos > 0:
                    old_buffer = self._input_buffer
                    end_pos = self._cursor_pos
                    # Move left past any spaces
                    start_pos = end_pos - 1
                    while start_pos >= 0 and old_buffer[start_pos].isspace():
                        start_pos -= 1
                    # Move left past the word
                    while start_pos >= 0 and not old_buffer[start_pos].isspace():
                        start_pos -= 1
                    new_cursor_pos = start_pos + 1
                    self._input_buffer = (
                        old_buffer[:new_cursor_pos] + old_buffer[end_pos:]
                    )
                    self._cursor_pos = new_cursor_pos
                    self._redraw_line_and_cursor()
            elif char.isprintable():
                self._input_buffer = (
                    self._input_buffer[: self._cursor_pos]
                    + char
                    + self._input_buffer[self._cursor_pos :]
                )
                self._cursor_pos += 1
                self._redraw_line_and_cursor()
            else:
                utils.debug_log(f"Ignoring unhandled character: {char!r}")

    def _clear_screen(self):
        if self._chan:
            self._chan.write("\033[2J\033[H")

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
                await eet_cmd.handle_eet(
                    self,
                    raw_text_full,
                    hashtags_in_full_line,
                    user_refs_in_full_line,
                )
            elif cmd == "timeline" or cmd == "tl" or cmd == "watch" or cmd == "w":
                await timeline_cmd.handle_timeline_and_watch(self, cmd, raw_text_full)
                if self._is_watching_timeline:
                    return
            elif cmd == "follow" or cmd == "f":
                await follow_cmd.handle_follow(self, raw_text_full)
            elif cmd == "unfollow" or cmd == "uf":
                await follow_cmd.handle_unfollow(self, raw_text_full)
            elif cmd == "ignore" or cmd == "i":
                await ignore_cmd.handle_ignore(self, raw_text_full)
            elif cmd == "unignore" or cmd == "ui":
                await ignore_cmd.handle_unignore(self, raw_text_full)
            elif cmd == "profile" or cmd == "p":
                await profile_cmd.handle_profile_command(
                    self, raw_text_full, user_refs_in_full_line
                )
            elif cmd == "settings" or cmd == "s":
                await settings_cmd.handle_settings(self, raw_text_full)
            elif cmd == "help" or cmd == "h":
                await misc_cmd.handle_help(self)
            elif cmd == "clear" or cmd == "c":
                await misc_cmd.handle_clear(self)
                return
            elif cmd == "exit" or cmd == "x":
                await misc_cmd.handle_exit_command(self)
                return
            else:
                self._write_to_channel(f"Sorry, unknown command: '{FG_BRIGHT_BLACK}{cmd}{RESET}'. Type '{FG_BRIGHT_BLACK}help{RESET}' to see what's possible.")
        except ValueError as ve:
            self._write_to_channel(f"Error: {ve}")
        except Exception as e:
            utils.debug_log(f"Error handling command '{cmd}': {e}")
            self._write_to_channel("An unexpected server error occurred.")
            if config.ITTER_DEBUG_MODE:
                import traceback

                self._write_to_channel(traceback.format_exc())
        if self._chan and not self._is_watching_timeline:
            self._prompt()

    async def handle_new_post_realtime(self, post_record: Dict[str, Any]):  # PRESERVED
        await timeline_cmd.handle_new_post_realtime(self, post_record)

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

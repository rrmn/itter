# /itter/ssh/shell.py
import asyncio
import asyncssh
import traceback
from typing import Optional, Dict, List, Tuple, TYPE_CHECKING, Any

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
from itter.core.utils import BOLD, RESET, FG_BRIGHT_BLACK, FG_RED


class ItterShell(asyncssh.SSHServerSession):
    def __init__(
        self,
        ssh_server_ref: "ItterSSHServer",
        initial_username: Optional[str],
        authenticated_key: Optional[str],
        is_registration_flow: bool,
        registration_details: Optional[Tuple[str, str]],
    ) -> None:
        self._ssh_server: "ItterSSHServer" = ssh_server_ref
        self.username: Optional[str] = initial_username
        self._authenticated_key: Optional[str] = authenticated_key
        self._is_registration_flow: bool = is_registration_flow

        if self._is_registration_flow and registration_details:
            self._reg_username_candidate: Optional[str] = registration_details[0]
            self._reg_public_key: Optional[str] = registration_details[1]
        else:
            self._reg_username_candidate = None
            self._reg_public_key = None

        self._chan: Optional[asyncssh.SSHServerChannel] = None
        self._current_target_filter: Dict[str, Optional[str]] = {
            "type": "all",
            "value": None,
        }
        self._is_watching_timeline: bool = False
        self._current_timeline_page: int = 1
        self._term_width: int = 80
        self._term_height: int = 24
        self._input_buffer: str = ""
        self._cursor_pos: int = 0
        self._command_history: CommandHistory = CommandHistory()
        self._active_sessions: Optional[Dict[str, "ItterShell"]] = None
        self._client_ip: Optional[str] = None
        self._timeline_page_size: int = config.DEFAULT_TIMELINE_PAGE_SIZE
        self._last_timeline_eets_count: Optional[int] = None

        self._sidebar_enabled: bool = False
        self._sidebar_scroll_offset: int = 0
        self._sidebar_full_user_list: List[str] = []

        self._timeline_auto_refresh_task: Optional[asyncio.Task[Any]] = None

        try:
            with open(str(config.BANNER_FILE), "r", encoding="utf-8") as f:
                self._banner_text = f.read()
        except Exception:
            self._banner_text = "Welcome to itter.sh!\n(Banner file missing. You get this minimalist experience.)"

        self._cached_prompt = (
            f"({self.username})itter> " if self.username else "itter> "
        )
        super().__init__()

    def set_active_sessions_ref(self, sessions_dict: Dict[str, "ItterShell"]) -> None:
        self._active_sessions = sessions_dict

    def _write_to_channel(self, message: str = "", newline: bool = True) -> None:
        if self._chan:
            try:
                processed_message = message.replace("\r\n", "\n").replace("\n", "\r\n")
                if newline and not processed_message.endswith("\r\n"):
                    processed_message += "\r\n"
                elif not newline and processed_message.endswith("\r\n"):
                    processed_message = processed_message[:-2]
                self._chan.write(processed_message)
            except (
                OSError,
                asyncssh.Error,
                ConnectionResetError,
                BrokenPipeError,
            ) as e:
                if config.ITTER_DEBUG_MODE:
                    utils.debug_log(f"Failed to write basic content: {e}")

    def _prompt(self) -> None:
        if self._chan and self.username:
            self._chan.write(self._cached_prompt)

    def _redraw_prompt_and_buffer(self) -> None:
        if self._chan and self.username:
            self._chan.write(f"{self._cached_prompt}{self._input_buffer}")

    def _redraw_line_and_cursor(self) -> None:
        if not self._chan:
            return
        payload = f"\r{self._cached_prompt}{self._input_buffer}\033[K"
        chars_to_move_left = utils.wcswidth(self._input_buffer[self._cursor_pos :])
        if chars_to_move_left > 0:
            payload += f"\033[{chars_to_move_left}D"
        self._chan.write(payload)

    def connection_made(self, chan: asyncssh.SSHServerChannel) -> None:
        utils.debug_log(
            f"ItterShell connection_made for {'REGISTRATION' if self._is_registration_flow else self.username}"
        )
        self._chan = chan
        if self._chan:
            peername = self._chan.get_extra_info("peername")
            if peername and isinstance(peername, tuple) and len(peername) > 0:
                self._client_ip = str(peername[0])
                utils.debug_log(f"Client IP captured: {self._client_ip}")
            else:
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
                self._write_to_channel(
                    f"\r\n{FG_RED}Error:{RESET} Login session started without a username."
                )
                self.close()
            else:
                utils.debug_log(
                    "CRITICAL: _active_sessions is None in ItterShell connection_made"
                )
                self._write_to_channel(
                    f"\r\n{FG_RED}Error:{RESET} Server state is inconsistent."
                )
                self.close()

    async def _handle_registration_flow(self) -> None:
        utils.debug_log(f"Finalizing registration for '{self._reg_username_candidate}'")
        if not self._reg_username_candidate or not self._reg_public_key:
            self._write_to_channel(
                f"\r\n{FG_RED}Registration Error:{RESET} Missing username or public key."
            )
            self.close()
            return
        try:
            await db.db_create_user(self._reg_username_candidate, self._reg_public_key)
            utils.debug_log(
                f"User '{self._reg_username_candidate}' registered successfully."
            )
            self._write_to_channel(
                f"\r\nSuccess! Account '{self._reg_username_candidate}' created.\r\n"
                f"You can now log in via:\r\n\r\n"
                f"  > {BOLD}ssh {self._reg_username_candidate}@app.itter.sh{RESET}\r\n\r\n"
                f"Have fun & see you on the other side!\r\n"
            )
        except Exception as e:
            utils.debug_log(
                f"[DB ERROR] Registration failed for '{self._reg_username_candidate}': {e}"
            )
            self._write_to_channel(
                f"\r\n{FG_RED}Registration Failed:{RESET} Could not create the account."
            )
        finally:
            self.close()

    def pty_requested(self, term_type: str, term_size: tuple, term_modes: dict) -> bool:
        cols, rows = 80, 24
        try:
            if isinstance(term_size, tuple) and len(term_size) >= 2:
                cols = int(term_size[0]) if term_size[0] > 0 else 80
                rows = int(term_size[1]) if term_size[1] > 0 else 24
        except Exception as e:
            utils.debug_log(f"Error parsing term_size tuple {term_size}: {e}")
        utils.debug_log(f"PTY requested: term={term_type}, size={cols}x{rows}")
        self._term_width = cols
        self._term_height = rows
        return True

    def shell_requested(self) -> bool:
        utils.debug_log("Shell requested by client.")
        return True

    def data_received(self, data: str, datatype: asyncssh.DataType) -> None:
        if not self._chan:
            return

        # RESTORED: Keystroke telemetry (Gated behind debug mode to preserve zero-latency typing in prod)
        if config.ITTER_DEBUG_MODE:
            utils.debug_log(f"Data received: {data!r} (datatype: {datatype})")

        if data.startswith("\x1b"):
            if data == "\x1b[A":
                self._input_buffer = self._command_history.scroll_up()
                self._cursor_pos = len(self._input_buffer)
                self._redraw_line_and_cursor()
            elif data == "\x1b[B":
                self._input_buffer = self._command_history.scroll_down()
                self._cursor_pos = len(self._input_buffer)
                self._redraw_line_and_cursor()
            elif data == "\x1b[D" and self._cursor_pos > 0:
                self._cursor_pos -= 1
                self._chan.write(data)
            elif data == "\x1b[C" and self._cursor_pos < len(self._input_buffer):
                self._cursor_pos += 1
                self._chan.write(data)
            elif data == "\x1b[5~":
                if self._is_watching_timeline:
                    if self._current_timeline_page > 1:
                        self._current_timeline_page -= 1
                        asyncio.create_task(
                            timeline_cmd.refresh_watch_display(
                                self, timeline_page_to_fetch=self._current_timeline_page
                            )
                        )
                    else:
                        self._write_to_channel(
                            f"\r\n{FG_BRIGHT_BLACK}You cannot move faster than time (yet).{RESET}",
                            newline=True,
                        )
                        self._redraw_prompt_and_buffer()
                elif self._last_timeline_eets_count is not None:
                    if self._current_timeline_page > 1:
                        self._current_timeline_page -= 1
                        asyncio.create_task(
                            timeline_cmd.render_and_display_timeline(
                                self,
                                page=self._current_timeline_page,
                                is_live_update=False,
                            )
                        )
                    else:
                        self._write_to_channel(
                            f"\r\n{FG_BRIGHT_BLACK}Already at the beginning of time(line). The only way is down.{RESET}",
                            newline=True,
                        )
                        self._prompt()
            elif data == "\x1b[6~":
                if self._is_watching_timeline:
                    if (
                        self._last_timeline_eets_count is not None
                        and self._last_timeline_eets_count >= self._timeline_page_size
                    ):
                        self._current_timeline_page += 1
                        asyncio.create_task(
                            timeline_cmd.refresh_watch_display(
                                self, timeline_page_to_fetch=self._current_timeline_page
                            )
                        )
                    else:
                        self._write_to_channel(
                            f"\r\n{FG_BRIGHT_BLACK}I'm afraid that I don't see a thing. Just...silence.{RESET}",
                            newline=True,
                        )
                        self._redraw_prompt_and_buffer()
                elif self._last_timeline_eets_count is not None:
                    if self._last_timeline_eets_count >= self._timeline_page_size:
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
                            f"\r\n{FG_BRIGHT_BLACK}End of the line. No more eets to show (for now).{RESET}",
                            newline=True,
                        )
                        self._prompt()
            elif data == "\x1b[5;5~" and self._sidebar_enabled:
                utils.debug_log(
                    f"Sidebar scroll up. Old offset: {self._sidebar_scroll_offset}"
                )
                self._sidebar_scroll_offset = max(
                    0, self._sidebar_scroll_offset - config.SIDEBAR_SCROLL_STEP
                )
                asyncio.create_task(
                    timeline_cmd.refresh_watch_display(
                        self, timeline_page_to_fetch=self._current_timeline_page
                    )
                )
            elif data == "\x1b[6;5~" and self._sidebar_enabled:
                utils.debug_log(
                    f"Sidebar scroll down. Old offset: {self._sidebar_scroll_offset}"
                )
                scrollable_body_height = max(1, self._term_height - 3 - 1 - 1)
                max_scroll = max(
                    0, len(self._sidebar_full_user_list) - scrollable_body_height
                )
                self._sidebar_scroll_offset = min(
                    max_scroll, self._sidebar_scroll_offset + config.SIDEBAR_SCROLL_STEP
                )
                asyncio.create_task(
                    timeline_cmd.refresh_watch_display(
                        self, timeline_page_to_fetch=self._current_timeline_page
                    )
                )
            else:
                if config.ITTER_DEBUG_MODE:
                    utils.debug_log(f"Unhandled escape sequence: {data!r}")
            return

        needs_redraw = False

        for char in data:
            if char in ("\r", "\n"):
                self._chan.write("\r\n")
                line_to_process = self._input_buffer
                self._input_buffer = ""
                if line_to_process:
                    self._cursor_pos = 0
                    asyncio.create_task(self._handle_command_line(line_to_process))
                else:
                    self._prompt()
                needs_redraw = False
            elif char in ("\x7f", "\x08") and self._cursor_pos > 0:
                self._input_buffer = (
                    self._input_buffer[: self._cursor_pos - 1]
                    + self._input_buffer[self._cursor_pos :]
                )
                self._cursor_pos -= 1
                needs_redraw = True
            elif char == "\x03":
                self._chan.write("^C\r\n")
                self.close()
                needs_redraw = False
            elif char == "\x04":
                self._chan.write("^D\r\n")
                self.close()
                needs_redraw = False
            elif char == "\x15" and self._input_buffer:
                self._input_buffer = ""
                self._cursor_pos = 0
                needs_redraw = True
            elif char == "\x17" and self._cursor_pos > 0:
                old_buffer = self._input_buffer
                end_pos = self._cursor_pos
                start_pos = end_pos - 1
                while start_pos >= 0 and old_buffer[start_pos].isspace():
                    start_pos -= 1
                while start_pos >= 0 and not old_buffer[start_pos].isspace():
                    start_pos -= 1
                new_cursor_pos = start_pos + 1
                self._input_buffer = old_buffer[:new_cursor_pos] + old_buffer[end_pos:]
                self._cursor_pos = new_cursor_pos
                needs_redraw = True
            elif char.isprintable():
                self._input_buffer = (
                    self._input_buffer[: self._cursor_pos]
                    + char
                    + self._input_buffer[self._cursor_pos :]
                )
                self._cursor_pos += 1
                needs_redraw = True

        if needs_redraw:
            self._redraw_line_and_cursor()

    def _clear_screen(self) -> None:
        if self._chan:
            self._chan.write("\033[2J\033[H")

    async def _handle_command_line(self, line: str) -> None:
        if not self.username and not self._is_registration_flow:
            self._write_to_channel(f"{FG_RED}Critical Error:{RESET} No user context.")
            self.close()
            return

        cmd, raw_text_full, hashtags_in_full_line, user_refs_in_full_line = (
            utils.parse_input_line(line)
        )
        utils.debug_log(f"Parsed command: cmd='{cmd}', raw_text_full='{raw_text_full}'")

        if not cmd:
            self._prompt()
            return

        try:
            self._command_history.add((cmd + " " + raw_text_full.strip()).strip())
            if cmd not in ["timeline", "tl", "watch", "w"]:
                self._last_timeline_eets_count = None

            if cmd in ["eet", "e"]:
                await eet_cmd.handle_eet(
                    self, raw_text_full, hashtags_in_full_line, user_refs_in_full_line
                )
            elif cmd in ["timeline", "tl", "watch", "w"]:
                await timeline_cmd.handle_timeline_and_watch(self, cmd, raw_text_full)
                if self._is_watching_timeline:
                    return
            elif cmd in ["follow", "f"]:
                await follow_cmd.handle_follow(self, raw_text_full)
            elif cmd in ["unfollow", "uf"]:
                await follow_cmd.handle_unfollow(self, raw_text_full)
            elif cmd in ["ignore", "i"]:
                await ignore_cmd.handle_ignore(self, raw_text_full)
            elif cmd in ["unignore", "ui"]:
                await ignore_cmd.handle_unignore(self, raw_text_full)
            elif cmd in ["profile", "p"]:
                await profile_cmd.handle_profile_command(
                    self, raw_text_full, user_refs_in_full_line
                )
            elif cmd in ["settings", "s"]:
                if raw_text_full.strip():
                    self._clear_screen()
                await settings_cmd.handle_settings(self, raw_text_full)
            elif cmd in ["help", "h"]:
                await misc_cmd.handle_help(self)
            elif cmd in ["clear", "c"]:
                await misc_cmd.handle_clear(self)
                return
            elif cmd in ["exit", "x"]:
                await misc_cmd.handle_exit_command(self)
                return
            else:
                self._write_to_channel(
                    f"Unknown command: '{FG_BRIGHT_BLACK}{cmd}{RESET}'. Try '{FG_BRIGHT_BLACK}help{RESET}'."
                )
        except ValueError as ve:
            self._write_to_channel(f"{FG_RED}Error:{RESET} {ve}")
        except Exception as e:
            utils.debug_log(f"Error handling command '{cmd}': {e}")
            self._write_to_channel(
                f"\r\n{FG_RED}An unexpected server error occurred.{RESET}"
            )
            # RESTORED: Python traceback rendering to the SSH client in debug mode
            if config.ITTER_DEBUG_MODE:
                self._write_to_channel(
                    "\r\n" + traceback.format_exc().replace("\n", "\r\n")
                )

        if self._chan and not self._is_watching_timeline:
            self._prompt()

    async def handle_new_post_realtime(self, post_record: Dict[str, Any]) -> None:
        utils.debug_log(f"RT check for {self.username}: Post {post_record.get('id')}")
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

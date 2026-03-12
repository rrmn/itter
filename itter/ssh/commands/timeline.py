# /itter/ssh/commands/timeline.py
import asyncio
import re
import textwrap
from typing import TYPE_CHECKING, Dict, Any, List

import itter.data.database as db
import itter.core.utils as utils
import itter.core.config as config
from itter.core.utils import (
    BOLD,
    RESET,
    FG_BRIGHT_BLACK,
    FG_BRIGHT_YELLOW,
    FG_GREEN,
    FG_RED,
)

if TYPE_CHECKING:
    from itter.ssh.shell import ItterShell


async def timeline_refresh_loop(shell: "ItterShell") -> None:
    """The TUI Tick Loop. Restored to update relative times and online users."""
    try:
        while shell._is_watching_timeline:
            await asyncio.sleep(config.WATCH_REFRESH_INTERVAL_SECONDS)
            if shell._is_watching_timeline:
                await refresh_watch_display(
                    shell, timeline_page_to_fetch=shell._current_timeline_page
                )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        if shell._chan:
            shell._clear_screen()
            shell._chan.write(
                f"{FG_RED}ERROR:{RESET} Live timeline tick failed: {e}\r\n"
            )
            shell._redraw_prompt_and_buffer()


async def handle_timeline_and_watch(
    shell: "ItterShell", cmd: str, raw_text: str
) -> None:
    shell._current_timeline_page = 1
    target_specifier_text = raw_text
    parts = raw_text.split()
    page_from_input = None

    if parts and parts[-1].isdigit():
        page_from_input = int(parts[-1])
        target_specifier_text = " ".join(parts[:-1]).strip()

    if target_specifier_text.startswith("@"):
        user_match = re.match(r"^@(\w{3,20})$", target_specifier_text)
        if user_match:
            shell._current_target_filter = {
                "type": "user",
                "value": user_match.group(1),
            }
        else:
            shell._chan.write(
                f"{FG_RED}Invalid user format:{RESET} '{target_specifier_text}'. Defaulting to 'all'.\r\n"
            ) if shell._chan else None
            shell._current_target_filter = {"type": "all", "value": None}
    elif target_specifier_text.startswith("#"):
        channel_match = re.match(r"^#(\w(?:[\w-]*\w)?)$", target_specifier_text)
        if channel_match:
            shell._current_target_filter = {
                "type": "channel",
                "value": channel_match.group(1),
            }
        else:
            shell._chan.write(
                f"{FG_RED}Invalid channel format:{RESET} '{target_specifier_text}'. Defaulting to 'all'.\r\n"
            ) if shell._chan else None
            shell._current_target_filter = {"type": "all", "value": None}
    else:
        shell._current_target_filter = utils.parse_target_filter(target_specifier_text)

    if page_from_input is not None:
        shell._current_timeline_page = page_from_input

    is_watch_command = cmd in ("watch", "w")
    shell._sidebar_enabled = is_watch_command
    shell._is_watching_timeline = is_watch_command

    if is_watch_command:
        await start_live_timeline_view(shell)
    else:
        await render_and_display_timeline(
            shell, page=shell._current_timeline_page, is_live_update=False
        )


async def start_live_timeline_view(shell: "ItterShell") -> None:
    shell._sidebar_enabled = True
    shell._sidebar_scroll_offset = 0
    shell._current_timeline_page = 1

    await refresh_watch_display(shell, timeline_page_to_fetch=1)

    if (
        shell._timeline_auto_refresh_task
        and not shell._timeline_auto_refresh_task.done()
    ):
        shell._timeline_auto_refresh_task.cancel()
    shell._timeline_auto_refresh_task = asyncio.create_task(
        timeline_refresh_loop(shell)
    )


async def render_and_display_timeline(
    shell: "ItterShell", page: int, is_live_update: bool = False
) -> None:
    if not shell.username:
        return
    if is_live_update and shell._is_watching_timeline:
        await refresh_watch_display(shell, timeline_page_to_fetch=page)
    else:
        shell._sidebar_enabled = False
        try:
            eets = await db.db_get_filtered_timeline_posts(
                shell.username,
                shell._current_target_filter,
                page=page,
                page_size=shell._timeline_page_size,
            )
            shell._current_timeline_page = page
            shell._last_timeline_eets_count = len(eets)
        except Exception as e:
            if shell._chan:
                shell._chan.write(f"{FG_RED}Timeline Error:{RESET} {e}\r\n")
            return

        formatted_output = await asyncio.to_thread(
            _format_timeline_output, shell, eets, shell._current_timeline_page
        )
        shell._clear_screen()
        if shell._chan:
            shell._chan.write(formatted_output + "\r\n")


def _format_timeline_output(
    shell: "ItterShell", eets: List[Dict[str, Any]], page: int
) -> str:
    time_w, user_w, sep_w = 12, 20, 3
    eet_w = max(10, shell._term_width - time_w - user_w - (sep_w * 2) - 2)
    target_type_display = str(shell._current_target_filter["type"])
    target_value_display = shell._current_target_filter["value"]

    if target_type_display == "channel" and target_value_display:
        timeline_title = f"#{target_value_display}"
    elif target_type_display == "user" and target_value_display:
        timeline_title = f"@{target_value_display}"
    elif target_type_display == "mine":
        timeline_title = "Your 'Mine' Feed"
    else:
        timeline_title = "All Eets"

    header_line = (
        f"--- {timeline_title} (Page {page}, {shell._timeline_page_size} items) ---"
    )
    output_lines = [f"{BOLD}{header_line}{RESET}"]
    output_lines.append(f"{'Time':<{time_w}}   {'User':<{user_w}}   {'Eet':<{eet_w}}")
    output_lines.append(
        f"{FG_BRIGHT_BLACK}"
        + "-" * min(shell._term_width, time_w + user_w + eet_w + (sep_w * 2))
        + f"{RESET}"
    )

    if not eets:
        output_lines.append(
            f" {FG_BRIGHT_BLACK}No eets found.{RESET}"
            if page == 1
            else f" {FG_BRIGHT_BLACK}End of timeline. Nothing more to see.{RESET}"
        )
    else:
        for eet in eets:
            time_str = utils.time_ago(eet.get("created_at"))
            author_username = str(eet.get("username", "ghost"))
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
            if shell.username and author_username.lower() == shell.username.lower():
                user_column_str_colored = f"{utils.FG_BRIGHT_YELLOW}{user_final_display_name_truncated}{utils.RESET}"

            user_col_visual_width_after_truncate = utils.wcswidth(
                utils.strip_ansi(user_final_display_name_truncated)
            )
            user_col_padding_spaces = user_w - user_col_visual_width_after_truncate
            user_col_padded_final = (
                f"{user_column_str_colored}{' ' * max(0, user_col_padding_spaces)}"
            )

            raw_content = (
                str(eet.get("content", "")).replace("\r", "").replace("\n", " ")
            )
            wrapper = textwrap.TextWrapper(
                width=eet_w,
                subsequent_indent="  ",
                break_long_words=True,
                break_on_hyphens=True,
                drop_whitespace=True,
            )
            content_lines_raw = wrapper.wrap(text=utils.strip_ansi(raw_content))

            if not content_lines_raw:
                output_lines.append(
                    f"{time_str:<{time_w}} | {user_col_padded_final} | "
                )
            else:
                first_line_formatted = utils.format_eet_content(
                    content_lines_raw[0], shell.username, utils.FG_BRIGHT_YELLOW
                )
                output_lines.append(
                    f"{time_str:<{time_w}}   {user_col_padded_final}   {first_line_formatted}"
                )
                indent_str = " " * (time_w + sep_w + user_w + sep_w)
                for i in range(1, len(content_lines_raw)):
                    line_formatted = utils.format_eet_content(
                        content_lines_raw[i], shell.username, utils.FG_BRIGHT_YELLOW
                    )
                    output_lines.append(f"{indent_str}{line_formatted}")

    footer_lines = []
    base_command_parts = ["timeline"]

    if (
        shell._current_target_filter["type"] == "user"
        and shell._current_target_filter["value"]
    ):
        base_command_parts.append(f"@{shell._current_target_filter['value']}")
    elif (
        shell._current_target_filter["type"] == "channel"
        and shell._current_target_filter["value"]
    ):
        base_command_parts.append(f"#{shell._current_target_filter['value']}")
    elif shell._current_target_filter["type"] != "all":
        base_command_parts.append(str(shell._current_target_filter["type"]))

    base_command_str = " ".join(base_command_parts)
    footer = ""

    if not eets and page > 1:
        footer = f"No more eets on page {page}. Try `{base_command_str} {page - 1}` for previous."
    elif len(eets) >= shell._timeline_page_size:
        if page > 1:
            footer = f"Type `{base_command_str} {page + 1}` for next, or `{base_command_str} {page - 1}` for previous."
        else:
            footer = f"Type `{base_command_str} {page + 1}` for next page."
    elif eets and page > 1:
        footer = f"End of results. Type `{base_command_str} {page - 1}` for previous."

    if footer:
        footer_lines.append(footer)
    if footer_lines:
        output_lines.append("\r\n" + "\r\n".join(footer_lines))

    return "\r\n".join(output_lines)


async def refresh_watch_display(
    shell: "ItterShell", timeline_page_to_fetch: int
) -> None:
    if not shell.username or not shell._is_watching_timeline:
        return

    shell._current_timeline_page = timeline_page_to_fetch
    try:
        eets = await db.db_get_filtered_timeline_posts(
            shell.username,
            shell._current_target_filter,
            page=shell._current_timeline_page,
            page_size=shell._timeline_page_size,
        )
        shell._last_timeline_eets_count = len(eets)

        if shell._sidebar_enabled:
            await _update_sidebar_full_user_list(shell)

        # FIX: Push heavy UI rendering into a background thread to prevent main loop typing stutter!
        screen_output = await asyncio.to_thread(_build_watch_screen_output, shell, eets)

        if shell._chan:
            shell._clear_screen()
            # FIX: Direct write bypasses slow string replacement overhead
            shell._chan.write(screen_output + "\r\n")
            shell._redraw_line_and_cursor()

    except Exception as e:
        if shell._chan:
            shell._clear_screen()
            shell._chan.write(f"{FG_RED}Timeline Refresh Error:{RESET} {e}\r\n")
            shell._redraw_prompt_and_buffer()


async def _update_sidebar_full_user_list(shell: "ItterShell") -> None:
    if not shell.username or not shell._active_sessions:
        shell._sidebar_full_user_list = []
        return

    online_usernames = sorted(
        list(shell._active_sessions.keys()), key=lambda u: u.lower()
    )
    followed_users_data = await db.db_get_user_following(shell.username)
    followed_usernames_set = {
        user_data["username"].lower() for user_data in followed_users_data
    }

    def sort_key(u_name: str) -> tuple:
        is_self = u_name.lower() == str(shell.username).lower()
        is_followed = u_name.lower() in followed_usernames_set
        return (not is_self, not is_followed, u_name.lower())

    sorted_online_users = sorted(online_usernames, key=sort_key)
    formatted_list = []

    for user in sorted_online_users:
        prefix = (
            f"{FG_GREEN}*{RESET} "
            if user.lower() in followed_usernames_set
            and user.lower() != shell.username.lower()
            else "  "
        )
        user_display_str = f"{prefix}@{user}"
        if user.lower() == shell.username.lower():
            user_display_str = f"  {FG_BRIGHT_YELLOW}@{user}{RESET}"
        truncated_user_str = utils.truncate_str_with_wcwidth(
            user_display_str, config.SIDEBAR_WIDTH - 1
        )
        formatted_list.append(truncated_user_str)

    shell._sidebar_full_user_list = formatted_list


def _get_timeline_body_lines_for_watch(
    shell: "ItterShell",
    eets: List[Dict[str, Any]],
    timeline_content_width: int,
    num_lines_available: int,
) -> List[str]:
    if not shell.username:
        return [" " * timeline_content_width] * num_lines_available

    time_w, user_w_max, sep_chars_count = 10, 18, 4
    eet_content_text_width = max(
        10, timeline_content_width - time_w - user_w_max - sep_chars_count
    )
    output_lines = []

    if not eets:
        return [" " * timeline_content_width] * num_lines_available

    for eet_idx, eet in enumerate(eets):
        if len(output_lines) >= num_lines_available:
            break

        time_str = utils.time_ago(eet.get("created_at"))
        author_username = str(eet.get("username", "ghost"))
        author_display_name = eet.get("display_name")
        user_display_raw = f"@{author_username}"

        if author_display_name:
            user_display_raw = f"{author_display_name} ({user_display_raw})"

        user_display_truncated_plain = utils.truncate_str_with_wcwidth(
            user_display_raw, user_w_max
        )
        user_col_str_colored = user_display_truncated_plain

        if author_username.lower() == shell.username.lower():
            user_col_str_colored = (
                f"{FG_BRIGHT_YELLOW}{user_display_truncated_plain}{RESET}"
            )

        user_col_padding = user_w_max - utils.wcswidth(user_display_truncated_plain)
        user_col_final_padded = (
            f"{user_col_str_colored}{' ' * max(0, user_col_padding)}"
        )

        raw_content = str(eet.get("content", "")).replace("\r", "").replace("\n", " ")
        wrapper = textwrap.TextWrapper(
            width=eet_content_text_width,
            subsequent_indent="  ",
            break_long_words=True,
            break_on_hyphens=True,
            drop_whitespace=True,
        )
        content_lines_wrapped_raw = wrapper.wrap(text=utils.strip_ansi(raw_content))
        if not content_lines_wrapped_raw:
            content_lines_wrapped_raw = [""]

        for i, line_content_raw in enumerate(content_lines_wrapped_raw):
            if len(output_lines) >= num_lines_available:
                break
            formatted_eet_line_content_colored = utils.format_eet_content(
                line_content_raw, shell.username
            )
            line_prefix_str = (
                f"{time_str:<{time_w}}  {user_col_final_padded}  "
                if i == 0
                else " " * (time_w + 2 + user_w_max + 2)
            )
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


def _get_sidebar_visible_content_lines(
    shell: "ItterShell", num_lines_available: int
) -> List[str]:
    if not shell._sidebar_enabled or not shell._sidebar_full_user_list:
        return [" " * config.SIDEBAR_WIDTH] * num_lines_available
    start_idx = shell._sidebar_scroll_offset
    end_idx = shell._sidebar_scroll_offset + num_lines_available
    visible_user_strings_already_truncated = shell._sidebar_full_user_list[
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


def _build_watch_screen_output(shell: "ItterShell", eets: List[Dict[str, Any]]) -> str:
    if not shell.username:
        return ""

    sidebar_w = config.SIDEBAR_WIDTH if shell._sidebar_enabled else 0
    separator_str = f" {FG_BRIGHT_BLACK}|{RESET} " if shell._sidebar_enabled else ""
    separator_visual_w = (
        utils.wcswidth(utils.strip_ansi(separator_str)) if shell._sidebar_enabled else 0
    )

    timeline_w = max(20, shell._term_width - sidebar_w - separator_visual_w)
    output_buffer = []

    scrollable_body_height = max(1, shell._term_height - 3 - 1 - 1)

    target_type = str(shell._current_target_filter["type"])
    target_val = shell._current_target_filter["value"]

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
            f"--- {tl_title_text_content} (Page {shell._current_timeline_page}, {shell._timeline_page_size} per page) ---",
            timeline_w,
        )
        + RESET
    )
    tl_title_padded = f"{_tl_title_raw}{' ' * max(0, timeline_w - utils.wcswidth(utils.strip_ansi(_tl_title_raw)))}"

    time_cw, user_cw_max, tl_col_seps = 10, 18, 4
    eet_cw = max(10, timeline_w - time_cw - user_cw_max - tl_col_seps)
    _tl_cols_raw = utils.truncate_str_with_wcwidth(
        f"{'Time':<{time_cw}}  {'User':<{user_cw_max}}  {'Eet':<{eet_cw}}", timeline_w
    )
    tl_cols_padded = f"{_tl_cols_raw}{' ' * max(0, timeline_w - utils.wcswidth(utils.strip_ansi(_tl_cols_raw)))}"

    _tl_sep_raw = FG_BRIGHT_BLACK + "-" * timeline_w + RESET
    tl_sep_padded = f"{_tl_sep_raw}{' ' * max(0, timeline_w - utils.wcswidth(utils.strip_ansi(_tl_sep_raw)))}"

    sb_title_padded = " " * sidebar_w
    sb_sep_padded = " " * sidebar_w
    if shell._sidebar_enabled:
        _sb_title_raw = (
            f"{BOLD}Souls Connected ({len(shell._sidebar_full_user_list)}){RESET}"
        )
        _sb_title_trunc = utils.truncate_str_with_wcwidth(_sb_title_raw, sidebar_w)
        sb_title_padded = f"{_sb_title_trunc}{' ' * max(0, sidebar_w - utils.wcswidth(utils.strip_ansi(_sb_title_trunc)))}"
        _sb_sep_raw = FG_BRIGHT_BLACK + "-" * sidebar_w + RESET
        sb_sep_padded = f"{_sb_sep_raw}{' ' * max(0, sidebar_w - utils.wcswidth(utils.strip_ansi(_sb_sep_raw)))}"

    output_buffer.append(f"{tl_title_padded}")
    output_buffer.append(f"{tl_cols_padded}{separator_str}{sb_title_padded}")
    output_buffer.append(
        f"{tl_sep_padded}{separator_str}{sb_sep_padded if shell._sidebar_enabled else ''}"
    )

    timeline_body_content = _get_timeline_body_lines_for_watch(
        shell, eets, timeline_w, scrollable_body_height
    )
    sidebar_body_content = _get_sidebar_visible_content_lines(
        shell, scrollable_body_height
    )

    for i in range(scrollable_body_height):
        timeline_line = timeline_body_content[i]
        sidebar_line = sidebar_body_content[i] if shell._sidebar_enabled else ""
        output_buffer.append(f"{timeline_line}{separator_str}{sidebar_line}")

    status_footer_raw = f"Live updating {tl_title_text_content}... {FG_BRIGHT_BLACK}(PgUp/PgDn to scroll, 'exit' to stop){RESET}"
    status_footer_padded = utils.truncate_str_with_wcwidth(
        status_footer_raw, shell._term_width
    )
    output_buffer.append(status_footer_padded)

    return "\r\n".join(output_buffer)


async def handle_new_post_realtime(
    shell: "ItterShell", post_record: Dict[str, Any]
) -> None:
    if not shell._is_watching_timeline or not shell.username:
        return
    await refresh_watch_display(shell, timeline_page_to_fetch=1)

import os
import json
import sys
import unicodedata

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

from rich.text import Text
from rich.color import Color
from rich.panel import Panel
from rich.console import Console
from rich.table import Table

console = Console()

VERSION = "2.5.0"

SUCCESS_COLOR = ["#67b6a6", "#92ded2"]
INFO_COLOR = ["#6d8da8", "#97cbe3"]
INFO_TEXT_COLOR = ["#97cbe3", "#c0ece8"]
WARN_COLOR = ["#e69c69", "#ffd680"]
ERROR_COLOR = ["#bb8b8a", "#ffd6d7"]
TEXT_COLOR = ["#cfd7e3", "#f6f6f6", "#e1ebf0"]
THINK_COLOR = ["#7e7d80", "#b4b1b2"]
STREAM_THINK_COLOR = "#7e7d80"
STREAM_RESPONSE_COLOR = "#f6f6f6"
VK_SHIFT = 0x10
VK_CONTROL = 0x11
WINDOWS_NEWLINE_KEYS = (VK_SHIFT, VK_CONTROL)
WINDOWS_SPECIAL_KEY_PREFIXES = ("\x00", "\xe0")

if os.name == "nt":
    STD_OUTPUT_HANDLE = -11

    class _COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class _SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", wintypes.SHORT),
            ("Top", wintypes.SHORT),
            ("Right", wintypes.SHORT),
            ("Bottom", wintypes.SHORT),
        ]

    class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", _COORD),
            ("dwCursorPosition", _COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", _SMALL_RECT),
            ("dwMaximumWindowSize", _COORD),
        ]

    KERNEL32 = ctypes.windll.kernel32
    USER32 = ctypes.windll.user32

    KERNEL32.GetStdHandle.argtypes = [wintypes.DWORD]
    KERNEL32.GetStdHandle.restype = wintypes.HANDLE
    KERNEL32.GetConsoleScreenBufferInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_CONSOLE_SCREEN_BUFFER_INFO),
    ]
    KERNEL32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
    KERNEL32.SetConsoleCursorPosition.argtypes = [wintypes.HANDLE, _COORD]
    KERNEL32.SetConsoleCursorPosition.restype = wintypes.BOOL
    KERNEL32.FillConsoleOutputCharacterW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_wchar,
        wintypes.DWORD,
        _COORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    KERNEL32.FillConsoleOutputCharacterW.restype = wintypes.BOOL
    KERNEL32.FillConsoleOutputAttribute.argtypes = [
        wintypes.HANDLE,
        wintypes.WORD,
        wintypes.DWORD,
        _COORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    KERNEL32.FillConsoleOutputAttribute.restype = wintypes.BOOL
    USER32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    USER32.GetAsyncKeyState.restype = ctypes.c_short

    STDOUT = KERNEL32.GetStdHandle(STD_OUTPUT_HANDLE)


def gradient_text(content, start_color, end_color, mid_color=None):
    gradient = Text()
    start_triplet = Color.parse(start_color).triplet
    end_triplet = Color.parse(end_color).triplet
    mid_triplet = Color.parse(mid_color).triplet if mid_color else None
    step_count = max(len(content) - 1, 1)

    for index, character in enumerate(content):
        progress = index / step_count

        if mid_triplet and progress <= 0.5:
            channels = _blend_channels(start_triplet, mid_triplet, progress * 2)
        elif mid_triplet:
            channels = _blend_channels(mid_triplet, end_triplet, (progress - 0.5) * 2)
        else:
            channels = _blend_channels(start_triplet, end_triplet, progress)

        gradient.append(character, style=f"bold rgb({channels})")

    return gradient


def background_block(content, background_color):
    style = f"bold {TEXT_COLOR[1]} on {background_color}"
    table = Table.grid(expand=True, padding=(0, 0))
    table.add_column(ratio=1, style=style)
    table.add_row(Text(str(content), style=style))
    return table


def diff_background_block(content):
    table = Table.grid(expand=True, padding=(0, 0))
    table.add_column(ratio=1)

    lines = str(content or "").splitlines() or [""]
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            style = f"bold {TEXT_COLOR[1]} on {SUCCESS_COLOR[0]}"
        elif line.startswith("-") and not line.startswith("---"):
            style = f"bold {TEXT_COLOR[1]} on {ERROR_COLOR[0]}"
        else:
            style = f"bold {TEXT_COLOR[1]}"
        table.add_row(Text(line, style=style))

    return table


def _blend_channels(start, end, progress):
    return ",".join(
        str(round(start_value + (end_value - start_value) * progress))
        for start_value, end_value in zip(start, end)
    )


def clean_display_text(text):
    if isinstance(text, list):
        text = "\n".join(_display_content_block(block) for block in text)
    elif isinstance(text, dict):
        text = json.dumps(text, ensure_ascii=False)
    else:
        text = str(text or "")
    return "\n".join(line for line in text.strip().split("\n") if line.strip())


def _display_content_block(block):
    if not isinstance(block, dict):
        return str(block)

    block_type = block.get("type")
    if block_type == "text":
        return block.get("text", "")
    if block_type == "thinking":
        return block.get("thinking", "")
    if block_type == "tool_use":
        return f"[tool_use] {block.get('name', '')} {json.dumps(block.get('input', {}), ensure_ascii=False)}"
    if block_type == "tool_result":
        return f"[tool_result] {block.get('content', '')}"
    return json.dumps(block, ensure_ascii=False)


def print_message(symbol, content, color):
    console.print(
        Text.assemble(
            "\n",
            gradient_text(symbol, *color),
            gradient_text(f" {content}", *TEXT_COLOR),
        )
    )


def print_success(content):
    print_message("[✓]", content, SUCCESS_COLOR)


def print_error(content):
    print_message("[✗]", content, ERROR_COLOR)


def print_warn(content):
    print_message("[!]", content, WARN_COLOR)


def print_info(content):
    print_message("[-]", content, INFO_COLOR)


def print_thinking(content):
    console.print(
        Text.assemble(
            "\n",
            gradient_text("[*]", *THINK_COLOR),
            gradient_text(f" Thinking: {content}", *THINK_COLOR),
        )
    )


def print_stream_thinking(content, leading_newline=True):
    prefix = "\n" if leading_newline else ""
    console.print(
        Text.assemble(
            prefix,
            gradient_text("[*] Thinking: ", *THINK_COLOR),
            Text(content, style=f"bold {STREAM_THINK_COLOR}"),
        ),
        end="",
    )


def print_stream_thinking_continue(content):
    # Collapse multiple \n into single \n
    while "\n\n" in content:
        content = content.replace("\n\n", "\n")
    console.print(Text(content, style=f"bold {STREAM_THINK_COLOR}"), end="")


def clear_current_line():
    if not console.is_terminal:
        return
    console.file.write("\r\033[2K")
    console.file.flush()


def clear_current_lines(line_count):
    if not console.is_terminal:
        return
    line_count = max(1, int(line_count or 1))
    console.file.write("\r\033[2K")
    for _ in range(line_count - 1):
        console.file.write("\033[1A\r\033[2K")
    console.file.flush()


def print_stream_response_start(model_name):
    console.print(
        Text.assemble(
            "\n",
            gradient_text("[✓] ", *SUCCESS_COLOR),
            gradient_text(f"{model_name.upper()}: ", *TEXT_COLOR),
        ),
        end="",
    )


def clean_and_print_stream_response(content):
    # Collapse multiple \n into single \n
    while "\n\n" in content:
        content = content.replace("\n\n", "\n")
    # Remove leading \n
    if content.startswith("\n"):
        content = content[1:]
    console.print(Text(content, style=f"bold {STREAM_RESPONSE_COLOR}"), end="")


def print_stream_response_continue(content):
    console.print(Text(content, style=f"bold {STREAM_RESPONSE_COLOR}"), end="")


def _dashboard_text_from_segments(*segments):
    max_length = max(console.width - 44, 0)
    full_text = "".join(content for content, _ in segments)

    if len(full_text) <= max_length:
        return Text.assemble(
            *(gradient_text(content, *colors) for content, colors in segments)
        )

    remaining_length = max(console.width - 47, 0)
    rendered_segments = []
    for content, colors in segments:
        if remaining_length <= 0:
            break

        visible_content = content[:remaining_length]
        if visible_content:
            rendered_segments.append(gradient_text(visible_content, *colors))
            remaining_length -= len(visible_content)

    rendered_segments.append(gradient_text("...", *TEXT_COLOR))
    return Text.assemble(*rendered_segments)


def _dashboard_centered_left_text(*segments):
    available_width = max(console.width - 2, 0)
    content_length = sum(len(content) for content, _ in segments)
    left_padding = max((available_width - content_length) // 2, 0)

    return Text.assemble(
        " " * left_padding,
        *(gradient_text(content, *colors) for content, colors in segments),
    )


def _get_last_record_text(line_number):
    record_dir = "record"
    if not os.path.exists(record_dir):
        files = []
    else:
        files = sorted(
            [f for f in os.listdir(record_dir) if f.endswith(".json")]
        )

    if not files:
        if line_number == 1:
            return _dashboard_text_from_segments((" No history record", TEXT_COLOR))
        return Text("")

    if len(files) > 5 and line_number == 1:
        return _dashboard_text_from_segments(
            ("[-]", INFO_COLOR),
            (" ...", TEXT_COLOR),
        )

    if len(files) > 5:
        visible_files = files[-4:]
        file_index = line_number - 2
    else:
        visible_files = files
        file_index = line_number - 1

    if file_index < 0 or file_index >= len(visible_files):
        return Text("")

    filename = visible_files[file_index]

    # Format: 2026-04-25-14-30.json -> " 2026.04.25 14:30 <version> <MODEL>"
    name = filename[:-5]
    parts = name.split("-")
    if len(parts) >= 5:
        formatted = f"{parts[0]}.{parts[1]}.{parts[2]} {parts[3]}:{parts[4]}"
    else:
        formatted = name

    # Read version, model and message count from JSON file
    version = ""
    model = ""
    msg_count = ""
    try:
        with open(
            os.path.join(record_dir, filename), "r", encoding="utf-8"
        ) as f:
            data = json.load(f)
        version = data.get("version", "")
        model = data.get("model", "").upper()
        conversation = data.get("conversation", [])
        msg_count = f"{len(conversation)} Messages"
    except Exception:
        pass

    return _dashboard_text_from_segments(
        (f" {formatted}", TEXT_COLOR),
        (f" <{version}>", THINK_COLOR),
        (f" <{model}>", THINK_COLOR),
        (f" <{msg_count}>", THINK_COLOR),
    )


def show_dashboard(model_name, workspace_dir=None):
    console.clear()
    console.print()
    title = Text.assemble(
        gradient_text("LLM Chat", *INFO_TEXT_COLOR),
        gradient_text(f" v{VERSION}", *TEXT_COLOR),
    )
    billing_text = f"{model_name.upper() + ' · API Usage Billing':^37}"
    cwd = workspace_dir or "No workspace directory"
    cwd_text = f"...{cwd[-34:]}" if len(cwd) > 37 else f"{cwd:^37}"

    if console.width < 68:
        dashboard_content = Text.assemble(
            "\n",
            _dashboard_centered_left_text(("Welcome back!", TEXT_COLOR)),
            "\n\n",
            _dashboard_centered_left_text(("▐▛███▜▌", INFO_COLOR)),
            "\n",
            _dashboard_centered_left_text(("▝▜█████▛▘", INFO_COLOR)),
            "\n",
            _dashboard_centered_left_text(("▘▘ ▝▝", INFO_COLOR)),
            "\n\n",
            _dashboard_centered_left_text((billing_text, THINK_COLOR)),
            "\n",
            _dashboard_centered_left_text((cwd_text, THINK_COLOR)),
        )
    else:
        dashboard_content = Text.assemble(
            " " * 39,
            Text("│", style="bold #6d8da8"),
            gradient_text(" Tips for getting started\n", *INFO_TEXT_COLOR),
            " " * 13,
            gradient_text("Welcome back!", *TEXT_COLOR),
            " " * 13,
            Text("│", style="bold #6d8da8"),
            _dashboard_text_from_segments(
                (
                    " Run /help to display a list of available commands and their descriptions.",
                    TEXT_COLOR,
                ),
            ),
            "\n",
            " " * 39,
            Text("│", style="bold #6d8da8"),
            " ",
            gradient_text("─" * (console.width - 44), *INFO_COLOR),
            "\n",
            " " * 16,
            gradient_text("▐▛███▜▌", *INFO_COLOR),
            " " * 16,
            Text("│", style="bold #6d8da8"),
            gradient_text(" History record", *INFO_TEXT_COLOR),
            "\n",
            " " * 15,
            gradient_text("▝▜█████▛▘", *INFO_COLOR),
            " " * 15,
            Text("│", style="bold #6d8da8"),
            _get_last_record_text(1),
            "\n",
            " " * 17,
            gradient_text("▘▘ ▝▝", *INFO_COLOR),
            " " * 17,
            Text("│", style="bold #6d8da8"),
            _get_last_record_text(2),
            "\n",
            " " * 39,
            Text("│", style="bold #6d8da8"),
            _get_last_record_text(3),
            "\n",
            " " * 1,
            gradient_text(billing_text, *THINK_COLOR),
            " " * 1,
            Text("│", style="bold #6d8da8"),
            _get_last_record_text(4),
            "\n",
            " " * 1,
            gradient_text(cwd_text, *THINK_COLOR),
            " " * 1,
            Text("│", style="bold #6d8da8"),
            _get_last_record_text(5),
        )

    console.print(
        Panel(
            dashboard_content,
            padding=0,
            title=title,
            title_align="left",
            border_style="bold #6d8da8",
        )
    )


def _input_prompt(prompt_text):
    return Text.assemble(
        "\n",
        gradient_text("[-]", *INFO_COLOR),
        gradient_text(f" {prompt_text}", *TEXT_COLOR),
    )


def _key_down(key):
    return bool(USER32.GetAsyncKeyState(key) & 0x8000)


def _windows_console_info():
    info = _CONSOLE_SCREEN_BUFFER_INFO()
    if not KERNEL32.GetConsoleScreenBufferInfo(STDOUT, ctypes.byref(info)):
        raise ctypes.WinError()
    return info


def _windows_set_cursor(position):
    if not KERNEL32.SetConsoleCursorPosition(STDOUT, position):
        raise ctypes.WinError()


def _windows_fill_spaces(start, cells, attrs):
    if cells <= 0:
        return

    written = wintypes.DWORD()
    KERNEL32.FillConsoleOutputCharacterW(
        STDOUT, " ", cells, start, ctypes.byref(written)
    )
    KERNEL32.FillConsoleOutputAttribute(
        STDOUT, attrs, cells, start, ctypes.byref(written)
    )


def _input_char_width(character):
    if unicodedata.combining(character):
        return 0
    if unicodedata.east_asian_width(character) in ("F", "W"):
        return 2
    return 1


def _input_position_after(origin, text, columns):
    x, y = origin.X, origin.Y

    for character in text:
        if character == "\n":
            x = 0
            y += 1
            continue

        width = _input_char_width(character)
        if width <= 0:
            continue
        if x + width > columns:
            x = 0
            y += 1

        x += width
        if x >= columns:
            x = 0
            y += 1

    return _COORD(x, y)


def _input_cell_span(start, end, columns):
    return max(0, (end.Y - start.Y) * columns + end.X - start.X)


class _WindowsInputRenderer:
    def __init__(self):
        info = _windows_console_info()
        self.origin = _COORD(info.dwCursorPosition.X, info.dwCursorPosition.Y)
        self.last_span = 0

    def redraw(self, chars, cursor):
        info = _windows_console_info()
        columns = info.dwSize.X

        _windows_fill_spaces(self.origin, self.last_span, info.wAttributes)
        _windows_set_cursor(self.origin)

        text = "".join(chars)
        console.file.write(text)
        console.file.flush()

        end = _input_position_after(self.origin, text, columns)
        self.last_span = _input_cell_span(self.origin, end, columns)
        self.move_cursor(chars, cursor)

    def move_cursor(self, chars, cursor):
        columns = _windows_console_info().dwSize.X
        _windows_set_cursor(
            _input_position_after(self.origin, "".join(chars[:cursor]), columns)
        )


def _input_line_bounds(chars, cursor):
    start = 0
    for index in range(cursor - 1, -1, -1):
        if chars[index] == "\n":
            start = index + 1
            break

    end = len(chars)
    for index in range(cursor, len(chars)):
        if chars[index] == "\n":
            end = index
            break

    return start, end


def _input_column_at(chars, start, cursor):
    return sum(_input_char_width(character) for character in chars[start:cursor])


def _input_cursor_at_column(chars, start, end, column):
    current_column = 0
    for index in range(start, end):
        next_column = current_column + _input_char_width(chars[index])
        if next_column > column:
            return index
        current_column = next_column

    return end


def _input_move_vertical(chars, cursor, step, column):
    start, end = _input_line_bounds(chars, cursor)

    if step < 0:
        if start == 0:
            return cursor
        start, end = _input_line_bounds(chars, start - 1)
    else:
        if end == len(chars):
            return cursor
        start, end = _input_line_bounds(chars, end + 1)

    return _input_cursor_at_column(chars, start, end, column)


def _read_windows_multiline_input(prompt):
    chars = []
    cursor = 0
    paste_active = False
    skip_lf = False
    target_column = None
    console.print(prompt, end="")
    renderer = _WindowsInputRenderer()

    def render(redraw=False):
        nonlocal target_column
        target_column = None
        if redraw:
            renderer.redraw(chars, cursor)
        else:
            renderer.move_cursor(chars, cursor)

    def insert_newline():
        nonlocal cursor
        chars.insert(cursor, "\n")
        cursor += 1
        render(redraw=True)

    while True:
        ch = msvcrt.getwch()

        if ch in WINDOWS_SPECIAL_KEY_PREFIXES:
            paste_active = False
            skip_lf = False
            key = msvcrt.getwch()
            if key in ("H", "P"):
                if target_column is None:
                    start, _ = _input_line_bounds(chars, cursor)
                    target_column = _input_column_at(chars, start, cursor)
                cursor = _input_move_vertical(
                    chars, cursor, -1 if key == "H" else 1, target_column
                )
                renderer.move_cursor(chars, cursor)
            elif key == "K" and cursor > 0:
                cursor -= 1
                render()
            elif key == "M" and cursor < len(chars):
                cursor += 1
                render()
            elif key == "G":
                cursor, _ = _input_line_bounds(chars, cursor)
                render()
            elif key == "O":
                _, cursor = _input_line_bounds(chars, cursor)
                render()
            elif key == "S" and cursor < len(chars):
                del chars[cursor]
                render(redraw=True)
            continue

        queued = msvcrt.kbhit()
        pasted = paste_active or queued

        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x1a":
            raise EOFError

        if ch in ("\r", "\n"):
            if ch == "\n" and skip_lf:
                skip_lf = False
                paste_active = queued
                continue

            if any(_key_down(key) for key in WINDOWS_NEWLINE_KEYS) or pasted:
                insert_newline()
                paste_active = queued
                skip_lf = ch == "\r"
                continue

            console.file.write("\n")
            console.file.flush()
            return "".join(chars)

        skip_lf = False

        if ch == "\b":
            if cursor > 0:
                cursor -= 1
                del chars[cursor]
                render(redraw=True)
            paste_active = queued
            continue

        if not ch.isprintable():
            paste_active = queued
            continue

        chars.insert(cursor, ch)
        cursor += 1
        render(redraw=True)
        paste_active = queued


def get_user_input(prompt_text, multiline=False):
    prompt = _input_prompt(prompt_text)
    if multiline and os.name == "nt" and sys.stdin.isatty() and sys.stdout.isatty():
        return _read_windows_multiline_input(prompt).strip()
    return console.input(prompt).strip()


def get_agent_edit_confirmation(file_path, occurrences, old_content, new_content):
    console.print(
        Text.assemble(
            "\n",
            gradient_text("[-]", *INFO_COLOR),
            gradient_text(f" Allow agent to edit file? ({file_path})\n", *TEXT_COLOR),
            gradient_text(f"Occurrences to replace: {occurrences}\n", *TEXT_COLOR),
        ),
        end="",
    )
    console.print(background_block(f"Old:\n{old_content}", ERROR_COLOR[0]))
    console.print(background_block(f"New:\n{new_content}", SUCCESS_COLOR[0]))
    answer = console.input(
        Text.assemble(
            gradient_text("Continue? (Y/N, Default: N): ", *TEXT_COLOR),
        )
    )
    return answer.strip().lower() in {"y", "yes"}


def get_agent_patch_confirmation(file_path, start_line, end_line, old_content, new_content):
    console.print(
        Text.assemble(
            "\n",
            gradient_text("[-]", *INFO_COLOR),
            gradient_text(
                f" Allow agent to patch file? ({file_path}:{start_line}-{end_line})\n",
                *TEXT_COLOR,
            ),
        ),
        end="",
    )
    console.print(background_block(f"Old lines:\n{old_content}", ERROR_COLOR[0]))
    console.print(background_block(f"New lines:\n{new_content}", SUCCESS_COLOR[0]))
    answer = console.input(
        Text.assemble(
            gradient_text("Continue? (Y/N, Default: N): ", *TEXT_COLOR),
        )
    )
    return answer.strip().lower() in {"y", "yes"}


def get_agent_diff_confirmation(title, file_path, diff_content):
    console.print(
        Text.assemble(
            "\n",
            gradient_text("[-]", *INFO_COLOR),
            gradient_text(f" {title} ({file_path})\n", *TEXT_COLOR),
        ),
        end="",
    )
    console.print(diff_background_block(diff_content))
    answer = console.input(
        Text.assemble(
            gradient_text("Continue? (Y/N, Default: N): ", *TEXT_COLOR),
        )
    )
    return answer.strip().lower() in {"y", "yes"}

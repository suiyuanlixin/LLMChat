import os
import json
import queue
import re
import shutil
import sys
import threading
import unicodedata
from io import StringIO

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

from rich.text import Text
from rich.color import Color
from rich.panel import Panel
from rich.console import Console
from rich.table import Table

from prompt_toolkit.application import Application
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.data_structures import Point, Size
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI as PromptANSI
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea


_console_override = threading.local()
_dashboard_capture = threading.local()
_tui_session = None


class _ConsoleProxy:
    def __init__(self):
        self._console = Console()

    def _delegate(self):
        return getattr(_console_override, "console", self._console)

    def __getattr__(self, name):
        return getattr(self._delegate(), name)

    def print(self, *objects, **kwargs):
        override = getattr(_console_override, "console", None)
        if override is not None:
            return override.print(*objects, **kwargs)
        if _tui_session is not None:
            _tui_session.append_console_print(*objects, **kwargs)
            return None
        return self._console.print(*objects, **kwargs)

    def input(self, prompt="", *args, **kwargs):
        override = getattr(_console_override, "console", None)
        if override is not None:
            return override.input(prompt, *args, **kwargs)
        if _tui_session is not None:
            return _tui_session.request_console_input(prompt)
        return self._console.input(prompt, *args, **kwargs)


console = _ConsoleProxy()

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

    class _CHAR_UNION(ctypes.Union):
        _fields_ = [
            ("UnicodeChar", ctypes.c_wchar),
            ("AsciiChar", ctypes.c_char),
        ]

    class _CHAR_INFO(ctypes.Structure):
        _fields_ = [
            ("Char", _CHAR_UNION),
            ("Attributes", wintypes.WORD),
        ]

    class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", _COORD),
            ("dwCursorPosition", _COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", _SMALL_RECT),
            ("dwMaximumWindowSize", _COORD),
        ]

    KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    USER32 = ctypes.WinDLL("user32", use_last_error=True)

    KERNEL32.GetStdHandle.argtypes = [wintypes.DWORD]
    KERNEL32.GetStdHandle.restype = wintypes.HANDLE
    KERNEL32.GetConsoleMode.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    KERNEL32.GetConsoleMode.restype = wintypes.BOOL
    KERNEL32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    KERNEL32.SetConsoleMode.restype = wintypes.BOOL
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
    KERNEL32.ReadConsoleOutputW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_CHAR_INFO),
        _COORD,
        _COORD,
        ctypes.POINTER(_SMALL_RECT),
    ]
    KERNEL32.ReadConsoleOutputW.restype = wintypes.BOOL
    KERNEL32.WriteConsoleOutputW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_CHAR_INFO),
        _COORD,
        _COORD,
        ctypes.POINTER(_SMALL_RECT),
    ]
    KERNEL32.WriteConsoleOutputW.restype = wintypes.BOOL
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
    return _DiffBackgroundBlock(content)


class _DiffBackgroundBlock:
    def __init__(self, content):
        self.content = str(content or "")

    def __rich_console__(self, rich_console, options):
        yield _build_diff_background_table(self.content, options.max_width)


def _build_diff_background_table(content, width=None):
    table = Table.grid(expand=True, padding=(0, 0))
    table.add_column(ratio=1)

    lines = str(content or "").splitlines() or [""]
    number_width = _diff_line_number_width(lines)
    old_line = None
    new_line = None

    for line in lines:
        hunk_match = re.match(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
            line,
        )
        if hunk_match:
            old_line = int(hunk_match.group(1))
            new_line = int(hunk_match.group(3))
            rendered_line = _diff_numbered_line(line, None, number_width)
        elif line.startswith("+") and not line.startswith("+++") and new_line is not None:
            rendered_line = _diff_numbered_line(line, new_line, number_width)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---") and old_line is not None:
            rendered_line = _diff_numbered_line(line, old_line, number_width)
            old_line += 1
        elif line.startswith(" ") and old_line is not None and new_line is not None:
            rendered_line = _diff_numbered_line(line, new_line, number_width)
            old_line += 1
            new_line += 1
        else:
            rendered_line = _diff_numbered_line(line, None, number_width)

        if line.startswith("+") and not line.startswith("+++"):
            style = f"bold {TEXT_COLOR[1]} on {SUCCESS_COLOR[0]}"
        elif line.startswith("-") and not line.startswith("---"):
            style = f"bold {TEXT_COLOR[1]} on {ERROR_COLOR[0]}"
        else:
            style = f"bold {TEXT_COLOR[1]}"
        table.add_row(_diff_row_text(rendered_line, style, width), style=style)

    return table


def _diff_line_number_width(lines):
    max_line = 0
    for line in lines:
        match = re.match(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
            line,
        )
        if not match:
            continue
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_count = int(match.group(4) or "1")
        max_line = max(
            max_line,
            old_start + max(0, old_count - 1),
            new_start + max(0, new_count - 1),
        )
    return max(1, len(str(max_line or 1)))


def _diff_numbered_line(line, line_number, width):
    number_text = f"{line_number:>{width}}" if line_number is not None else " " * width
    return f"{number_text} | {line}"


def _diff_row_text(line, style, width=None):
    line = str(line or "")
    text = Text(line, style=style)
    if width is None:
        return text
    pad_width = max(0, int(width or 0) - _input_text_width(line))
    if pad_width:
        text.append(" " * pad_width, style=style)
    return text


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
    if _tui_session is not None:
        console.print(
            Text.assemble(
                prefix,
                gradient_text("[*] Thinking: ", *THINK_COLOR),
            ),
            end="",
        )
        if content:
            _tui_session.append_stream_text(content, f"bold {STREAM_THINK_COLOR}")
        return

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
    if _tui_session is not None:
        _tui_session.append_stream_text(content, f"bold {STREAM_THINK_COLOR}")
        return
    console.print(Text(content, style=f"bold {STREAM_THINK_COLOR}"), end="")


def clear_current_line():
    if _tui_session is not None:
        _tui_session.clear_current_lines(1)
        return
    if not console.is_terminal:
        return
    console.file.write("\r\033[2K")
    console.file.flush()


def clear_current_lines(line_count):
    if _tui_session is not None:
        _tui_session.clear_current_lines(line_count)
        return
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
    if _tui_session is not None:
        _tui_session.append_stream_text(content, f"bold {STREAM_RESPONSE_COLOR}")
        return
    console.print(Text(content, style=f"bold {STREAM_RESPONSE_COLOR}"), end="")


def print_stream_response_continue(content):
    if _tui_session is not None:
        _tui_session.append_stream_text(content, f"bold {STREAM_RESPONSE_COLOR}")
        return
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
    if _tui_session is not None and not _is_dashboard_capture():
        _tui_session.set_dashboard(model_name, workspace_dir)
        return

    if not _is_dashboard_capture():
        console.clear()
    if not _is_dashboard_capture():
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


def _windows_read_region(top_row, height, columns):
    info = _windows_console_info()
    viewport_left = info.srWindow.Left
    viewport_top = info.srWindow.Top
    viewport_right = info.srWindow.Right
    viewport_bottom = info.srWindow.Bottom

    top = viewport_top + max(1, top_row) - 1
    bottom = min(top + max(1, height) - 1, viewport_bottom)
    right = min(viewport_left + max(1, columns) - 1, viewport_right)
    width = right - viewport_left + 1
    height = bottom - top + 1
    if width <= 0 or height <= 0:
        return None

    buffer = (_CHAR_INFO * (width * height))()
    rect = _SMALL_RECT(viewport_left, top, right, bottom)
    buffer_size = _COORD(width, height)
    buffer_coord = _COORD(0, 0)

    if not KERNEL32.ReadConsoleOutputW(
        STDOUT, buffer, buffer_size, buffer_coord, ctypes.byref(rect)
    ):
        return None

    return buffer, width, height, viewport_left, top, right, bottom


def _windows_write_region(saved_region):
    if not saved_region:
        return

    buffer, width, height, left, top, right, bottom = saved_region
    rect = _SMALL_RECT(left, top, right, bottom)
    KERNEL32.WriteConsoleOutputW(
        STDOUT,
        buffer,
        _COORD(width, height),
        _COORD(0, 0),
        ctypes.byref(rect),
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


BOTTOM_INPUT_BORDER = "\u2500"
BOTTOM_INPUT_PROMPT = "❯ "
BOTTOM_INPUT_PLACEHOLDER = "Type a message..."
ANSI = "\x1b["
MAX_TUI_MESSAGE_CHARS = 400000
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MOUSE_ESCAPE_LEAK_PATTERN = re.compile(r"(?:\x1b\[|\[\[?|\[)<\d+;\d+;\d+[mM]")
TUI_MOUSE_SCROLL_LINES = 4
TUI_PAGE_SCROLL_MARGIN = 2


def _is_dashboard_capture():
    return bool(getattr(_dashboard_capture, "active", False))


def _strip_ansi(text):
    return ANSI_ESCAPE_PATTERN.sub("", str(text or ""))


def _ansi_styled_text(text, style):
    text = str(text or "")
    if not text:
        return ""

    codes = []
    style_text = str(style or "")
    if "bold" in style_text.split():
        codes.append("1")

    match = re.search(r"#([0-9a-fA-F]{6})", style_text)
    if match:
        value = match.group(1)
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
        codes.append(f"38;2;{red};{green};{blue}")

    if not codes:
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def _render_console_print_to_ansi(width, *objects, **kwargs):
    output = StringIO()
    render_console = Console(
        file=output,
        force_terminal=True,
        color_system="truecolor",
        width=max(1, int(width or 80)),
        legacy_windows=False,
        highlight=False,
    )
    render_console.print(*objects, **kwargs)
    return output.getvalue()


def _render_diff_background_block_ansi(width, content, print_kwargs=None):
    return _render_console_print_to_ansi(
        width,
        _build_diff_background_table(content, width),
        **dict(print_kwargs or {}),
    )


def _render_dashboard_ansi(model_name, workspace_dir, width):
    output = StringIO()
    render_console = Console(
        file=output,
        force_terminal=True,
        color_system="truecolor",
        width=max(1, int(width or 80)),
        legacy_windows=False,
        highlight=False,
    )
    previous_console = getattr(_console_override, "console", None)
    previous_capture = getattr(_dashboard_capture, "active", False)
    _console_override.console = render_console
    _dashboard_capture.active = True
    try:
        show_dashboard(model_name, workspace_dir)
    finally:
        if previous_console is None:
            try:
                del _console_override.console
            except AttributeError:
                pass
        else:
            _console_override.console = previous_console
        _dashboard_capture.active = previous_capture
    return output.getvalue().rstrip("\n")


def _todo_panel_renderable(todos, width, max_lines):
    width = max(10, int(width or 80))
    max_lines = max(1, int(max_lines or 1))
    content_width = max(1, width - 2)
    visible_todos = list(todos or [])[:max_lines]

    lines = []
    for todo in visible_todos:
        status = str(todo.get("status") or "pending").strip().lower()
        marker = "[ ]"
        marker_colors = TEXT_COLOR
        if status == "in_progress":
            marker = "[-]"
            marker_colors = INFO_COLOR
        elif status == "completed":
            marker = "[✓]"
            marker_colors = SUCCESS_COLOR
        elif status == "blocked":
            marker = "[!]"
            marker_colors = WARN_COLOR
        elif status == "failed":
            marker = "[x]"
            marker_colors = ERROR_COLOR

        priority = str(todo.get("priority") or "").strip().upper()
        prefix = f"{priority} " if priority and priority != "P2" else ""
        suffix_parts = []
        depends_on = todo.get("depends_on") or []
        if depends_on:
            suffix_parts.append("after " + ", ".join(str(item) for item in depends_on))
        reason = str(todo.get("reason") or "").strip()
        if reason and status in {"blocked", "failed"}:
            suffix_parts.append(reason)
        suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""

        content = _truncate_cells(
            prefix + str(todo.get("content") or "") + suffix,
            max(1, content_width - _input_text_width(marker) - 1),
        )
        lines.append(
            Text.assemble(
                gradient_text(marker, *marker_colors),
                " ",
                gradient_text(content, *TEXT_COLOR),
            )
        )

    body_parts = []
    for index, line in enumerate(lines):
        body_parts.append(line)
        if index < len(lines) - 1:
            body_parts.append("\n")

    title = Text.assemble(gradient_text("Todo List", *INFO_TEXT_COLOR))
    return Panel(
        Text.assemble(*body_parts),
        padding=0,
        title=title,
        title_align="left",
        border_style="bold #6d8da8",
    )


def _render_todo_panel_ansi(todos, width, max_lines):
    return _render_console_print_to_ansi(
        width,
        _todo_panel_renderable(todos, width, max_lines),
    ).rstrip("\n")


class _TUIPlaceholderProcessor(Processor):
    def __init__(self, session):
        self.session = session

    def apply_transformation(self, transformation_input):
        if (
            transformation_input.lineno == 0
            and not self.session.input_area.text
        ):
            return Transformation(
                [("class:input.placeholder", BOTTOM_INPUT_PLACEHOLDER)]
            )
        return Transformation(transformation_input.fragments)


class _ScrollableMessageControl(FormattedTextControl):
    def __init__(self, session, *args, **kwargs):
        self.session = session
        super().__init__(*args, **kwargs)

    def mouse_handler(self, mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.session.scroll_messages(TUI_MOUSE_SCROLL_LINES)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.session.scroll_messages(-TUI_MOUSE_SCROLL_LINES)
            return None
        return super().mouse_handler(mouse_event)


def _patch_windows_output_full_width(output):
    if os.name != "nt" or output is None:
        return

    target = getattr(output, "win32_output", output)
    if getattr(target, "_llmchat_full_width_patched", False):
        return
    if not hasattr(target, "get_win32_screen_buffer_info"):
        return

    original_get_size = target.get_size

    def get_size_full_width():
        try:
            info = target.get_win32_screen_buffer_info()
            if getattr(target, "use_complete_width", False):
                width = int(info.dwSize.X)
            else:
                width = int(info.srWindow.Right - info.srWindow.Left + 1)
            height = int(info.srWindow.Bottom - info.srWindow.Top + 1)
            width = min(max(1, int(info.dwSize.X)), max(1, width))
            return Size(rows=max(1, height), columns=width)
        except Exception:
            return original_get_size()

    target.get_size = get_size_full_width
    target._llmchat_full_width_patched = True


class ChatTUISession:
    def __init__(
        self,
        model_name=None,
        workspace_dir=None,
        app_input=None,
        app_output=None,
    ):
        self.model_name = model_name or ""
        self.workspace_dir = workspace_dir
        self.messages_ansi = ""
        self.messages_plain = ""
        self.message_fragments = []
        self.message_blocks = []
        self.message_blocks_have_dynamic = False
        self.message_blocks_width = None
        self.message_scroll_offset = 0
        self.message_render_line_lengths = [0]
        self.message_content_line_lengths = [0]
        self.message_render_lines_dirty = False
        self.message_plain_newline_count = 0
        self.input_enabled = False
        self.pending_prompt_text = ""
        self.pending_prompt_rendered = False
        self.confirmation_active = False
        self.confirmation_selected = False
        self.lock = threading.RLock()
        self.input_queue = queue.Queue()
        self.dashboard_cache_key = None
        self.dashboard_cache_text = ""
        self.terminal_select_mode = False
        self.todo_items = []
        self.todo_cache_key = None
        self.todo_cache_text = ""
        self.last_terminal_size = None
        self.resize_watch_stop = threading.Event()
        self.resize_watch_thread = None

        self.input_area = TextArea(
            multiline=True,
            prompt="",
            scrollbar=False,
            wrap_lines=True,
            height=1,
            read_only=Condition(
                lambda: not self.input_enabled or self.confirmation_active
            ),
            input_processors=[_TUIPlaceholderProcessor(self)],
            style="class:input",
        )
        self.input_area.buffer.on_text_changed += lambda _: self._input_changed()

        self.message_control = _ScrollableMessageControl(
            self,
            self._message_text,
            show_cursor=False,
            get_cursor_position=self._message_cursor_position,
        )
        self.message_window = Window(
            content=self.message_control,
            wrap_lines=True,
            always_hide_cursor=True,
        )
        self.dashboard_window = Window(
            content=FormattedTextControl(
                self._dashboard_text,
                show_cursor=False,
            ),
            height=self._dashboard_height,
            always_hide_cursor=True,
        )
        self.todo_window = Window(
            content=FormattedTextControl(
                self._todo_text,
                show_cursor=False,
            ),
            height=self._todo_height,
            wrap_lines=False,
            always_hide_cursor=True,
        )
        self.todo_container = ConditionalContainer(
            content=self.todo_window,
            filter=Condition(self._todo_visible),
        )
        self.input_top_border = Window(
            content=FormattedTextControl(
                lambda: [("class:input.border", self._border_line())]
            ),
            height=1,
            always_hide_cursor=True,
        )
        self.input_bottom_border = Window(
            content=FormattedTextControl(
                lambda: [("class:input.border", self._border_line())]
            ),
            height=1,
            always_hide_cursor=True,
        )
        self.root = HSplit(
            [
                self.dashboard_window,
                self.message_window,
                self.todo_container,
                self.input_top_border,
                self.input_area,
                self.input_bottom_border,
            ]
        )
        self.layout = Layout(self.root, focused_element=self.input_area)
        self.app = Application(
            layout=self.layout,
            key_bindings=self._key_bindings(),
            full_screen=True,
            mouse_support=False,
            color_depth=ColorDepth.TRUE_COLOR,
            input=app_input,
            output=app_output,
            max_render_postpone_time=0,
            style=Style.from_dict(
                {
                    "input": f"bold {STREAM_RESPONSE_COLOR}",
                    "input.border": f"bold {STREAM_RESPONSE_COLOR}",
                    "input.placeholder": f"bold {STREAM_THINK_COLOR}",
                    "text-area": f"bold {STREAM_RESPONSE_COLOR}",
                }
            ),
        )
        _patch_windows_output_full_width(self.app.output)

    def set_dashboard(self, model_name, workspace_dir=None):
        with self.lock:
            self.model_name = model_name or ""
            self.workspace_dir = workspace_dir
            self.dashboard_cache_key = None
        self.invalidate()

    def run(self, worker):
        def start_worker():
            self._disable_terminal_mouse_support_now()
            self._start_resize_watcher()
            worker_thread = threading.Thread(
                target=self._run_worker,
                args=(worker,),
                daemon=True,
            )
            worker_thread.start()

        try:
            self.app.run(pre_run=start_worker)
        except KeyboardInterrupt:
            self._wake_input(KeyboardInterrupt())
            try:
                self.app.exit()
            except Exception:
                pass
        finally:
            self._stop_resize_watcher()
            self._disable_terminal_mouse_support_now()
            self.input_enabled = False

    def stop(self):
        self._stop_resize_watcher()
        self._disable_terminal_mouse_support_now()
        self._wake_input(EOFError())
        try:
            self.app.exit()
        except Exception:
            pass
        self.invalidate()

    def append_console_print(self, *objects, **kwargs):
        if len(objects) == 1 and isinstance(objects[0], _DiffBackgroundBlock):
            self.append_diff_block(objects[0], kwargs)
            return
        ansi = _render_console_print_to_ansi(self._columns(), *objects, **kwargs)
        self.append_ansi(ansi)

    def append_diff_block(self, block, print_kwargs=None):
        width = self._columns()
        print_kwargs = dict(print_kwargs or {})
        ansi = _render_diff_background_block_ansi(
            width,
            block.content,
            print_kwargs,
        )
        if not ansi:
            return
        with self.lock:
            self.message_blocks.append(("diff", block.content, print_kwargs))
            self.message_blocks_have_dynamic = True
            self.message_blocks_width = width
            self.messages_ansi += ansi
            plain = _strip_ansi(ansi)
            self.messages_plain += plain
            self.message_plain_newline_count += plain.count("\n")
            self.message_fragments.extend(to_formatted_text(PromptANSI(ansi)))
            self._mark_message_render_lines_dirty_locked()
            self._trim_messages()
            self._clamp_message_scroll_offset_locked()
        self.invalidate()

    def append_ansi(self, text):
        if not text:
            return
        text = str(text)
        with self.lock:
            self.message_blocks.append(("ansi", text))
            self.messages_ansi += text
            plain = _strip_ansi(text)
            self.messages_plain += plain
            self.message_plain_newline_count += plain.count("\n")
            self.message_fragments.extend(to_formatted_text(PromptANSI(text)))
            self._mark_message_render_lines_dirty_locked()
            self._trim_messages()
            self._clamp_message_scroll_offset_locked()
        self.invalidate()

    def snapshot_messages(self):
        with self.lock:
            return {
                "messages_ansi": self.messages_ansi,
                "messages_plain": self.messages_plain,
                "message_blocks": list(self.message_blocks),
                "message_blocks_have_dynamic": self.message_blocks_have_dynamic,
                "message_blocks_width": self.message_blocks_width,
                "message_plain_newline_count": self.message_plain_newline_count,
                "message_fragments": list(self.message_fragments),
                "message_render_line_lengths": list(self.message_render_line_lengths),
                "message_content_line_lengths": list(self.message_content_line_lengths),
            }

    def restore_messages(self, snapshot):
        if not snapshot:
            return
        with self.lock:
            self.messages_ansi = snapshot["messages_ansi"]
            self.messages_plain = snapshot["messages_plain"]
            self.message_blocks = list(snapshot["message_blocks"])
            self.message_blocks_have_dynamic = snapshot["message_blocks_have_dynamic"]
            self.message_blocks_width = snapshot["message_blocks_width"]
            self.message_plain_newline_count = snapshot["message_plain_newline_count"]
            self.message_fragments = list(snapshot["message_fragments"])
            self.message_render_line_lengths = list(
                snapshot["message_render_line_lengths"]
            )
            self.message_content_line_lengths = list(
                snapshot["message_content_line_lengths"]
            )
            self._mark_message_render_lines_dirty_locked()
            self._clamp_message_scroll_offset_locked()
        self.invalidate()

    def append_styled_text(self, text, style):
        if not text:
            return
        text = str(text)
        ansi = _ansi_styled_text(text, style)
        with self.lock:
            self.message_blocks.append(("ansi", ansi))
            self.messages_ansi += ansi
            self.messages_plain += text
            self.message_plain_newline_count += text.count("\n")
            self.message_fragments.append((style, text))
            self._mark_message_render_lines_dirty_locked()
            self._trim_messages()
            self._clamp_message_scroll_offset_locked()
        self.invalidate()

    def append_stream_text(self, text, style):
        text = str(text or "")
        if not text:
            return
        self.append_styled_text(text, style)

    def set_todos(self, todos):
        normalized = []
        for todo in todos or []:
            if not isinstance(todo, dict):
                continue
            content = str(todo.get("content") or "").strip()
            if not content:
                continue
            normalized.append(
                {
                    "content": content,
                    "status": str(todo.get("status") or "pending").strip().lower(),
                    "priority": str(todo.get("priority") or "").strip().lower(),
                    "depends_on": _todo_string_list(todo.get("depends_on")),
                    "reason": str(todo.get("reason") or "").strip(),
                }
            )
        with self.lock:
            self.todo_items = normalized
            self.todo_cache_key = None
        self.invalidate()

    def clear_current_lines(self, line_count=1):
        line_count = max(1, int(line_count or 1))
        with self.lock:
            plain = self.messages_plain
            if not plain:
                return

            text = self.messages_ansi.rstrip("\n")
            for _ in range(line_count):
                index = text.rfind("\n")
                if index < 0:
                    text = ""
                    break
                text = text[: index + 1]
            self.messages_ansi = text
            self.message_blocks = [("ansi", self.messages_ansi)]
            self.message_blocks_have_dynamic = False
            self.message_blocks_width = None
            self._rebuild_message_cache()
            self._clamp_message_scroll_offset_locked()
        self.invalidate()

    def request_console_input(self, prompt):
        return self.request_input(
            prompt_text="",
            prompt_renderable=prompt,
            prompt_rendered=True,
        )

    def request_confirmation(self, prompt_renderable=None, default=False):
        while True:
            try:
                self.input_queue.get_nowait()
            except queue.Empty:
                break

        with self.lock:
            self.pending_prompt_text = ""
            self.pending_prompt_rendered = True
            self.confirmation_active = True
            self.confirmation_selected = bool(default)
            self.input_enabled = True

        self.input_area.buffer.set_document(Document("", 0), bypass_readonly=True)
        self._render_confirmation_line()
        self._resize_input()
        self.layout.focus(self.input_area)
        self.invalidate()

        result = self.input_queue.get()
        if isinstance(result, BaseException):
            raise result
        return bool(result)

    def request_input(
        self,
        prompt_text="",
        prompt_renderable=None,
        prompt_rendered=False,
    ):
        while True:
            try:
                self.input_queue.get_nowait()
            except queue.Empty:
                break

        if prompt_renderable is not None:
            self.append_console_print(prompt_renderable, end="")

        with self.lock:
            self.pending_prompt_text = str(prompt_text or "")
            self.pending_prompt_rendered = bool(prompt_rendered)
            self.confirmation_active = False
            self.input_enabled = True

        self.input_area.buffer.set_document(Document("", 0), bypass_readonly=True)
        self._resize_input()
        self.layout.focus(self.input_area)
        self.invalidate()

        result = self.input_queue.get()
        if isinstance(result, BaseException):
            raise result
        return str(result)

    def invalidate(self):
        with self.lock:
            if self.terminal_select_mode:
                return
        self._invalidate_app()

    def _invalidate_app(self):
        try:
            self.app.invalidate()
        except Exception:
            pass

    def _mouse_support_enabled(self):
        return False

    def _toggle_terminal_select_mode(self):
        with self.lock:
            active = not self.terminal_select_mode
        self._set_terminal_select_mode(active)

    def _set_terminal_select_mode(self, active):
        active = bool(active)
        with self.lock:
            if self.terminal_select_mode == active:
                return
            self.terminal_select_mode = active

        if active:
            self._disable_terminal_mouse_support_now()
        else:
            self._invalidate_app()

    def _disable_terminal_mouse_support_now(self):
        try:
            output = self.app.output
            output.disable_mouse_support()
            output.flush()
            renderer = getattr(self.app, "renderer", None)
            if renderer is not None:
                renderer._mouse_support_enabled = False
        except Exception:
            pass

    def _run_worker(self, worker):
        try:
            worker()
        except BaseException as error:
            if not isinstance(error, (KeyboardInterrupt, EOFError)):
                print_error(f"Error occurred: {error}")
        finally:
            self.stop()

    def _key_bindings(self):
        bindings = KeyBindings()

        @bindings.add("f2", eager=True, is_global=True)
        def _(event):
            self._toggle_terminal_select_mode()

        @bindings.add("enter")
        def _(event):
            if self.confirmation_active:
                self._submit_confirmation()
                return
            if (
                self.input_enabled
                and os.name == "nt"
                and (
                    _key_down(VK_SHIFT) != _key_down(VK_CONTROL)
                )
            ):
                event.current_buffer.insert_text("\n")
                return
            self._submit_input()

        @bindings.add("escape", "[", "1", "3", ";", "2", "u")
        @bindings.add("escape", "[", "1", "3", ";", "5", "u")
        @bindings.add("escape", "[", "1", "3", ";", "2", "~")
        @bindings.add("escape", "[", "1", "3", ";", "5", "~")
        def _(event):
            if self.input_enabled:
                event.current_buffer.insert_text("\n")

        @bindings.add("c-c")
        def _(event):
            self._wake_input(KeyboardInterrupt())

        @bindings.add("c-d")
        def _(event):
            if not self.input_area.text:
                self._wake_input(EOFError())

        confirm_filter = Condition(lambda: self.confirmation_active)

        @bindings.add("left", eager=True, filter=confirm_filter)
        @bindings.add("up", eager=True, filter=confirm_filter)
        @bindings.add("s-tab", eager=True, filter=confirm_filter)
        def _(event):
            self._set_confirmation_selected(True)

        @bindings.add("right", eager=True, filter=confirm_filter)
        @bindings.add("down", eager=True, filter=confirm_filter)
        @bindings.add("tab", eager=True, filter=confirm_filter)
        def _(event):
            self._set_confirmation_selected(False)

        @bindings.add("y", eager=True, filter=confirm_filter)
        def _(event):
            self._set_confirmation_selected(True)
            self._submit_confirmation()

        @bindings.add("n", eager=True, filter=confirm_filter)
        def _(event):
            self._set_confirmation_selected(False)
            self._submit_confirmation()

        @bindings.add("pageup", eager=True, is_global=True)
        def _(event):
            self.scroll_messages(self._message_page_lines())

        @bindings.add("pagedown", eager=True, is_global=True)
        def _(event):
            self.scroll_messages(-self._message_page_lines())

        @bindings.add("c-end", eager=True, is_global=True)
        def _(event):
            self.scroll_messages_to_bottom()

        return bindings

    def _submit_input(self):
        if not self.input_enabled:
            return
        if self.confirmation_active:
            self._submit_confirmation()
            return

        value = self.input_area.text
        with self.lock:
            prompt_text = self.pending_prompt_text
            prompt_rendered = self.pending_prompt_rendered
            self.input_enabled = False
            self.pending_prompt_text = ""
            self.pending_prompt_rendered = False
            self.confirmation_active = False

        self.input_area.buffer.set_document(Document("", 0), bypass_readonly=True)
        self._resize_input()
        self._echo_submitted_input(value, prompt_text, prompt_rendered)
        self.input_queue.put(value)
        self.invalidate()

    def _submit_confirmation(self):
        if not self.input_enabled or not self.confirmation_active:
            return

        with self.lock:
            selected = bool(self.confirmation_selected)
            self.input_enabled = False
            self.confirmation_active = False
            self.pending_prompt_text = ""
            self.pending_prompt_rendered = False

        self.input_area.buffer.set_document(Document("", 0), bypass_readonly=True)
        self._resize_input()
        self._render_confirmation_line(selected=selected, confirmed=True)
        self.input_queue.put(selected)
        self.invalidate()

    def _set_confirmation_selected(self, selected):
        with self.lock:
            if not self.confirmation_active:
                return
            self.confirmation_selected = bool(selected)
        self._render_confirmation_line()
        self.invalidate()

    def _render_confirmation_line(self, selected=None, confirmed=False):
        if selected is None:
            with self.lock:
                selected = bool(self.confirmation_selected)

        renderable = _confirmation_line_renderable(selected, confirmed=confirmed)
        ansi = _render_console_print_to_ansi(
            self._columns(),
            renderable,
            end="\n",
            soft_wrap=True,
        )
        self._replace_confirmation_line(ansi)

    def _replace_confirmation_line(self, ansi):
        width = self._columns()
        with self.lock:
            if self.message_blocks and self.message_blocks[-1][0] == "confirmation":
                self.message_blocks[-1] = ("confirmation", ansi)
            else:
                self.message_blocks.append(("confirmation", ansi))

            self.message_blocks_have_dynamic = any(
                block and block[0] == "diff" for block in self.message_blocks
            )
            self.message_blocks_width = width if self.message_blocks_have_dynamic else None
            self.messages_ansi = self._render_message_blocks_locked(width)
            self.messages_plain = _strip_ansi(self.messages_ansi)
            self.message_plain_newline_count = self.messages_plain.count("\n")
            self.message_fragments = list(to_formatted_text(PromptANSI(self.messages_ansi)))
            self._mark_message_render_lines_dirty_locked()
            self._trim_messages()
            self._clamp_message_scroll_offset_locked()
        self.invalidate()

    def _wake_input(self, exception):
        if self.input_enabled:
            self.input_queue.put(exception)
        self.input_enabled = False
        self.confirmation_active = False
        self.invalidate()

    def _echo_submitted_input(self, value, prompt_text, prompt_rendered):
        if prompt_rendered:
            ansi = _render_console_print_to_ansi(
                self._columns(),
                Text(str(value), style=f"bold {STREAM_RESPONSE_COLOR}"),
                end="\n",
                soft_wrap=True,
            )
            self.append_ansi(ansi)
            return

        ansi = _render_console_print_to_ansi(
            self._columns(),
            _submitted_prompt(prompt_text),
            end="",
        )
        ansi += _render_console_print_to_ansi(
            self._columns(),
            Text(str(value), style=f"bold {STREAM_RESPONSE_COLOR}"),
            end="\n",
            soft_wrap=True,
        )
        self.append_ansi(ansi)

    def _message_text(self):
        width = self._columns()
        with self.lock:
            self._ensure_message_render_cache_locked(width)
            fragments = list(self.message_fragments) or [("", "")]
            self.message_content_line_lengths = (
                list(self.message_render_line_lengths) or [0]
            )
        return fragments or [("", "")]

    def _message_cursor_position(self):
        with self.lock:
            scroll_offset = self.message_scroll_offset
            line_lengths = list(self.message_content_line_lengths) or [0]
        max_offset = max(0, len(line_lengths) - 1)
        line_index = max(0, len(line_lengths) - 1 - min(scroll_offset, max_offset))
        return Point(x=line_lengths[line_index], y=line_index)

    def scroll_messages(self, delta):
        with self.lock:
            self.message_scroll_offset = max(
                0,
                min(
                    self.message_scroll_offset + int(delta or 0),
                    self._message_max_scroll_offset_locked(),
                ),
            )
        self.invalidate()

    def scroll_messages_to_bottom(self):
        with self.lock:
            self.message_scroll_offset = 0
        self.invalidate()

    def _start_resize_watcher(self):
        if self.resize_watch_thread and self.resize_watch_thread.is_alive():
            return
        self.resize_watch_stop.clear()
        self.last_terminal_size = self._terminal_size()
        self.resize_watch_thread = threading.Thread(
            target=self._watch_terminal_resize,
            daemon=True,
        )
        self.resize_watch_thread.start()

    def _stop_resize_watcher(self):
        self.resize_watch_stop.set()

    def _watch_terminal_resize(self):
        while not self.resize_watch_stop.wait(0.2):
            size = self._terminal_size()
            if size == self.last_terminal_size:
                continue
            self._handle_terminal_resize(size)

    def _handle_terminal_resize(self, size):
        with self.lock:
            self.last_terminal_size = size
            self.dashboard_cache_key = None
            self.todo_cache_key = None
            if self.message_blocks_have_dynamic:
                self._rebuild_message_cache(size[0])
            else:
                self._mark_message_render_lines_dirty_locked()
            self._clamp_message_scroll_offset_locked()
        self._resize_input()
        self.invalidate()

    def _message_page_lines(self):
        render_info = getattr(self.message_window, "render_info", None)
        if render_info is not None:
            return max(1, int(render_info.window_height) - TUI_PAGE_SCROLL_MARGIN)
        return max(1, self._rows() - self._dashboard_height() - self._input_height() - 2)

    def _message_max_scroll_offset_locked(self):
        return max(0, self.message_plain_newline_count)

    def _mark_message_render_lines_dirty_locked(self):
        self.message_render_lines_dirty = True

    def _refresh_message_render_lines_if_needed_locked(self, fragments):
        if not self.message_render_lines_dirty:
            return
        self._refresh_message_render_lines_locked(fragments)
        self.message_render_lines_dirty = False

    def _ensure_message_render_cache_locked(self, width=None):
        width = max(1, int(width or self._columns()))
        if (
            self.message_blocks_have_dynamic
            and self.message_blocks_width != width
        ):
            self._rebuild_message_cache(width)
        self._refresh_message_render_lines_if_needed_locked(
            self.message_fragments or [("", "")]
        )

    def _refresh_message_render_lines_locked(self, fragments):
        try:
            line_lengths = [
                len(fragment_list_to_text(line))
                for line in split_lines(fragments or [("", "")])
            ]
        except Exception:
            line_lengths = [0]
        self.message_render_line_lengths = line_lengths or [0]

    def _dashboard_text(self):
        width = self._columns()
        with self.lock:
            key = (self.model_name, self.workspace_dir, width)
            if key != self.dashboard_cache_key:
                self.dashboard_cache_text = _render_dashboard_ansi(
                    self.model_name,
                    self.workspace_dir,
                    width,
                )
                self.dashboard_cache_key = key
            text = self.dashboard_cache_text
        return PromptANSI(text)

    def _dashboard_height(self):
        plain = _strip_ansi(fragment_list_to_text_safe(self._dashboard_text()))
        return max(1, len(plain.splitlines()) or 1)

    def _todo_visible(self):
        with self.lock:
            return bool(self.todo_items)

    def _todo_height(self):
        plain = _strip_ansi(fragment_list_to_text_safe(self._todo_text()))
        return max(1, len(plain.splitlines()) or 1)

    def _todo_max_lines(self):
        return max(1, min(8, self._rows() - self._dashboard_height() - self._input_height() - 6))

    def _todo_text(self):
        width = self._columns()
        max_lines = self._todo_max_lines()
        with self.lock:
            todos = list(self.todo_items)
            key = (
                width,
                max_lines,
                tuple(
                    (
                        todo.get("status"),
                        todo.get("priority"),
                        todo.get("content"),
                        tuple(todo.get("depends_on") or []),
                        todo.get("reason"),
                    )
                    for todo in todos
                ),
            )
            if key == self.todo_cache_key:
                return PromptANSI(self.todo_cache_text)
        if not todos:
            return PromptANSI("")

        text = _render_todo_panel_ansi(todos, width, max_lines)
        with self.lock:
            self.todo_cache_key = key
            self.todo_cache_text = text
        return PromptANSI(text)

    def _input_height(self):
        width = max(1, self._columns())
        text = self.input_area.text if hasattr(self, "input_area") else ""
        lines = text.split("\n") or [""]
        height = 0
        for line in lines:
            line_width = max(1, _input_text_width(line))
            height += max(1, (line_width + width - 1) // width)
        return max(1, min(10, height))

    def _input_changed(self):
        if self._strip_mouse_escape_leaks_from_input():
            return
        self._resize_input()
        self.invalidate()

    def _strip_mouse_escape_leaks_from_input(self):
        if not hasattr(self, "input_area"):
            return False
        text = self.input_area.text
        cleaned = MOUSE_ESCAPE_LEAK_PATTERN.sub("", text)
        if cleaned == text:
            return False
        cursor_position = min(self.input_area.buffer.cursor_position, len(cleaned))
        self.input_area.buffer.set_document(
            Document(cleaned, cursor_position),
            bypass_readonly=True,
        )
        self._resize_input()
        self.invalidate()
        return True

    def _resize_input(self):
        if not hasattr(self, "input_area"):
            return
        self.input_area.window.height = Dimension.exact(self._input_height())

    def _border_line(self):
        return BOTTOM_INPUT_BORDER * self._columns()

    def _columns(self):
        return self._terminal_size()[0]

    def _rows(self):
        return self._terminal_size()[1]

    def _terminal_size(self):
        size = shutil.get_terminal_size(fallback=(80, 24))
        return max(10, int(size.columns)), max(5, int(size.lines))

    def _trim_messages(self):
        if len(self.messages_ansi) <= MAX_TUI_MESSAGE_CHARS:
            return

        overflow = len(self.messages_ansi) - MAX_TUI_MESSAGE_CHARS
        index = self.messages_ansi.find("\n", overflow)
        if index < 0:
            self.messages_ansi = self.messages_ansi[-MAX_TUI_MESSAGE_CHARS:]
        else:
            self.messages_ansi = self.messages_ansi[index + 1 :]
        self.message_blocks = [("ansi", self.messages_ansi)]
        self.message_blocks_have_dynamic = False
        self.message_blocks_width = None
        self._rebuild_message_cache()

    def _rebuild_message_cache(self, width=None):
        if self.message_blocks_have_dynamic:
            width = max(1, int(width or self._columns()))
            self.messages_ansi = self._render_message_blocks_locked(width)
            self.message_blocks_width = width
        self.messages_plain = _strip_ansi(self.messages_ansi)
        self.message_plain_newline_count = self.messages_plain.count("\n")
        self.message_fragments = list(to_formatted_text(PromptANSI(self.messages_ansi)))
        self._mark_message_render_lines_dirty_locked()
        self._clamp_message_scroll_offset_locked()

    def _render_message_blocks_locked(self, width):
        rendered = []
        for block in self.message_blocks:
            if not block:
                continue
            if block[0] == "diff":
                _, content, print_kwargs = block
                rendered.append(
                    _render_diff_background_block_ansi(width, content, print_kwargs)
                )
            else:
                rendered.append(block[1])
        return "".join(rendered)

    def _clamp_message_scroll_offset_locked(self):
        self.message_scroll_offset = max(
            0,
            min(self.message_scroll_offset, self._message_max_scroll_offset_locked()),
        )


def fragment_list_to_text_safe(value):
    try:
        from prompt_toolkit.formatted_text import fragment_list_to_text
        from prompt_toolkit.formatted_text import to_formatted_text

        return fragment_list_to_text(to_formatted_text(value))
    except Exception:
        return str(value or "")


def start_tui(model_name=None, workspace_dir=None):
    global _tui_session
    _tui_session = ChatTUISession(model_name, workspace_dir)
    return _tui_session


def set_todos_panel(todos):
    if _tui_session is not None:
        _tui_session.set_todos(todos)


def clear_todos_panel():
    set_todos_panel([])


def run_tui(worker):
    global _tui_session
    if _tui_session is None:
        start_tui()
    try:
        _tui_session.run(worker)
    except KeyboardInterrupt:
        pass
    finally:
        _tui_session = None


def stop_tui():
    if _tui_session is not None:
        _tui_session.stop()


def _ansi_write(text):
    console.file.write(text)
    console.file.flush()


def _enable_ansi_input_rendering():
    if os.name != "nt":
        return

    mode = wintypes.DWORD()
    if not KERNEL32.GetConsoleMode(STDOUT, ctypes.byref(mode)):
        return

    enable_virtual_terminal_processing = 0x0004
    KERNEL32.SetConsoleMode(
        STDOUT, mode.value | enable_virtual_terminal_processing
    )


def _bottom_terminal_size():
    if os.name == "nt":
        try:
            info = _windows_console_info()
            rows = info.srWindow.Bottom - info.srWindow.Top + 1
            columns = info.srWindow.Right - info.srWindow.Left + 1
            return max(4, rows), max(10, columns)
        except OSError:
            pass

    size = shutil.get_terminal_size(fallback=(80, 24))
    return max(4, size.lines), max(10, size.columns)


def _input_text_width(text):
    return sum(_input_char_width(character) for character in text)


def _truncate_cells(text, max_width):
    max_width = max(1, int(max_width or 1))
    text = str(text or "").replace("\r", " ").replace("\n", " ")
    if _input_text_width(text) <= max_width:
        return text
    if max_width <= 3:
        return "." * max_width

    kept = []
    current_width = 0
    target_width = max_width - 3
    for character in text:
        width = _input_char_width(character)
        if current_width + width > target_width:
            break
        kept.append(character)
        current_width += width
    return "".join(kept).rstrip() + "..."


def _todo_string_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        values = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        values = [str(part).strip() for part in value]
    else:
        values = [str(value).strip()]
    return [value for value in values if value]


def _input_rich_render(text, style, width):
    output = StringIO()
    render_console = Console(
        file=output,
        force_terminal=True,
        color_system="truecolor",
        width=max(1, width),
        legacy_windows=False,
        highlight=False,
    )
    render_console.print(Text(text, style=style), end="", soft_wrap=True)
    return output.getvalue()


def _submitted_prompt(prompt_text):
    return Text.assemble(
        "\n",
        gradient_text("[-]", *INFO_COLOR),
        gradient_text(f" {prompt_text}", *TEXT_COLOR),
    )


class _BottomInputRenderer:
    def __init__(self, prompt_text):
        _enable_ansi_input_rendering()
        self.prompt_text = str(prompt_text)
        self.rows, self.columns = _bottom_terminal_size()
        self.input_height = 0
        self.output_bottom = self.rows
        self.saved_region = None
        info = _windows_console_info()
        self.output_row = min(
            max(info.dwCursorPosition.Y - info.srWindow.Top + 1, 1), self.rows
        )
        self.output_col = min(
            max(info.dwCursorPosition.X - info.srWindow.Left + 1, 1), self.columns
        )

    def render(self, chars, cursor):
        self._restore_saved_region()
        self.rows, self.columns = _bottom_terminal_size()
        self.output_col = min(max(self.output_col, 1), self.columns)
        lines, cursor_row, cursor_col = self._build_lines(chars, cursor)
        max_content_height = max(1, self.rows - 3)
        content_height = min(len(lines), max_content_height)
        self._set_input_height(content_height + 2)

        input_top = self.rows - self.input_height + 1
        self._save_input_area(input_top)
        self._clear_input_area()
        self._write_border(input_top)
        self._write_border(self.rows)

        visible_start = max(0, cursor_row - content_height + 1)
        visible_start = min(visible_start, max(0, len(lines) - content_height))
        first_content_row = input_top + 1

        visible_lines = lines[visible_start : visible_start + content_height]
        for offset, line in enumerate(visible_lines):
            row = first_content_row + offset
            _ansi_write(f"{ANSI}{row};1H")
            self._write_input_line(line, visible_start + offset)

        cursor_screen_row = first_content_row + cursor_row - visible_start
        _ansi_write(f"{ANSI}{cursor_screen_row};{cursor_col}H{ANSI}?25h")

    def submit(self, value):
        self._restore()
        self.input_height = 3
        self.output_bottom = max(1, self.rows - self.input_height)
        self._scroll_output_if_needed()
        self._set_output_scroll_region()
        _ansi_write(f"{ANSI}{self.output_row};{self.output_col}H")
        console.print(_submitted_prompt(self.prompt_text), end="")
        console.print(Text(value, style=f"bold {STREAM_RESPONSE_COLOR}"), end="")
        console.file.write("\n")
        console.file.flush()
        self._capture_output_cursor()
        self._render_idle()

    def close(self):
        self._restore()

    def _restore(self):
        self._restore_saved_region()
        _ansi_write(f"{ANSI}r{ANSI}?25h{ANSI}{self.output_row};{self.output_col}H")

    def _set_input_height(self, height):
        height = max(3, min(height, self.rows))
        self.input_height = height
        self.output_bottom = max(1, self.rows - height)
        _ansi_write(f"{ANSI}?25l")

    def _set_output_scroll_region(self):
        _ansi_write(f"{ANSI}1;{self.output_bottom}r")

    def _scroll_output_if_needed(self):
        if self.output_row <= self.output_bottom:
            return

        scroll_lines = self.output_row - self.output_bottom
        _ansi_write(f"{ANSI}r{ANSI}{self.rows};1H" + ("\n" * scroll_lines))
        self.output_row = self.output_bottom

    def _capture_output_cursor(self):
        info = _windows_console_info()
        self.output_row = min(
            max(info.dwCursorPosition.Y - info.srWindow.Top + 1, 1), self.rows
        )
        self.output_col = min(
            max(info.dwCursorPosition.X - info.srWindow.Left + 1, 1), self.columns
        )
        self._scroll_output_if_needed()
        self._set_output_scroll_region()

    def _render_idle(self):
        self.rows, self.columns = _bottom_terminal_size()
        self.input_height = 3
        self.output_bottom = max(1, self.rows - self.input_height)
        self._scroll_output_if_needed()
        self._set_output_scroll_region()

        input_top = self.rows - self.input_height + 1
        self._clear_input_area()
        self._write_border(input_top)
        self._write_border(self.rows)
        _ansi_write(f"{ANSI}{input_top + 1};1H")
        self._write_input_line(BOTTOM_INPUT_PROMPT, 0)
        _ansi_write(f"{ANSI}{self.output_row};{self.output_col}H{ANSI}?25h")

    def _clear_input_area(self, height=None):
        height = self.input_height if height is None else height
        if height <= 0:
            return

        height = min(height, self.rows)
        input_top = max(1, self.rows - height + 1)
        for row in range(input_top, self.rows + 1):
            _ansi_write(f"{ANSI}{row};1H{ANSI}2K")

    def _write_border(self, row):
        _ansi_write(f"{ANSI}{row};1H{BOTTOM_INPUT_BORDER * self.columns}")

    def _save_input_area(self, input_top):
        self.saved_region = _windows_read_region(
            input_top, self.input_height, self.columns
        )

    def _restore_saved_region(self):
        if not self.saved_region:
            return

        _windows_write_region(self.saved_region)
        self.saved_region = None

    def _write_input_line(self, line, line_index):
        if line_index == 0:
            prompt_width = _input_text_width(BOTTOM_INPUT_PROMPT)
            _ansi_write(
                _input_rich_render(
                    BOTTOM_INPUT_PROMPT,
                    f"bold {STREAM_RESPONSE_COLOR}",
                    prompt_width,
                )
            )
            body = line[len(BOTTOM_INPUT_PROMPT) :]
            width = self.columns - prompt_width
        else:
            body = line
            width = self.columns

        if line_index == 0 and not body and width > 0:
            placeholder = BOTTOM_INPUT_PLACEHOLDER[:width]
            _ansi_write(
                _input_rich_render(placeholder, f"bold {STREAM_THINK_COLOR}", width)
            )
            return

        if body and width > 0:
            _ansi_write(_input_rich_render(body, "bold", width))

    def _build_lines(self, chars, cursor):
        lines = [BOTTOM_INPUT_PROMPT]
        widths = [_input_text_width(BOTTOM_INPUT_PROMPT)]
        cursor_row = 0
        cursor_col = min(widths[0] + 1, self.columns)

        for index, character in enumerate(chars):
            if index == cursor:
                cursor_row = len(lines) - 1
                cursor_col = min(widths[-1] + 1, self.columns)

            if character == "\n":
                lines.append("")
                widths.append(0)
                continue

            width = _input_char_width(character)
            if widths[-1] + width > self.columns:
                lines.append("")
                widths.append(0)

            lines[-1] += character
            widths[-1] += width

        if cursor == len(chars):
            cursor_row = len(lines) - 1
            cursor_col = min(widths[-1] + 1, self.columns)

        return lines, cursor_row, max(1, cursor_col)


def _read_bottom_multiline_input(prompt_text):
    chars = []
    cursor = 0
    paste_active = False
    skip_lf = False
    target_column = None
    renderer = _BottomInputRenderer(prompt_text)
    renderer.render(chars, cursor)

    try:
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
                    renderer.render(chars, cursor)
                elif key == "K" and cursor > 0:
                    target_column = None
                    cursor -= 1
                    renderer.render(chars, cursor)
                elif key == "M" and cursor < len(chars):
                    target_column = None
                    cursor += 1
                    renderer.render(chars, cursor)
                elif key == "G":
                    target_column = None
                    cursor, _ = _input_line_bounds(chars, cursor)
                    renderer.render(chars, cursor)
                elif key == "O":
                    target_column = None
                    _, cursor = _input_line_bounds(chars, cursor)
                    renderer.render(chars, cursor)
                elif key == "S" and cursor < len(chars):
                    target_column = None
                    del chars[cursor]
                    renderer.render(chars, cursor)
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
                    target_column = None
                    chars.insert(cursor, "\n")
                    cursor += 1
                    renderer.render(chars, cursor)
                    paste_active = queued
                    skip_lf = ch == "\r"
                    continue

                value = "".join(chars)
                renderer.submit(value)
                return value

            skip_lf = False

            if ch == "\b":
                if cursor > 0:
                    target_column = None
                    cursor -= 1
                    del chars[cursor]
                    renderer.render(chars, cursor)
                paste_active = queued
                continue

            if not ch.isprintable():
                paste_active = queued
                continue

            target_column = None
            chars.insert(cursor, ch)
            cursor += 1
            renderer.render(chars, cursor)
            paste_active = queued
    except BaseException:
        renderer.close()
        raise


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
    if _tui_session is not None:
        prompt_renderable = (
            None if str(prompt_text or "") == "You: " else _input_prompt(prompt_text)
        )
        return _tui_session.request_input(
            prompt_text,
            prompt_renderable=prompt_renderable,
            prompt_rendered=prompt_renderable is not None,
        ).strip()

    prompt = _input_prompt(prompt_text)
    if multiline and os.name == "nt" and sys.stdin.isatty() and sys.stdout.isatty():
        return _read_bottom_multiline_input(prompt_text).strip()
    return console.input(prompt).strip()


def get_continue_confirmation():
    if _tui_session is not None:
        return _tui_session.request_confirmation(default=False)

    if sys.stdin.isatty() and sys.stdout.isatty():
        return _read_terminal_confirmation(default=False)

    console.print(_confirmation_line_prompt(), end="")
    answer = console.input(" [y/N]: ")
    return answer.strip().lower() in {"y", "yes"}


def get_agent_confirmation(title, detail):
    detail = str(detail or "").rstrip()
    detail_line = f"{detail}\n" if detail else ""
    console.print(
        Text.assemble(
            "\n",
            gradient_text("[-]", *INFO_COLOR),
            gradient_text(f" {title}\n", *TEXT_COLOR),
            gradient_text(detail_line, *TEXT_COLOR),
        ),
        end="",
    )
    return get_continue_confirmation()


def _confirmation_line_prompt():
    return Text.assemble(
        gradient_text("Continue? ", *TEXT_COLOR),
        gradient_text("(use arrows, Enter): ", *THINK_COLOR),
    )


def _confirmation_line_renderable(selected, confirmed=False):
    selected = bool(selected)
    confirmed = bool(confirmed)
    yes_colors = SUCCESS_COLOR if selected and confirmed else TEXT_COLOR
    no_colors = SUCCESS_COLOR if not selected and confirmed else TEXT_COLOR
    return Text.assemble(
        _confirmation_line_prompt(),
        " ",
        gradient_text("[Yes]" if selected else "Yes", *yes_colors),
        "  ",
        gradient_text("No" if selected else "[No]", *no_colors),
    )


def _read_terminal_confirmation(default=False):
    _enable_ansi_input_rendering()
    selected = bool(default)
    line = _confirmation_line_ansi(selected)
    sys.stdout.write(line)
    sys.stdout.flush()

    while True:
        key = _read_terminal_confirmation_key()
        if key in {"left", "up", "s-tab", "yes"}:
            selected = True
        elif key in {"right", "down", "tab", "no"}:
            selected = False
        elif key == "enter":
            break
        elif key == "interrupt":
            raise KeyboardInterrupt()
        else:
            continue

        next_line = _confirmation_line_ansi(selected)
        if next_line != line:
            sys.stdout.write("\r\x1b[2K" + next_line)
            sys.stdout.flush()
            line = next_line
        if key in {"yes", "no"}:
            break

    confirmed_line = _confirmation_line_ansi(selected, confirmed=True)
    if confirmed_line != line:
        sys.stdout.write("\r\x1b[2K" + confirmed_line)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return selected


def _confirmation_line_ansi(selected, confirmed=False):
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    return _render_console_print_to_ansi(
        width,
        _confirmation_line_renderable(selected, confirmed=confirmed),
        end="",
        soft_wrap=True,
    )


def _read_terminal_confirmation_key():
    if os.name == "nt":
        ch = msvcrt.getwch()
        if ch in {"\x03"}:
            return "interrupt"
        if ch in {"\r", "\n"}:
            return "enter"
        if ch in {"\t"}:
            return "tab"
        value = ch.lower()
        if value == "y":
            return "yes"
        if value == "n":
            return "no"
        if ch in WINDOWS_SPECIAL_KEY_PREFIXES:
            code = msvcrt.getwch()
            return {
                "K": "left",
                "M": "right",
                "H": "up",
                "P": "down",
            }.get(code, "")
        return ""

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x03":
            return "interrupt"
        if ch in {"\r", "\n"}:
            return "enter"
        if ch == "\t":
            return "tab"
        value = ch.lower()
        if value == "y":
            return "yes"
        if value == "n":
            return "no"
        if ch == "\x1b" and select.select([sys.stdin], [], [], 0.05)[0]:
            seq = sys.stdin.read(2)
            return {
                "[D": "left",
                "[C": "right",
                "[A": "up",
                "[B": "down",
                "[Z": "s-tab",
            }.get(seq, "")
        return ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def get_agent_plan_confirmation(todos, next_tool=""):
    snapshot = _tui_session.snapshot_messages() if _tui_session is not None else None
    size = shutil.get_terminal_size(fallback=(80, 24))
    width = max(40, size.columns)
    normalized_todos = []
    for todo in todos or []:
        if not isinstance(todo, dict):
            continue
        content = str(todo.get("content") or "").strip()
        if not content:
            continue
        normalized_todos.append(
            {
                "content": content,
                "status": str(todo.get("status") or "pending").strip().lower(),
                "priority": str(todo.get("priority") or "").strip().lower(),
                "depends_on": _todo_string_list(todo.get("depends_on")),
                "reason": str(todo.get("reason") or "").strip(),
            }
        )

    try:
        console.print(
            Text.assemble(
                "\n",
                gradient_text("[-]", *INFO_COLOR),
                gradient_text(" Approve current agent plan?\n", *TEXT_COLOR),
            ),
            end="",
        )
        if next_tool:
            console.print(
                Text.assemble(
                    gradient_text(f"Next tool: {next_tool}\n", *THINK_COLOR),
                ),
                end="",
            )
        if normalized_todos:
            console.print(
                _todo_panel_renderable(
                    normalized_todos,
                    width,
                    max(1, len(normalized_todos)),
                )
            )
        approved = get_continue_confirmation()
    finally:
        if _tui_session is not None and snapshot is not None:
            _tui_session.restore_messages(snapshot)
    return approved


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
    return get_continue_confirmation()


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
    return get_continue_confirmation()


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
    return get_continue_confirmation()

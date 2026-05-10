import os
import json

from rich.text import Text
from rich.color import Color
from rich.panel import Panel
from rich.console import Console
from rich.table import Table

console = Console()

VERSION = "2.0.0"

SUCCESS_COLOR = ["#67b6a6", "#92ded2"]
INFO_COLOR = ["#6d8da8", "#97cbe3"]
INFO_TEXT_COLOR = ["#97cbe3", "#c0ece8"]
WARN_COLOR = ["#e69c69", "#ffd680"]
ERROR_COLOR = ["#bb8b8a", "#ffd6d7"]
TEXT_COLOR = ["#cfd7e3", "#f6f6f6", "#e1ebf0"]
THINK_COLOR = ["#7e7d80", "#b4b1b2"]
STREAM_THINK_COLOR = "#7e7d80"
STREAM_RESPONSE_COLOR = "#f6f6f6"


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


def print_stream_thinking(content):
    console.print(
        Text.assemble(
            "\n",
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

    # Format: 2026-04-25-14-30.json -> " 2026.04.25 14:30 <json> <version> <MODEL>"
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
        (" <json>", THINK_COLOR),
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


def get_user_input(prompt_text):
    return console.input(
        Text.assemble(
            "\n",
            gradient_text("[-]", *INFO_COLOR),
            gradient_text(f" {prompt_text}", *TEXT_COLOR),
        )
    ).strip()


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

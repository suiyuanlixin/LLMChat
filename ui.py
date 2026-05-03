import os
import json

from rich.text import Text
from rich.color import Color
from rich.panel import Panel
from rich.console import Console

console = Console()

VERSION = "0.2.1"

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


def _blend_channels(start, end, progress):
    return ",".join(
        str(round(start_value + (end_value - start_value) * progress))
        for start_value, end_value in zip(start, end)
    )


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
            [f for f in os.listdir(record_dir) if f.endswith(".json")], reverse=True
        )

    if not files:
        if line_number == 1:
            return _dashboard_text_from_segments((" No history record", TEXT_COLOR))
        return Text("")

    if line_number > len(files):
        return Text("")

    if len(files) > 5 and line_number == 5:
        return _dashboard_text_from_segments(
            ("[-]", INFO_COLOR),
            (" ...", TEXT_COLOR),
        )

    # Format: 2026-04-25-14-30.json -> " 2026.04.25 14:30 <json> <version> <MODEL>"
    name = files[line_number - 1][:-5]
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
            os.path.join(record_dir, files[line_number - 1]), "r", encoding="utf-8"
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


def show_dashboard(model_name):
    console.clear()
    console.print()
    title = Text.assemble(
        gradient_text("LLM Chat", *INFO_TEXT_COLOR),
        gradient_text(f" v{VERSION}", *TEXT_COLOR),
    )
    billing_text = f"{model_name.upper() + ' · API Usage Billing':^37}"
    cwd = os.getcwd()
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

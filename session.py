import os
import json

from rich.text import Text
from datetime import datetime

from ui import (
    console,
    gradient_text,
    print_success,
    print_error,
    print_info,
    get_user_input,
    clean_display_text,
    TEXT_COLOR,
    THINK_COLOR,
)


def save_conversation(conversation_history, model_name):
    if not conversation_history:
        print_error("No conversation history to save.")
        return False

    record_dir = "record"
    if not os.path.exists(record_dir):
        os.makedirs(record_dir)

    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M")
    filename = f"{record_dir}/{timestamp}.json"
    if os.path.exists(filename):
        print_error("A conversation was already saved this minute. Please try again after one minute.")
        return False

    save_data = {
        "version": "2.5.0",
        "model": model_name,
        "created_at": datetime.now().isoformat(),
        "conversation": conversation_history,
    }

    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(save_data, file, indent=2, ensure_ascii=False)
        print_success(f"Conversation saved to {filename}")
        return True
    except Exception as error:
        print_error(f"Failed to save conversation: {error}")
        return False


def _print_loaded_conversation(conversation, model_name):
    assistant_name = (model_name or "assistant").upper()
    for message in conversation:
        role = message.get("role", "")
        content = clean_display_text(message.get("content", ""))
        if role == "assistant":
            print_success(f"{assistant_name}: {content}")
        elif role == "user":
            print_info(f"YOU: {content}")
        else:
            print_info(f"{role.upper() or 'MESSAGE'}: {content}")


def load_conversation():
    record_dir = "record"
    if not os.path.exists(record_dir):
        print_error("No record directory found.")
        return None

    files = [f for f in os.listdir(record_dir) if f.endswith(".json")]
    if not files:
        print_error("No saved conversations found.")
        return None

    files.sort()

    file_info = []
    for f in files:
        try:
            with open(os.path.join(record_dir, f), "r", encoding="utf-8") as file:
                data = json.load(file)
            name = f[:-5]
            parts = name.split("-")
            if len(parts) >= 5:
                formatted = f"{parts[0]}.{parts[1]}.{parts[2]} {parts[3]}:{parts[4]}"
            else:
                formatted = name
            version = data.get("version", "")
            model = data.get("model", "").upper()
            conversation = data.get("conversation", [])
            msg_count = f"{len(conversation)} Messages"
            display = f"{formatted} <{version}> <{model}> <{msg_count}>"
            file_info.append((f, display))
        except Exception:
            file_info.append((f, f))

    print_info("Available conversations:")
    for i, (fname, display) in enumerate(file_info, 1):
        parts = display.split(" <", 1)
        if len(parts) == 2:
            date_part = parts[0]
            meta_part = f" <{parts[1]}"
            console.print(
                Text.assemble(
                    gradient_text(f"[{i}] {date_part}", *TEXT_COLOR),
                    gradient_text(meta_part, *THINK_COLOR),
                )
            )
        else:
            console.print(
                Text.assemble(
                    gradient_text(f"[{i}] ", *TEXT_COLOR),
                    gradient_text(display, *TEXT_COLOR),
                )
            )

    choice = get_user_input("Select number to load (Enter to cancel): ")
    if not choice:
        return None

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(files):
            print_error("Invalid selection.")
            return None
        filename = f"{record_dir}/{files[idx]}"
        with open(filename, "r", encoding="utf-8") as file:
            data = json.load(file)
        conversation = data.get("conversation", [])
        model = data.get("model", "")
        name = files[idx][:-5]
        parts = name.split("-")
        if len(parts) >= 5:
            date_str = f"{parts[0]}.{parts[1]}.{parts[2]} {parts[3]}:{parts[4]}"
        else:
            date_str = name
        _print_loaded_conversation(conversation, model)
        print_success(
            f"Loaded {len(conversation)} messages from [{idx + 1}] {date_str}."
        )
        return conversation
    except Exception as error:
        print_error(f"Failed to load conversation: {error}")
        return None

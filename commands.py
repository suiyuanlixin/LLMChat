from ui import print_error, print_success, print_warn, print_info
from config import parse_max_tokens, parse_temperature, save_config_field, update_config
from session import save_conversation, load_conversation

COMMANDS = {
    "/help": "Display a list of available commands and their descriptions.",
    "/quit": "Exit the chat.",
    "/clear": "Clear the conversation history.",
    "/save": "Save the current conversation history to a JSON file.",
    "/load": "Load a previous conversation from JSON file.",
    "/conf": "Update the API type, base URL, API Key and model configuration.",
    "/token": "Set the maximum tokens for responses (Example: /token 4096).",
    "/temp": "Set the temperature for responses (Example: /temp 0.7).",
    "/mode": "Switch between normal and stream output modes (Example: /mode stream).",
    "/think": "Toggle thinking mode on/off (Example: /think on).",
}


def show_help():
    command_list = "\n".join(f"{cmd:<8} {desc}" for cmd, desc in COMMANDS.items())
    additional = "Ctrl+C   Force exit the chat."
    print_warn(f"All commands: \n{command_list}\n{additional}")


def process_command(user_input, chat):
    parts = user_input.split(maxsplit=1)
    base = parts[0].lower()
    args = parts[1] if len(parts) > 1 else None

    handler = COMMAND_HANDLERS.get(base)
    if handler:
        return handler(chat, args)
    print_error(f"Unknown command: {base}. Use /help to see available commands.")
    return True


def handle_help(chat, args):
    show_help()
    return True


def handle_quit(chat, args):
    print_success("Goodbye!")
    return False


def handle_clear(chat, args):
    chat.clear_history()
    print_success("Conversation history cleared.")
    return True


def handle_conf(chat, args):
    result = update_config()
    if result:
        try:
            chat.configure(
                result.api_type,
                result.base_url,
                result.model,
                result.api_key,
                result.max_tokens,
                result.temperature,
                result.stream_mode,
                result.thinking_mode,
            )
        except Exception as error:
            print_error(f"Failed to apply configuration: {error}")
            return True
        chat.clear_history()
    return True


def handle_token(chat, args):
    if args is None:
        print_info(f"Current max tokens: {chat.max_tokens}")
        return True
    try:
        new_max_tokens = parse_max_tokens(args)
        chat.set_max_tokens(new_max_tokens)
        save_config_field("max_tokens", new_max_tokens)
        print_success(f"Max tokens set to {new_max_tokens}.")
    except ValueError as error:
        print_error(str(error))
    return True


def handle_temp(chat, args):
    if args is None:
        print_info(f"Current temperature: {chat.temperature}")
        return True
    try:
        new_temp = parse_temperature(args)
        chat.set_temperature(new_temp)
        save_config_field("temperature", new_temp)
        print_success(f"Temperature set to {new_temp}.")
    except ValueError as error:
        print_error(str(error))
    return True


def handle_save(chat, args):
    save_conversation(chat.get_history(), chat.model)
    return True


def handle_load(chat, args):
    conversation = load_conversation()
    if conversation is not None:
        chat.conversation_history = conversation
    return True


def handle_mode(chat, args):
    if args is None:
        current = "stream" if chat.stream_mode else "normal"
        print_info(f"Current mode: {current}. Usage: /mode normal | /mode stream")
        return True

    mode = args.lower().strip()
    if mode == "stream":
        chat.set_stream_mode(True)
        save_config_field("stream_mode", True)
        print_success("Switched to stream mode.")
    elif mode == "normal":
        chat.set_stream_mode(False)
        save_config_field("stream_mode", False)
        print_success("Switched to normal mode.")
    else:
        print_error(f"Invalid mode: {mode}. Use /mode normal or /mode stream.")
    return True


def handle_think(chat, args):
    if args is None:
        current = "on" if chat.thinking_mode else "off"
        print_info(f"Current thinking mode: {current}. Usage: /think on | /think off")
        return True

    think = args.lower().strip()
    if think == "on":
        chat.set_thinking_mode(True)
        save_config_field("thinking_mode", True)
        print_success("Thinking mode turned on.")
    elif think == "off":
        chat.set_thinking_mode(False)
        save_config_field("thinking_mode", False)
        print_success("Thinking mode turned off.")
    else:
        print_error(f"Invalid option: {think}. Use /think on or /think off.")
    return True


COMMAND_HANDLERS = {
    "/help": handle_help,
    "/quit": handle_quit,
    "/clear": handle_clear,
    "/conf": handle_conf,
    "/save": handle_save,
    "/load": handle_load,
    "/mode": handle_mode,
    "/think": handle_think,
    "/token": handle_token,
    "/temp": handle_temp,
}

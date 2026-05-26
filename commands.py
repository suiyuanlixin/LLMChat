from ui import print_error, print_success, print_warn, print_info
from config import (
    parse_agent_approval_mode,
    parse_agent_rounds,
    parse_agent_show_thinking,
    parse_agent_tool_calls,
    parse_max_tokens,
    parse_temperature,
    requires_api_key,
    reload_config,
    save_config_field,
    save_config_fields,
    update_config,
)
from session import save_conversation, load_conversation

COMMANDS = {
    "/help": "Display a list of available commands and their descriptions.",
    "/quit": "Exit the chat.",
    "/clear": "Clear the conversation history.",
    "/save": "Save the current conversation history to a JSON file.",
    "/load": "Load a previous conversation from JSON file.",
    "/conf": "Update configuration, or reload config.json (Example: /conf reload).",
    "/token": "Set the maximum tokens for responses (Example: /token 4096).",
    "/temp": "Set the temperature for responses (Example: /temp 0.7).",
    "/mode": "Switch between normal and stream output modes (Example: /mode stream).",
    "/think": "Toggle thinking mode on/off (Example: /think on).",
    "/comp": "Compact the current conversation context immediately.",
    "/memory": "Inspect or search persistent memory (Example: /memory today, /memory search <query>).",
    "/agent": "Toggle, inspect or configure local file-editing agent mode (Example: /agent show-thinking summary).",
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
    if args:
        action = args.strip().lower()
        if action == "reload":
            result = reload_config()
            if _apply_config(chat, result):
                chat.clear_history()
                print_success("Configuration reloaded from config.json.")
            return True

        print_error("Usage: /conf | /conf reload")
        return True

    result = update_config()
    if result:
        if _apply_config(chat, result):
            chat.clear_history()
    return True


def _apply_config(chat, config):
    if not config.api_key and requires_api_key(config.api_type):
        print_error("Configuration API Key is empty. Reload aborted.")
        return False

    try:
        chat.configure(
            config.api_type,
            config.base_url,
            config.model,
            config.api_key,
            config.max_tokens,
            config.temperature,
            config.stream_mode,
            config.thinking_mode,
        )
        chat.set_agent_limits(config.max_agent_rounds, config.max_agent_tool_calls)
        chat.set_agent_approval_mode(config.agent_approval_mode)
        chat.set_agent_show_thinking(config.agent_show_thinking)
        chat.set_agent_summary_model(config.agent_summary_model)
        chat.set_compaction_config(
            config.compaction_enable,
            config.compaction_max_chars,
            config.compaction_keep_recent_messages,
            config.compaction_compact_model,
        )

        if config.agent_mode and not chat.get_agent_status().get("workspace_dir"):
            chat.set_agent_mode(False)
            save_config_field("agent_mode", False)
            print_warn("Agent mode requires a startup workspace directory and has been turned off.")
        else:
            chat.set_agent_mode(config.agent_mode)
    except Exception as error:
        print_error(f"Failed to apply configuration: {error}")
        return False
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
        chat.set_history(conversation)
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


def handle_comp(chat, args):
    if args:
        print_error("Usage: /comp")
        return True

    result = chat.compact_context(manual=True)
    if result.get("compacted"):
        memory_update = result.get("memory_update") or {}
        memory_changed = memory_update.get("changed") or []
        memory_suffix = ""
        if memory_changed:
            memory_suffix = f" Memory updated: {', '.join(memory_changed)}."
        elif memory_update.get("error"):
            memory_suffix = f" Memory update failed: {memory_update.get('error')}."
        print_success(
            "Context compacted: "
            f"{result.get('before_messages')} -> {result.get('after_messages')} messages, "
            f"{result.get('before_chars')} -> {result.get('after_chars')} chars."
            f"{memory_suffix}"
        )
        return True

    reason = result.get("reason") or "Context compaction was cancelled."
    if result.get("error"):
        print_error(reason)
    else:
        print_info(reason)
    return True


def handle_memory(chat, args):
    store = getattr(chat, "memory_store", None)
    if store is None:
        print_error("Persistent memory is not initialized.")
        return True

    action = "show"
    value = ""
    if args:
        parts = args.strip().split(maxsplit=1)
        action = parts[0].lower()
        value = parts[1].strip() if len(parts) > 1 else ""

    if action in {"show", "status"}:
        print_info(
            "Persistent memory files:\n"
            f"{store.paths_summary()}\n\n"
            "Usage: /memory core | /memory prefs | /memory today | "
            "/memory date YYYY-MM-DD | /memory search <query>"
        )
        _print_memory_section("Core memory", store.read_core_body())
        _print_memory_section("Preference memory", store.read_preference_body())
        today = store.episodic_for_date()
        if today:
            _print_memory_section("Today's episodic memory", today)
        return True

    if action == "core":
        _print_memory_section("Core memory", store.read_core_body())
        return True

    if action in {"prefs", "preferences", "preference"}:
        _print_memory_section("Preference memory", store.read_preference_body())
        return True

    if action in {"today", "daily"}:
        _print_memory_section("Today's episodic memory", store.episodic_for_date())
        return True

    if action == "date":
        if not value:
            print_error("Usage: /memory date YYYY-MM-DD")
            return True
        _print_memory_section(f"Episodic memory for {value}", store.episodic_for_date(value))
        return True

    if action == "search":
        if not value:
            print_error("Usage: /memory search <query>")
            return True
        _print_memory_section(f"Episodic memory search: {value}", store.search_episodic(value))
        return True

    if action in {"path", "paths"}:
        print_info(store.paths_summary())
        return True

    print_error(
        "Usage: /memory core | /memory prefs | /memory today | "
        "/memory date YYYY-MM-DD | /memory search <query> | /memory path"
    )
    return True


def _print_memory_section(title, content):
    content = str(content or "").strip()
    if not content:
        content = "(empty)"
    print_info(f"{title}:\n{content}")


def handle_agent(chat, args):
    status = chat.get_agent_status()

    if args is None:
        current = "on" if status["enabled"] else "off"
        running = "running" if status.get("running") else "idle"
        budget = f"{status.get('max_rounds')} rounds / {status.get('max_tool_calls')} tools"
        approval = status.get("approval_mode", "confirm")
        show_thinking = status.get("show_thinking", "summary")
        summary_model = status.get("summary_model") or "local"
        print_info(
            f"Current agent mode: {current} ({running}).\n"
            f"Budget: {budget}.\n"
            f"Approval: {approval}.\n"
            f"Show thinking: {show_thinking}.\n"
            f"Summary model: {summary_model}.\n"
            f"Usage: /agent on | /agent off | /agent stop | /agent budget <rounds> <tool-calls> | "
            f"/agent approve confirm|auto | /agent show-thinking summary|full|off"
        )
        return True

    parts = args.split()
    mode = parts[0].lower().strip() if parts else ""
    if mode == "on" and len(parts) == 1:
        if not status["workspace_dir"]:
            chat.set_agent_mode(False)
            save_config_field("agent_mode", False)
            print_error("Agent mode requires a startup workspace directory. Example: python main.py <workspace>")
            return True
        chat.set_agent_mode(True)
        save_config_field("agent_mode", True)
        print_success("Agent mode turned on.")
    elif mode == "off" and len(parts) == 1:
        chat.set_agent_mode(False)
        save_config_field("agent_mode", False)
        print_success("Agent mode turned off.")
    elif mode == "stop" and len(parts) == 1:
        if chat.request_agent_stop():
            print_warn("Agent stop requested.")
        else:
            print_info("No agent task is currently running.")
    elif mode in {"budget", "limits"}:
        if len(parts) == 1:
            print_info(
                f"Current agent budget: {status.get('max_rounds')} rounds / "
                f"{status.get('max_tool_calls')} tool calls. "
                f"Usage: /agent budget <rounds> <tool-calls>"
            )
            return True
        if len(parts) != 3:
            print_error("Usage: /agent budget <rounds> <tool-calls>")
            return True
        try:
            max_rounds = parse_agent_rounds(parts[1])
            max_tool_calls = parse_agent_tool_calls(parts[2])
        except ValueError as error:
            print_error(str(error))
            return True

        chat.set_agent_limits(max_rounds, max_tool_calls)
        save_config_fields(
            {
                "max_agent_rounds": max_rounds,
                "max_agent_tool_calls": max_tool_calls,
            }
        )
        print_success(f"Agent budget set to {max_rounds} rounds / {max_tool_calls} tool calls.")
    elif mode in {"approve", "approval"}:
        if len(parts) == 1:
            print_info(
                f"Current agent approval mode: {status.get('approval_mode', 'confirm')}. "
                "Usage: /agent approve confirm|auto"
            )
            return True
        if len(parts) != 2:
            print_error("Usage: /agent approve confirm|auto")
            return True
        try:
            approval_mode = parse_agent_approval_mode(parts[1])
        except ValueError as error:
            print_error(str(error))
            return True

        chat.set_agent_approval_mode(approval_mode)
        save_config_field("agent_approval_mode", approval_mode)
        print_success(f"Agent approval mode set to {approval_mode}.")
    elif mode in {"show-thinking", "show_thinking", "thinking"}:
        if len(parts) == 1:
            current = status.get("show_thinking", "summary")
            print_info(
                f"Current agent thinking display: {current}. "
                "Usage: /agent show-thinking summary|full|off"
            )
            return True
        if len(parts) != 2:
            print_error("Usage: /agent show-thinking summary|full|off")
            return True
        try:
            show_thinking = parse_agent_show_thinking(parts[1])
        except ValueError as error:
            print_error(str(error))
            return True

        chat.set_agent_show_thinking(show_thinking)
        save_config_field("agent_show_thinking", show_thinking)
        print_success(f"Agent thinking display set to {show_thinking}.")
    else:
        print_error(
            f"Invalid option: {args}. Use /agent on, /agent off, /agent stop or "
            f"/agent budget <rounds> <tool-calls>, /agent approve confirm|auto, "
            f"/agent show-thinking summary|full|off."
        )
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
    "/comp": handle_comp,
    "/memory": handle_memory,
    "/agent": handle_agent,
    "/token": handle_token,
    "/temp": handle_temp,
}

import json

from ui import print_error, print_success, print_warn, print_info
from config import (
    parse_agent_approval_mode,
    parse_agent_rounds,
    parse_agent_show_thinking,
    parse_agent_tool_calls,
    parse_max_tokens,
    parse_skill_max_chars,
    parse_temperature,
    parse_web_search_depth,
    parse_web_search_max_results,
    parse_web_search_provider,
    parse_web_search_topic,
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
    "/plan": "Inspect, approve, recover, or clear current agent todos (Example: /plan check).",
    "/memory": "Inspect or search persistent memory (Example: /memory today, /memory search <query>).",
    "/search": "Toggle, inspect or configure web search (Example: /search on).",
    "/skills": "Toggle, inspect or configure agent skills (Example: /skills workspace on).",
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
            config.reasoning_effort,
        )
        chat.set_context_window_tokens(config.context_window_tokens)
        chat.set_agent_limits(config.max_agent_rounds, config.max_agent_tool_calls)
        chat.set_agent_approval_mode(config.agent_approval_mode)
        chat.set_agent_show_thinking(config.agent_show_thinking)
        chat.set_agent_summary_model(config.agent_summary_model)
        chat.set_skills_config(
            config.skills_enable,
            config.skills_source_app,
            config.skills_source_workspace,
            config.skills_auto_catalog,
            config.skills_max_chars,
        )
        chat.set_compaction_config(
            config.compaction_enable,
            config.compaction_keep_recent_messages,
            config.compaction_compact_model,
            config.compaction_trigger_ratio,
        )
        chat.set_memory_model(config.memory_model)
        chat.set_web_search_config(
            config.web_search_enable,
            config.web_search_provider,
            config.web_search_api_key,
            config.web_search_max_results,
            config.web_search_depth,
            config.web_search_topic,
        )

        if config.agent_mode and not chat.get_agent_status().get("workspace_dir"):
            chat.set_agent_mode(False)
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
            f"{result.get('before_chars')} -> {result.get('after_chars')} chars, "
            f"{result.get('before_input_tokens')} -> {result.get('after_input_tokens')} input tokens "
            f"(threshold {result.get('token_threshold')})."
            f"{memory_suffix}"
        )
        return True

    reason = result.get("reason") or "Context compaction was cancelled."
    if result.get("error"):
        print_error(reason)
    else:
        print_info(reason)
    return True


def handle_plan(chat, args):
    raw_args = str(args or "").strip()
    parts = raw_args.split(maxsplit=2)
    action = parts[0].lower() if parts else ""
    if action in {"clear", "reset"}:
        chat.clear_todos()
        print_success("Todos cleared.")
        return True

    if action == "approve":
        tail = raw_args.split(maxsplit=1)
        note = tail[1] if len(tail) > 1 else ""
        if chat.approve_todos(note):
            print_success("Plan approved.")
        else:
            print_info("No plan approval change needed.")
        return True

    if action in {"reject", "deny"}:
        tail = raw_args.split(maxsplit=1)
        reason = tail[1] if len(tail) > 1 else ""
        if chat.reject_todos(reason):
            print_warn("Plan rejected.")
        else:
            print_info("No current plan to reject.")
        return True

    if action == "check":
        print_info(chat.get_todo_quality_report())
        return True

    if action in {"history", "log", "events"}:
        limit = 20
        if len(parts) >= 2:
            try:
                limit = max(1, int(parts[1]))
            except ValueError:
                print_error("Usage: /plan history [limit]")
                return True
        events = chat.get_todo_history(limit)
        if not events:
            print_info("No plan events.")
            return True
        print_info("Plan events:\n" + _format_plan_events(events))
        return True

    if action in {"retry", "unblock"}:
        if len(parts) < 2:
            print_error(f"Usage: /plan {action} <todo-id> [reason]")
            return True
        todo_id = parts[1]
        reason = parts[2] if len(parts) >= 3 else ""
        try:
            if action == "retry":
                chat.retry_todo(todo_id, reason)
                print_success(f"Todo retried: {todo_id}")
            else:
                chat.unblock_todo(todo_id, reason)
                print_success(f"Todo unblocked: {todo_id}")
        except ValueError as error:
            print_error(str(error))
        return True

    if action:
        print_error(
            "Usage: /plan | /plan check | /plan history [limit] | "
            "/plan approve [note] | /plan reject [reason] | "
            "/plan retry <todo-id> [reason] | /plan unblock <todo-id> [reason] | "
            "/plan clear"
        )
        return True

    status = chat.get_todo_status()
    items = status.get("items") or []
    if not items:
        print_info("No todos.")
        return True

    summary = _format_todos_for_display(items)
    meta = _format_plan_meta(status)
    if meta:
        summary = summary + "\n\n" + meta
    if status.get("all_completed"):
        print_info("No active todos. Last completed plan:\n" + summary)
    else:
        print_info("Current todos:\n" + summary)
    return True


def _format_todos_for_display(items):
    lines = []
    for item in items:
        status = item.get("status") or "pending"
        marker = "[ ]"
        if status == "in_progress":
            marker = "[-]"
        elif status == "completed":
            marker = "[" + chr(0x2713) + "]"
        elif status == "blocked":
            marker = "[!]"
        elif status == "failed":
            marker = "[x]"

        details = []
        priority = str(item.get("priority") or "").upper()
        if priority:
            details.append(priority)
        if item.get("id"):
            details.append(f"id: {item.get('id')}")
        if item.get("depends_on"):
            details.append("after: " + ", ".join(item.get("depends_on") or []))
        if item.get("completion_criteria"):
            details.append(
                "done when: "
                + _format_completion_criteria(item.get("completion_criteria") or [])
            )
        if item.get("verified"):
            details.append("verified")
        if item.get("reason"):
            details.append("reason: " + str(item.get("reason")))
        suffix = f" ({'; '.join(details)})" if details else ""
        lines.append(f"{marker} {item.get('content') or ''}{suffix}")
    return "\n".join(lines)


def _format_completion_criteria(criteria):
    parts = []
    for criterion in criteria:
        if isinstance(criterion, str):
            parts.append(criterion)
            continue
        if not isinstance(criterion, dict):
            parts.append(str(criterion))
            continue
        criterion_type = criterion.get("type") or "manual"
        target = criterion.get("target") or ""
        expected = criterion.get("expected") or ""
        if target and expected:
            parts.append(f"{criterion_type}:{target} => {expected}")
        else:
            parts.append(expected or f"{criterion_type}:{target}")
    return "; ".join(parts)


def _format_plan_meta(status):
    lines = []
    approval_state = status.get("approval_state")
    if approval_state:
        approval = approval_state.replace("_", " ")
        note = status.get("approval_note") or ""
        lines.append(f"Approval: {approval}{(': ' + note) if note else ''}")

    budget = status.get("budget") or {}
    if budget:
        lines.append(
            "Budget: "
            f"{budget.get('remaining_tool_calls')}/{budget.get('max_tool_calls')} "
            "tool calls remaining"
        )
        next_items = budget.get("next") or []
        if next_items:
            lines.append(
                "Next: "
                + ", ".join(
                    f"{item.get('priority', '').upper()} {item.get('id')}"
                    for item in next_items
                )
            )

    warnings = status.get("quality_warnings") or []
    if warnings:
        lines.append("Quality warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    if status.get("plan_path"):
        lines.append(f"Plan file: {status.get('plan_path')}")
    if status.get("events_path"):
        lines.append(f"Event log: {status.get('events_path')}")
    return "\n".join(lines)


def _format_plan_events(events):
    lines = []
    for event in events:
        timestamp = event.get("ts") or ""
        event_type = event.get("type") or "event"
        payload = event.get("payload") or {}
        lines.append(f"- {timestamp} {event_type}: {_short_json(payload)}")
    return "\n".join(lines)


def _short_json(value, limit=220):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def handle_search(chat, args):
    if args is None or not args.strip():
        _print_search_status(chat)
        return True

    parts = args.strip().split(maxsplit=1)
    action = parts[0].lower()
    value = parts[1].strip() if len(parts) > 1 else ""

    if action in {"status", "config"}:
        _print_search_status(chat)
        return True

    if action == "on" and not value:
        chat.set_web_search_config(enabled=True)
        save_config_field("web_search_enable", True)
        print_success("Web search enabled.")
        return True

    if action == "off" and not value:
        chat.set_web_search_config(enabled=False)
        save_config_field("web_search_enable", False)
        print_success("Web search disabled.")
        return True

    if action == "key":
        if not value:
            print_error("Usage: /search key <tavily-api-key>")
            return True
        if not value.startswith("tvly-"):
            print_error("Tavily API keys usually start with tvly-.")
            return True
        chat.set_web_search_config(api_key=value)
        save_config_field("web_search_api_key", value)
        print_success("Tavily API key saved to config.json.")
        return True

    if action == "provider":
        if not value:
            print_error("Usage: /search provider tavily")
            return True
        try:
            provider = parse_web_search_provider(value)
        except ValueError as error:
            print_error(str(error))
            return True
        chat.set_web_search_config(provider=provider)
        save_config_field("web_search_provider", provider)
        print_success(f"Web search provider set to {provider}.")
        return True

    if action in {"max", "results", "max-results"}:
        if not value:
            print_error("Usage: /search max <1-20>")
            return True
        try:
            max_results = parse_web_search_max_results(value)
        except ValueError as error:
            print_error(str(error))
            return True
        chat.set_web_search_config(max_results=max_results)
        save_config_field("web_search_max_results", max_results)
        print_success(f"Web search max results set to {max_results}.")
        return True

    if action in {"depth", "search-depth"}:
        if not value:
            print_error("Usage: /search depth basic|fast|ultra-fast|advanced")
            return True
        try:
            depth = parse_web_search_depth(value)
        except ValueError as error:
            print_error(str(error))
            return True
        chat.set_web_search_config(search_depth=depth)
        save_config_field("web_search_depth", depth)
        print_success(f"Web search depth set to {depth}.")
        return True

    if action == "topic":
        if not value:
            print_error("Usage: /search topic general|news|finance")
            return True
        try:
            topic = parse_web_search_topic(value)
        except ValueError as error:
            print_error(str(error))
            return True
        chat.set_web_search_config(topic=topic)
        save_config_field("web_search_topic", topic)
        print_success(f"Web search topic set to {topic}.")
        return True

    print_error(
        "Usage: /search | /search status | /search on|off | "
        "/search key <tavily-api-key> | /search provider tavily | "
        "/search max <1-20> | /search depth basic|fast|ultra-fast|advanced | "
        "/search topic general|news|finance"
    )
    return True


def _print_search_status(chat):
    status = chat.get_web_search_status()
    current = "on" if status.get("enabled") else "off"
    if not status.get("enabled"):
        available = "disabled"
    elif status.get("available"):
        available = "available"
    else:
        available = "missing key"
    print_info(
        f"Web search: {current} ({available}).\n"
        "Scope: normal chat auto-search and agent web_search tool.\n"
        f"Provider: {status.get('provider')}.\n"
        f"Max results: {status.get('max_results')}.\n"
        f"Depth: {status.get('search_depth')}.\n"
        f"Topic: {status.get('topic')}.\n"
        "Usage: /search status | /search on|off | /search key <tavily-api-key> | "
        "/search provider tavily | /search max <1-20> | "
        "/search depth basic|fast|ultra-fast|advanced | "
        "/search topic general|news|finance"
    )


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
        memory_model_status = (
            chat.get_memory_model_status()
            if hasattr(chat, "get_memory_model_status")
            else {}
        )
        configured_model = memory_model_status.get("configured_model") or "None"
        effective_model = memory_model_status.get("effective_model") or getattr(chat, "model", "")
        print_info(
            "Persistent memory files:\n"
            f"{store.paths_summary()}\n\n"
            f"Memory model: {effective_model} (configured: {configured_model}).\n\n"
            "Usage: /memory core | /memory prefs | /memory today | "
            "/memory date YYYY-MM-DD | /memory search <query> | "
            "/memory history"
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
        if value:
            return _handle_preference_memory_command(store, value)
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

    if action == "history":
        stats = store.history_stats()
        rows = store.history_tail(limit=10)
        lines = [
            f"path: {stats.get('path')}",
            f"rows: {stats.get('rows')}",
            f"bytes: {stats.get('bytes')}",
        ]
        for row in rows:
            role = row.get("role", "")
            ts = row.get("ts", "")
            content = str(row.get("content", "")).replace("\n", " ")
            if len(content) > 160:
                content = content[:157].rstrip() + "..."
            lines.append(f"- {ts} {role}: {content}")
        _print_memory_section("Hot history", "\n".join(lines))
        return True

    if action in {"path", "paths"}:
        print_info(store.paths_summary())
        return True

    print_error(
        "Usage: /memory core | /memory prefs | /memory today | "
        "/memory date YYYY-MM-DD | /memory search <query> | "
        "/memory history | /memory path"
    )
    return True


def _handle_preference_memory_command(store, value):
    parts = value.strip().split(maxsplit=2)
    action = parts[0].lower() if parts else ""

    if action in {"tidy", "clean", "dedupe"} and len(parts) == 1:
        result = store.tidy_preference_memory()
        if result.get("changed"):
            print_success(
                "Preference memory tidied"
                f" ({result.get('removed_duplicates', 0)} duplicate candidates found)."
            )
        else:
            print_info("Preference memory already looks tidy.")
        return True

    if action in {"remove", "delete"}:
        if len(parts) < 2:
            print_error("Usage: /memory prefs remove <text>")
            return True
        query = value.split(maxsplit=1)[1]
        result = store.remove_preference(query)
        removed = result.get("removed") or []
        if removed:
            print_success("Removed preference lines:\n" + "\n".join(removed))
        else:
            print_info("No matching preference lines found.")
        return True

    if action in {"level", "move", "set-level"}:
        if len(parts) != 3:
            print_error("Usage: /memory prefs level Critical|High|Medium|Low <text>")
            return True
        level = parts[1]
        query = parts[2]
        result = store.set_preference_level(query, level)
        moved = result.get("moved") or []
        if moved:
            print_success(f"Moved preference to {result.get('level')}:\n" + "\n".join(moved))
        else:
            print_info("No matching preference lines found, or the level was invalid.")
        return True

    print_error(
        "Usage: /memory prefs | /memory prefs tidy | "
        "/memory prefs remove <text> | /memory prefs level Critical|High|Medium|Low <text>"
    )
    return True


def _print_memory_section(title, content):
    content = str(content or "").strip()
    if not content:
        content = "(empty)"
    print_info(f"{title}:\n{content}")


def _format_skills_status(skills):
    state = "on" if skills.get("enabled") else "off"
    sources = skills.get("sources") or {}
    counts = skills.get("counts") or {}
    directories = skills.get("directories") or {}
    auto_catalog = "on" if skills.get("auto_catalog") else "off"
    return (
        f"Current skills: {state} ({skills.get('count', 0)} loaded).\n"
        f"Sources: app={'on' if sources.get('app') else 'off'} "
        f"({counts.get('app', 0)} loaded), "
        f"workspace={'on' if sources.get('workspace') else 'off'} "
        f"({counts.get('workspace', 0)} loaded).\n"
        f"App directory: {directories.get('app') or 'skills'}.\n"
        f"Workspace directory: {directories.get('workspace') or 'no workspace'}.\n"
        f"Auto catalog: {auto_catalog}. Max chars: {skills.get('max_skill_chars', 0)}.\n"
        "Usage: /skills on|off|reload|app on|off|workspace on|off|catalog on|off|max-chars <num>"
    )


def handle_skills(chat, args):
    status = chat.get_agent_status().get("skills") or {}
    parts = args.split() if args else []
    if not parts:
        print_info(_format_skills_status(status))
        return True

    action = parts[0].lower().strip()
    if action in {"on", "off"} and len(parts) == 1:
        enabled = action == "on"
        chat.set_skills_config(enabled=enabled)
        save_config_field("skills_enable", enabled)
        print_success(f"Skills turned {'on' if enabled else 'off'}.")
        return True

    if action == "reload" and len(parts) == 1:
        chat.agent_tools.skill_registry.reload()
        status = chat.get_agent_status().get("skills") or {}
        print_success(f"Skills reloaded ({status.get('count', 0)} loaded).")
        return True

    if action in {"app", "workspace"}:
        if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
            print_error(f"Usage: /skills {action} on|off")
            return True
        enabled = parts[1].lower() == "on"
        if action == "app":
            chat.set_skills_config(app_enabled=enabled)
            save_config_field("skills_source_app", enabled)
            print_success(f"App skills source turned {'on' if enabled else 'off'}.")
            return True

        chat.set_skills_config(workspace_enabled=enabled)
        save_config_field("skills_source_workspace", enabled)
        status = chat.get_agent_status().get("skills") or {}
        message = f"Workspace skills source turned {'on' if enabled else 'off'}."
        if enabled and not (status.get("directories") or {}).get("workspace"):
            message += " No workspace directory is active."
        elif enabled and (status.get("counts") or {}).get("workspace", 0) == 0:
            message += " No workspace skills loaded; add .llmchat/skills/<name>/SKILL.md if needed."
        print_success(message)
        return True

    if action in {"catalog", "auto-catalog", "auto_catalog"}:
        if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
            print_error("Usage: /skills catalog on|off")
            return True
        enabled = parts[1].lower() == "on"
        chat.set_skills_config(auto_catalog=enabled)
        save_config_field("skills_auto_catalog", enabled)
        print_success(f"Skills auto catalog turned {'on' if enabled else 'off'}.")
        return True

    if action in {"max-chars", "max_chars", "max-skill-chars", "max_skill_chars"}:
        if len(parts) != 2:
            print_error("Usage: /skills max-chars <num>")
            return True
        try:
            max_chars = parse_skill_max_chars(parts[1])
        except ValueError as error:
            print_error(str(error))
            return True
        chat.set_skills_config(max_chars=max_chars)
        save_config_field("skills_max_chars", max_chars)
        print_success(f"Skills max chars set to {max_chars}.")
        return True

    print_error(
        "Usage: /skills on|off|reload|app on|off|workspace on|off|catalog on|off|max-chars <num>"
    )
    return True


def handle_agent(chat, args):
    status = chat.get_agent_status()

    if args is None:
        current = "on" if status["enabled"] else "off"
        running = "running" if status.get("running") else "idle"
        budget = f"{status.get('max_rounds')} rounds / {status.get('max_tool_calls')} tools"
        approval = status.get("approval_mode", "confirm")
        show_thinking = status.get("show_thinking", "summary")
        summary_model = status.get("summary_model") or "local"
        skills = status.get("skills") or {}
        skills_state = "on" if skills.get("enabled") else "off"
        skill_sources = skills.get("sources") or {}
        print_info(
            f"Current agent mode: {current} ({running}).\n"
            f"Budget: {budget}.\n"
            f"Approval: {approval}.\n"
            f"Show thinking: {show_thinking}.\n"
            f"Summary model: {summary_model}.\n"
            f"Skills: {skills_state} ({skills.get('count', 0)} loaded; "
            f"app={'on' if skill_sources.get('app') else 'off'}, "
            f"workspace={'on' if skill_sources.get('workspace') else 'off'}).\n"
            f"Usage: /agent on | /agent off | /agent stop | /agent budget <rounds> <tool-calls> | "
            f"/agent approve confirm|auto | /agent show-thinking summary|full|off | /skills"
        )
        return True

    parts = args.split()
    mode = parts[0].lower().strip() if parts else ""
    if mode == "on" and len(parts) == 1:
        if not status["workspace_dir"]:
            chat.set_agent_mode(False)
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
    elif mode == "skills":
        return handle_skills(chat, " ".join(parts[1:]) if len(parts) > 1 else None)
    else:
        print_error(
            f"Invalid option: {args}. Use /agent on, /agent off, /agent stop or "
            f"/agent budget <rounds> <tool-calls>, /agent approve confirm|auto, "
            f"/agent show-thinking summary|full|off, or /skills."
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
    "/plan": handle_plan,
    "/memory": handle_memory,
    "/search": handle_search,
    "/skills": handle_skills,
    "/agent": handle_agent,
    "/token": handle_token,
    "/temp": handle_temp,
}

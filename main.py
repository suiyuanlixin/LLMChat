import sys
import re
from pathlib import Path

from ui import (
    console,
    print_success,
    print_error,
    print_warn,
    print_thinking,
    get_user_input,
    run_tui,
    show_dashboard,
    start_tui,
    print_stream_thinking_continue,
    clean_and_print_stream_response,
    clean_display_text,
)
from config import load_config
from chat import OmniAgent
from commands import process_command
from tools import MAX_READ_CHARS, normalize_workspace_dir


FILE_REFERENCE_PATTERN = re.compile(r"\[([^\[\]\r\n]+)\]")


def _clean_text(text):
    return clean_display_text(text)


def stream_print_thinking(content):
    if content == "\n":
        print_stream_thinking_continue("\n")
    elif content:
        print_stream_thinking_continue(content)


def stream_print_response(content):
    if content == "\n":
        clean_and_print_stream_response("\n")
    elif content:
        clean_and_print_stream_response(content)


def _should_print_unstreamed_thinking(response, stream_mode, thinking_mode):
    return (
        response.get("thinking")
        and thinking_mode
        and (stream_mode or response.get("response_streamed"))
        and response.get("thinking_streamed") is False
    )


def handle_response(response, model_name, stream_mode=False, thinking_mode=False):
    if not response:
        print_error(
            "Failed to get response, please check your APIKey and network connection."
        )
        return

    if _should_print_unstreamed_thinking(response, stream_mode, thinking_mode):
        print_thinking(_clean_text(response.get("thinking")))

    if stream_mode:
        console.print()
        return

    if response.get("response_streamed"):
        console.print()
        return

    if response.get("agent_stopped"):
        return

    thinking = response.get("thinking")
    if thinking and thinking_mode:
        print_thinking(_clean_text(thinking))

    if response.get("thinking_needs_separator") and not response.get("agent_stopped"):
        console.print()

    reply = _clean_text(response["response"])
    print_success(f"{model_name.upper()}: {reply}")


def get_startup_workspace(argv):
    if len(argv) < 2:
        return None, None

    workspace = normalize_workspace_dir(argv[1])
    if workspace is None:
        return None, f"Invalid workspace directory: {argv[1]}"
    return str(workspace), None


def _is_file_reference_path(path_text):
    value = str(path_text or "").strip()
    if not value:
        return False
    if value.startswith(("/", "\\", "~")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    return value.startswith(("./", ".\\", "../", "..\\"))


def _external_file_references(user_input):
    references = []
    seen = set()
    for match in FILE_REFERENCE_PATTERN.finditer(user_input):
        path_text = match.group(1).strip()
        if not _is_file_reference_path(path_text):
            continue
        if path_text in seen:
            continue
        seen.add(path_text)
        references.append(path_text)
    return references


def _read_external_file_reference(path_text):
    try:
        path = Path(path_text).expanduser().resolve(strict=True)
    except OSError as error:
        raise ValueError(f"Referenced file does not exist: {path_text}") from error

    if not path.is_file():
        raise ValueError(f"Referenced path is not a file: {path}")

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ValueError(f"Failed to read referenced file: {path}") from error

    truncated = content[:MAX_READ_CHARS]
    if len(content) > MAX_READ_CHARS:
        truncated += (
            f"\n\n[referenced file truncated after {MAX_READ_CHARS} characters]"
        )
    return path, truncated


def attach_external_file_references(user_input):
    references = _external_file_references(user_input)
    if not references:
        return user_input

    blocks = [
        (
            "[Referenced external files]\n"
            "The user explicitly attached these read-only file contents. "
            "They do not grant access to directories or other external files."
        )
    ]
    for path_text in references:
        path, content = _read_external_file_reference(path_text)
        blocks.append(f"--- File: {path} ---\n{content}\n--- End file: {path} ---")

    return f"{user_input}\n\n" + "\n\n".join(blocks)


def run_chat_loop(
    config, workspace_dir, workspace_error=None, agent_auto_disabled=False
):
    if workspace_error:
        print_error(workspace_error)
    if agent_auto_disabled:
        print_warn(
            "Agent mode requires a startup workspace directory and has been turned off."
        )
    try:
        chat = OmniAgent(
            model=config.model,
            api_key=config.api_key,
            api_type=config.api_type,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            context_window_tokens=config.context_window_tokens,
            temperature=config.temperature,
            stream_mode=config.stream_mode,
            thinking_mode=config.thinking_mode,
            reasoning_effort=config.reasoning_effort,
            agent_mode=config.agent_mode,
            workspace_dir=workspace_dir,
            max_agent_rounds=config.max_agent_rounds,
            max_agent_tool_calls=config.max_agent_tool_calls,
            agent_approval_mode=config.agent_approval_mode,
            agent_show_thinking=config.agent_show_thinking,
            agent_summary_model=config.agent_summary_model,
            skills_enabled=config.skills_enable,
            skills_source_app=config.skills_source_app,
            skills_source_workspace=config.skills_source_workspace,
            skills_auto_catalog=config.skills_auto_catalog,
            skills_max_chars=config.skills_max_chars,
            compaction_enable=config.compaction_enable,
            compaction_trigger_ratio=config.compaction_trigger_ratio,
            compaction_keep_recent_messages=config.compaction_keep_recent_messages,
            compaction_compact_model=config.compaction_compact_model,
            memory_model=config.memory_model,
            debug=config.debug,
            web_search_enabled=config.web_search_enable,
            web_search_provider=config.web_search_provider,
            web_search_api_key=config.web_search_api_key,
            web_search_max_results=config.web_search_max_results,
            web_search_depth=config.web_search_depth,
            web_search_topic=config.web_search_topic,
            agent_plan_enabled=config.agent_plan_enable,
        )
    except Exception as error:
        print_error(f"Failed to initialize client: {error}")
        return

    while True:
        try:
            user_input = get_user_input("You: ", multiline=True)

            if not user_input:
                print_error("Please enter a non-empty message.")
                continue

            if user_input.startswith("/"):
                should_continue = process_command(user_input, chat)
                if should_continue is False:
                    break
                if should_continue is True:
                    continue

            try:
                user_input = attach_external_file_references(user_input)
            except ValueError as error:
                print_error(str(error))
                continue

            response = chat.send_message(
                user_input, stream_print_thinking, stream_print_response
            )
            handle_response(
                response,
                chat.model,
                chat.stream_mode and not chat.agent_mode,
                chat.thinking_mode,
            )
            if response and not response.get("agent_stopped"):
                chat.update_session_episodic_memory()

        except KeyboardInterrupt:
            console.print()
            print_success("Conversation interrupted, goodbye!")
            break
        except EOFError:
            console.print()
            print_success("Conversation interrupted, goodbye!")
            break
        except Exception as error:
            print_error(f"Error occurred: {error}")


def main():
    config = load_config()
    workspace_dir, workspace_error = get_startup_workspace(sys.argv)
    agent_auto_disabled = False
    if config.agent_mode and not workspace_dir:
        config.agent_mode = False
        agent_auto_disabled = True

    if sys.stdin.isatty() and sys.stdout.isatty():
        start_tui(config.model, workspace_dir)
        run_tui(
            lambda: run_chat_loop(
                config,
                workspace_dir,
                workspace_error,
                agent_auto_disabled,
            )
        )
        return

    show_dashboard(config.model, workspace_dir)
    run_chat_loop(config, workspace_dir, workspace_error, agent_auto_disabled)


if __name__ == "__main__":
    main()

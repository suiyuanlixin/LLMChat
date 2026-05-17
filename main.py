import sys

from ui import (
    console,
    print_success,
    print_error,
    print_warn,
    print_thinking,
    get_user_input,
    show_dashboard,
    print_stream_thinking_continue,
    clean_and_print_stream_response,
    clean_display_text,
)
from config import load_config, save_config_field
from chat import LLMChat
from commands import process_command
from tools import normalize_workspace_dir


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


def handle_response(response, model_name, stream_mode=False, thinking_mode=False):
    if not response:
        print_error(
            "Failed to get response, please check your APIKey and network connection."
        )
        return

    if stream_mode:
        print()
        return

    if response.get("response_streamed"):
        print()
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


def main():
    config = load_config()
    workspace_dir, workspace_error = get_startup_workspace(sys.argv)
    agent_auto_disabled = False
    if config.agent_mode and not workspace_dir:
        config.agent_mode = False
        save_config_field("agent_mode", False)
        agent_auto_disabled = True

    show_dashboard(config.model, workspace_dir)
    if workspace_error:
        print_error(workspace_error)
    if agent_auto_disabled:
        print_warn("Agent mode requires a startup workspace directory and has been turned off.")
    try:
        chat = LLMChat(
            model=config.model,
            api_key=config.api_key,
            api_type=config.api_type,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            stream_mode=config.stream_mode,
            thinking_mode=config.thinking_mode,
            agent_mode=config.agent_mode,
            workspace_dir=workspace_dir,
            max_agent_rounds=config.max_agent_rounds,
            max_agent_tool_calls=config.max_agent_tool_calls,
            agent_approval_mode=config.agent_approval_mode,
            agent_show_thinking=config.agent_show_thinking,
            agent_summary_model=config.agent_summary_model,
        )
    except Exception as error:
        print_error(f"Failed to initialize client: {error}")
        return

    while True:
        try:
            user_input = get_user_input("You: ")

            if not user_input:
                print_error("Please enter a non-empty message.")
                continue

            if user_input.startswith("/"):
                should_continue = process_command(user_input, chat)
                if should_continue is False:
                    break
                if should_continue is True:
                    continue

            response = chat.send_message(user_input, stream_print_thinking, stream_print_response)
            handle_response(response, chat.model, chat.stream_mode and not chat.agent_mode, chat.thinking_mode)

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


if __name__ == "__main__":
    main()

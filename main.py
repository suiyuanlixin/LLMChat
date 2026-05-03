from ui import (
    console,
    print_success,
    print_error,
    print_thinking,
    get_user_input,
    show_dashboard,
    print_stream_thinking_continue,
    clean_and_print_stream_response,
)
from config import load_config
from chat import LLMChat
from commands import process_command


def _clean_text(text):
    return "\n".join(line for line in text.strip().split("\n") if line.strip())


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

    thinking = response.get("thinking")
    if thinking and thinking_mode:
        print_thinking(_clean_text(thinking))

    reply = _clean_text(response["response"])
    print_success(f"{model_name.upper()}: {reply}")


def main():
    config = load_config()
    api_type, base_url, model, api_key, max_tokens, temperature, stream_mode, thinking_mode = config
    show_dashboard(model)
    try:
        chat = LLMChat(
            model=model,
            api_key=api_key,
            api_type=api_type,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
            stream_mode=stream_mode,
            thinking_mode=thinking_mode,
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
            handle_response(response, chat.model, chat.stream_mode, chat.thinking_mode)

        except KeyboardInterrupt:
            console.print()
            print_success("Conversation interrupted, goodbye!")
            break
        except Exception as error:
            print_error(f"Error occurred: {error}")


if __name__ == "__main__":
    main()

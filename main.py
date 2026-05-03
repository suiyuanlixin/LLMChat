import os
import json

from rich.text import Text
from datetime import datetime

from ui import (
    console,
    gradient_text,
    print_success,
    print_error,
    print_warn,
    print_info,
    print_thinking,
    get_user_input,
    show_dashboard,
    TEXT_COLOR,
    THINK_COLOR,
    print_stream_thinking,
    print_stream_thinking_continue,
    print_stream_response_start,
    _clean_and_print_stream_response,
    _print_newline,
)
from config import (
    API_TYPE_ANTHROPIC,
    API_TYPE_GLM,
    load_config,
    normalize_api_type,
    save_max_tokens,
    save_stream_mode,
    save_thinking_mode,
    save_temperature,
    update_config,
)

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


class LLMChat:
    def __init__(
        self,
        model,
        api_key,
        api_type=API_TYPE_GLM,
        base_url="",
        max_tokens=4096,
        temperature=0.7,
        stream_mode=False,
        thinking_mode=False,
    ):
        self.conversation_history = []
        self.client = None
        self.thinking_mode = thinking_mode
        self.configure(api_type, base_url, model, api_key, max_tokens, temperature, stream_mode)

    def configure(
        self,
        api_type,
        base_url,
        model,
        api_key,
        max_tokens=None,
        temperature=None,
        stream_mode=None,
    ):
        api_type = normalize_api_type(api_type)
        if api_type not in {API_TYPE_GLM, API_TYPE_ANTHROPIC}:
            raise ValueError(f"Unsupported API type: {api_type}")

        base_url = "" if api_type == API_TYPE_GLM else (base_url or "").strip()
        client = self._create_client(api_type, api_key, base_url)

        self.api_type = api_type
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.client = client
        if max_tokens is not None:
            self.max_tokens = max_tokens
        if temperature is not None:
            self.temperature = temperature
        if stream_mode is not None:
            self.stream_mode = stream_mode

    def _create_client(self, api_type, api_key, base_url):
        if api_type == API_TYPE_ANTHROPIC:
            try:
                import anthropic
            except ImportError as error:
                raise RuntimeError("Anthropic SDK is not installed. Run: pip install anthropic") from error

            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            return anthropic.Anthropic(**kwargs)

        try:
            from zai import ZhipuAiClient
        except ImportError as error:
            raise RuntimeError("ZhipuAI SDK is not installed. Run: pip install zai") from error

        return ZhipuAiClient(api_key=api_key)

    def set_max_tokens(self, max_tokens):
        self.max_tokens = max_tokens

    def set_temperature(self, temperature):
        self.temperature = temperature

    def set_stream_mode(self, enabled):
        self.stream_mode = enabled

    def set_thinking_mode(self, enabled):
        self.thinking_mode = enabled

    def send_message(self, user_message, stream_callback_thinking=None, stream_callback_response=None):
        self.conversation_history.append({"role": "user", "content": user_message})

        try:
            if self.stream_mode:
                return self._stream_response(stream_callback_thinking, stream_callback_response, self.model)
            elif self.api_type == API_TYPE_ANTHROPIC:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    messages=self._anthropic_messages(),
                )
                return self._parse_anthropic_response(response)
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.conversation_history,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    thinking={"type": "enabled"},
                )
                return self._parse_response(response)

        except Exception as error:
            print_error(f"Request error: {error}")
            return None

    def _stream_response(self, callback_thinking, callback_response, model_name):
        if self.api_type == API_TYPE_ANTHROPIC:
            return self._stream_anthropic_response(callback_thinking, callback_response, model_name)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.conversation_history,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                thinking={"type": "enabled"},
                stream=True,
            )

            if self.thinking_mode:
                print_stream_thinking("")  # Start thinking line
            full_thinking = ""
            full_response = ""
            thinking_ended = False

            for chunk in response:
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", "") or ""
                if reasoning:
                    full_thinking += reasoning
                    if callback_thinking and self.thinking_mode:
                        callback_thinking(reasoning)

                content = getattr(delta, "content", "") or ""
                if content:
                    if not thinking_ended:
                        if full_thinking and self.thinking_mode:
                            _print_newline()
                        print_stream_response_start(model_name)
                        thinking_ended = True
                    full_response += content
                    if callback_response:
                        callback_response(content)

            self.conversation_history.append({"role": "assistant", "content": full_response})
            return {"thinking": full_thinking, "response": full_response}

        except Exception as error:
            print_error(f"Stream error: {error}")
            return None

    def _stream_anthropic_response(self, callback_thinking, callback_response, model_name):
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=self._anthropic_messages(),
                stream=True,
            )

            if self.thinking_mode:
                print_stream_thinking("")
            full_thinking = ""
            full_response = ""
            response_started = False

            for chunk in response:
                chunk_type = self._get_field(chunk, "type", "")

                if chunk_type == "content_block_start":
                    content_block = self._get_field(chunk, "content_block")
                    if self._get_field(content_block, "type") == "text" and not response_started:
                        if full_thinking and self.thinking_mode:
                            _print_newline()
                        print_stream_response_start(model_name)
                        response_started = True
                    continue

                if chunk_type != "content_block_delta":
                    continue

                delta = self._get_field(chunk, "delta")
                delta_type = self._get_field(delta, "type", "")

                if delta_type == "thinking_delta":
                    thinking = self._get_field(delta, "thinking", "") or ""
                    if thinking:
                        full_thinking += thinking
                        if callback_thinking and self.thinking_mode:
                            callback_thinking(thinking)
                elif delta_type == "text_delta":
                    text = self._get_field(delta, "text", "") or ""
                    if text:
                        if not response_started:
                            if full_thinking:
                                _print_newline()
                            print_stream_response_start(model_name)
                            response_started = True
                        full_response += text
                        if callback_response:
                            callback_response(text)

            self.conversation_history.append({"role": "assistant", "content": full_response})
            return {"thinking": full_thinking, "response": full_response}

        except Exception as error:
            print_error(f"Stream error: {error}")
            return None

    def _parse_response(self, response):
        try:
            message = response.choices[0].message
            assistant_message = getattr(message, "content", "") or ""
            thinking_content = getattr(message, "reasoning_content", "") or ""

            self.conversation_history.append(
                {"role": "assistant", "content": assistant_message}
            )
            return {"thinking": thinking_content, "response": assistant_message}
        except (AttributeError, IndexError) as error:
            print_error(f"Failed to parse response: {error}")
            return None

    def _parse_anthropic_response(self, response):
        try:
            content = self._get_field(response, "content", [])
            full_thinking = ""
            full_response = ""

            if isinstance(content, str):
                full_response = content
            else:
                for block in content or []:
                    block_type = self._get_field(block, "type", "")
                    if block_type == "thinking":
                        full_thinking += self._get_field(block, "thinking", "") or ""
                    elif block_type == "text":
                        full_response += self._get_field(block, "text", "") or ""

            self.conversation_history.append({"role": "assistant", "content": full_response})
            return {"thinking": full_thinking, "response": full_response}
        except (AttributeError, TypeError) as error:
            print_error(f"Failed to parse response: {error}")
            return None

    def _anthropic_messages(self):
        messages = []
        for message in self.conversation_history:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            messages.append({"role": role, "content": message.get("content", "")})
        return messages

    @staticmethod
    def _get_field(item, field, default=None):
        if isinstance(item, dict):
            return item.get(field, default)
        return getattr(item, field, default)

    def clear_history(self):
        self.conversation_history = []

    def get_history(self):
        return self.conversation_history


def save_conversation(conversation_history, model_name):
    if not conversation_history:
        print_error("No conversation history to save.")
        return False

    record_dir = "record"
    if not os.path.exists(record_dir):
        os.makedirs(record_dir)

    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M")
    filename = f"{record_dir}/{timestamp}.json"

    save_data = {
        "version": "1.0",
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


def load_conversation():
    record_dir = "record"
    if not os.path.exists(record_dir):
        print_error("No record directory found.")
        return None

    files = [f for f in os.listdir(record_dir) if f.endswith(".json")]
    if not files:
        print_error("No saved conversations found.")
        return None

    files.sort(reverse=True)

    # Collect formatted info for each file
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
            display = f"{formatted} <json> <{version}> <{model}> <{msg_count}>"
            file_info.append((f, display))
        except Exception:
            file_info.append((f, f))

    print_info("Available conversations:")
    for i, (fname, display) in enumerate(file_info, 1):
        parts = display.split(" <json>", 1)
        if len(parts) == 2:
            date_part = parts[0]
            meta_part = f" <json>{parts[1]}"
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
        name = files[idx][:-5]
        parts = name.split("-")
        if len(parts) >= 5:
            date_str = f"{parts[0]}.{parts[1]}.{parts[2]} {parts[3]}:{parts[4]}"
        else:
            date_str = name
        print_success(
            f"Loaded {len(conversation)} messages from [{idx + 1}] {date_str}."
        )
        return conversation
    except Exception as error:
        print_error(f"Failed to load conversation: {error}")
        return None


def show_help():
    command_list = "\n".join(f"{cmd:<8} {desc}" for cmd, desc in COMMANDS.items())
    additional = "Ctrl+C   Force exit the chat."
    print_warn(f"All commands: \n{command_list}\n{additional}")


def process_command(command, chat):
    if command.startswith("/token "):
        value = command[7:].strip()
        return handle_token(chat, value)
    if command.startswith("/temp "):
        value = command[6:].strip()
        return handle_temp(chat, value)
    if command.startswith("/mode "):
        value = command[6:].strip()
        return handle_mode(chat, value)
    if command.startswith("/think "):
        value = command[7:].strip()
        return handle_think(chat, value)
    if command == "/token":
        print_info(f"Current max tokens: {chat.max_tokens}")
        return True
    if command == "/temp":
        print_info(f"Current temperature: {chat.temperature}")
        return True
    if command == "/mode":
        return handle_mode(chat)
    handler = COMMAND_HANDLERS.get(command)
    if handler:
        return handler(chat)
    return None


def handle_help(chat):
    show_help()
    return True


def handle_quit(chat):
    print_success("Goodbye!")
    return False


def handle_clear(chat):
    chat.clear_history()
    print_success("Conversation history cleared.")
    return True


def handle_conf(chat):
    global api_type, base_url, model, api_key, max_tokens, temperature
    result = update_config()
    if result:
        (
            new_api_type,
            new_base_url,
            new_model,
            new_api_key,
            new_max_tokens,
            new_temperature,
            stream_mode,
        ) = result
        try:
            chat.configure(
                new_api_type,
                new_base_url,
                new_model,
                new_api_key,
                new_max_tokens,
                new_temperature,
                stream_mode,
            )
        except Exception as error:
            print_error(f"Failed to apply configuration: {error}")
            return True

        api_type = new_api_type
        base_url = new_base_url
        model = new_model
        api_key = new_api_key
        max_tokens = new_max_tokens
        temperature = new_temperature
        chat.clear_history()
    return True


def handle_token(chat, value_str):
    global max_tokens
    try:
        new_max_tokens = int(value_str)
        if new_max_tokens <= 0:
            print_error("Token number must be greater than 0.")
            return True
        max_tokens = new_max_tokens
        chat.set_max_tokens(max_tokens)
        save_max_tokens(max_tokens)
        print_success(f"Max tokens set to {max_tokens}.")
    except ValueError:
        print_error(f"Invalid token number: {value_str}")
    return True


def handle_temp(chat, value_str):
    try:
        new_temp = float(value_str)
        if new_temp < 0 or new_temp > 1:
            print_error("Temperature must be between 0 and 1.")
            return True
        chat.set_temperature(new_temp)
        save_temperature(new_temp)
        print_success(f"Temperature set to {new_temp}.")
    except ValueError:
        print_error(f"Invalid temperature value: {value_str}")
    return True


def handle_save(chat):
    save_conversation(chat.get_history(), chat.model)
    return True


def handle_load(chat):
    conversation = load_conversation()
    if conversation is not None:
        chat.conversation_history = conversation
    return True


def handle_mode(chat, mode_arg=None):
    if mode_arg is None:
        current = "stream" if chat.stream_mode else "normal"
        print_info(f"Current mode: {current}. Usage: /mode normal | /mode stream")
        return True

    mode = mode_arg.lower().strip()
    if mode == "stream":
        chat.set_stream_mode(True)
        save_stream_mode(True)
        print_success("Switched to stream mode.")
        return True
    elif mode == "normal":
        chat.set_stream_mode(False)
        save_stream_mode(False)
        print_success("Switched to normal mode.")
        return True
    else:
        print_error(f"Invalid mode: {mode}. Use /mode normal or /mode stream.")
        return True


def handle_think(chat, think_arg=None):
    if think_arg is None:
        current = "on" if chat.thinking_mode else "off"
        print_info(f"Current thinking mode: {current}. Usage: /think on | /think off")
        return True

    think = think_arg.lower().strip()
    if think == "on":
        chat.set_thinking_mode(True)
        save_thinking_mode(True)
        print_success("Thinking mode turned on.")
        return True
    elif think == "off":
        chat.set_thinking_mode(False)
        save_thinking_mode(False)
        print_success("Thinking mode turned off.")
        return True
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
}


def _clean_text(text):
    return "\n".join(line for line in text.strip().split("\n") if line.strip())


def stream_print_thinking(content):
    if content == "\n":
        print_stream_thinking_continue("\n")
    elif content:
        print_stream_thinking_continue(content)


def stream_print_response(content):
    if content == "\n":
        _clean_and_print_stream_response("\n")
    elif content:
        _clean_and_print_stream_response(content)


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
    global api_type, base_url, model, api_key, max_tokens, temperature

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

            command = user_input.lower()
            if command in COMMANDS or command == "/token" or command == "/temp" or command == "/mode" or command == "/think" or command.startswith("/token ") or command.startswith("/temp ") or command.startswith("/mode ") or command.startswith("/think "):
                should_continue = process_command(user_input, chat)
                if should_continue is False:
                    break
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

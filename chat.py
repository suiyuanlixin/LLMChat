from ui import console, print_error, print_stream_thinking, print_stream_response_start
from config import API_TYPE_ANTHROPIC, API_TYPE_GLM, normalize_api_type


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
                    thinking={"type": "enabled"} if self.thinking_mode else {},
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
                thinking={"type": "enabled"} if self.thinking_mode else {},
                stream=True,
            )

            if self.thinking_mode:
                print_stream_thinking("")
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
                        if full_thinking and not full_thinking.endswith("\n"):
                            console.print()
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
                        if full_thinking and not full_thinking.endswith("\n"):
                            console.print()
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
                            if full_thinking and not full_thinking.endswith("\n"):
                                console.print()
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

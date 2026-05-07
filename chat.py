import json

from ui import console, print_error, print_stream_thinking, print_stream_response_start
from config import API_TYPE_ANTHROPIC, API_TYPE_GLM, normalize_api_type
from tools import AgentTools, anthropic_tool_schemas, glm_tool_schemas


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
        agent_mode=False,
        workspace_dir=None,
    ):
        self.conversation_history = []
        self.client = None
        self.thinking_mode = thinking_mode
        self.agent_tools = AgentTools(workspace_dir)
        self.agent_mode = bool(agent_mode and self.agent_tools.enabled)
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
        thinking_mode=None,
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
        if thinking_mode is not None:
            self.thinking_mode = thinking_mode

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
            raise RuntimeError("ZhipuAI SDK is not installed. Run: pip install zai-sdk") from error

        return ZhipuAiClient(api_key=api_key)

    def set_max_tokens(self, max_tokens):
        self.max_tokens = max_tokens

    def set_temperature(self, temperature):
        self.temperature = temperature

    def set_stream_mode(self, enabled):
        self.stream_mode = enabled

    def set_thinking_mode(self, enabled):
        self.thinking_mode = enabled

    def set_workspace_dir(self, workspace_dir):
        self.agent_tools.set_workspace_dir(workspace_dir)
        if not self.agent_tools.enabled:
            self.agent_mode = False

    def set_agent_mode(self, enabled):
        self.agent_mode = bool(enabled and self.agent_tools.enabled)
        return self.agent_mode

    def get_agent_status(self):
        return {
            "enabled": self.agent_mode,
            "workspace_dir": str(self.agent_tools.workspace_dir) if self.agent_tools.enabled else None,
        }

    def send_message(self, user_message, stream_callback_thinking=None, stream_callback_response=None):
        original_history_length = len(self.conversation_history)
        self.conversation_history.append({"role": "user", "content": user_message})

        try:
            if self.agent_mode and not self.agent_tools.enabled:
                self.agent_mode = False

            if self.agent_mode:
                response = self._agent_response()
            elif self.stream_mode:
                response = self._stream_response(stream_callback_thinking, stream_callback_response, self.model)
            elif self.api_type == API_TYPE_ANTHROPIC:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    messages=self._anthropic_messages(),
                )
                response = self._parse_anthropic_response(response)
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.conversation_history,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    thinking={"type": "enabled"} if self.thinking_mode else {},
                )
                response = self._parse_response(response)

            if response is None:
                self._rollback_history(original_history_length)
            return response

        except Exception as error:
            self._rollback_history(original_history_length)
            print_error(f"Request error: {error}")
            return None

    def _agent_response(self):
        if self.api_type == API_TYPE_ANTHROPIC:
            return self._anthropic_agent_response()
        return self._glm_agent_response()

    def _anthropic_agent_response(self):
        full_thinking = ""
        final_response = ""

        for _ in range(10):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=self._anthropic_messages(),
                tools=anthropic_tool_schemas(),
            )

            blocks = self._anthropic_content_blocks(self._get_field(response, "content", []))
            self.conversation_history.append({"role": "assistant", "content": blocks})

            thinking, text, tool_uses = self._parse_anthropic_blocks(blocks)
            full_thinking += thinking
            final_response += text

            if not tool_uses:
                return {"thinking": full_thinking, "response": final_response}

            tool_results = []
            for tool_use in tool_uses:
                tool_result = self.agent_tools.execute(
                    tool_use.get("name", ""),
                    tool_use.get("input", {}),
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.get("id", ""),
                        "content": tool_result,
                        "is_error": tool_result.startswith("ERROR:"),
                    }
                )
            self.conversation_history.append({"role": "user", "content": tool_results})

        print_error("Agent loop stopped after 10 tool rounds.")
        return {"thinking": full_thinking, "response": final_response or "Agent loop stopped after 10 tool rounds."}

    def _glm_agent_response(self):
        full_thinking = ""
        final_response = ""

        for _ in range(10):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.conversation_history,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                thinking={"type": "enabled"} if self.thinking_mode else {},
                tools=glm_tool_schemas(),
            )

            message = response.choices[0].message
            assistant_message, thinking_content, text, tool_calls = self._glm_message_parts(message)
            self.conversation_history.append(assistant_message)
            full_thinking += thinking_content
            final_response += text

            if not tool_calls:
                return {"thinking": full_thinking, "response": final_response}

            for tool_call in tool_calls:
                tool_result = self.agent_tools.execute(tool_call["name"], tool_call["arguments"])
                self.conversation_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": tool_call["name"],
                        "content": tool_result,
                    }
                )

        print_error("Agent loop stopped after 10 tool rounds.")
        return {"thinking": full_thinking, "response": final_response or "Agent loop stopped after 10 tool rounds."}

    def _rollback_history(self, history_length):
        self.conversation_history = self.conversation_history[:history_length]

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

    def _anthropic_content_blocks(self, content):
        if isinstance(content, str):
            return [{"type": "text", "text": content}]

        blocks = []
        for block in content or []:
            block_type = self._get_field(block, "type", "")
            if block_type == "text":
                blocks.append({"type": "text", "text": self._get_field(block, "text", "") or ""})
            elif block_type == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": self._get_field(block, "id", "") or "",
                        "name": self._get_field(block, "name", "") or "",
                        "input": self._get_field(block, "input", {}) or {},
                    }
                )
            elif block_type == "thinking":
                thinking_block = {
                    "type": "thinking",
                    "thinking": self._get_field(block, "thinking", "") or "",
                }
                signature = self._get_field(block, "signature")
                if signature:
                    thinking_block["signature"] = signature
                blocks.append(thinking_block)
        return blocks

    def _parse_anthropic_blocks(self, blocks):
        thinking = ""
        text = ""
        tool_uses = []
        for block in blocks:
            block_type = block.get("type")
            if block_type == "thinking":
                thinking += block.get("thinking", "") or ""
            elif block_type == "text":
                text += block.get("text", "") or ""
            elif block_type == "tool_use":
                tool_uses.append(block)
        return thinking, text, tool_uses

    def _glm_message_parts(self, message):
        text = self._get_field(message, "content", "") or ""
        thinking_content = self._get_field(message, "reasoning_content", "") or ""
        raw_tool_calls = self._get_field(message, "tool_calls", None) or []

        assistant_message = {"role": "assistant", "content": text}
        tool_calls = []
        if raw_tool_calls:
            assistant_tool_calls = []
            for call in raw_tool_calls:
                call_id = self._get_field(call, "id", "") or ""
                function = self._get_field(call, "function", {}) or {}
                name = self._get_field(function, "name", "") or ""
                arguments = self._get_field(function, "arguments", {}) or {}
                parsed_arguments = self._parse_tool_arguments(arguments)

                assistant_tool_calls.append(
                    {
                        "id": call_id,
                        "type": self._get_field(call, "type", "function") or "function",
                        "function": {
                            "name": name,
                            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                        },
                    }
                )
                tool_calls.append(
                    {
                        "id": call_id,
                        "name": name,
                        "arguments": parsed_arguments,
                    }
                )
            assistant_message["tool_calls"] = assistant_tool_calls

        return assistant_message, thinking_content, text, tool_calls

    @staticmethod
    def _parse_tool_arguments(arguments):
        if isinstance(arguments, dict):
            return arguments
        if not arguments:
            return {}
        try:
            return json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            return {}

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

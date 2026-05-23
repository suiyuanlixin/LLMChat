import json
import re
import time

from rich.cells import cell_len

from ui import (
    clear_current_lines,
    clean_and_print_stream_response,
    clean_display_text,
    console,
    print_error,
    print_stream_thinking,
    print_stream_thinking_continue,
    print_stream_response_continue,
    print_stream_response_start,
    print_warn,
)
from config import (
    API_TYPE_ANTHROPIC,
    API_TYPE_GLM,
    API_TYPE_OLLAMA,
    API_TYPE_OPENAI,
    AGENT_THINKING_FULL,
    AGENT_THINKING_SUMMARY,
    DEFAULT_MAX_AGENT_ROUNDS,
    DEFAULT_MAX_AGENT_TOOL_CALLS,
    normalize_api_type,
    parse_agent_show_thinking,
)
from tools import (
    AgentTools,
    anthropic_tool_schemas,
    glm_tool_schemas,
    ollama_tool_schemas,
    openai_tool_schemas,
)


AGENT_CONTEXT_WARN_CHARS = 180000
AGENT_TOOL_RESULT_CONTEXT_CHARS = 12000
AGENT_SUMMARY_THINKING_CHAR_DELAY_SECONDS = 0.006
AGENT_RESPONSE_CHAR_DELAY_SECONDS = 0.003
AGENT_SUMMARY_MAX_TOKENS = 96
AGENT_SUMMARY_PREFIX_CHARS = len("[*] Thinking: ")
AGENT_SUMMARY_SYSTEM_PROMPT = (
    "Summarize hidden local-agent thinking for a terminal status line. "
    "Return one very short sentence, ideally 4-8 words or 8-16 Chinese characters. "
    "Prefer brevity over detail. Use no markdown, code, quotes, or prefix. "
    "Describe what the agent is doing now, not step-by-step reasoning. "
    "Use the same language as the thinking when obvious."
)


AGENT_SYSTEM_PROMPT = """You are in local workspace agent mode.

Rules:
- Work only inside the configured workspace and use tools for local file facts.
- Explore before editing: list directories, search, and read relevant line ranges first.
- Prefer small, targeted changes. Do not rewrite unrelated code.
- Use read_file with line ranges and line numbers when a file is long.
- Prefer apply_unified_patch for contextual edits, apply_patch for simple line-range edits, and edit_file for exact small replacements.
- Use git_status and git_diff to understand existing and resulting workspace changes.
- After editing, run a lightweight verification command when it is safe and relevant.
- Do not claim the task is complete until you have inspected the resulting diff or verification output.
- In the final summary, distinguish files you edited in this run from pre-existing workspace changes.
- If a tool fails, explain the failure and try a different precise approach instead of repeating the same call.
- Stop when the task is complete and summarize what changed."""


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
        max_agent_rounds=DEFAULT_MAX_AGENT_ROUNDS,
        max_agent_tool_calls=DEFAULT_MAX_AGENT_TOOL_CALLS,
        agent_approval_mode="confirm",
        agent_show_thinking=True,
        agent_summary_model="",
    ):
        self.conversation_history = []
        self.client = None
        self.thinking_mode = thinking_mode
        self.agent_tools = AgentTools(
            workspace_dir,
            approval_mode=agent_approval_mode,
            visible_output_callback=self._before_agent_visible_output,
        )
        self.agent_mode = bool(agent_mode and self.agent_tools.enabled)
        self.agent_show_thinking = parse_agent_show_thinking(agent_show_thinking)
        self.agent_summary_model = str(agent_summary_model or "").strip()
        self.max_agent_rounds = max(1, int(max_agent_rounds))
        self.max_agent_tool_calls = max(1, int(max_agent_tool_calls))
        self.agent_running = False
        self.agent_stop_requested = False
        self.agent_tool_calls = 0
        self.agent_final_check_done = False
        self.agent_context_warning_sent = False
        self.agent_thinking_streamed = False
        self.agent_thinking_needs_separator = False
        self.agent_summary_thinking_active = False
        self.agent_summary_rendered_lines = 1
        self.agent_response_streamed = False
        self.agent_response_started = False
        self.agent_output_needs_separator = False
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
        if api_type not in {API_TYPE_GLM, API_TYPE_ANTHROPIC, API_TYPE_OPENAI, API_TYPE_OLLAMA}:
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

        if api_type == API_TYPE_OPENAI:
            try:
                from openai import OpenAI
            except ImportError as error:
                raise RuntimeError("OpenAI SDK is not installed. Run: pip install openai") from error

            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            return OpenAI(**kwargs)

        if api_type == API_TYPE_OLLAMA:
            try:
                from ollama import Client
            except ImportError as error:
                raise RuntimeError("Ollama SDK is not installed. Run: pip install ollama") from error

            kwargs = {}
            if base_url:
                kwargs["host"] = base_url
            if api_key:
                kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
            return Client(**kwargs)

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

    def set_agent_limits(self, max_rounds=None, max_tool_calls=None):
        if max_rounds is not None:
            self.max_agent_rounds = max(1, int(max_rounds))
        if max_tool_calls is not None:
            self.max_agent_tool_calls = max(1, int(max_tool_calls))

    def set_agent_approval_mode(self, approval_mode):
        self.agent_tools.set_approval_mode(approval_mode)

    def set_agent_show_thinking(self, enabled):
        self.agent_show_thinking = parse_agent_show_thinking(enabled)

    def set_agent_summary_model(self, model):
        self.agent_summary_model = str(model or "").strip()

    def set_workspace_dir(self, workspace_dir):
        self.agent_tools.set_workspace_dir(workspace_dir)
        if not self.agent_tools.enabled:
            self.agent_mode = False

    def set_agent_mode(self, enabled):
        self.agent_mode = bool(enabled and self.agent_tools.enabled)
        if not self.agent_mode:
            self.request_agent_stop()
        return self.agent_mode

    def request_agent_stop(self):
        was_running = self.agent_running
        self.agent_stop_requested = True
        return was_running

    def get_agent_status(self):
        return {
            "enabled": self.agent_mode,
            "workspace_dir": str(self.agent_tools.workspace_dir) if self.agent_tools.enabled else None,
            "running": self.agent_running,
            "max_rounds": self.max_agent_rounds,
            "max_tool_calls": self.max_agent_tool_calls,
            "approval_mode": self.agent_tools.approval_mode,
            "show_thinking": self.agent_show_thinking,
            "summary_model": self.agent_summary_model,
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
            elif self.api_type == API_TYPE_OLLAMA:
                response = self.client.chat(
                    **self._ollama_chat_kwargs(messages=self.conversation_history)
                )
                response = self._parse_ollama_response(response)
            else:
                response = self.client.chat.completions.create(
                    **self._chat_completion_kwargs(messages=self.conversation_history)
                )
                response = self._parse_response(response)

            if response is None:
                self._rollback_history(original_history_length)
            elif response.get("agent_stopped"):
                self._rollback_history(original_history_length)
            elif self.agent_mode:
                self._compact_agent_history(original_history_length, response)
            return response

        except KeyboardInterrupt:
            if self.agent_running:
                self.request_agent_stop()
                self._rollback_history(original_history_length)
                self._separate_after_agent_thinking()
                print_warn("Agent stopped by user.")
                return {"thinking": "", "response": "Agent stopped by user.", "agent_stopped": True}
            raise
        except Exception as error:
            self._rollback_history(original_history_length)
            if self.agent_running:
                self._separate_after_agent_thinking()
            print_error(f"Request error: {error}")
            return None

    def _agent_response(self):
        self.agent_running = True
        self.agent_stop_requested = False
        self.agent_tool_calls = 0
        self.agent_final_check_done = False
        self.agent_context_warning_sent = False
        self.agent_thinking_streamed = False
        self.agent_thinking_needs_separator = False
        self.agent_summary_thinking_active = False
        self.agent_summary_rendered_lines = 1
        self.agent_response_streamed = False
        self.agent_response_started = False
        self.agent_output_needs_separator = False
        self.agent_tools.begin_agent_session()
        try:
            if self.api_type == API_TYPE_ANTHROPIC:
                return self._finalize_agent_response(self._anthropic_agent_response())
            if self.api_type == API_TYPE_OLLAMA:
                return self._finalize_agent_response(self._ollama_agent_response())
            return self._finalize_agent_response(self._chat_completion_agent_response())
        except KeyboardInterrupt:
            self.agent_stop_requested = True
            print_warn("Agent stopped by user.")
            return self._finalize_agent_response(
                {"thinking": "", "response": "Agent stopped by user.", "agent_stopped": True}
            )
        finally:
            self.agent_running = False

    def _finalize_agent_response(self, response):
        if response and self.agent_show_thinking != AGENT_THINKING_FULL:
            response = dict(response)
            response["thinking"] = ""
            response["thinking_streamed"] = self.agent_thinking_streamed
            response["response_streamed"] = self.agent_response_streamed
            response["thinking_needs_separator"] = (
                self.agent_thinking_needs_separator and not response.get("agent_stopped")
            )
            return response
        if response and self.agent_thinking_streamed:
            response = dict(response)
            response["thinking"] = ""
            response["thinking_streamed"] = True
            response["response_streamed"] = self.agent_response_streamed
            response["thinking_needs_separator"] = (
                self.agent_thinking_needs_separator and not response.get("agent_stopped")
            )
        elif response:
            response = dict(response)
            response["response_streamed"] = self.agent_response_streamed
        return response

    def _anthropic_agent_response(self):
        full_thinking = ""
        final_response = ""

        for round_index in range(1, self.max_agent_rounds + 1):
            if self._agent_should_stop():
                return self._agent_stopped_response(full_thinking, final_response)
            self._print_agent_round(round_index)
            self._warn_agent_context_if_needed()
            blocks = self._stream_anthropic_agent_turn()
            self.conversation_history.append({"role": "assistant", "content": blocks})

            thinking, text, tool_uses = self._parse_anthropic_blocks(blocks)
            full_thinking += thinking
            final_response += text

            if not tool_uses:
                if self._append_agent_final_check_if_needed():
                    final_response = ""
                    continue
                self._stream_agent_response_text(final_response, pseudo=True)
                return {"thinking": full_thinking, "response": final_response}

            if self._agent_tool_budget_exceeded(len(tool_uses)):
                message = self._agent_tool_budget_message()
                self.conversation_history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.get("id", ""),
                                "content": _error_text(message),
                                "is_error": True,
                            }
                            for tool_use in tool_uses
                        ],
                    }
                )
                self._separate_after_agent_thinking()
                print_error(message)
                return {"thinking": full_thinking, "response": final_response or message}

            tool_results = []
            for tool_use in tool_uses:
                if self._agent_should_stop():
                    return self._agent_stopped_response(full_thinking, final_response)
                tool_result = self._execute_agent_tool(
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

        message = f"Agent loop stopped after {self.max_agent_rounds} tool rounds."
        self._separate_after_agent_thinking()
        print_error(message)
        return {"thinking": full_thinking, "response": final_response or message}

    def _chat_completion_agent_response(self):
        full_thinking = ""
        final_response = ""

        for round_index in range(1, self.max_agent_rounds + 1):
            if self._agent_should_stop():
                return self._agent_stopped_response(full_thinking, final_response)
            self._print_agent_round(round_index)
            self._warn_agent_context_if_needed()
            response = self.client.chat.completions.create(
                **self._chat_completion_kwargs(
                    messages=self._chat_agent_messages(),
                    tools=self._chat_tool_schemas(),
                )
            )

            message = response.choices[0].message
            assistant_message, thinking_content, text, tool_calls = self._chat_message_parts(message)
            self.conversation_history.append(assistant_message)
            full_thinking += thinking_content
            self._show_agent_thinking(thinking_content)
            final_response += text

            if not tool_calls:
                if self._append_agent_final_check_if_needed():
                    final_response = ""
                    continue
                self._stream_agent_response_text(final_response, pseudo=True)
                return {"thinking": full_thinking, "response": final_response}

            if self._agent_tool_budget_exceeded(len(tool_calls)):
                message = self._agent_tool_budget_message()
                for tool_call in tool_calls:
                    self.conversation_history.append(
                        self._chat_tool_result_message(
                            tool_call["id"],
                            tool_call["name"],
                            _error_text(message),
                        )
                    )
                self._separate_after_agent_thinking()
                print_error(message)
                return {"thinking": full_thinking, "response": final_response or message}

            for tool_call in tool_calls:
                if self._agent_should_stop():
                    return self._agent_stopped_response(full_thinking, final_response)
                tool_result = self._execute_agent_tool(tool_call["name"], tool_call["arguments"])
                self.conversation_history.append(
                    self._chat_tool_result_message(
                        tool_call["id"],
                        tool_call["name"],
                        tool_result,
                    )
                )

        message = f"Agent loop stopped after {self.max_agent_rounds} tool rounds."
        self._separate_after_agent_thinking()
        print_error(message)
        return {"thinking": full_thinking, "response": final_response or message}

    def _ollama_agent_response(self):
        full_thinking = ""
        final_response = ""

        for round_index in range(1, self.max_agent_rounds + 1):
            if self._agent_should_stop():
                return self._agent_stopped_response(full_thinking, final_response)
            self._print_agent_round(round_index)
            self._warn_agent_context_if_needed()
            response = self.client.chat(
                **self._ollama_chat_kwargs(
                    messages=self._ollama_agent_messages(),
                    tools=ollama_tool_schemas(),
                )
            )

            message = self._get_field(response, "message", {})
            assistant_message, thinking_content, text, tool_calls = self._ollama_message_parts(message)
            self.conversation_history.append(assistant_message)
            full_thinking += thinking_content
            self._show_agent_thinking(thinking_content)
            final_response += text

            if not tool_calls:
                if self._append_agent_final_check_if_needed():
                    final_response = ""
                    continue
                self._stream_agent_response_text(final_response, pseudo=True)
                return {"thinking": full_thinking, "response": final_response}

            if self._agent_tool_budget_exceeded(len(tool_calls)):
                message = self._agent_tool_budget_message()
                for tool_call in tool_calls:
                    self.conversation_history.append(
                        self._ollama_tool_result_message(
                            tool_call["name"],
                            _error_text(message),
                        )
                    )
                self._separate_after_agent_thinking()
                print_error(message)
                return {"thinking": full_thinking, "response": final_response or message}

            for tool_call in tool_calls:
                if self._agent_should_stop():
                    return self._agent_stopped_response(full_thinking, final_response)
                tool_result = self._execute_agent_tool(tool_call["name"], tool_call["arguments"])
                self.conversation_history.append(
                    self._ollama_tool_result_message(tool_call["name"], tool_result)
                )

        message = f"Agent loop stopped after {self.max_agent_rounds} tool rounds."
        self._separate_after_agent_thinking()
        print_error(message)
        return {"thinking": full_thinking, "response": final_response or message}

    def _stream_anthropic_agent_turn(self):
        blocks = []
        active_block_index = None

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=self._anthropic_messages(),
            system=AGENT_SYSTEM_PROMPT,
            tools=anthropic_tool_schemas(),
            stream=True,
        )

        for chunk in response:
            chunk_type = self._get_field(chunk, "type", "")

            if chunk_type == "content_block_start":
                content_block = self._get_field(chunk, "content_block")
                block_type = self._get_field(content_block, "type", "")
                if block_type == "text":
                    block = {"type": "text", "text": ""}
                elif block_type == "thinking":
                    block = {"type": "thinking", "thinking": ""}
                elif block_type == "tool_use":
                    block = {
                        "type": "tool_use",
                        "id": self._get_field(content_block, "id", "") or "",
                        "name": self._get_field(content_block, "name", "") or "",
                        "input": {},
                        "_input_json": "",
                    }
                else:
                    block = {"type": block_type or "unknown"}
                blocks.append(block)
                active_block_index = len(blocks) - 1
                continue

            if chunk_type == "content_block_delta" and active_block_index is not None:
                delta = self._get_field(chunk, "delta")
                delta_type = self._get_field(delta, "type", "")
                block = blocks[active_block_index]

                if delta_type == "text_delta":
                    text_delta = self._get_field(delta, "text", "") or ""
                    block["text"] = block.get("text", "") + text_delta
                elif delta_type == "thinking_delta":
                    thinking_delta = self._get_field(delta, "thinking", "") or ""
                    block["thinking"] = block.get("thinking", "") + thinking_delta
                    self._stream_agent_thinking(thinking_delta)
                elif delta_type == "signature_delta":
                    block["signature"] = block.get("signature", "") + (
                        self._get_field(delta, "signature", "") or ""
                    )
                elif delta_type == "input_json_delta":
                    block["_input_json"] = block.get("_input_json", "") + (
                        self._get_field(delta, "partial_json", "") or ""
                    )
                continue

            if chunk_type == "content_block_stop" and active_block_index is not None:
                block = blocks[active_block_index]
                if block.get("type") == "tool_use":
                    raw_input = block.pop("_input_json", "")
                    if raw_input:
                        block["input"] = self._parse_tool_arguments(raw_input)
                elif block.get("type") == "thinking":
                    self._show_agent_thinking_summary(block.get("thinking", ""))
                active_block_index = None

        for block in blocks:
            block.pop("_input_json", None)
        return blocks

    def _agent_should_stop(self):
        return self.agent_stop_requested

    def _agent_stopped_response(self, thinking, response):
        message = "Agent stopped by user."
        self._separate_after_agent_thinking()
        print_warn(message)
        return {
            "thinking": thinking,
            "response": response or message,
            "agent_stopped": True,
        }

    def _agent_tool_budget_exceeded(self, requested_tool_calls):
        return self.agent_tool_calls + requested_tool_calls > self.max_agent_tool_calls

    def _agent_tool_budget_message(self):
        return f"Agent stopped after {self.max_agent_tool_calls} tool calls."

    def _print_agent_round(self, round_index):
        return

    def _stream_agent_thinking(self, content):
        if (
            not self.thinking_mode
            or self.agent_show_thinking != AGENT_THINKING_FULL
            or not content
        ):
            return
        leading_newline = True
        if self.agent_output_needs_separator:
            console.print()
            self.agent_output_needs_separator = False
            self.agent_thinking_streamed = False
            self.agent_thinking_needs_separator = False
            self.agent_summary_thinking_active = False
            self.agent_summary_rendered_lines = 1
            leading_newline = False
        if not self.agent_thinking_streamed:
            print_stream_thinking("", leading_newline=leading_newline)
            self.agent_thinking_streamed = True
        print_stream_thinking_continue(content)
        self.agent_thinking_needs_separator = True
        self.agent_summary_thinking_active = False

    def _show_agent_thinking(self, content):
        if self.agent_show_thinking == AGENT_THINKING_FULL:
            self._stream_agent_thinking(content)
        elif self.agent_show_thinking == AGENT_THINKING_SUMMARY:
            self._show_agent_thinking_summary(content)

    def _show_agent_thinking_summary(self, content):
        if (
            not self.thinking_mode
            or self.agent_show_thinking != AGENT_THINKING_SUMMARY
            or not content
        ):
            return

        replace_current_line = (
            console.is_terminal
            and self.agent_summary_thinking_active
            and self.agent_thinking_needs_separator
            and not self.agent_output_needs_separator
        )
        leading_newline = True
        if self.agent_output_needs_separator:
            console.print()
            self.agent_output_needs_separator = False
            self.agent_thinking_streamed = False
            self.agent_thinking_needs_separator = False
            self.agent_summary_thinking_active = False
            self.agent_summary_rendered_lines = 1
            leading_newline = False
        elif replace_current_line:
            leading_newline = False
        elif self.agent_thinking_needs_separator:
            console.print()
            self.agent_thinking_needs_separator = False
            self.agent_summary_thinking_active = False
            self.agent_summary_rendered_lines = 1

        summary = self._stream_agent_thinking_summary_with_model(
            content,
            leading_newline=leading_newline,
            replace_current_line=replace_current_line,
        )
        if not summary:
            summary = _summarize_agent_thinking(content)
            if not summary:
                return
            self._print_agent_thinking_summary(
                summary,
                leading_newline=leading_newline,
                replace_current_line=replace_current_line,
            )

        self.agent_thinking_streamed = True
        self.agent_thinking_needs_separator = True
        self.agent_summary_thinking_active = True

    def _print_agent_thinking_summary(self, summary, leading_newline=True, replace_current_line=False):
        summary = _clean_summary_stream_delta(summary).strip()
        if not summary:
            return
        self._start_agent_thinking_summary_line(leading_newline, replace_current_line)
        for character in summary:
            print_stream_thinking_continue(character)
            if console.is_terminal:
                time.sleep(AGENT_SUMMARY_THINKING_CHAR_DELAY_SECONDS)
        self.agent_summary_rendered_lines = self._agent_summary_rendered_line_count(summary)

    def _start_agent_thinking_summary_line(self, leading_newline=True, replace_current_line=False):
        if replace_current_line:
            clear_current_lines(self.agent_summary_rendered_lines)
            print_stream_thinking("", leading_newline=False)
        else:
            print_stream_thinking("", leading_newline=leading_newline)

    def _agent_summary_rendered_line_count(self, summary):
        if not console.is_terminal:
            return 1
        width = max(1, int(console.width or 80))
        text_length = AGENT_SUMMARY_PREFIX_CHARS + cell_len(str(summary or ""))
        return max(1, (text_length + width - 1) // width)

    def _stream_agent_thinking_summary_with_model(
        self,
        content,
        leading_newline=True,
        replace_current_line=False,
    ):
        if not self.agent_summary_model:
            return ""

        prompt = _summary_model_prompt(content)
        if not prompt:
            return ""

        summary_text = ""
        raw_summary_text = ""
        started = False

        def emit(delta):
            nonlocal started, summary_text
            delta = _clean_summary_stream_delta(delta)
            if not summary_text:
                delta = delta.lstrip()
            if not delta:
                return
            if not started:
                self._start_agent_thinking_summary_line(leading_newline, replace_current_line)
                started = True
            summary_text += delta
            print_stream_thinking_continue(delta)
            self.agent_summary_rendered_lines = self._agent_summary_rendered_line_count(summary_text)

        try:
            if self.api_type == API_TYPE_ANTHROPIC:
                response = self.client.messages.create(
                    model=self.agent_summary_model,
                    max_tokens=AGENT_SUMMARY_MAX_TOKENS,
                    temperature=min(float(self.temperature), 0.3),
                    system=AGENT_SUMMARY_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    stream=True,
                )
                for chunk in response:
                    if self._get_field(chunk, "type", "") != "content_block_delta":
                        continue
                    delta = self._get_field(chunk, "delta")
                    if self._get_field(delta, "type", "") == "text_delta":
                        emit(self._get_field(delta, "text", "") or "")
            elif self.api_type == API_TYPE_OLLAMA:
                response = self.client.chat(
                    **self._ollama_chat_kwargs(
                        model=self.agent_summary_model,
                        messages=[
                            {"role": "system", "content": AGENT_SUMMARY_SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=min(float(self.temperature), 0.3),
                        max_tokens=AGENT_SUMMARY_MAX_TOKENS,
                        stream=True,
                        include_reasoning=False,
                    )
                )
                for chunk in response:
                    message = self._get_field(chunk, "message", {})
                    _, raw_summary_text = self._split_stream_delta(
                        raw_summary_text,
                        self._get_field(message, "content", "") or "",
                    )
                    clean_summary = _clean_summary_stream_delta(
                        _clean_content_text(raw_summary_text)
                    ).strip()
                    visible_delta, summary_text_candidate = self._split_stream_delta(
                        summary_text,
                        clean_summary,
                    )
                    if visible_delta:
                        emit(visible_delta)
                        summary_text = summary_text_candidate
            else:
                kwargs = self._chat_completion_kwargs(
                    model=self.agent_summary_model,
                    messages=[
                        {"role": "system", "content": AGENT_SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=min(float(self.temperature), 0.3),
                    max_tokens=AGENT_SUMMARY_MAX_TOKENS,
                    stream=True,
                    include_reasoning=False,
                )
                if self._uses_minimax_openai_compat(self.agent_summary_model):
                    kwargs["extra_body"] = {"reasoning_split": True}

                response = self.client.chat.completions.create(**kwargs)
                for chunk in response:
                    delta = chunk.choices[0].delta
                    _, raw_summary_text = self._split_stream_delta(
                        raw_summary_text,
                        self._get_field(delta, "content", "") or "",
                    )
                    clean_summary = _clean_summary_stream_delta(
                        _clean_content_text(raw_summary_text)
                    ).strip()
                    visible_delta, summary_text_candidate = self._split_stream_delta(
                        summary_text,
                        clean_summary,
                    )
                    if visible_delta:
                        emit(visible_delta)
                        summary_text = summary_text_candidate
        except Exception:
            return summary_text.strip()

        return summary_text.strip()

    def _stream_agent_response_text(self, content, pseudo=False):
        if not self.stream_mode or not content:
            return
        if pseudo:
            content = clean_display_text(content)
            if not content:
                return
        if not self.agent_response_started:
            self._separate_after_agent_thinking()
            if self.agent_output_needs_separator:
                console.print()
                self.agent_output_needs_separator = False
            print_stream_response_start(self.model)
            self.agent_response_started = True
        if pseudo and console.is_terminal:
            for character in content:
                print_stream_response_continue(character)
                time.sleep(AGENT_RESPONSE_CHAR_DELAY_SECONDS)
        else:
            clean_and_print_stream_response(content)
        self.agent_response_streamed = True

    def _separate_after_agent_thinking(self):
        if not self.agent_thinking_needs_separator:
            return
        console.print()
        self.agent_thinking_needs_separator = False
        self.agent_summary_thinking_active = False
        self.agent_summary_rendered_lines = 1

    def _before_agent_visible_output(self):
        self._separate_after_agent_thinking()

    def _warn_agent_context_if_needed(self):
        if self.agent_context_warning_sent:
            return
        estimated_chars = _estimate_history_chars(self.conversation_history)
        if estimated_chars < AGENT_CONTEXT_WARN_CHARS:
            return

        self.agent_context_warning_sent = True
        warning = (
            "Agent context budget warning: the current conversation and tool results are large. "
            "Use narrower searches, read smaller line ranges, and avoid repeating bulky outputs."
        )
        self._separate_after_agent_thinking()
        print_warn(warning)
        self.agent_output_needs_separator = True
        self.conversation_history.append({"role": "user", "content": warning})

    def _compact_agent_history(self, history_start, response):
        if history_start >= len(self.conversation_history):
            return

        user_message = self.conversation_history[history_start]
        if user_message.get("role") != "user":
            return

        assistant_text = response.get("response", "") or ""
        run_summary = self.agent_tools.session_summary()
        history_text = assistant_text
        if run_summary:
            history_text = f"{assistant_text}\n\n[Agent run summary]\n{run_summary}".strip()

        self.conversation_history = (
            self.conversation_history[:history_start]
            + [user_message, {"role": "assistant", "content": history_text}]
        )

    def _append_agent_final_check_if_needed(self):
        if self.agent_final_check_done or not self.agent_tools.session_has_changes():
            return False

        self.agent_final_check_done = True
        check_result = self.agent_tools.final_check()

        self.conversation_history.append(
            {
                "role": "user",
                "content": (
                    "Automatic final verification for this local agent run:\n\n"
                    f"{check_result}\n\n"
                    "If the verification output shows a problem, continue using tools to fix it. "
                    "Do not attribute pre-existing workspace changes to this run unless they are listed "
                    "as agent-edited files or agent mutating commands. "
                    "If the task is complete, provide the final response with a concise summary "
                    "and mention what verification was performed."
                ),
            }
        )
        return True

    def _execute_agent_tool(self, name, tool_input):
        self.agent_tool_calls += 1
        change_count_before = self.agent_tools.session_change_count()
        tool_result = self.agent_tools.execute(name, tool_input)
        if self.agent_tools.consume_output_separator():
            self.agent_output_needs_separator = True
            self.agent_summary_thinking_active = False
        if self.agent_tools.session_change_count() > change_count_before:
            self.agent_final_check_done = False
        context_result = _compact_tool_result_for_context(tool_result)
        return context_result

    def _rollback_history(self, history_length):
        self.conversation_history = self.conversation_history[:history_length]

    def _stream_response(self, callback_thinking, callback_response, model_name):
        if self.api_type == API_TYPE_ANTHROPIC:
            return self._stream_anthropic_response(callback_thinking, callback_response, model_name)
        if self.api_type == API_TYPE_OLLAMA:
            return self._stream_ollama_response(callback_thinking, callback_response, model_name)

        try:
            response = self.client.chat.completions.create(
                **self._chat_completion_kwargs(
                    messages=self.conversation_history,
                    stream=True,
                )
            )

            if self.thinking_mode:
                print_stream_thinking("")
            full_thinking = ""
            raw_thinking = ""
            full_response = ""
            raw_response = ""
            thinking_ended = False

            for chunk in response:
                delta = chunk.choices[0].delta
                reasoning, full_thinking, raw_thinking = self._stream_reasoning_delta(
                    delta,
                    full_thinking,
                    raw_thinking,
                )
                if reasoning:
                    if callback_thinking and self.thinking_mode:
                        callback_thinking(reasoning)

                content, full_response, raw_response = self._stream_content_delta(
                    self._get_field(delta, "content", "") or "",
                    full_response,
                    raw_response,
                )
                if content:
                    if not thinking_ended:
                        if full_thinking and not full_thinking.endswith("\n"):
                            console.print()
                        print_stream_response_start(model_name)
                        thinking_ended = True
                    if callback_response:
                        callback_response(content)

            self.conversation_history.append(
                self._chat_stream_assistant_message(full_response, full_thinking)
            )
            return {"thinking": full_thinking, "response": full_response}

        except Exception as error:
            print_error(f"Stream error: {error}")
            return None

    def _stream_ollama_response(self, callback_thinking, callback_response, model_name):
        try:
            response = self.client.chat(
                **self._ollama_chat_kwargs(
                    messages=self.conversation_history,
                    stream=True,
                )
            )

            if self.thinking_mode:
                print_stream_thinking("")
            full_thinking = ""
            full_response = ""
            response_started = False

            for chunk in response:
                message = self._get_field(chunk, "message", {})
                thinking = self._get_field(message, "thinking", "") or ""
                if thinking:
                    full_thinking += thinking
                    if callback_thinking and self.thinking_mode:
                        callback_thinking(thinking)

                content = self._get_field(message, "content", "") or ""
                if content:
                    if not response_started:
                        if full_thinking and not full_thinking.endswith("\n"):
                            console.print()
                        print_stream_response_start(model_name)
                        response_started = True
                    full_response += content
                    if callback_response:
                        callback_response(content)

            self.conversation_history.append(
                self._ollama_assistant_message(full_response, full_thinking)
            )
            return {
                "thinking": _clean_reasoning_text(full_thinking),
                "response": full_response,
            }

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
            assistant_message, thinking_content, text, _ = self._chat_message_parts(message)

            self.conversation_history.append(assistant_message)
            return {"thinking": thinking_content, "response": text}
        except (AttributeError, IndexError) as error:
            print_error(f"Failed to parse response: {error}")
            return None

    def _parse_ollama_response(self, response):
        try:
            message = self._get_field(response, "message", {})
            assistant_message, thinking_content, text, _ = self._ollama_message_parts(message)

            self.conversation_history.append(assistant_message)
            return {"thinking": thinking_content, "response": text}
        except (AttributeError, TypeError) as error:
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

    def _chat_message_parts(self, message):
        text = self._message_content_text(self._get_field(message, "content", "") or "")
        thinking_content = self._message_reasoning_text(message)
        raw_tool_calls = self._get_field(message, "tool_calls", None) or []

        assistant_message = {"role": "assistant", "content": text}
        reasoning_details = self._get_field(message, "reasoning_details", None)
        if reasoning_details:
            assistant_message["reasoning_details"] = self._plain_data(reasoning_details)

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

    def _ollama_message_parts(self, message):
        text = str(self._get_field(message, "content", "") or "")
        thinking_content = _clean_reasoning_text(
            self._get_field(message, "thinking", "") or ""
        )
        raw_tool_calls = self._get_field(message, "tool_calls", None) or []

        tool_calls = []
        assistant_tool_calls = []
        for index, call in enumerate(raw_tool_calls):
            function = self._get_field(call, "function", {}) or {}
            name = self._get_field(function, "name", "") or ""
            arguments = self._get_field(function, "arguments", {}) or {}
            parsed_arguments = self._parse_tool_arguments(arguments)
            function_call = {
                "name": name,
                "arguments": parsed_arguments,
            }
            raw_index = self._get_field(function, "index", None)
            if raw_index is not None:
                function_call["index"] = raw_index
            elif len(raw_tool_calls) > 1:
                function_call["index"] = index
            assistant_tool_calls.append(
                {
                    "type": self._get_field(call, "type", "function") or "function",
                    "function": function_call,
                }
            )
            tool_calls.append(
                {
                    "name": name,
                    "arguments": parsed_arguments,
                }
            )

        assistant_message = self._ollama_assistant_message(
            text,
            thinking_content,
            assistant_tool_calls,
        )
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

    def _ollama_messages(self, messages=None):
        converted = []
        source_messages = messages if messages is not None else self.conversation_history
        for message in source_messages:
            role = message.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                continue

            converted_message = {
                "role": role,
                "content": self._message_content_text_for_ollama(
                    message.get("content", "")
                ),
            }
            if role == "assistant":
                thinking = message.get("thinking")
                if thinking:
                    converted_message["thinking"] = thinking
                tool_calls = self._ollama_normalized_tool_calls(
                    message.get("tool_calls") or []
                )
                if tool_calls:
                    converted_message["tool_calls"] = tool_calls
            elif role == "tool":
                tool_name = message.get("tool_name") or message.get("name") or ""
                if tool_name:
                    converted_message["tool_name"] = tool_name
            converted.append(converted_message)
        return converted

    def _chat_agent_messages(self):
        return [{"role": "system", "content": AGENT_SYSTEM_PROMPT}] + self.conversation_history

    def _ollama_agent_messages(self):
        return self._ollama_messages(
            [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
            + self.conversation_history
        )

    def _chat_completion_kwargs(
        self,
        model=None,
        messages=None,
        temperature=None,
        max_tokens=None,
        stream=False,
        tools=None,
        include_reasoning=True,
    ):
        kwargs = {
            "model": model or self.model,
            "messages": messages if messages is not None else self.conversation_history,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if self.api_type == API_TYPE_GLM and include_reasoning:
            kwargs["thinking"] = {"type": "enabled"} if self.thinking_mode else {}
        elif include_reasoning and self._uses_minimax_openai_compat(model):
            kwargs["extra_body"] = {"reasoning_split": True}
        if stream:
            kwargs["stream"] = True
        if tools is not None:
            kwargs["tools"] = tools
        return kwargs

    def _ollama_chat_kwargs(
        self,
        model=None,
        messages=None,
        temperature=None,
        max_tokens=None,
        stream=False,
        tools=None,
        include_reasoning=True,
    ):
        options = {
            "temperature": self.temperature if temperature is None else temperature,
            "num_predict": self.max_tokens if max_tokens is None else max_tokens,
        }
        kwargs = {
            "model": model or self.model,
            "messages": self._ollama_messages(messages),
            "options": options,
            "think": self._ollama_think_value(model, include_reasoning),
        }
        if stream:
            kwargs["stream"] = True
        if tools is not None:
            kwargs["tools"] = tools
        return kwargs

    def _chat_tool_schemas(self):
        if self.api_type == API_TYPE_OPENAI:
            return openai_tool_schemas()
        return glm_tool_schemas()

    def _chat_tool_result_message(self, tool_call_id, name, content):
        message = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }
        if self.api_type == API_TYPE_GLM and name:
            message["name"] = name
        return message

    @staticmethod
    def _ollama_tool_result_message(name, content):
        return {
            "role": "tool",
            "tool_name": name,
            "content": content,
        }

    def _chat_stream_assistant_message(self, content, thinking):
        message = {"role": "assistant", "content": content}
        if self._uses_minimax_openai_compat() and thinking:
            message["reasoning_details"] = [{"text": thinking}]
        return message

    @staticmethod
    def _ollama_assistant_message(content, thinking="", tool_calls=None):
        message = {"role": "assistant", "content": content}
        if thinking:
            message["thinking"] = thinking
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    def _ollama_think_value(self, model=None, include_reasoning=True):
        if not include_reasoning:
            return False
        if not self.thinking_mode:
            return False
        model_name = str(model or self.model or "").lower()
        if "gpt-oss" in model_name:
            return "medium"
        return True

    def _message_content_text_for_ollama(self, content):
        if isinstance(content, str):
            return content
        if isinstance(content, (list, dict)):
            return clean_display_text(content)
        return str(content or "")

    def _ollama_normalized_tool_calls(self, tool_calls):
        normalized = []
        for index, call in enumerate(tool_calls or []):
            function = self._get_field(call, "function", {}) or {}
            name = self._get_field(function, "name", "") or ""
            arguments = self._parse_tool_arguments(
                self._get_field(function, "arguments", {}) or {}
            )
            function_call = {
                "name": name,
                "arguments": arguments,
            }
            raw_index = self._get_field(function, "index", None)
            if raw_index is not None:
                function_call["index"] = raw_index
            elif len(tool_calls) > 1:
                function_call["index"] = index
            normalized.append(
                {
                    "type": self._get_field(call, "type", "function") or "function",
                    "function": function_call,
                }
            )
        return normalized

    def _uses_minimax_openai_compat(self, model=None):
        if self.api_type != API_TYPE_OPENAI:
            return False
        base_url = str(self.base_url or "").lower()
        model_name = str(model or self.model or "").lower()
        return "minimax" in base_url or "minimaxi" in base_url or model_name.startswith("minimax")

    def _message_reasoning_text(self, message):
        reasoning_content = self._get_field(message, "reasoning_content", "") or ""
        reasoning_details = self._reasoning_details_text(
            self._get_field(message, "reasoning_details", None)
        )
        reasoning = self._get_field(message, "reasoning", "") or ""
        return _clean_reasoning_text(reasoning_content or reasoning_details or reasoning)

    def _message_content_text(self, content):
        if self._uses_minimax_openai_compat():
            return _clean_content_text(content)
        return str(content or "")

    def _stream_content_delta(self, content, current_response, raw_response):
        if not self._uses_minimax_openai_compat():
            delta, current_response = self._split_stream_delta(current_response, content)
            return delta, current_response, raw_response

        delta, raw_response = self._split_stream_delta(raw_response, content)
        if not delta and not content:
            return "", current_response, raw_response
        clean_response = _clean_content_text(raw_response)
        clean_delta, clean_response = self._split_stream_delta(current_response, clean_response)
        return clean_delta, clean_response, raw_response

    def _stream_reasoning_delta(self, delta, current_thinking, raw_thinking):
        reasoning = (
            self._get_field(delta, "reasoning_content", "")
            or self._get_field(delta, "reasoning", "")
            or ""
        )
        if reasoning:
            raw_thinking += reasoning
            clean_thinking = _clean_reasoning_text(raw_thinking)
            clean_delta, clean_thinking = self._split_stream_delta(current_thinking, clean_thinking)
            return clean_delta, clean_thinking, raw_thinking

        reasoning_details = self._reasoning_details_text(
            self._get_field(delta, "reasoning_details", None)
        )
        if reasoning_details:
            if raw_thinking and reasoning_details.startswith(raw_thinking):
                raw_thinking = reasoning_details
            else:
                raw_thinking += reasoning_details
            clean_thinking = _clean_reasoning_text(raw_thinking)
            clean_delta, clean_thinking = self._split_stream_delta(current_thinking, clean_thinking)
            return clean_delta, clean_thinking, raw_thinking
        return "", current_thinking, raw_thinking

    def _reasoning_details_text(self, details):
        if not details:
            return ""
        if isinstance(details, str):
            return details
        if isinstance(details, dict):
            return details.get("text") or details.get("content") or ""
        if isinstance(details, (list, tuple)):
            return "".join(self._reasoning_details_text(detail) for detail in details)

        text = self._get_field(details, "text", None)
        if text is not None:
            return str(text)
        content = self._get_field(details, "content", None)
        if content is not None:
            return str(content)
        return ""

    @staticmethod
    def _split_stream_delta(current_text, next_text):
        next_text = str(next_text or "")
        if not next_text:
            return "", current_text
        if current_text and next_text.startswith(current_text):
            return next_text[len(current_text) :], next_text
        return next_text, current_text + next_text

    def _plain_data(self, value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [self._plain_data(item) for item in value]
        if isinstance(value, tuple):
            return [self._plain_data(item) for item in value]
        if isinstance(value, dict):
            return {key: self._plain_data(item) for key, item in value.items()}
        if hasattr(value, "model_dump"):
            return self._plain_data(value.model_dump(exclude_none=True))
        if hasattr(value, "to_dict"):
            return self._plain_data(value.to_dict())
        return value

    @staticmethod
    def _get_field(item, field, default=None):
        if isinstance(item, dict):
            return item.get(field, default)
        return getattr(item, field, default)

    def clear_history(self):
        self.conversation_history = []

    def get_history(self):
        return self.conversation_history


def _summarize_tool_input(tool_input):
    if isinstance(tool_input, str):
        text = tool_input
    else:
        try:
            text = json.dumps(tool_input, ensure_ascii=False)
        except TypeError:
            text = str(tool_input)
    return _single_line(text, 280)


def _summarize_tool_result(tool_result):
    if not tool_result:
        return "(empty)"
    return _single_line(str(tool_result).splitlines()[0], 220)


def _compact_tool_result_for_context(tool_result):
    text = str(tool_result or "")
    if len(text) <= AGENT_TOOL_RESULT_CONTEXT_CHARS:
        return text

    head_chars = AGENT_TOOL_RESULT_CONTEXT_CHARS * 2 // 3
    tail_chars = AGENT_TOOL_RESULT_CONTEXT_CHARS - head_chars - 300
    omitted = len(text) - head_chars - tail_chars
    if tail_chars < 0:
        tail_chars = 0
    return (
        text[:head_chars]
        + f"\n\n[tool result compacted by client: {omitted} characters omitted]\n\n"
        + (text[-tail_chars:] if tail_chars else "")
    )


def _estimate_history_chars(history):
    total = 0
    for message in history:
        try:
            total += len(json.dumps(message, ensure_ascii=False, default=str))
        except TypeError:
            total += len(str(message))
    return total


def _clean_reasoning_text(content):
    text = str(content or "")
    text = re.sub(r"<\s*/?\s*think\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/?\s*(?:t|th|thi|thin|think)?\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _clean_content_text(content):
    text = str(content or "")
    text = re.sub(r"<\s*think\s*>.*?<\s*/\s*think\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<\s*think\s*>.*$", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<\s*/\s*think\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/?\s*(?:t|th|thi|thin|think)?\s*$", "", text, flags=re.IGNORECASE)
    return text


def _summarize_agent_thinking(content):
    text = _strip_code_from_thinking(content)
    if not text:
        return "Working out the next agent step."

    sentence = _pick_thinking_summary_sentence(text)
    return _single_line_unlimited(sentence)


def _summary_model_prompt(content):
    text = _summary_model_source_text(content)
    if not text:
        return ""
    return (
        "Summarize this agent thinking into one very short terminal status sentence. "
        "Keep it as short as possible while still being useful.\n\n"
        f"{_single_line(text, 4000)}"
    )


def _summary_model_source_text(content):
    text = str(content or "")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = text.replace("`", "")

    kept_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _looks_like_code_line(stripped):
            continue
        stripped = _strip_list_marker(stripped)
        if stripped:
            kept_lines.append(stripped)
    return "\n".join(kept_lines)


def _clean_summary_stream_delta(delta):
    text = str(delta or "")
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.replace("`", "")
    return text


def _strip_code_from_thinking(content):
    text = str(content or "")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)

    kept_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _looks_like_code_line(stripped):
            continue
        stripped = _strip_list_marker(stripped)
        if stripped:
            kept_lines.append(stripped)
    return "\n".join(kept_lines)


def _looks_like_code_line(line):
    code_prefixes = (
        "{",
        "}",
        "[",
        "]",
        "(",
        ")",
        "<",
        ">",
        "+",
        "-",
        "*",
        "#",
        "//",
        "/*",
        "*/",
        "def ",
        "class ",
        "import ",
        "from ",
        "return ",
        "if ",
        "elif ",
        "else:",
        "for ",
        "while ",
        "try:",
        "except ",
        "with ",
        "async ",
        "await ",
        "const ",
        "let ",
        "var ",
        "function ",
    )
    if line.startswith(code_prefixes):
        return True

    code_marks = sum(
        line.count(mark)
        for mark in ("=", "{", "}", "(", ")", "[", "]", ";", "=>", "::", "</", "/>")
    )
    if code_marks >= 5:
        return True

    if len(line) > 160 and code_marks >= 3:
        return True
    return False


def _pick_thinking_summary_sentence(text):
    sentences = _summary_sentence_candidates(text)
    if not sentences:
        return text

    intent_pattern = re.compile(
        r"("
        r"need|plan|check|inspect|read|search|find|edit|update|modify|implement|"
        r"verify|test|compare|decide|ensure|confirm|analy[sz]e|定位|检查|读取|"
        r"搜索|查找|修改|更新|实现|验证|测试|确认|分析|计划|整理"
        r")",
        re.IGNORECASE,
    )
    for sentence in sentences:
        if intent_pattern.search(sentence):
            return _finish_summary_sentence(sentence)
    return _finish_summary_sentence(sentences[0])


def _summary_sentence_candidates(text):
    raw_sentences = re.split(r"(?<=[!?。！？])\s*|(?<=\.)\s+|\n+", text)
    sentences = []
    for raw_sentence in raw_sentences:
        sentence = _clean_summary_sentence(raw_sentence)
        if sentence:
            sentences.append(sentence)
    return sentences


def _clean_summary_sentence(sentence):
    sentence = _strip_list_marker(str(sentence or ""))
    sentence = re.sub(r"[:：]\s*(?:\d+|[A-Za-z]|[一二三四五六七八九十]+)[.)、]?\s*$", "", sentence)
    sentence = sentence.strip(" \t\r\n-:;：")
    if not sentence or re.fullmatch(r"(?:\d+|[A-Za-z]|[一二三四五六七八九十]+)[.)、]?", sentence):
        return ""
    return sentence


def _strip_list_marker(text):
    return re.sub(
        r"^\s*(?:\d+|[A-Za-z]|[一二三四五六七八九十]+)[.)、]\s*",
        "",
        str(text or "").strip(),
    )


def _finish_summary_sentence(sentence):
    sentence = str(sentence or "").strip()
    if not sentence or sentence[-1] in ".!?。！？":
        return sentence
    if re.search(r"[\u4e00-\u9fff]", sentence):
        return sentence + "。"
    return sentence + "."


def _single_line(text, max_chars):
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _single_line_unlimited(text):
    return " ".join(str(text or "").split())


def _error_text(message):
    return f"ERROR: {message}"

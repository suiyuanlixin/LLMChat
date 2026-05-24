import json
import re
import threading
import time
from pathlib import Path

from rich.cells import cell_len

from ui import (
    clear_current_lines,
    clean_and_print_stream_response,
    clean_display_text,
    console,
    print_error,
    print_info,
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
from memory import MemoryStore, parse_memory_update_response
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
COMPACTION_MAX_TOKENS = 2048
MEMORY_UPDATE_MAX_TOKENS = 4096
COMPACTION_SUMMARY_PREFIX = "[Compressed conversation summary for continuity]"
USER_PROMPT_FILE = "prompt.md"
USER_PROMPT_MAX_CHARS = 20000
USER_PROMPT_TEMPLATE = """<!--
在这里写你的自定义提示词、人格、回复风格和偏好。
默认模板内容不会发送给模型；请把真实提示词写在注释外。

示例：
- 默认使用中文回答。
- 回答尽量简洁，优先给出可执行结论。
- 修改代码时保持项目现有风格，避免无关重构。
-->
"""
USER_PROMPT_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
AGENT_SUMMARY_SYSTEM_PROMPT = (
    "把 agent 的内部思考压成一句很短的终端状态。"
    "优先中文，除非输入明显要求其他语言。"
    "不要 markdown、代码、引号或前缀。只说正在做什么。"
)


NORMAL_SYSTEM_PROMPT = (
    "You are the built-in assistant for the LLMChat project, a terminal LLM "
    "chat client. Help the user discuss, understand, configure, and improve "
    "this project."
)


AGENT_PROJECT_PROMPT = """You are the built-in local file-editing agent for the LLMChat project, a terminal LLM chat client. Help the user inspect and modify this project safely."""


AGENT_SYSTEM_PROMPT = f"""{AGENT_PROJECT_PROMPT}

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


COMPACTION_SYSTEM_PROMPT = """你负责压缩聊天上下文。
只返回连续性摘要。保留目标、偏好、约束、决定、项目事实、错误、验证、待办和仍有用的旧摘要。
遵循持久记忆，尤其语言偏好。不要编造。简洁。"""


MEMORY_UPDATE_SYSTEM_PROMPT = """你负责更新持久记忆。
只返回 JSON。遵循持久记忆和偏好记忆，尤其语言偏好。事实准确；情景记忆可以有人情味，但不能编造。"""


def _ensure_user_prompt_file():
    prompt_path = Path(USER_PROMPT_FILE)
    if prompt_path.exists():
        return

    try:
        prompt_path.write_text(USER_PROMPT_TEMPLATE, encoding="utf-8")
    except OSError as error:
        print_warn(f"Failed to create {USER_PROMPT_FILE}: {error}")


def _read_user_custom_prompt():
    prompt_path = Path(USER_PROMPT_FILE)
    if not prompt_path.is_file():
        return ""

    try:
        content = prompt_path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        print_warn(f"Failed to read {USER_PROMPT_FILE}: {error}")
        return ""

    content = USER_PROMPT_COMMENT_PATTERN.sub("", content).strip()
    if not content:
        return ""
    if len(content) <= USER_PROMPT_MAX_CHARS:
        return content
    return (
        content[:USER_PROMPT_MAX_CHARS]
        + f"\n\n[{USER_PROMPT_FILE} truncated after {USER_PROMPT_MAX_CHARS} characters]"
    )


def _with_user_custom_prompt(base_prompt):
    custom_prompt = _read_user_custom_prompt()
    if not custom_prompt:
        return base_prompt
    return (
        f"{base_prompt}\n\n"
        f"User custom instructions from {USER_PROMPT_FILE}:\n"
        f"{custom_prompt}"
    )


def _with_persistent_memory(base_prompt, memory_store):
    if memory_store is None:
        return base_prompt
    try:
        memory_block = memory_store.system_prompt_block()
    except Exception as error:
        print_warn(f"Failed to read persistent memory: {error}")
        return base_prompt
    if not memory_block:
        return base_prompt
    return f"{base_prompt}\n\n{memory_block}"


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
        compaction_enable=True,
        compaction_max_chars=60000,
        compaction_keep_recent_messages=12,
        compaction_compact_model="",
    ):
        _ensure_user_prompt_file()
        self.memory_store = MemoryStore()
        self.memory_lock = threading.Lock()
        self.first_episodic_memory_pending = False
        self.conversation_history = []
        self.client = None
        self.thinking_mode = thinking_mode
        self.set_compaction_config(
            compaction_enable,
            compaction_max_chars,
            compaction_keep_recent_messages,
            compaction_compact_model,
        )
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

    def set_compaction_config(
        self,
        enabled=None,
        max_chars=None,
        keep_recent_messages=None,
        compact_model=None,
    ):
        if enabled is not None:
            self.compaction_enable = bool(enabled)
        if max_chars is not None:
            self.compaction_max_chars = max(1, int(max_chars))
        if keep_recent_messages is not None:
            self.compaction_keep_recent_messages = max(1, int(keep_recent_messages))
        if compact_model is not None:
            self.compaction_compact_model = str(compact_model or "").strip()

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
        original_history = self._history_snapshot()
        self.conversation_history.append({"role": "user", "content": user_message})
        self._record_preference_signal(user_message)

        try:
            self._auto_compact_context()
            self._sanitize_orphan_tool_results_in_history()
            user_message_index = len(self.conversation_history) - 1

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
                    system=self._normal_system_prompt(),
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
                self._restore_history(original_history)
            elif response.get("agent_stopped"):
                self._restore_history(original_history)
            elif self.agent_mode:
                self._compact_agent_history(user_message_index, response)
            return response

        except KeyboardInterrupt:
            if self.agent_running:
                self.request_agent_stop()
                self._restore_history(original_history)
                self._separate_after_agent_thinking()
                print_warn("Agent stopped by user.")
                return {"thinking": "", "response": "Agent stopped by user.", "agent_stopped": True}
            raise
        except Exception as error:
            self._restore_history(original_history)
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
            system=self._agent_system_prompt(),
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

    def _auto_compact_context(self):
        if not self.compaction_enable:
            return {"compacted": False, "reason": "Context compaction is disabled."}

        estimated_chars = _estimate_history_chars(self.conversation_history)
        if estimated_chars < self.compaction_max_chars:
            return {
                "compacted": False,
                "reason": "Context is below the compaction threshold.",
                "before_chars": estimated_chars,
            }

        result = self.compact_context(manual=False)
        if result.get("compacted"):
            print_info(
                "Context compacted automatically: "
                f"{result.get('before_messages')} -> {result.get('after_messages')} messages."
            )
            self._print_memory_update_result(result.get("memory_update"))
        elif result.get("error"):
            print_warn(result.get("reason", "Automatic context compaction failed."))
        return result

    def compact_context(self, manual=False):
        before_messages = len(self.conversation_history)
        before_chars = _estimate_history_chars(self.conversation_history)
        keep_recent = max(1, int(self.compaction_keep_recent_messages))
        compact_model = self.compaction_compact_model or self.model

        if before_messages <= keep_recent:
            return {
                "compacted": False,
                "reason": (
                    "Context compaction cancelled: "
                    f"{before_messages} messages is not more than keep_recent_messages={keep_recent}."
                ),
                "before_messages": before_messages,
                "before_chars": before_chars,
                "model": compact_model,
            }

        recent_messages = self.conversation_history[-keep_recent:]
        source_messages = self.conversation_history[:-keep_recent]
        existing_summary, source_messages = self._split_existing_compaction_summary(source_messages)
        source_messages, recent_messages = self._fold_leading_tool_results_into_source(
            source_messages,
            recent_messages,
        )
        if not source_messages and not existing_summary:
            return {
                "compacted": False,
                "reason": "Context compaction cancelled: no messages older than the recent window.",
                "before_messages": before_messages,
                "before_chars": before_chars,
                "model": compact_model,
            }

        try:
            summary = self._create_compaction_summary(
                existing_summary,
                source_messages,
                compact_model,
            )
        except Exception as error:
            return {
                "compacted": False,
                "error": True,
                "reason": f"Context compaction failed: {error}",
                "before_messages": before_messages,
                "before_chars": before_chars,
                "model": compact_model,
            }

        summary = summary.strip()
        if not summary:
            return {
                "compacted": False,
                "error": True,
                "reason": "Context compaction failed: compact model returned an empty summary.",
                "before_messages": before_messages,
                "before_chars": before_chars,
                "model": compact_model,
            }

        memory_update = self._schedule_memory_update_from_compaction(
            summary,
            source_messages,
            compact_model,
        )
        self.conversation_history = [
            {"role": "user", "content": self._compaction_summary_message(summary)},
            *recent_messages,
        ]
        removed_tool_results = self._sanitize_orphan_tool_results_in_history()
        after_chars = _estimate_history_chars(self.conversation_history)
        return {
            "compacted": True,
            "manual": manual,
            "before_messages": before_messages,
            "after_messages": len(self.conversation_history),
            "before_chars": before_chars,
            "after_chars": after_chars,
            "model": compact_model,
            "removed_orphan_tool_results": removed_tool_results,
            "memory_update": memory_update,
        }

    def _create_compaction_summary(self, existing_summary, source_messages, compact_model):
        prompt = self._compaction_prompt(existing_summary, source_messages)
        system_prompt = _with_persistent_memory(
            COMPACTION_SYSTEM_PROMPT,
            self.memory_store,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        temperature = min(float(self.temperature), 0.2)

        if self.api_type == API_TYPE_ANTHROPIC:
            response = self.client.messages.create(
                model=compact_model,
                max_tokens=COMPACTION_MAX_TOKENS,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            return self._anthropic_response_text(response)

        if self.api_type == API_TYPE_OLLAMA:
            response = self.client.chat(
                **self._ollama_chat_kwargs(
                    model=compact_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=COMPACTION_MAX_TOKENS,
                    include_reasoning=False,
                )
            )
            message = self._get_field(response, "message", {})
            return str(self._get_field(message, "content", "") or "")

        response = self.client.chat.completions.create(
            **self._chat_completion_kwargs(
                model=compact_model,
                messages=messages,
                temperature=temperature,
                max_tokens=COMPACTION_MAX_TOKENS,
                include_reasoning=False,
            )
        )
        message = response.choices[0].message
        return _clean_content_text(self._get_field(message, "content", "") or "")

    def _schedule_memory_update_from_compaction(self, summary, source_messages, compact_model):
        compacted_messages = self._format_messages_for_compaction(source_messages)
        self._start_memory_background_task(
            self._update_memory_from_compaction,
            summary,
            compacted_messages,
            compact_model,
        )
        return {"changed": [], "scheduled": True}

    def _update_memory_from_compaction(self, summary, compacted_messages, compact_model):
        try:
            prompt = self.memory_store.build_update_prompt(compacted_messages, summary)
            raw_update = self._create_memory_update(prompt, compact_model)
            update = parse_memory_update_response(raw_update)
            if not update:
                return {
                    "changed": [],
                    "error": "memory update model returned no parseable JSON",
                }
            with self.memory_lock:
                return self.memory_store.apply_update(update)
        except Exception as error:
            return {"changed": [], "error": str(error)}

    def write_first_episodic_memory_if_needed(self):
        with self.memory_lock:
            if self.memory_store.read_episodic_text():
                return {"changed": [], "reason": "Episodic memory already has entries."}
            if self.first_episodic_memory_pending:
                return {"changed": [], "reason": "First episodic memory is already pending."}

            messages = self._last_completed_dialogue_messages()
            if not messages:
                return {"changed": [], "reason": "No completed dialogue turn to remember."}

            self.first_episodic_memory_pending = True

        formatted_messages = self._format_messages_for_compaction(messages)
        self._start_memory_background_task(
            self._write_first_episodic_memory,
            formatted_messages,
        )
        return {"changed": [], "scheduled": True}

    def _write_first_episodic_memory(self, formatted_messages):
        try:
            prompt = self.memory_store.build_first_episodic_prompt(formatted_messages)
            compact_model = self.compaction_compact_model or self.model
            raw_update = self._create_memory_update(prompt, compact_model)
            update = parse_memory_update_response(raw_update)
            episodic_entry = ""
            if isinstance(update, dict):
                episodic_entry = update.get("episodic_entry", update.get("episodic", ""))
            if not str(episodic_entry or "").strip():
                return {
                    "changed": [],
                    "error": "first episodic memory model returned no episodic_entry",
                }

            with self.memory_lock:
                if self.memory_store.read_episodic_text():
                    return {"changed": [], "reason": "Episodic memory already has entries."}
                if self.memory_store.append_first_episodic_entry(episodic_entry):
                    return {"changed": ["episodic"]}
                return {"changed": []}
        except Exception as error:
            return {"changed": [], "error": str(error)}
        finally:
            with self.memory_lock:
                self.first_episodic_memory_pending = False

    def _start_memory_background_task(self, target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def _last_completed_dialogue_messages(self):
        last_assistant_index = None
        for index in range(len(self.conversation_history) - 1, -1, -1):
            if self.conversation_history[index].get("role") == "assistant":
                last_assistant_index = index
                break
        if last_assistant_index is None:
            return []

        start_index = None
        for index in range(last_assistant_index - 1, -1, -1):
            if self.conversation_history[index].get("role") == "user":
                start_index = index
                break
        if start_index is None:
            return []

        return self.conversation_history[start_index : last_assistant_index + 1]

    def _create_memory_update(self, prompt, compact_model):
        system_prompt = _with_persistent_memory(
            MEMORY_UPDATE_SYSTEM_PROMPT,
            self.memory_store,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        temperature = min(float(self.temperature), 0.2)

        if self.api_type == API_TYPE_ANTHROPIC:
            response = self.client.messages.create(
                model=compact_model,
                max_tokens=MEMORY_UPDATE_MAX_TOKENS,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            return self._anthropic_response_text(response)

        if self.api_type == API_TYPE_OLLAMA:
            response = self.client.chat(
                **self._ollama_chat_kwargs(
                    model=compact_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=MEMORY_UPDATE_MAX_TOKENS,
                    include_reasoning=False,
                )
            )
            message = self._get_field(response, "message", {})
            return str(self._get_field(message, "content", "") or "")

        response = self.client.chat.completions.create(
            **self._chat_completion_kwargs(
                model=compact_model,
                messages=messages,
                temperature=temperature,
                max_tokens=MEMORY_UPDATE_MAX_TOKENS,
                include_reasoning=False,
            )
        )
        message = response.choices[0].message
        return _clean_content_text(self._get_field(message, "content", "") or "")

    def _print_memory_update_result(self, memory_update):
        if not memory_update:
            return
        changed = memory_update.get("changed") or []
        if changed:
            print_info("Persistent memory updated: " + ", ".join(changed) + ".")
        elif memory_update.get("error"):
            print_warn(f"Persistent memory update failed: {memory_update.get('error')}")

    def _record_preference_signal(self, user_message):
        try:
            self.memory_store.record_preference_signal(user_message)
        except Exception as error:
            print_warn(f"Failed to record preference memory: {error}")

    def _compaction_prompt(self, existing_summary, source_messages):
        existing_summary = str(existing_summary or "").strip()
        compacted_messages = self._format_messages_for_compaction(source_messages)
        if not compacted_messages:
            compacted_messages = "(No additional messages.)"
        if not existing_summary:
            existing_summary = "(No existing summary.)"

        return (
            "为后续对话更新压缩摘要。遵循持久记忆和用户偏好，尤其语言偏好。只返回摘要。\n\n"
            "已有摘要：\n"
            f"{existing_summary}\n\n"
            "需要合并的消息：\n"
            f"{compacted_messages}\n\n"
            "只返回更新后的压缩摘要。"
        )

    def _format_messages_for_compaction(self, messages):
        formatted = []
        for index, message in enumerate(messages, start=1):
            role = message.get("role", "unknown")
            content = self._message_content_for_compaction(message)
            formatted.append(f"[{index}] {role}:\n{content}")
        return "\n\n".join(formatted)

    def _message_content_for_compaction(self, message):
        parts = []
        content = message.get("content", "")
        if content:
            parts.append(self._plain_text_for_compaction(content))
        tool_calls = message.get("tool_calls")
        if tool_calls:
            parts.append(
                "tool_calls: "
                + json.dumps(self._plain_data(tool_calls), ensure_ascii=False)
            )
        tool_name = message.get("tool_name") or message.get("name")
        if tool_name:
            parts.append(f"tool_name: {tool_name}")
        return "\n".join(parts).strip() or "(empty)"

    def _plain_text_for_compaction(self, content):
        if isinstance(content, str):
            return content
        if isinstance(content, (list, dict)):
            try:
                return json.dumps(content, ensure_ascii=False, default=str)
            except TypeError:
                return clean_display_text(content)
        return str(content or "")

    def _split_existing_compaction_summary(self, messages):
        if not messages:
            return "", []
        first_message = messages[0]
        content = str(first_message.get("content", "") or "")
        if (
            first_message.get("role") == "user"
            and content.startswith(COMPACTION_SUMMARY_PREFIX)
        ):
            summary = content[len(COMPACTION_SUMMARY_PREFIX) :].strip()
            return summary, messages[1:]
        return "", messages

    def _fold_leading_tool_results_into_source(self, source_messages, recent_messages):
        source_messages = list(source_messages)
        recent_messages = list(recent_messages)
        while recent_messages and self._message_has_tool_result(recent_messages[0]):
            source_messages.append(recent_messages.pop(0))
        return source_messages, recent_messages

    def _message_has_tool_result(self, message):
        if message.get("role") == "tool":
            return True
        content = message.get("content")
        if not isinstance(content, list):
            return False
        return any(self._get_field(block, "type") == "tool_result" for block in content)

    @staticmethod
    def _compaction_summary_message(summary):
        return f"{COMPACTION_SUMMARY_PREFIX}\n{summary.strip()}"

    def _sanitize_orphan_tool_results_in_history(self):
        available_tool_ids = set()
        cleaned = []
        removed_count = 0

        for message in self.conversation_history:
            filtered_message, removed, consumed_tool_ids = self._filter_orphan_tool_results(
                message,
                available_tool_ids,
            )
            removed_count += removed
            if filtered_message is None:
                continue

            cleaned.append(filtered_message)
            available_tool_ids.update(self._message_tool_use_ids(filtered_message))
            for tool_id in consumed_tool_ids:
                available_tool_ids.discard(tool_id)

        if removed_count:
            self.conversation_history = cleaned
        return removed_count

    def _filter_orphan_tool_results(self, message, available_tool_ids):
        role = message.get("role")
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            if tool_call_id and tool_call_id not in available_tool_ids:
                return None, 1, set()
            return message, 0, {tool_call_id} if tool_call_id else set()

        content = message.get("content")
        if not isinstance(content, list):
            return message, 0, set()

        filtered_content = []
        removed_count = 0
        consumed_tool_ids = set()
        for block in content:
            if self._get_field(block, "type") != "tool_result":
                filtered_content.append(block)
                continue

            tool_use_id = str(self._get_field(block, "tool_use_id", "") or "")
            if tool_use_id and tool_use_id not in available_tool_ids:
                removed_count += 1
                continue

            filtered_content.append(block)
            if tool_use_id:
                consumed_tool_ids.add(tool_use_id)

        if not removed_count:
            return message, 0, consumed_tool_ids
        if not filtered_content:
            return None, removed_count, consumed_tool_ids

        filtered_message = dict(message)
        filtered_message["content"] = filtered_content
        return filtered_message, removed_count, consumed_tool_ids

    def _message_tool_use_ids(self, message):
        tool_ids = []
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if self._get_field(block, "type") == "tool_use":
                    tool_id = str(self._get_field(block, "id", "") or "")
                    if tool_id:
                        tool_ids.append(tool_id)

        for tool_call in message.get("tool_calls") or []:
            tool_id = str(self._get_field(tool_call, "id", "") or "")
            if tool_id:
                tool_ids.append(tool_id)
        return tool_ids

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

    def _history_snapshot(self):
        return [dict(message) for message in self.conversation_history]

    def _restore_history(self, history):
        self.conversation_history = [dict(message) for message in history]

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
                system=self._normal_system_prompt(),
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
            full_thinking, full_response = self._anthropic_response_parts(response)

            self.conversation_history.append({"role": "assistant", "content": full_response})
            return {"thinking": full_thinking, "response": full_response}
        except (AttributeError, TypeError) as error:
            print_error(f"Failed to parse response: {error}")
            return None

    def _anthropic_response_text(self, response):
        _, text = self._anthropic_response_parts(response)
        return text

    def _anthropic_response_parts(self, response):
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
        return full_thinking, full_response

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
        return [{"role": "system", "content": self._agent_system_prompt()}] + self.conversation_history

    def _chat_messages(self, messages=None):
        source_messages = messages if messages is not None else self.conversation_history
        if source_messages and source_messages[0].get("role") == "system":
            return source_messages
        return [{"role": "system", "content": self._normal_system_prompt()}] + source_messages

    def _ollama_agent_messages(self):
        return self._ollama_messages(
            [{"role": "system", "content": self._agent_system_prompt()}]
            + self.conversation_history
        )

    def _ollama_normal_messages(self, messages=None):
        source_messages = messages if messages is not None else self.conversation_history
        if source_messages and source_messages[0].get("role") == "system":
            return self._ollama_messages(source_messages)
        return self._ollama_messages(
            [{"role": "system", "content": self._normal_system_prompt()}] + source_messages
        )

    def _normal_system_prompt(self):
        return _with_user_custom_prompt(
            _with_persistent_memory(NORMAL_SYSTEM_PROMPT, self.memory_store)
        )

    def _agent_system_prompt(self):
        return _with_user_custom_prompt(
            _with_persistent_memory(AGENT_SYSTEM_PROMPT, self.memory_store)
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
            "messages": self._chat_messages(messages),
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
            "messages": self._ollama_normal_messages(messages),
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
        return "整理下一步。"

    sentence = _pick_thinking_summary_sentence(text)
    return _single_line_unlimited(sentence)


def _summary_model_prompt(content):
    text = _summary_model_source_text(content)
    if not text:
        return ""
    return (
        "把这段 agent 思考压成一句很短的终端状态，优先中文，越短越好。\n\n"
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

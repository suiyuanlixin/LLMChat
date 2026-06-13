from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

TEAM_STORE_DIR = ".omniagent" / Path("team")

DEFAULT_TEAMMATE_MAX_TURNS = 12
DEFAULT_TEAMMATE_TOOL_CALL_FACTOR = 4

FORBIDDEN_TEAM_TOOL_NAMES = {
    "dispatch_subagent",
    "spawn_teammate",
    "update_plan",
    "ask_user",
}

SPAWN_TEAMMATE_TOOL_NAME = "spawn_teammate"
LIST_TEAMMATES_TOOL_NAME = "list_teammates"
SEND_MESSAGE_TOOL_NAME = "send_message"
READ_INBOX_TOOL_NAME = "read_inbox"
BROADCAST_TOOL_NAME = "broadcast"
SHUTDOWN_TEAMMATE_TOOL_NAME = "shutdown_teammate"
TEAM_TOOL_NAMES = {
    SPAWN_TEAMMATE_TOOL_NAME,
    LIST_TEAMMATES_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
    READ_INBOX_TOOL_NAME,
    BROADCAST_TOOL_NAME,
    SHUTDOWN_TEAMMATE_TOOL_NAME,
}


@dataclass(frozen=True)
class TeammateSpec:
    name: str
    role: str
    description: str
    system_prompt: str
    tool_names: tuple[str, ...] = field(default_factory=tuple)
    max_turns: int = DEFAULT_TEAMMATE_MAX_TURNS


_BUILTIN_TEAMMATES: dict[str, dict[str, Any]] = {
    "architect": {
        "role": "System Architect",
        "description": (
            "Software architecture expert. Best for system design review, "
            "technology stack decisions, API design, data modeling, and "
            "architecture validation."
        ),
        "tool_names": (
            "read_file",
            "list_dir",
            "grep",
            "glob",
            "git_status",
            "git_diff",
            "web_fetch",
            "web_search",
            "list_skills",
            "read_skill",
        ),
        "max_turns": 10,
        "system_prompt": (
            "You are a system architect teammate in OmniAgent's team.\n"
            "- Analyze the codebase and provide architecture insights.\n"
            "- Recommend design patterns, technology choices, and structural improvements.\n"
            "- Identify architectural risks, bottlenecks, and technical debt.\n"
            "- Use read-only tools to inspect the codebase.\n"
            "- Do not edit files, run mutating commands, or dispatch subagents.\n"
            "- Reply concisely with: analysis, recommendation, risks, and next steps."
        ),
    },
    "reviewer": {
        "role": "Code Reviewer",
        "description": (
            "Code quality reviewer. Best for code review, style checking, "
            "bug detection, security audit, and test coverage analysis."
        ),
        "tool_names": (
            "read_file",
            "list_dir",
            "grep",
            "glob",
            "bash",
            "git_status",
            "git_diff",
            "list_skills",
            "read_skill",
        ),
        "max_turns": 12,
        "system_prompt": (
            "You are a code reviewer teammate in OmniAgent's team.\n"
            "- Review code for correctness, style, security, and performance.\n"
            "- Run safe diagnostic commands (no installs, no file mutations).\n"
            "- Identify bugs, anti-patterns, and improvement opportunities.\n"
            "- Cross-reference changes with existing code conventions.\n"
            "- Do not edit files, run mutating commands, or dispatch subagents.\n"
            "- Reply concisely with: findings, severity, evidence, and fix suggestions."
        ),
    },
    "devops": {
        "role": "DevOps Engineer",
        "description": (
            "DevOps and infrastructure specialist. Best for CI/CD configuration, "
            "Docker/containerization, deployment scripts, environment setup, "
            "and build system optimization."
        ),
        "tool_names": (
            "read_file",
            "list_dir",
            "grep",
            "glob",
            "bash",
            "web_fetch",
            "web_search",
            "git_status",
            "git_diff",
            "list_skills",
            "read_skill",
            "write_file",
            "edit_file",
            "apply_patch",
            "apply_unified_patch",
        ),
        "max_turns": 14,
        "system_prompt": (
            "You are a DevOps teammate in OmniAgent's team.\n"
            "- Handle CI/CD, Docker, deployment, and infrastructure tasks.\n"
            "- Read existing configuration before making changes.\n"
            "- Follow security best practices for credentials and secrets.\n"
            "- Write operations require approval unless auto mode is enabled.\n"
            "- Do not dispatch subagents or spawn teammates.\n"
            "- Reply concisely with: actions taken, evidence, risks, and next steps."
        ),
    },
    "debugger": {
        "role": "Debug Specialist",
        "description": (
            "Debugging and troubleshooting expert. Best for error diagnosis, "
            "log analysis, runtime issue investigation, and root cause analysis."
        ),
        "tool_names": (
            "read_file",
            "list_dir",
            "grep",
            "glob",
            "bash",
            "web_fetch",
            "web_search",
            "git_status",
            "git_diff",
            "list_skills",
            "read_skill",
        ),
        "max_turns": 15,
        "system_prompt": (
            "You are a debug specialist teammate in OmniAgent's team.\n"
            "- Analyze errors, logs, and test failures systematically.\n"
            "- Trace issues through the codebase to find root causes.\n"
            "- Run safe diagnostic commands (no installs, no file mutations).\n"
            "- Suggest fixes with reasoning and evidence.\n"
            "- Do not edit files, run mutating commands, or dispatch subagents.\n"
            "- Reply concisely with: diagnosis, root cause, evidence, fix suggestion."
        ),
    },
}

_TEAMMATE_ALIASES = {
    "arch": "architect",
    "rev": "reviewer",
    "ops": "devops",
    "dbg": "debugger",
}


class TeamStore:
    def __init__(
        self,
        workspace_dir: str | Path | None = None,
        templates_dir: str | Path | None = None,
    ):
        self.workspace_dir = Path(workspace_dir) if workspace_dir else None
        self.templates_dir = Path(templates_dir) if templates_dir else None
        self._specs: dict[str, TeammateSpec] = {}
        self._load_builtin_specs()

    @property
    def team_dir(self) -> Path | None:
        if self.workspace_dir is None:
            return None
        return self.workspace_dir / TEAM_STORE_DIR

    @property
    def config_path(self) -> Path | None:
        td = self.team_dir
        return td / "config.json" if td else None

    @property
    def inbox_dir(self) -> Path | None:
        td = self.team_dir
        return td / "inbox" if td else None

    @property
    def threads_dir(self) -> Path | None:
        td = self.team_dir
        return td / "threads" if td else None

    def _load_builtin_specs(self) -> None:
        for name, config in _BUILTIN_TEAMMATES.items():
            tool_names = tuple(config["tool_names"])
            forbidden = sorted(set(tool_names) & FORBIDDEN_TEAM_TOOL_NAMES)
            if forbidden:
                raise ValueError(
                    f"Teammate '{name}' includes forbidden tools: {', '.join(forbidden)}"
                )
            self._specs[name] = TeammateSpec(
                name=name,
                role=config["role"],
                description=config["description"],
                system_prompt=self._template_prompt(name, config["system_prompt"]),
                tool_names=tool_names,
                max_turns=int(config["max_turns"]),
            )

    def _template_prompt(self, name: str, fallback: str) -> str:
        templates_dir = self.templates_dir
        if templates_dir is None and self.workspace_dir is not None:
            templates_dir = self.workspace_dir / ".omniagent" / "team"
        if templates_dir is None:
            return fallback
        template_path = templates_dir / f"{name}.md"
        if not template_path.is_file():
            return fallback
        try:
            content = template_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return fallback
        content = content.strip()
        return content or fallback

    def resolve_name(self, name: str) -> str:
        key = str(name or "").strip().lower()
        return _TEAMMATE_ALIASES.get(key, key)

    def get_spec(self, name: str) -> TeammateSpec | None:
        return self._specs.get(self.resolve_name(name))

    def names(self, *, include_aliases: bool = False) -> list[str]:
        result = set(self._specs.keys())
        if include_aliases:
            result.update(_TEAMMATE_ALIASES.keys())
        return sorted(result)

    def describe(self) -> str:
        lines = [
            f"- {spec.name} ({spec.role}): {spec.description}"
            for spec in self._specs.values()
        ]
        if _TEAMMATE_ALIASES:
            aliases = ", ".join(
                f"{a} -> {t}" for a, t in sorted(_TEAMMATE_ALIASES.items())
            )
            lines.append(f"- aliases: {aliases}")
        return "\n".join(lines)

    def ensure_dirs(self) -> None:
        if self.team_dir is None:
            return
        self.team_dir.mkdir(parents=True, exist_ok=True)
        if self.inbox_dir:
            self.inbox_dir.mkdir(parents=True, exist_ok=True)
        if self.threads_dir:
            self.threads_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> dict[str, Any]:
        cp = self.config_path
        if cp is None or not cp.is_file():
            return {}
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_config(self, data: dict[str, Any]) -> None:
        cp = self.config_path
        if cp is None:
            return
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get_roster(self) -> list[dict[str, Any]]:
        config = self.load_config()
        return config.get("teammates", [])

    def is_active(self, name: str) -> bool:
        roster = self.get_roster()
        resolved = self.resolve_name(name)
        return any(t.get("name") == resolved for t in roster)

    def add_teammate(self, name: str) -> dict[str, Any]:
        resolved = self.resolve_name(name)
        spec = self.get_spec(resolved)
        if spec is None:
            raise ValueError(f"Unknown teammate type: {name!r}")
        config = self.load_config()
        teammates = config.get("teammates", [])
        existing = [t for t in teammates if t.get("name") == resolved]
        if existing:
            existing[0]["status"] = "active"
            self.save_config(config)
            return existing[0]
        entry = {
            "name": resolved,
            "role": spec.role,
            "status": "active",
            "created_at": _now_iso(),
            "task_count": 0,
        }
        teammates.append(entry)
        config["teammates"] = teammates
        self.save_config(config)
        return entry

    def remove_teammate(self, name: str) -> bool:
        resolved = self.resolve_name(name)
        config = self.load_config()
        teammates = config.get("teammates", [])
        new_list = [t for t in teammates if t.get("name") != resolved]
        if len(new_list) == len(teammates):
            return False
        config["teammates"] = new_list
        self.save_config(config)
        return True

    def update_status(self, name: str, status: str, task_count: int = 0) -> None:
        resolved = self.resolve_name(name)
        config = self.load_config()
        teammates = config.get("teammates", [])
        for t in teammates:
            if t.get("name") == resolved:
                t["status"] = status
                t["task_count"] = t.get("task_count", 0) + task_count
                break
        self.save_config(config)

    def send_message(self, from_name: str, to_name: str, content: str) -> str:
        self.ensure_dirs()
        resolved_to = self.resolve_name(to_name)
        inbox_path = self.inbox_dir / f"{resolved_to}.jsonl"
        entry = {
            "from": from_name or "lead",
            "content": content,
            "timestamp": _now_iso(),
        }
        with open(str(inbox_path), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return f"Message sent to teammate '{resolved_to}'."

    def read_inbox(self, name: str, clear: bool = False) -> list[dict[str, Any]]:
        self.ensure_dirs()
        resolved = self.resolve_name(name) if name else "lead"
        inbox_path = self.inbox_dir / f"{resolved}.jsonl"
        if not inbox_path.is_file():
            return []
        try:
            lines = inbox_path.read_text(encoding="utf-8").strip().splitlines()
            messages = [json.loads(line) for line in lines if line.strip()]
        except (json.JSONDecodeError, OSError):
            return []
        if clear and messages:
            inbox_path.write_text("", encoding="utf-8")
        return messages

    def broadcast(
        self, from_name: str, content: str, teammate_names: list[str] | None = None
    ) -> str:
        config = self.load_config()
        teammates = config.get("teammates", [])
        if teammate_names:
            resolved = {self.resolve_name(n) for n in teammate_names}
            targets = [t for t in teammates if t.get("name") in resolved]
        else:
            targets = teammates
        if not targets:
            return "No active teammates to broadcast to."
        sent = []
        for t in targets:
            self.send_message(from_name, t["name"], content)
            sent.append(t["name"])
        return f"Broadcast sent to: {', '.join(sent)}."

    def save_thread(self, name: str, messages: list[dict[str, Any]]) -> None:
        self.ensure_dirs()
        resolved = self.resolve_name(name)
        thread_path = self.threads_dir / f"{resolved}.jsonl"
        with open(str(thread_path), "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def load_thread(self, name: str) -> list[dict[str, Any]]:
        resolved = self.resolve_name(name)
        thread_path = self.threads_dir / f"{resolved}.jsonl"
        if not thread_path.is_file():
            return []
        try:
            lines = thread_path.read_text(encoding="utf-8").strip().splitlines()
            return [json.loads(line) for line in lines if line.strip()]
        except (json.JSONDecodeError, OSError):
            return []


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat()


class TeamRunner:
    def __init__(
        self,
        parent_agent: Any,
        spec: TeammateSpec,
        tool_schemas: list[dict[str, Any]],
        execute_tool: Callable[[str, dict[str, Any]], str],
        compact_tool_result: Callable[[str], str] | None = None,
        team_store: TeamStore | None = None,
        api_type: str = "anthropic",
        max_tool_calls: int | None = None,
    ):
        self.parent = parent_agent
        self.spec = spec
        self.tool_schemas = tool_schemas
        self.execute_tool = execute_tool
        self.compact_tool_result = compact_tool_result or (lambda value: value)
        self.team_store = team_store
        self.api_type = str(api_type or "anthropic")
        self.max_tool_calls = max(
            1,
            int(max_tool_calls or spec.max_turns * DEFAULT_TEAMMATE_TOOL_CALL_FACTOR),
        )
        self.tool_calls_used = 0
        self.allowed_tool_names = set(spec.tool_names)

    def run(self, task: str) -> str:
        history: list[dict[str, Any]] = [{"role": "user", "content": task}]
        final_response = ""

        for _round_index in range(1, self.spec.max_turns + 1):
            if self.api_type == "anthropic":
                assistant_message, _thinking, text, tool_calls = self._anthropic_turn(
                    history
                )
            elif self.api_type == "ollama":
                assistant_message, _thinking, text, tool_calls = self._ollama_turn(
                    history
                )
            else:
                assistant_message, _thinking, text, tool_calls = self._chat_turn(
                    history
                )

            history.append(assistant_message)
            final_response += text

            if not tool_calls:
                return final_response.strip() or (
                    f"Teammate '{self.spec.name}' completed without a text summary."
                )

            if self.tool_calls_used + len(tool_calls) > self.max_tool_calls:
                return (
                    f"Teammate '{self.spec.name}' stopped after "
                    f"{self.max_tool_calls} tool calls."
                )

            self._append_tool_results(history, tool_calls)

        return (
            f"Teammate '{self.spec.name}' stopped after "
            f"{self.spec.max_turns} tool rounds."
        )

    def _chat_turn(self, history: list[dict[str, Any]]):
        response = self.parent.client.chat.completions.create(
            **self.parent._chat_completion_kwargs(
                messages=self._messages(history),
                tools=self.tool_schemas,
            )
        )
        message = response.choices[0].message
        return self.parent._chat_message_parts(message)

    def _ollama_turn(self, history: list[dict[str, Any]]):
        response = self.parent.client.chat(
            **self.parent._ollama_chat_kwargs(
                messages=self._messages(history),
                tools=self.tool_schemas,
            )
        )
        message = self.parent._get_field(response, "message", {})
        return self.parent._ollama_message_parts(message)

    def _anthropic_turn(self, history: list[dict[str, Any]]):
        blocks = []
        active_block_index = None
        response = self.parent.client.messages.create(
            model=self.parent.model,
            max_tokens=self.parent.max_tokens,
            temperature=self.parent.temperature,
            messages=self._anthropic_messages(history),
            system=self.spec.system_prompt,
            tools=self.tool_schemas,
            stream=True,
            **self.parent._anthropic_request_options(),
        )

        for chunk in response:
            chunk_type = self.parent._get_field(chunk, "type", "")
            if chunk_type == "content_block_start":
                content_block = self.parent._get_field(chunk, "content_block")
                block_type = self.parent._get_field(content_block, "type", "")
                initial_reasoning = self.parent._anthropic_reasoning_text(
                    content_block
                )
                if block_type == "text":
                    if initial_reasoning:
                        blocks.append({
                            "type": "thinking",
                            "thinking": initial_reasoning,
                        })
                    block = {"type": "text", "text": ""}
                elif (
                    self.parent._is_anthropic_reasoning_block_type(block_type)
                    or initial_reasoning
                ):
                    block = {"type": "thinking", "thinking": initial_reasoning}
                elif block_type == "tool_use":
                    block = {
                        "type": "tool_use",
                        "id": self.parent._get_field(content_block, "id", "") or "",
                        "name": self.parent._get_field(content_block, "name", "") or "",
                        "input": {},
                        "_input_json": "",
                    }
                else:
                    block = {"type": block_type or "unknown"}
                blocks.append(block)
                active_block_index = len(blocks) - 1
                continue

            if chunk_type == "content_block_delta" and active_block_index is not None:
                delta = self.parent._get_field(chunk, "delta")
                delta_type = self.parent._get_field(delta, "type", "")
                block = blocks[active_block_index]
                if delta_type == "text_delta":
                    block["text"] = block.get("text", "") + (
                        self.parent._get_field(delta, "text", "") or ""
                    )
                elif self.parent._is_anthropic_reasoning_delta_type(delta_type):
                    block["thinking"] = block.get(
                        "thinking", ""
                    ) + self.parent._anthropic_delta_reasoning_text(delta)
                elif delta_type == "signature_delta":
                    block["signature"] = block.get("signature", "") + (
                        self.parent._get_field(delta, "signature", "") or ""
                    )
                elif delta_type == "input_json_delta":
                    block["_input_json"] = block.get("_input_json", "") + (
                        self.parent._get_field(delta, "partial_json", "") or ""
                    )
                continue

            if chunk_type == "content_block_stop" and active_block_index is not None:
                block = blocks[active_block_index]
                if block.get("type") == "tool_use":
                    raw_input = block.pop("_input_json", "")
                    if raw_input:
                        block["input"] = self.parent._parse_tool_arguments(raw_input)
                active_block_index = None

        for block in blocks:
            block.pop("_input_json", None)
        thinking, text, tool_uses = self.parent._parse_anthropic_blocks(blocks)
        return {"role": "assistant", "content": blocks}, thinking, text, tool_uses

    def _append_tool_results(
        self,
        history: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
    ) -> None:
        if self.api_type == "anthropic":
            results = []
            for tool_call in tool_calls:
                result = self._run_tool_call(
                    tool_call.get("name", ""),
                    tool_call.get("input", {}),
                )
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.get("id", ""),
                    "content": result,
                    "is_error": result.startswith("ERROR:"),
                })
            history.append({"role": "user", "content": results})
            return

        for tool_call in tool_calls:
            result = self._run_tool_call(
                tool_call.get("name", ""),
                tool_call.get("arguments", {}),
            )
            if self.api_type == "ollama":
                history.append(
                    self.parent._ollama_tool_result_message(
                        tool_call.get("name", ""), result
                    )
                )
            else:
                history.append(
                    self.parent._chat_tool_result_message(
                        tool_call.get("id", ""),
                        tool_call.get("name", ""),
                        result,
                    )
                )

    def _run_tool_call(self, name: str, arguments: dict[str, Any]) -> str:
        self.tool_calls_used += 1
        if name in FORBIDDEN_TEAM_TOOL_NAMES or name in TEAM_TOOL_NAMES:
            return f"Team tool '{name}' is not available to teammates."
        if name not in self.allowed_tool_names:
            return f"Tool '{name}' is not available to teammate '{self.spec.name}'."
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        result = self.execute_tool(name, arguments)
        return self.compact_tool_result(str(result or ""))

    def _messages(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self.spec.system_prompt}, *history]

    @staticmethod
    def _anthropic_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages = []
        for message in history:
            role = message.get("role")
            if role in {"user", "assistant"}:
                messages.append({"role": role, "content": message.get("content", "")})
        return messages


def compose_teammate_task(
    task: str,
    *,
    expected_output: str | None = None,
    evidence_required: str | None = None,
    scope_limit: str | None = None,
) -> str:
    contract = []
    if expected_output:
        contract.append(f"- Expected output: {expected_output}")
    if evidence_required:
        contract.append(f"- Evidence required: {evidence_required}")
    if scope_limit:
        contract.append(f"- Scope limit: {scope_limit}")
    contract.append(
        "- Final reply must include: conclusion, evidence, risks, and suggested next step."
    )
    return f"{str(task or '').rstrip()}\n\nTeammate contract:\n" + "\n".join(contract)

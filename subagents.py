from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable


DISPATCH_SUBAGENT_TOOL_NAME = "dispatch_subagent"
API_TYPE_ANTHROPIC = "anthropic"
API_TYPE_OLLAMA = "ollama"
FORBIDDEN_SUBAGENT_TOOL_NAMES = {
    DISPATCH_SUBAGENT_TOOL_NAME,
    "update_plan",
    "ask_user",
}
DEFAULT_SUBAGENT_TOOL_CALL_FACTOR = 4
WORKSPACE_SUBAGENTS_RELATIVE_DIR = Path(".omniagent") / "subagents"


@dataclass(frozen=True)
class SubagentSpec:
    name: str
    description: str
    system_prompt: str
    tool_names: tuple[str, ...] = field(default_factory=tuple)
    max_turns: int = 12


_BUILTIN_SPECS: dict[str, dict[str, Any]] = {
    "reader": {
        "description": (
            "Read-only code and document reader. Best for inspecting files, "
            "summarizing structure, and gathering local facts."
        ),
        "tool_names": (
            "read_file",
            "list_dir",
            "grep",
            "glob",
            "git_status",
            "git_diff",
            "list_skills",
            "read_skill",
        ),
        "max_turns": 10,
        "system_prompt": (
            "You are a focused read-only subagent for OmniAgent.\n"
            "- Complete exactly the task delegated by the main agent.\n"
            "- Use local read-only tools to gather evidence.\n"
            "- Do not edit files, run mutating commands, ask the user, or dispatch other subagents.\n"
            "- Final reply must be concise and include: conclusion, evidence, risks, and suggested next step."
        ),
    },
    "researcher": {
        "description": (
            "External and cross-source researcher. Best for reading URLs, optional web search, "
            "and comparing outside facts with local files."
        ),
        "tool_names": (
            "read_file",
            "list_dir",
            "grep",
            "glob",
            "web_fetch",
            "web_search",
            "list_skills",
            "read_skill",
        ),
        "max_turns": 12,
        "system_prompt": (
            "You are a research subagent for OmniAgent.\n"
            "- Use web_fetch for specific URLs and web_search only when current/external facts are needed.\n"
            "- Keep source URLs and local file paths in the evidence.\n"
            "- Do not edit files, ask the user, or dispatch other subagents.\n"
            "- Final reply must be concise and include: conclusion, evidence, risks, and suggested next step."
        ),
    },
    "auditor": {
        "description": (
            "Read-only verifier. Best for checking diffs, running safe diagnostics, "
            "spotting omissions, and validating completion evidence."
        ),
        "tool_names": (
            "read_file",
            "list_dir",
            "grep",
            "glob",
            "bash",
            "local_http_check",
            "git_status",
            "git_diff",
            "list_skills",
            "read_skill",
        ),
        "max_turns": 12,
        "system_prompt": (
            "You are an audit subagent for OmniAgent.\n"
            "- Verify claims with read-only inspection, git status/diff, and safe commands that exit.\n"
            "- Do not edit files, ask the user, or dispatch other subagents.\n"
            "- If a command could mutate files or start a foreground server, do not run it.\n"
            "- Final reply must be concise and include: conclusion, evidence, risks, and suggested next step."
        ),
    },
    "builder": {
        "description": (
            "Implementation subagent. Best for scoped edits, local checks, and small build/test tasks; "
            "write actions still follow the main approval mode."
        ),
        "tool_names": (
            "read_file",
            "list_dir",
            "grep",
            "glob",
            "web_fetch",
            "web_search",
            "bash",
            "local_http_check",
            "git_status",
            "git_diff",
            "list_skills",
            "read_skill",
            "write_file",
            "edit_file",
            "apply_patch",
            "apply_unified_patch",
        ),
        "max_turns": 16,
        "system_prompt": (
            "You are an implementation subagent for OmniAgent.\n"
            "- Make only the scoped change requested by the main agent.\n"
            "- Prefer small, targeted edits and inspect relevant files before editing.\n"
            "- Write operations and risky commands remain subject to the main approval mode.\n"
            "- Do not ask the user, update the main plan, or dispatch other subagents.\n"
            "- Final reply must be concise and include: conclusion, evidence, risks, and suggested next step."
        ),
    },
}

_ALIASES = {
    "general": "builder",
    "investigator": "researcher",
}


class SubagentRegistry:
    def __init__(
        self,
        workspace_dir: str | Path | None = None,
        templates_dir: str | Path | None = None,
        skills_summary_provider: Callable[[], str] | None = None,
    ):
        self._skills_summary_provider = skills_summary_provider
        self.workspace_dir = Path(workspace_dir) if workspace_dir else None
        self.templates_dir = Path(templates_dir) if templates_dir else None
        self._specs: dict[str, SubagentSpec] = {}
        self._ensure_templates_dir()
        self._load_builtin_specs()

    def configure(
        self,
        workspace_dir: str | Path | None = None,
        templates_dir: str | Path | None = None,
    ) -> None:
        self.workspace_dir = Path(workspace_dir) if workspace_dir else None
        self.templates_dir = Path(templates_dir) if templates_dir else None
        self._specs = {}
        self._ensure_templates_dir()
        self._load_builtin_specs()

    @property
    def effective_templates_dir(self) -> Path | None:
        if self.templates_dir is not None:
            return self.templates_dir
        if self.workspace_dir is None:
            return None
        return self.workspace_dir / WORKSPACE_SUBAGENTS_RELATIVE_DIR

    def _ensure_templates_dir(self) -> None:
        templates_dir = self.effective_templates_dir
        if templates_dir is None:
            return
        try:
            templates_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _load_builtin_specs(self) -> None:
        for name, config in _BUILTIN_SPECS.items():
            tool_names = tuple(config["tool_names"])
            forbidden = sorted(set(tool_names) & FORBIDDEN_SUBAGENT_TOOL_NAMES)
            if forbidden:
                raise ValueError(
                    f"Subagent '{name}' includes forbidden tools: {', '.join(forbidden)}"
                )
            self._specs[name] = SubagentSpec(
                name=name,
                description=config["description"],
                system_prompt=self._template_prompt(name, config["system_prompt"]),
                tool_names=tool_names,
                max_turns=int(config["max_turns"]),
            )

    def _template_prompt(self, name: str, fallback: str) -> str:
        templates_dir = self.effective_templates_dir
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
        return _ALIASES.get(key, key)

    def get(self, name: str) -> SubagentSpec | None:
        spec = self._specs.get(self.resolve_name(name))
        if spec is None:
            return None
        return self._with_skills_summary(spec)

    def names(self, *, include_aliases: bool = False) -> list[str]:
        names = set(self._specs.keys())
        if include_aliases:
            names.update(_ALIASES.keys())
        return sorted(names)

    def aliases(self) -> dict[str, str]:
        return dict(_ALIASES)

    def describe(self) -> str:
        lines = [
            f"- {spec.name}: {spec.description}" for spec in self._specs.values()
        ]
        if _ALIASES:
            aliases = ", ".join(
                f"{alias} -> {target}" for alias, target in sorted(_ALIASES.items())
            )
            lines.append(f"- aliases: {aliases}")
        return "\n".join(lines)

    def _with_skills_summary(self, spec: SubagentSpec) -> SubagentSpec:
        if not self._skills_summary_provider:
            return spec
        if not {"list_skills", "read_skill"} & set(spec.tool_names):
            return spec
        summary = str(self._skills_summary_provider() or "").strip()
        if not summary:
            return spec
        prompt = (
            f"{spec.system_prompt}\n\n"
            "Available skills can be discovered with list_skills and read with read_skill.\n"
            f"{summary}"
        )
        return replace(spec, system_prompt=prompt)


def compose_subagent_task(
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
    return f"{str(task or '').rstrip()}\n\nSubagent contract:\n" + "\n".join(contract)


def tool_definition_name(definition: dict[str, Any]) -> str:
    function = definition.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(definition.get("name") or "")


def filter_tool_definitions(
    definitions: list[dict[str, Any]],
    allowed_tool_names: set[str] | tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    allowed = set(allowed_tool_names)
    return [
        definition
        for definition in definitions
        if tool_definition_name(definition) in allowed
    ]


class SubagentRunner:
    def __init__(
        self,
        parent_agent: Any,
        spec: SubagentSpec,
        tool_schemas: list[dict[str, Any]],
        execute_tool: Callable[[str, dict[str, Any]], str],
        compact_tool_result: Callable[[str], str] | None = None,
        max_tool_calls: int | None = None,
    ):
        self.parent = parent_agent
        self.spec = spec
        self.tool_schemas = tool_schemas
        self.execute_tool = execute_tool
        self.compact_tool_result = compact_tool_result or (lambda value: value)
        self.max_tool_calls = max(
            1,
            int(max_tool_calls or spec.max_turns * DEFAULT_SUBAGENT_TOOL_CALL_FACTOR),
        )
        self.tool_calls_used = 0
        self.allowed_tool_names = set(spec.tool_names)

    def run(self, task: str) -> str:
        history: list[dict[str, Any]] = [{"role": "user", "content": task}]
        final_response = ""

        for _round_index in range(1, self.spec.max_turns + 1):
            if self.parent.api_type == API_TYPE_ANTHROPIC:
                assistant_message, _thinking, text, tool_calls = self._anthropic_turn(
                    history
                )
            elif self.parent.api_type == API_TYPE_OLLAMA:
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
                    f"Subagent '{self.spec.name}' completed without a text summary."
                )

            if self.tool_calls_used + len(tool_calls) > self.max_tool_calls:
                return (
                    f"ERROR: Subagent '{self.spec.name}' stopped after "
                    f"{self.max_tool_calls} tool calls."
                )

            self._append_tool_results(history, tool_calls)

        return (
            f"ERROR: Subagent '{self.spec.name}' stopped after "
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
        if self.parent.api_type == API_TYPE_ANTHROPIC:
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
            if self.parent.api_type == API_TYPE_OLLAMA:
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
        if name in FORBIDDEN_SUBAGENT_TOOL_NAMES or name not in self.allowed_tool_names:
            return f"ERROR: Tool '{name}' is not available to subagent '{self.spec.name}'."
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

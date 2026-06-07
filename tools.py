import fnmatch
import difflib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from planning import TodoStore
from skills import SkillRegistry
from ui import (
    get_agent_confirmation,
    get_agent_diff_confirmation,
    get_agent_plan_confirmation,
)
from search import (
    DEFAULT_WEB_SEARCH_DEPTH,
    DEFAULT_WEB_SEARCH_ENABLE,
    DEFAULT_WEB_SEARCH_MAX_RESULTS,
    DEFAULT_WEB_SEARCH_PROVIDER,
    DEFAULT_WEB_SEARCH_TOPIC,
    is_web_search_configured,
    normalize_tavily_search_depth,
    normalize_tavily_topic,
    normalize_web_search_provider,
    search_tavily,
)


MAX_READ_CHARS = 60000
MAX_TOOL_OUTPUT_CHARS = 20000
MAX_GREP_MATCHES = 200
MAX_GLOB_MATCHES = 500
MAX_LIST_ENTRIES = 300
COMMAND_TIMEOUT_SECONDS = 60
GIT_TIMEOUT_SECONDS = 30
AGENT_APPROVAL_CONFIRM = "confirm"
AGENT_APPROVAL_AUTO = "auto"

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
}

TOOL_DEFINITIONS = [
    {
        "name": "update_todos",
        "description": (
            "Replace the current task plan with a full todo list. "
            "Use this for multi-step agent work. Supports dependencies, priorities, "
            "completion criteria, and blocked/failed states. Keep existing approved "
            "todo ids until they are completed, blocked, or failed. At most one item "
            "may be in_progress."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": (
                        "The complete current todo list. Preserve existing approved "
                        "todos in this array until they are completed, blocked, or failed. "
                        "Use an empty array only when intentionally clearing an inactive plan."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Short task description.",
                            },
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                    "blocked",
                                    "failed",
                                ],
                                "description": (
                                    "Current task state. blocked/failed require reason."
                                ),
                            },
                            "id": {
                                "type": "string",
                                "description": (
                                    "Stable id for dependencies, such as inspect, implement, verify."
                                ),
                            },
                            "priority": {
                                "type": "string",
                                "enum": [
                                    "p0",
                                    "p1",
                                    "p2",
                                    "p3",
                                    "high",
                                    "medium",
                                    "low",
                                ],
                                "description": (
                                    "Task priority. p0 is urgent, p1 high, p2 normal, p3 low."
                                ),
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Todo ids that must be completed before this task can be "
                                    "in_progress or completed."
                                ),
                            },
                            "completion_criteria": {
                                "type": "array",
                                "items": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {
                                            "type": "object",
                                            "properties": {
                                                "id": {
                                                    "type": "string",
                                                    "description": "Optional stable criterion id.",
                                                },
                                                "type": {
                                                    "type": "string",
                                                    "enum": [
                                                        "build",
                                                        "command",
                                                        "diff_check",
                                                        "file_change",
                                                        "file_exists",
                                                        "lint",
                                                        "manual",
                                                        "review",
                                                        "test",
                                                        "tool_output",
                                                    ],
                                                    "description": "Kind of evidence required.",
                                                },
                                                "target": {
                                                    "type": "string",
                                                    "description": (
                                                        "Command, file, diff check, or tool output "
                                                        "that should prove the condition."
                                                    ),
                                                },
                                                "expected": {
                                                    "type": "string",
                                                    "description": (
                                                        "Observable expected result, such as an exit "
                                                        "code, file state, or output phrase."
                                                    ),
                                                },
                                            },
                                        },
                                    ]
                                },
                                "description": (
                                    "Observable conditions required before this todo may be "
                                    "considered done. Prefer structured objects with type, "
                                    "target, and expected."
                                ),
                            },
                            "reason": {
                                "type": "string",
                                "description": (
                                    "Required when status is blocked or failed. Explain the blocker "
                                    "or failure plainly."
                                ),
                            },
                            "verified": {
                                "type": "boolean",
                                "description": (
                                    "Whether the completion criteria were verified by tool output. "
                                    "Use only with completed todos."
                                ),
                            },
                            "verification_note": {
                                "type": "string",
                                "description": (
                                    "Short evidence summary when verified is true, such as the "
                                    "test or diff check that passed."
                                ),
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the configured workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path or workspace-relative path to the file.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional 1-based first line to read.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional 1-based final line to read.",
                },
                "line_numbers": {
                    "type": "boolean",
                    "description": "Whether to prefix returned lines with line numbers. Defaults to true.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and directories under a workspace path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional workspace-relative or absolute directory path. Defaults to workspace root.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to include nested entries. Defaults to false.",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum recursive directory depth. Defaults to 2.",
                },
            },
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a UTF-8 text file in the configured workspace. Shows a unified diff before writing unless auto approval is enabled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path or workspace-relative path to write.",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write.",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit a text file by replacing an exact string. Shows a unified diff before writing unless auto approval is enabled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path or workspace-relative path to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to replace.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every occurrence. Defaults to false for safer single edits.",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "apply_patch",
        "description": "Replace a 1-based inclusive line range in a text file. Shows a unified diff before writing unless auto approval is enabled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path or workspace-relative path to edit.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-based first line to replace.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "1-based final line to replace.",
                },
                "new_content": {
                    "type": "string",
                    "description": "Replacement content for the selected line range. Empty string deletes the range.",
                },
            },
            "required": ["file_path", "start_line", "end_line", "new_content"],
        },
    },
    {
        "name": "apply_unified_patch",
        "description": "Apply a unified diff patch to one UTF-8 text file. Validates context lines and shows the resulting diff unless auto approval is enabled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path or workspace-relative path to edit.",
                },
                "patch": {
                    "type": "string",
                    "description": "Unified diff for this file, including @@ hunk headers and +/-/space lines.",
                },
            },
            "required": ["file_path", "patch"],
        },
    },
    {
        "name": "bash",
        "description": (
            "Run a shell command inside the configured workspace. The command must exit; "
            "do not start foreground dev/static servers. Commands with obvious file writes "
            "or deletes require user confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command to run from the workspace directory.",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "local_http_check",
        "description": (
            "Start a temporary Python static HTTP server inside the workspace, request one "
            "or more local paths, then terminate the server before returning. Use this for "
            "static-site todos like 'start static service + curl check 200' instead of "
            "running a foreground server with bash."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "root": {
                    "type": "string",
                    "description": "Workspace-relative directory to serve. Defaults to the workspace root.",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URL paths to request, such as ['/', '/settings']. Defaults to ['/'].",
                },
                "expected_status": {
                    "type": "integer",
                    "description": "Expected HTTP status for every path. Defaults to 200.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Total startup/request timeout in seconds, between 1 and 60. Defaults to 10.",
                },
            },
        },
    },
    {
        "name": "git_status",
        "description": "Show the workspace git status in short format. Read-only and does not require confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "git_diff",
        "description": "Show git diff output, diff stat, or diff whitespace checks for the workspace. Read-only and does not require confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Optional workspace-relative or absolute file path to limit the diff.",
                },
                "cached": {
                    "type": "boolean",
                    "description": "Show staged changes instead of unstaged changes. Defaults to false.",
                },
                "stat": {
                    "type": "boolean",
                    "description": "Return diff statistics instead of the full patch. Defaults to false.",
                },
                "check": {
                    "type": "boolean",
                    "description": "Run git diff --check to find whitespace/conflict-marker issues. Defaults to false.",
                },
            },
        },
    },
    {
        "name": "grep",
        "description": "Search workspace files with a regular expression.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Python regular expression to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional workspace-relative or absolute directory/file path to search.",
                },
                "include": {
                    "type": "string",
                    "description": "Optional filename glob such as *.py.",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether matching is case-sensitive. Defaults to false.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "glob",
        "description": "Find files in the workspace by glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Workspace-relative glob pattern, for example **/*.py.",
                }
            },
            "required": ["pattern"],
        },
    },
]


WEB_SEARCH_TOOL_DEFINITION = {
    "name": "web_search",
    "description": (
        "Search the public web with Tavily for current or external information. "
        "Use it for recent facts, releases, prices, laws, schedules, and source-backed answers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The web search query.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of search results to return. Defaults to the app setting.",
            },
            "search_depth": {
                "type": "string",
                "enum": ["basic", "fast", "ultra-fast", "advanced"],
                "description": "Tavily search depth. basic/fast/ultra-fast cost 1 credit; advanced costs 2.",
            },
            "topic": {
                "type": "string",
                "enum": ["general", "news", "finance"],
                "description": "Search topic. Use news for current events and finance for market-related queries.",
            },
            "time_range": {
                "type": "string",
                "enum": ["day", "week", "month", "year", "d", "w", "m", "y"],
                "description": "Optional recency filter.",
            },
            "include_answer": {
                "type": "boolean",
                "description": "Whether Tavily should include its generated answer. Defaults to false.",
            },
            "include_raw_content": {
                "type": "boolean",
                "description": "Whether Tavily should include parsed page content. Use sparingly.",
            },
            "include_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional domains to include.",
            },
            "exclude_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional domains to exclude.",
            },
            "country": {
                "type": "string",
                "description": "Optional country boost for general search, such as united states or china.",
            },
        },
        "required": ["query"],
    },
}


SKILL_TOOL_DEFINITIONS = [
    {
        "name": "list_skills",
        "description": "List reusable agent skills available from enabled skill sources.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "read_skill",
        "description": (
            "Read a skill's SKILL.md instructions and optionally additional files from that skill directory. "
            "Call this before following a matching skill workflow."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name, such as git-commit.",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional skill-relative files to read after SKILL.md.",
                },
            },
            "required": ["name"],
        },
    },
]


def tool_definitions(include_web_search=False, include_skills=False):
    definitions = list(TOOL_DEFINITIONS)
    if include_skills:
        definitions.extend(SKILL_TOOL_DEFINITIONS)
    if include_web_search:
        definitions.append(WEB_SEARCH_TOOL_DEFINITION)
    return definitions


def anthropic_tool_schemas(include_web_search=False, include_skills=False):
    return tool_definitions(include_web_search, include_skills)


def glm_tool_schemas(include_web_search=False, include_skills=False):
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in tool_definitions(include_web_search, include_skills)
    ]


def openai_tool_schemas(include_web_search=False, include_skills=False):
    return glm_tool_schemas(include_web_search, include_skills)


def ollama_tool_schemas(include_web_search=False, include_skills=False):
    return glm_tool_schemas(include_web_search, include_skills)


class AgentToolError(Exception):
    pass


class AgentTools:
    def __init__(
        self,
        workspace_dir=None,
        approval_mode=AGENT_APPROVAL_CONFIRM,
        visible_output_callback=None,
        web_search_enabled=DEFAULT_WEB_SEARCH_ENABLE,
        web_search_provider=DEFAULT_WEB_SEARCH_PROVIDER,
        web_search_api_key="",
        web_search_max_results=DEFAULT_WEB_SEARCH_MAX_RESULTS,
        web_search_depth=DEFAULT_WEB_SEARCH_DEPTH,
        web_search_topic=DEFAULT_WEB_SEARCH_TOPIC,
        todo_update_callback=None,
        plan_approval_output_callback=None,
        skills_enabled=True,
        skills_app_enabled=True,
        skills_workspace_enabled=False,
        skills_auto_catalog=True,
        skills_max_chars=12000,
    ):
        self.workspace_dir = normalize_workspace_dir(workspace_dir)
        self.visible_output_callback = visible_output_callback
        self.plan_approval_output_callback = plan_approval_output_callback
        self.todo_store = TodoStore(
            on_change=todo_update_callback,
            plan_dir=_plan_dir_for_workspace(self.workspace_dir),
        )
        self.max_tool_calls = None
        self.used_tool_calls = 0
        self.skill_registry = SkillRegistry(
            enabled=skills_enabled,
            app_enabled=skills_app_enabled,
            workspace_enabled=skills_workspace_enabled,
            workspace_dir=self.workspace_dir,
            auto_catalog=skills_auto_catalog,
            max_chars=skills_max_chars,
        )
        self.set_approval_mode(approval_mode)
        self.set_web_search_config(
            web_search_enabled,
            web_search_provider,
            web_search_api_key,
            web_search_max_results,
            web_search_depth,
            web_search_topic,
        )
        self.begin_agent_session(clear_todos=False)

    @property
    def enabled(self):
        return self.workspace_dir is not None

    def set_workspace_dir(self, workspace_dir):
        self.workspace_dir = normalize_workspace_dir(workspace_dir)
        self.todo_store.set_plan_dir(
            _plan_dir_for_workspace(self.workspace_dir),
            load=True,
        )
        self.skill_registry.configure(workspace_dir=self.workspace_dir)

    def set_approval_mode(self, approval_mode):
        mode = str(approval_mode or AGENT_APPROVAL_CONFIRM).strip().lower()
        if mode not in {AGENT_APPROVAL_CONFIRM, AGENT_APPROVAL_AUTO}:
            mode = AGENT_APPROVAL_CONFIRM
        self.approval_mode = mode

    def set_visible_output_callback(self, callback):
        self.visible_output_callback = callback

    def set_todo_update_callback(self, callback):
        self.todo_store.set_on_change(callback)

    def set_budget_context(self, max_tool_calls=None, used_tool_calls=0):
        if max_tool_calls is None:
            self.max_tool_calls = None
        else:
            self.max_tool_calls = max(1, int(max_tool_calls))
        self.used_tool_calls = max(0, int(used_tool_calls or 0))

    def set_skills_enabled(self, enabled):
        self.skill_registry.configure(enabled=enabled)

    def set_skills_config(
        self,
        enabled=None,
        app_enabled=None,
        workspace_enabled=None,
        auto_catalog=None,
        max_chars=None,
    ):
        self.skill_registry.configure(
            enabled=enabled,
            app_enabled=app_enabled,
            workspace_enabled=workspace_enabled,
            workspace_dir=self.workspace_dir,
            auto_catalog=auto_catalog,
            max_chars=max_chars,
        )

    @property
    def skills_available(self):
        return bool(self.skill_registry.enabled)

    def skills_catalog_prompt(self):
        return self.skill_registry.catalog_prompt()

    def skills_status(self):
        return self.skill_registry.status()

    def set_web_search_config(
        self,
        enabled=None,
        provider=None,
        api_key=None,
        max_results=None,
        search_depth=None,
        topic=None,
    ):
        if enabled is not None:
            self.web_search_enabled = bool(enabled)
        elif not hasattr(self, "web_search_enabled"):
            self.web_search_enabled = DEFAULT_WEB_SEARCH_ENABLE
        if provider is not None:
            self.web_search_provider = normalize_web_search_provider(provider)
        elif not hasattr(self, "web_search_provider"):
            self.web_search_provider = DEFAULT_WEB_SEARCH_PROVIDER
        if api_key is not None:
            self.web_search_api_key = str(api_key or "").strip()
        elif not hasattr(self, "web_search_api_key"):
            self.web_search_api_key = ""
        if max_results is not None:
            self.web_search_max_results = _bounded_int(
                max_results,
                DEFAULT_WEB_SEARCH_MAX_RESULTS,
                1,
                20,
                "web_search_max_results",
            )
        elif not hasattr(self, "web_search_max_results"):
            self.web_search_max_results = DEFAULT_WEB_SEARCH_MAX_RESULTS
        if search_depth is not None:
            self.web_search_depth = normalize_tavily_search_depth(search_depth)
        elif not hasattr(self, "web_search_depth"):
            self.web_search_depth = DEFAULT_WEB_SEARCH_DEPTH
        if topic is not None:
            self.web_search_topic = normalize_tavily_topic(topic)
        elif not hasattr(self, "web_search_topic"):
            self.web_search_topic = DEFAULT_WEB_SEARCH_TOPIC

    @property
    def web_search_available(self):
        return self.web_search_enabled and is_web_search_configured(
            self.web_search_provider,
            self.web_search_api_key,
        )

    def web_search_status(self):
        return {
            "enabled": self.web_search_enabled,
            "available": self.web_search_available,
            "provider": self.web_search_provider,
            "max_results": self.web_search_max_results,
            "search_depth": self.web_search_depth,
            "topic": self.web_search_topic,
        }

    def search_web(self, query, **kwargs):
        payload = {"query": query, **kwargs}
        return self._web_search(payload)

    def begin_agent_session(self, clear_todos=True):
        if clear_todos:
            self.todo_store.clear()
        self.session_changed_files = []
        self._session_changed_file_set = set()
        self.session_mutating_commands = []
        self.output_needs_separator = False

    def consume_output_separator(self):
        needs_separator = self.output_needs_separator
        self.output_needs_separator = False
        return needs_separator

    def session_has_changes(self):
        return bool(self.session_changed_files or self.session_mutating_commands)

    def session_change_count(self):
        return len(self.session_changed_files) + len(self.session_mutating_commands)

    def todo_revision(self):
        return self.todo_store.revision

    def todo_status(self):
        return self.todo_store.status(
            max_tool_calls=self.max_tool_calls,
            used_tool_calls=self.used_tool_calls,
        )

    def todo_summary(self, include_completed=True):
        return self.todo_store.summary(include_completed=include_completed)

    def todo_incomplete_summary(self):
        return self.todo_store.incomplete_summary()

    def has_incomplete_todos(self):
        return self.todo_store.has_actionable_incomplete()

    def has_unverified_completed_todos(self):
        return self.todo_store.has_unverified_completed_criteria()

    def todo_actionable_summary(self):
        return self.todo_store.actionable_summary()

    def todo_quality_report(self):
        return self.todo_store.quality_report(
            max_tool_calls=self.max_tool_calls,
            used_tool_calls=self.used_tool_calls,
        )

    def todo_budget_summary(self):
        return self.todo_store.budget_summary(
            max_tool_calls=self.max_tool_calls,
            used_tool_calls=self.used_tool_calls,
        )

    def todo_history(self, limit=20):
        return self.todo_store.history_tail(limit)

    def approve_todos(self, note=""):
        return self.todo_store.approve_plan(note=note, source="user")

    def reject_todos(self, reason=""):
        return self.todo_store.reject_plan(reason=reason, source="user")

    def retry_todo(self, todo_id, reason=""):
        return self.todo_store.retry_todo(todo_id, reason=reason)

    def unblock_todo(self, todo_id, reason=""):
        return self.todo_store.unblock_todo(todo_id, reason=reason)

    def apply_todo_final_verification(self, passed, check_result):
        return self.todo_store.apply_final_verification(
            passed,
            _final_verification_note(check_result, passed),
        )

    def clear_todos(self):
        self.todo_store.clear()

    def session_summary(self):
        parts = []
        if self.session_changed_files:
            parts.append("Changed files: " + ", ".join(self.session_changed_files))
        if self.session_mutating_commands:
            parts.append(
                "Mutating commands: "
                + "; ".join(
                    _truncate(command, 180)
                    for command in self.session_mutating_commands
                )
            )
        return "\n".join(parts)

    def final_check(self):
        if not self.enabled:
            return _error_result("No workspace directory")

        sections = []
        diff_scope = "workspace"
        diff_path_args = []
        if self.session_changed_files and not self.session_mutating_commands:
            diff_scope = "agent-edited files"
            diff_path_args = ["--"] + self.session_changed_files

        if self.session_changed_files:
            sections.append(
                "Agent-edited files:\n"
                + "\n".join(f"- {path}" for path in self.session_changed_files)
            )
        if self.session_mutating_commands:
            sections.append(
                "Agent mutating commands:\n"
                + "\n".join(
                    f"- {_truncate(command, 220)}"
                    for command in self.session_mutating_commands
                )
            )

        sections.append(
            f"git diff --check ({diff_scope}):\n"
            + self._run_git_command(
                ["diff", "--check"] + diff_path_args, "(no whitespace errors)"
            )
        )
        sections.append(
            "git status --short:\n"
            + self._run_git_command(["status", "--short"], "(working tree clean)")
        )
        sections.append(
            f"git diff --stat ({diff_scope}):\n"
            + self._run_git_command(
                ["diff", "--stat"] + diff_path_args, "(no tracked diff)"
            )
        )
        return "\n\n".join(sections)

    def final_check_passed(self, check_result):
        return _final_check_passed(check_result)

    def execute(self, name, tool_input):
        if not self.enabled:
            return _error_result("No workspace directory")

        try:
            if isinstance(tool_input, str):
                tool_input = json.loads(tool_input or "{}")
            if not isinstance(tool_input, dict):
                raise AgentToolError("Tool input must be an object.")

            handlers = {
                "update_todos": self._update_todos,
                "read_file": self._read_file,
                "list_dir": self._list_dir,
                "write_file": self._write_file,
                "edit_file": self._edit_file,
                "apply_patch": self._apply_patch,
                "apply_unified_patch": self._apply_unified_patch,
                "bash": self._bash,
                "local_http_check": self._local_http_check,
                "git_status": self._git_status,
                "git_diff": self._git_diff,
                "grep": self._grep,
                "glob": self._glob,
                "web_search": self._web_search,
                "list_skills": self._list_skills,
                "read_skill": self._read_skill,
            }
            handler = handlers.get(name)
            if handler is None:
                raise AgentToolError(f"Unknown tool: {name}")
            plan_gate_result = self._plan_action_gate(name, tool_input)
            if plan_gate_result is not None:
                return plan_gate_result
            return handler(tool_input)
        except Exception as error:
            return _error_result(str(error))

    def _update_todos(self, tool_input):
        todos = tool_input.get("todos")
        self.todo_store.update(todos)
        return self.todo_store.tool_result(
            max_tool_calls=self.max_tool_calls,
            used_tool_calls=self.used_tool_calls,
        )

    def _read_file(self, tool_input):
        file_path = self._resolve_path(_required_string(tool_input, "file_path"))
        if not file_path.is_file():
            raise AgentToolError(
                f"File does not exist: {self._display_path(file_path)}"
            )

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return f"File: {self._display_path(file_path)}\nLines: 0\n\n(empty file)"

        start_line = _optional_positive_int(tool_input, "start_line") or 1
        end_line = _optional_positive_int(tool_input, "end_line") or total_lines
        if start_line > total_lines:
            raise AgentToolError(
                f"start_line exceeds file length ({total_lines} lines)."
            )
        if end_line < start_line:
            raise AgentToolError(
                "end_line must be greater than or equal to start_line."
            )
        end_line = min(end_line, total_lines)

        line_numbers = _optional_bool(tool_input, "line_numbers", True)
        selected_lines = lines[start_line - 1 : end_line]
        body = _format_lines(selected_lines, start_line, line_numbers)
        truncated = _truncate(body, MAX_READ_CHARS)
        suffix = "" if len(body) <= MAX_READ_CHARS else "\n\n[truncated]"
        return (
            f"File: {self._display_path(file_path)}\n"
            f"Lines: {start_line}-{end_line} of {total_lines}\n\n"
            f"{truncated}{suffix}"
        )

    def _list_dir(self, tool_input):
        root = self._resolve_path(str(tool_input.get("path") or "."))
        if not root.exists():
            raise AgentToolError(f"Path does not exist: {self._display_path(root)}")
        if not root.is_dir():
            raise AgentToolError(f"Path is not a directory: {self._display_path(root)}")

        recursive = _optional_bool(tool_input, "recursive", False)
        max_depth = _optional_positive_int(tool_input, "max_depth") or 2
        entries = []

        if recursive:
            for current_root, dirnames, filenames in os.walk(root):
                current_path = Path(current_root)
                depth = len(current_path.relative_to(root).parts)
                dirnames[:] = [
                    name
                    for name in sorted(dirnames)
                    if name not in SKIP_DIRS and depth < max_depth
                ]
                for dirname in dirnames:
                    entries.append(f"{self._display_path(current_path / dirname)}/")
                    if len(entries) >= MAX_LIST_ENTRIES:
                        break
                if len(entries) >= MAX_LIST_ENTRIES:
                    break
                for filename in sorted(filenames):
                    entries.append(self._display_path(current_path / filename))
                    if len(entries) >= MAX_LIST_ENTRIES:
                        break
                if len(entries) >= MAX_LIST_ENTRIES:
                    break
        else:
            children = sorted(
                root.iterdir(), key=lambda path: (path.is_file(), path.name.lower())
            )
            for child in children:
                if child.name in SKIP_DIRS:
                    continue
                suffix = "/" if child.is_dir() else ""
                entries.append(f"{self._display_path(child)}{suffix}")
                if len(entries) >= MAX_LIST_ENTRIES:
                    break

        if not entries:
            return f"Directory: {self._display_path(root)}\n(empty directory)"
        suffix = "\n[truncated]" if len(entries) >= MAX_LIST_ENTRIES else ""
        return (
            f"Directory: {self._display_path(root)}\n\n" + "\n".join(entries) + suffix
        )

    def _write_file(self, tool_input):
        file_path = self._resolve_path(_required_string(tool_input, "file_path"))
        content = _required_string(tool_input, "content", allow_empty=True)
        action = "overwrite" if file_path.exists() else "create"
        old_content = (
            file_path.read_text(encoding="utf-8", errors="replace")
            if file_path.exists()
            else ""
        )
        diff = _unified_diff_text(old_content, content, self._display_path(file_path))

        if not self._confirm_diff(
            f"Allow agent to {action} file?",
            self._display_path(file_path),
            diff or f"(no content changes, {len(content)} characters)",
            "file_edit",
        ):
            return _error_result("User rejected write_file.")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        self._record_changed_file(file_path)
        return f"Wrote {len(content)} characters to {self._display_path(file_path)}."

    def _edit_file(self, tool_input):
        file_path = self._resolve_path(_required_string(tool_input, "file_path"))
        old_string = _required_string(tool_input, "old_string")
        new_string = _required_string(tool_input, "new_string", allow_empty=True)
        replace_all = _optional_bool(tool_input, "replace_all", False)

        if not file_path.is_file():
            raise AgentToolError(
                f"File does not exist: {self._display_path(file_path)}"
            )

        content = file_path.read_text(encoding="utf-8", errors="replace")
        occurrences = content.count(old_string)
        if occurrences == 0:
            raise AgentToolError("old_string was not found in the file.")
        if occurrences > 1 and not replace_all:
            raise AgentToolError(
                f"old_string occurs {occurrences} times. Set replace_all=true or provide a more specific string."
            )

        replace_count = occurrences if replace_all else 1
        updated = content.replace(old_string, new_string, replace_count)
        diff = _unified_diff_text(content, updated, self._display_path(file_path))

        if not self._confirm_diff(
            f"Allow agent to edit file? ({replace_count} replacement(s))",
            self._display_path(file_path),
            diff,
            "file_edit",
        ):
            return _error_result("User rejected edit_file.")

        file_path.write_text(updated, encoding="utf-8")
        self._record_changed_file(file_path)
        return (
            f"Edited {self._display_path(file_path)} ({replace_count} replacement(s))."
        )

    def _apply_patch(self, tool_input):
        file_path = self._resolve_path(_required_string(tool_input, "file_path"))
        start_line = _required_positive_int(tool_input, "start_line")
        end_line = _required_positive_int(tool_input, "end_line")
        new_content = _required_string(tool_input, "new_content", allow_empty=True)

        if not file_path.is_file():
            raise AgentToolError(
                f"File does not exist: {self._display_path(file_path)}"
            )
        if end_line < start_line:
            raise AgentToolError(
                "end_line must be greater than or equal to start_line."
            )

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        if start_line > len(lines) or end_line > len(lines):
            raise AgentToolError(
                f"Line range exceeds file length ({len(lines)} lines)."
            )

        old_lines = lines[start_line - 1 : end_line]
        new_lines = new_content.splitlines()
        old_display = _format_lines(old_lines, start_line, True)
        new_display = (
            _format_lines(new_lines, start_line, True)
            if new_lines
            else "(delete selected lines)"
        )
        updated_lines = lines[: start_line - 1] + new_lines + lines[end_line:]
        newline = _detect_newline(content)
        updated = newline.join(updated_lines)
        if content.endswith(("\n", "\r")):
            updated += newline
        diff = _unified_diff_text(content, updated, self._display_path(file_path))

        if not self._confirm_diff(
            f"Allow agent to patch file? (lines {start_line}-{end_line})",
            self._display_path(file_path),
            diff or f"Old lines:\n{old_display}\n\nNew lines:\n{new_display}",
            "file_edit",
        ):
            return _error_result("User rejected apply_patch.")

        file_path.write_text(updated, encoding="utf-8")
        self._record_changed_file(file_path)
        return (
            f"Patched {self._display_path(file_path)} (lines {start_line}-{end_line})."
        )

    def _apply_unified_patch(self, tool_input):
        file_path = self._resolve_path(_required_string(tool_input, "file_path"))
        patch = _required_string(tool_input, "patch")

        if not file_path.is_file():
            raise AgentToolError(
                f"File does not exist: {self._display_path(file_path)}"
            )

        content = file_path.read_text(encoding="utf-8", errors="replace")
        updated = _apply_unified_diff_to_content(content, patch)
        diff = _unified_diff_text(content, updated, self._display_path(file_path))

        if not self._confirm_diff(
            "Allow agent to apply unified patch?",
            self._display_path(file_path),
            diff or patch,
            "file_edit",
        ):
            return _error_result("User rejected apply_unified_patch.")

        file_path.write_text(updated, encoding="utf-8")
        self._record_changed_file(file_path)
        return f"Applied unified patch to {self._display_path(file_path)}."

    def _bash(self, tool_input):
        command = _required_string(tool_input, "command")
        self._validate_command_scope(command)

        risk_level, risk_reason = _command_risk(command)
        if risk_level == "blocked":
            raise AgentToolError(f"Command blocked: {risk_reason}")
        foreground_server_reason = _foreground_server_command_reason(command)
        if foreground_server_reason:
            return _error_result(
                f"{foreground_server_reason}. The bash tool only runs commands that exit. "
                "For local HTTP checks, use a bounded script that starts the server as a "
                "subprocess, performs the request, and terminates the server before exiting."
            )
        if risk_level == "confirm" and not self._confirm(
            "Allow agent to run a command?",
            f"{risk_reason}\n{command}",
            risk_reason,
        ):
            return _error_result("User rejected bash command.")

        try:
            completed = subprocess.run(
                command,
                cwd=str(self.workspace_dir),
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            if (
                risk_level == "confirm"
                and risk_reason != "script or shell execution detected"
            ):
                self._record_mutating_command(command)
            return _timeout_result(command, error, COMMAND_TIMEOUT_SECONDS)
        if (
            risk_level == "confirm"
            and risk_reason != "script or shell execution detected"
        ):
            self._record_mutating_command(command)
        output = completed.stdout or ""
        error_output = completed.stderr or ""
        combined = output
        if error_output:
            combined = (
                f"{combined}\n[stderr]\n{error_output}"
                if combined
                else f"[stderr]\n{error_output}"
            )
        if not combined:
            combined = "(no output)"
        combined = _truncate(combined, MAX_TOOL_OUTPUT_CHARS)
        return f"Exit code: {completed.returncode}\n{combined}"

    def _local_http_check(self, tool_input):
        root = self._resolve_path(str(tool_input.get("root") or "."))
        if not root.is_dir():
            raise AgentToolError(f"root is not a directory: {self._display_path(root)}")

        paths = _http_check_paths(tool_input.get("paths"))
        expected_status = _bounded_int(
            tool_input.get("expected_status"),
            200,
            100,
            599,
            "expected_status",
        )
        timeout_seconds = _bounded_int(
            tool_input.get("timeout_seconds"),
            10,
            1,
            60,
            "timeout_seconds",
        )
        port = _free_local_port()
        command = [
            sys.executable or "python",
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
        ]
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        try:
            _wait_for_local_port(process, port, timeout_seconds)
            lines = [
                f"Served {self._display_path(root)} at http://127.0.0.1:{port}",
                f"Expected status: {expected_status}",
            ]
            passed = True
            deadline = time.monotonic() + timeout_seconds
            for path in paths:
                remaining = max(0.5, min(5, deadline - time.monotonic()))
                url_path = _normalize_http_path(path)
                url = f"http://127.0.0.1:{port}{url_path}"
                status, detail = _request_http_status(url, remaining)
                ok = status == expected_status
                passed = passed and ok
                status_text = str(status) if status is not None else "no response"
                suffix = "OK" if ok else "FAILED"
                if detail:
                    lines.append(f"{suffix} {url_path} -> {status_text} ({detail})")
                else:
                    lines.append(f"{suffix} {url_path} -> {status_text}")
            result = "\n".join(lines)
            if not passed:
                return _error_result(result)
            return "Local HTTP check passed.\n" + result
        finally:
            _terminate_process(process)

    def _git_status(self, tool_input):
        return self._run_git_command(["status", "--short"], "(working tree clean)")

    def _git_diff(self, tool_input):
        cached = _optional_bool(tool_input, "cached", False)
        stat = _optional_bool(tool_input, "stat", False)
        check = _optional_bool(tool_input, "check", False)
        if stat and check:
            raise AgentToolError("Use either stat=true or check=true, not both.")

        args = ["diff"]
        if cached:
            args.append("--cached")
        if check:
            args.append("--check")
            empty_message = "(no whitespace errors)"
        elif stat:
            args.append("--stat")
            empty_message = "(no tracked diff)"
        else:
            empty_message = "(no tracked diff)"

        file_path = tool_input.get("file_path")
        if file_path:
            resolved = self._resolve_path(file_path)
            args.extend(["--", str(resolved.relative_to(self.workspace_dir))])

        return self._run_git_command(args, empty_message)

    def _grep(self, tool_input):
        pattern = _required_string(tool_input, "pattern")
        search_path = self._resolve_path(str(tool_input.get("path") or "."))
        include = str(tool_input.get("include") or "*")
        case_sensitive = _optional_bool(tool_input, "case_sensitive", False)
        flags = 0 if case_sensitive else re.IGNORECASE

        try:
            regex = re.compile(pattern, flags)
        except re.error as error:
            raise AgentToolError(f"Invalid regex: {error}") from error

        files = (
            [search_path] if search_path.is_file() else self._iter_files(search_path)
        )
        matches = []
        for file_path in files:
            if len(matches) >= MAX_GREP_MATCHES:
                break
            if not fnmatch.fnmatch(file_path.name, include):
                continue
            try:
                lines = file_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except Exception:
                continue
            for line_number, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append(
                        f"{self._display_path(file_path)}:{line_number}: {_truncate(line.strip(), 500)}"
                    )
                    if len(matches) >= MAX_GREP_MATCHES:
                        break

        if not matches:
            return "No matches found."
        suffix = "\n[truncated]" if len(matches) >= MAX_GREP_MATCHES else ""
        return "\n".join(matches) + suffix

    def _glob(self, tool_input):
        pattern = _required_string(tool_input, "pattern")
        if _has_parent_reference(pattern):
            raise AgentToolError(
                "Glob pattern cannot contain parent directory references."
            )
        if _looks_absolute(pattern):
            root = self._resolve_path(pattern)
            matches = [root] if root.exists() else []
        else:
            matches = list(self.workspace_dir.glob(pattern))

        safe_matches = []
        for path in matches:
            try:
                resolved = path.resolve(strict=False)
                self._ensure_inside_workspace(resolved)
            except AgentToolError:
                continue
            if resolved.is_file():
                safe_matches.append(self._display_path(resolved))
            if len(safe_matches) >= MAX_GLOB_MATCHES:
                break

        if not safe_matches:
            return "No files found."
        safe_matches.sort()
        suffix = "\n[truncated]" if len(safe_matches) >= MAX_GLOB_MATCHES else ""
        return "\n".join(safe_matches) + suffix

    def _list_skills(self, tool_input):
        return self.skill_registry.list_for_tool()

    def _read_skill(self, tool_input):
        name = _required_string(tool_input, "name")
        return self.skill_registry.read_skill(name, tool_input.get("files"))

    def _web_search(self, tool_input):
        if not self.web_search_enabled:
            raise AgentToolError("Web search is disabled. Use /search on to enable it.")
        if self.web_search_provider != DEFAULT_WEB_SEARCH_PROVIDER:
            raise AgentToolError(
                f"Unsupported web search provider: {self.web_search_provider}"
            )

        query = _required_string(tool_input, "query")
        max_results = (
            _optional_positive_int(tool_input, "max_results")
            or self.web_search_max_results
        )
        search_depth = str(tool_input.get("search_depth") or self.web_search_depth)
        topic = str(tool_input.get("topic") or self.web_search_topic)
        time_range = str(tool_input.get("time_range") or "")
        include_answer = _optional_bool(tool_input, "include_answer", False)
        include_raw_content = _optional_bool(tool_input, "include_raw_content", False)

        return search_tavily(
            query,
            api_key=self.web_search_api_key,
            max_results=max_results,
            search_depth=search_depth,
            topic=topic,
            time_range=time_range,
            include_answer=include_answer,
            include_raw_content=include_raw_content,
            include_domains=tool_input.get("include_domains"),
            exclude_domains=tool_input.get("exclude_domains"),
            country=tool_input.get("country", ""),
        )

    def _iter_files(self, root):
        if not root.exists():
            raise AgentToolError(f"Path does not exist: {self._display_path(root)}")
        if not root.is_dir():
            raise AgentToolError(f"Path is not a directory: {self._display_path(root)}")

        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
            for filename in filenames:
                yield Path(current_root) / filename

    def _resolve_path(self, path_value):
        path_text = str(path_value or "").strip()
        if not path_text:
            raise AgentToolError("Path cannot be empty.")
        if _has_parent_reference(path_text):
            raise AgentToolError("Path cannot contain parent directory references.")

        path = Path(path_text)
        if not path.is_absolute():
            path = self.workspace_dir / path
        resolved = path.resolve(strict=False)
        self._ensure_inside_workspace(resolved)
        return resolved

    def _ensure_inside_workspace(self, path):
        workspace = os.path.normcase(str(self.workspace_dir))
        candidate = os.path.normcase(str(path))
        try:
            common = os.path.commonpath([workspace, candidate])
        except ValueError as error:
            raise AgentToolError("Path is outside the workspace.") from error
        if common != workspace:
            raise AgentToolError("Path is outside the workspace.")

    def _display_path(self, path):
        try:
            return str(path.relative_to(self.workspace_dir))
        except ValueError:
            return str(path)

    def _record_changed_file(self, file_path):
        display_path = self._display_path(file_path)
        if display_path not in self._session_changed_file_set:
            self._session_changed_file_set.add(display_path)
            self.session_changed_files.append(display_path)

    def _record_mutating_command(self, command):
        self.session_mutating_commands.append(command)

    def _run_git_command(self, args, empty_message):
        try:
            completed = subprocess.run(
                ["git"] + list(args),
                cwd=str(self.workspace_dir),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return "Exit code: 127\nERROR: git executable was not found."
        except subprocess.TimeoutExpired:
            return f"ERROR: git command timed out after {GIT_TIMEOUT_SECONDS} seconds."

        output = completed.stdout or ""
        error_output = completed.stderr or ""
        combined = output
        if error_output:
            combined = (
                f"{combined}\n[stderr]\n{error_output}"
                if combined
                else f"[stderr]\n{error_output}"
            )
        if not combined:
            combined = empty_message
        combined = _truncate(combined, MAX_TOOL_OUTPUT_CHARS)
        return f"Exit code: {completed.returncode}\n{combined}"

    def _validate_command_scope(self, command):
        if _has_parent_reference(command):
            raise AgentToolError(
                "Bash command cannot contain parent directory references."
            )
        outside_paths = []
        for candidate in _absolute_path_candidates(command):
            try:
                resolved = Path(candidate).resolve(strict=False)
                self._ensure_inside_workspace(resolved)
            except Exception:
                outside_paths.append(candidate)
        if outside_paths:
            raise AgentToolError(
                "Bash command references paths outside the workspace: "
                + ", ".join(outside_paths[:3])
            )
        if re.search(r"(?i)(\$env:|%[^%\s]+%|\$home|~)", command):
            raise AgentToolError(
                "Bash command cannot reference environment or home paths."
            )

    def _confirm_diff(self, title, file_path, diff_content, risk_reason):
        if self._auto_approves(risk_reason):
            return True
        self._before_visible_output()
        approved = get_agent_diff_confirmation(title, file_path, diff_content)
        return approved

    def _confirm(self, title, detail, risk_reason=""):
        if self._auto_approves(risk_reason):
            return True
        self._before_visible_output()
        return get_agent_confirmation(title, detail)

    def _plan_action_gate(self, name, tool_input):
        if not self.todo_store.needs_action_approval():
            return self._active_todo_gate(name)
        if name == "update_todos":
            return None

        if self.todo_store.approval_state == "rejected":
            return _error_result(
                "Current plan was rejected. Revise the plan with update_todos, "
                "or ask the user to run /plan approve before using more tools."
            )

        if self.approval_mode == AGENT_APPROVAL_AUTO:
            self.todo_store.approve_plan(
                note="Auto approval mode approved the current plan.",
                source="auto",
            )
            return self._active_todo_gate(name)

        self._before_plan_approval_output()
        if get_agent_plan_confirmation(self.todo_store.to_dicts()):
            self.todo_store.approve_plan(
                note="User approved the current plan before action.",
                source="user",
            )
            return self._active_todo_gate(name)

        self.todo_store.reject_plan(
            reason="User rejected the current plan before action.",
            source="user",
        )
        return _error_result(
            "User rejected the current plan. Revise the plan before using more tools."
        )

    def _active_todo_gate(self, name):
        if name == "update_todos":
            return None
        if not self.todo_store.requires_active_todo():
            return None
        active = self.todo_store.active_item()
        if active is not None:
            return None
        return _error_result(
            "Before using more tools, call update_todos and mark exactly one ready "
            "todo as in_progress. This keeps the Todo List synchronized with the "
            "work being executed."
        )

    def _before_visible_output(self):
        if self.visible_output_callback:
            self.visible_output_callback()
        self.output_needs_separator = True

    def _before_plan_approval_output(self):
        if self.plan_approval_output_callback:
            self.plan_approval_output_callback()
        elif self.visible_output_callback:
            self.visible_output_callback()

    def _auto_approves(self, risk_reason):
        if self.approval_mode != AGENT_APPROVAL_AUTO:
            return False
        blocked_reasons = (
            "delete command detected",
            "mutating git command detected",
            "package manager mutation detected",
            "package installation detected",
        )
        return risk_reason not in blocked_reasons


def normalize_workspace_dir(workspace_dir):
    if not workspace_dir:
        return None
    try:
        path = Path(str(workspace_dir)).expanduser().resolve(strict=True)
    except Exception:
        return None
    if not path.is_dir():
        return None
    return path


def _plan_dir_for_workspace(workspace_dir):
    if workspace_dir is None:
        return None
    return Path(workspace_dir) / ".omniagent" / "plans"


def _final_check_passed(check_result):
    text = str(check_result or "")
    diff_check = _section_after(text, "git diff --check")
    if diff_check:
        exit_codes = [
            int(value) for value in re.findall(r"Exit code:\s*(-?\d+)", diff_check)
        ]
        if exit_codes and any(code != 0 for code in exit_codes):
            return False
    if re.search(r"(?m)^ERROR:", text):
        return False
    return True


def _final_verification_note(check_result, passed):
    text = str(check_result or "")
    if passed:
        return "Automatic final verification passed."

    diff_check = _section_after(text, "git diff --check")
    source = diff_check or text
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if (
            "Exit code:" in stripped
            or stripped.startswith("ERROR:")
            or "[stderr]" in stripped
        ):
            lines.append(stripped)
        elif lines and len(lines) < 4:
            lines.append(stripped)
        if len(lines) >= 4:
            break
    if not lines:
        return "Automatic final verification failed."
    return " ".join(lines)


def _section_after(text, heading):
    marker_index = str(text or "").find(heading)
    if marker_index < 0:
        return ""
    section = text[marker_index:]
    next_section = section.find("\n\n", len(heading))
    if next_section >= 0:
        section = section[:next_section]
    return section


def _unified_diff_text(old_content, new_content, display_path):
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    diff_lines = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{display_path}",
        tofile=f"b/{display_path}",
        lineterm="",
    )
    return "\n".join(diff_lines)


def _apply_unified_diff_to_content(content, patch):
    original_lines = content.splitlines()
    output_lines = []
    position = 0
    patch_lines = patch.splitlines()
    index = 0
    saw_hunk = False

    while index < len(patch_lines):
        line = patch_lines[index]
        hunk_match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if not hunk_match:
            index += 1
            continue

        saw_hunk = True
        old_start = int(hunk_match.group(1))
        target = max(old_start - 1, 0)
        if target < position:
            raise AgentToolError("Unified patch hunks overlap or are out of order.")

        output_lines.extend(original_lines[position:target])
        position = target
        index += 1

        while index < len(patch_lines):
            hunk_line = patch_lines[index]
            if hunk_line.startswith("@@ "):
                break
            if hunk_line.startswith("\\"):
                index += 1
                continue
            if not hunk_line:
                raise AgentToolError("Invalid unified patch hunk line.")

            marker = hunk_line[0]
            text = hunk_line[1:]
            if marker == " ":
                _assert_patch_line_matches(original_lines, position, text)
                output_lines.append(original_lines[position])
                position += 1
            elif marker == "-":
                _assert_patch_line_matches(original_lines, position, text)
                position += 1
            elif marker == "+":
                output_lines.append(text)
            else:
                raise AgentToolError(f"Invalid unified patch hunk marker: {marker}")
            index += 1

    if not saw_hunk:
        raise AgentToolError("Unified patch does not contain any @@ hunks.")

    output_lines.extend(original_lines[position:])
    newline = _detect_newline(content)
    updated = newline.join(output_lines)
    if content.endswith(("\n", "\r")):
        updated += newline
    return updated


def _assert_patch_line_matches(lines, position, expected):
    if position >= len(lines):
        raise AgentToolError("Unified patch context exceeds file length.")
    actual = lines[position]
    if actual != expected:
        raise AgentToolError(
            "Unified patch context mismatch at line "
            f"{position + 1}. Expected {expected!r}, found {actual!r}."
        )


def _required_string(data, key, allow_empty=False):
    value = data.get(key)
    if not isinstance(value, str):
        raise AgentToolError(f"{key} must be a string.")
    if not allow_empty and not value:
        raise AgentToolError(f"{key} cannot be empty.")
    return value


def _required_positive_int(data, key):
    value = _coerce_int(data.get(key), key)
    if value < 1:
        raise AgentToolError(f"{key} must be greater than 0.")
    return value


def _optional_positive_int(data, key):
    value = data.get(key)
    if value is None:
        return None
    value = _coerce_int(value, key)
    if value < 1:
        raise AgentToolError(f"{key} must be greater than 0.")
    return value


def _bounded_int(value, default, minimum, maximum, key):
    if value is None:
        return default
    value = _coerce_int(value, key)
    if value < minimum or value > maximum:
        raise AgentToolError(f"{key} must be between {minimum} and {maximum}.")
    return value


def _coerce_int(value, key):
    if isinstance(value, bool):
        raise AgentToolError(f"{key} must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as error:
            raise AgentToolError(f"{key} must be an integer.") from error
    else:
        raise AgentToolError(f"{key} must be an integer.")


def _optional_bool(data, key, default=False):
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return bool(value)


def _error_result(message):
    return f"ERROR: {message}"


def _truncate(text, max_chars):
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _single_line(text, max_chars):
    value = " ".join(str(text or "").split())
    return _truncate(value, max_chars)


def _timeout_result(command, error, timeout_seconds):
    output = _timeout_stream(error.stdout)
    error_output = _timeout_stream(error.stderr)
    combined = output
    if error_output:
        combined = (
            f"{combined}\n[stderr]\n{error_output}"
            if combined
            else f"[stderr]\n{error_output}"
        )
    message = (
        f"Command timed out after {timeout_seconds} seconds: "
        f"{_truncate(str(command or ''), 240)}"
    )
    if combined:
        message += "\nPartial output before timeout:\n" + _truncate(
            combined,
            MAX_TOOL_OUTPUT_CHARS,
        )
    return _error_result(message)


def _timeout_stream(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _format_lines(lines, start_line, line_numbers=True):
    if not line_numbers:
        return "\n".join(lines)
    if not lines:
        return ""
    width = len(str(start_line + len(lines) - 1))
    return "\n".join(
        f"{line_number:>{width}} | {line}"
        for line_number, line in enumerate(lines, start_line)
    )


def _detect_newline(content):
    return "\r\n" if "\r\n" in content else "\n"


def _has_parent_reference(value):
    return any(part == ".." for part in re.split(r"[\\/]+", str(value)))


def _looks_absolute(value):
    text = str(value)
    return bool(
        re.match(r"^[a-zA-Z]:[\\/]", text)
        or text.startswith("\\\\")
        or text.startswith("/")
    )


def _absolute_path_candidates(command):
    drive_paths = re.findall(r"[a-zA-Z]:[\\/][^\s\"'<>|]+", command)
    unc_paths = re.findall(r"\\\\[^\s\"'<>|]+", command)
    return drive_paths + unc_paths


def _http_check_paths(value):
    if value is None:
        return ["/"]
    if isinstance(value, str):
        paths = [value]
    elif isinstance(value, (list, tuple)):
        paths = [str(item) for item in value]
    else:
        raise AgentToolError("paths must be an array of strings.")
    normalized = []
    for path in paths:
        path = str(path or "").strip()
        if not path:
            continue
        normalized.append(path)
    return normalized or ["/"]


def _normalize_http_path(path):
    value = str(path or "/").strip() or "/"
    if not value.startswith("/"):
        value = "/" + value
    return urllib.parse.quote(value, safe="/:?&=#%+-._~")


def _free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_local_port(process, port, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _, stderr = _communicate_process(process, 0.2)
            detail = _single_line(stderr, 300) if stderr else "no stderr"
            raise AgentToolError(f"local HTTP server exited early: {detail}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError as error:
            last_error = str(error)
            time.sleep(0.1)
    raise AgentToolError(
        f"local HTTP server did not accept connections within {timeout_seconds} seconds"
        + (f": {last_error}" if last_error else "")
    )


def _request_http_status(url, timeout_seconds):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "OmniAgent-local-http-check"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=max(0.5, timeout_seconds)
        ) as response:
            return int(response.status), ""
    except urllib.error.HTTPError as error:
        return int(error.code), str(error.reason or "")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, str(error)


def _terminate_process(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            _communicate_process(process, 2)
            return
        except subprocess.TimeoutExpired:
            process.kill()
    try:
        _communicate_process(process, 2)
    except subprocess.TimeoutExpired:
        pass


def _communicate_process(process, timeout):
    return process.communicate(timeout=timeout)


def _foreground_server_command_reason(command):
    lowered = str(command or "").lower()
    patterns = [
        (
            r"(?:^|[;&|]\s*)(?:python|python3|py)\s+-m\s+http\.server\b",
            "python -m http.server starts a foreground static server",
        ),
        (
            r"(?:^|[;&|]\s*)(?:npx\s+)?(?:http-server|live-server)\b",
            "static server command starts a foreground process",
        ),
        (
            r"(?:^|[;&|]\s*)(?:npx\s+)?serve(?:\.cmd)?\b",
            "serve starts a foreground static server",
        ),
        (
            r"(?:^|[;&|]\s*)(?:npx\s+)?vite(?:\.cmd)?\b",
            "vite starts a foreground dev server",
        ),
        (
            r"(?:^|[;&|]\s*)(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:dev|start|serve|preview)\b",
            "package script appears to start a foreground dev/static server",
        ),
        (
            r"(?:^|[;&|]\s*)next\s+dev\b",
            "next dev starts a foreground dev server",
        ),
        (
            r"(?:^|[;&|]\s*)astro\s+dev\b",
            "astro dev starts a foreground dev server",
        ),
    ]
    for pattern, reason in patterns:
        if re.search(pattern, lowered):
            return reason
    return ""


def _command_risk(command):
    lowered = command.lower()
    blocked_patterns = [
        (r"(^|\s)git\s+reset\s+--hard\b", "git reset --hard is blocked"),
        (r"(^|\s)git\s+clean\b", "git clean is blocked"),
        (
            r"(^|\s)(format|shutdown|restart-computer|stop-computer)\b",
            "system-level command is blocked",
        ),
        (
            r"(^|\s)(invoke-expression|iex|set-executionpolicy)\b",
            "dynamic PowerShell execution is blocked",
        ),
        (
            r"(^|\s)(rm|del|erase|rmdir|rd|remove-item|ri)\b[^\n]*(?:-recurse|-r|-rf|-fr|/s)\b",
            "recursive delete command is blocked",
        ),
    ]
    for pattern, reason in blocked_patterns:
        if re.search(pattern, lowered):
            return "blocked", reason

    confirm_patterns = [
        (r"(^|\s)(rm|del|erase|rmdir|rd|remove-item|ri)\b", "delete command detected"),
        (
            r"(^|\s)(mv|move|cp|copy|xcopy|robocopy|move-item|copy-item)\b",
            "file move/copy command detected",
        ),
        (
            r"(^|\s)(mkdir|md|new-item|ni|touch)\b",
            "directory/file creation command detected",
        ),
        (
            r"(^|\s)(set-content|add-content|out-file|tee|tee-object)\b",
            "file write command detected",
        ),
        (r"(^|\s)sed\s+(-i|--in-place)\b", "in-place file edit command detected"),
        (r">\s*[^&|]", "shell redirection detected"),
        (r">>\s*[^&|]", "shell append redirection detected"),
        (
            r"(^|\s)git\s+(checkout|reset|clean|apply|am|merge|rebase|commit|add|rm|mv)\b",
            "mutating git command detected",
        ),
        (
            r"(^|\s)(npm|pnpm|yarn)\s+(install|add|remove|update)\b",
            "package manager mutation detected",
        ),
        (r"(^|\s)pip\s+install\b", "package installation detected"),
        (
            r"(^|\s)(python|python3|py|node|deno|ruby|perl|powershell|pwsh|cmd|bash|sh)\b",
            "script or shell execution detected",
        ),
    ]
    for pattern, reason in confirm_patterns:
        if re.search(pattern, lowered):
            return "confirm", reason
    return "allow", ""

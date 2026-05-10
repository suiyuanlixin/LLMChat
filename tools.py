import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path

from ui import get_agent_edit_confirmation, get_agent_patch_confirmation, get_user_input


MAX_READ_CHARS = 60000
MAX_TOOL_OUTPUT_CHARS = 20000
MAX_GREP_MATCHES = 200
MAX_GLOB_MATCHES = 500
MAX_LIST_ENTRIES = 300
COMMAND_TIMEOUT_SECONDS = 60

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}


TOOL_DEFINITIONS = [
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
        "description": "Create or overwrite a UTF-8 text file in the configured workspace. Requires user confirmation.",
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
        "description": "Edit a text file by replacing an exact string. Requires user confirmation.",
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
        "description": "Replace a 1-based inclusive line range in a text file. Requires user confirmation and shows old/new content.",
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
        "name": "bash",
        "description": "Run a shell command inside the configured workspace. Commands with obvious file writes or deletes require user confirmation.",
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


def anthropic_tool_schemas():
    return TOOL_DEFINITIONS


def glm_tool_schemas():
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in TOOL_DEFINITIONS
    ]


class AgentToolError(Exception):
    pass


class AgentTools:
    def __init__(self, workspace_dir=None):
        self.workspace_dir = normalize_workspace_dir(workspace_dir)

    @property
    def enabled(self):
        return self.workspace_dir is not None

    def set_workspace_dir(self, workspace_dir):
        self.workspace_dir = normalize_workspace_dir(workspace_dir)

    def execute(self, name, tool_input):
        if not self.enabled:
            return _error_result("No workspace directory")

        try:
            if isinstance(tool_input, str):
                tool_input = json.loads(tool_input or "{}")
            if not isinstance(tool_input, dict):
                raise AgentToolError("Tool input must be an object.")

            handlers = {
                "read_file": self._read_file,
                "list_dir": self._list_dir,
                "write_file": self._write_file,
                "edit_file": self._edit_file,
                "apply_patch": self._apply_patch,
                "bash": self._bash,
                "grep": self._grep,
                "glob": self._glob,
            }
            handler = handlers.get(name)
            if handler is None:
                raise AgentToolError(f"Unknown tool: {name}")
            return handler(tool_input)
        except Exception as error:
            return _error_result(str(error))

    def _read_file(self, tool_input):
        file_path = self._resolve_path(_required_string(tool_input, "file_path"))
        if not file_path.is_file():
            raise AgentToolError(f"File does not exist: {self._display_path(file_path)}")

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return f"File: {self._display_path(file_path)}\nLines: 0\n\n(empty file)"

        start_line = _optional_positive_int(tool_input, "start_line") or 1
        end_line = _optional_positive_int(tool_input, "end_line") or total_lines
        if start_line > total_lines:
            raise AgentToolError(f"start_line exceeds file length ({total_lines} lines).")
        if end_line < start_line:
            raise AgentToolError("end_line must be greater than or equal to start_line.")
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
            children = sorted(root.iterdir(), key=lambda path: (path.is_file(), path.name.lower()))
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
        return f"Directory: {self._display_path(root)}\n\n" + "\n".join(entries) + suffix

    def _write_file(self, tool_input):
        file_path = self._resolve_path(_required_string(tool_input, "file_path"))
        content = _required_string(tool_input, "content", allow_empty=True)
        action = "overwrite" if file_path.exists() else "create"

        if not self._confirm(
            f"Allow agent to {action} file?",
            f"{self._display_path(file_path)} ({len(content)} characters)",
        ):
            return _error_result("User rejected write_file.")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {self._display_path(file_path)}."

    def _edit_file(self, tool_input):
        file_path = self._resolve_path(_required_string(tool_input, "file_path"))
        old_string = _required_string(tool_input, "old_string")
        new_string = _required_string(tool_input, "new_string", allow_empty=True)
        replace_all = _optional_bool(tool_input, "replace_all", False)

        if not file_path.is_file():
            raise AgentToolError(f"File does not exist: {self._display_path(file_path)}")

        content = file_path.read_text(encoding="utf-8", errors="replace")
        occurrences = content.count(old_string)
        if occurrences == 0:
            raise AgentToolError("old_string was not found in the file.")
        if occurrences > 1 and not replace_all:
            raise AgentToolError(
                f"old_string occurs {occurrences} times. Set replace_all=true or provide a more specific string."
            )

        replace_count = occurrences if replace_all else 1
        if not get_agent_edit_confirmation(
            self._display_path(file_path),
            replace_count,
            old_string,
            new_string,
        ):
            return _error_result("User rejected edit_file.")

        updated = content.replace(old_string, new_string, replace_count)
        file_path.write_text(updated, encoding="utf-8")
        return f"Edited {self._display_path(file_path)} ({replace_count} replacement(s))."

    def _apply_patch(self, tool_input):
        file_path = self._resolve_path(_required_string(tool_input, "file_path"))
        start_line = _required_positive_int(tool_input, "start_line")
        end_line = _required_positive_int(tool_input, "end_line")
        new_content = _required_string(tool_input, "new_content", allow_empty=True)

        if not file_path.is_file():
            raise AgentToolError(f"File does not exist: {self._display_path(file_path)}")
        if end_line < start_line:
            raise AgentToolError("end_line must be greater than or equal to start_line.")

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        if start_line > len(lines) or end_line > len(lines):
            raise AgentToolError(f"Line range exceeds file length ({len(lines)} lines).")

        old_lines = lines[start_line - 1 : end_line]
        new_lines = new_content.splitlines()
        old_display = _format_lines(old_lines, start_line, True)
        new_display = _format_lines(new_lines, start_line, True) if new_lines else "(delete selected lines)"

        if not get_agent_patch_confirmation(
            self._display_path(file_path),
            start_line,
            end_line,
            old_display,
            new_display,
        ):
            return _error_result("User rejected apply_patch.")

        updated_lines = lines[: start_line - 1] + new_lines + lines[end_line:]
        newline = _detect_newline(content)
        updated = newline.join(updated_lines)
        if content.endswith(("\n", "\r")):
            updated += newline
        file_path.write_text(updated, encoding="utf-8")
        return f"Patched {self._display_path(file_path)} (lines {start_line}-{end_line})."

    def _bash(self, tool_input):
        command = _required_string(tool_input, "command")
        self._validate_command_scope(command)

        risk_level, risk_reason = _command_risk(command)
        if risk_level == "blocked":
            raise AgentToolError(f"Command blocked: {risk_reason}")
        if risk_level == "confirm" and not self._confirm(
            "Allow agent to run a command that may modify files?",
            f"{risk_reason}\n{command}",
        ):
            return _error_result("User rejected bash command.")

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
        output = completed.stdout or ""
        error_output = completed.stderr or ""
        combined = output
        if error_output:
            combined = f"{combined}\n[stderr]\n{error_output}" if combined else f"[stderr]\n{error_output}"
        if not combined:
            combined = "(no output)"
        combined = _truncate(combined, MAX_TOOL_OUTPUT_CHARS)
        return f"Exit code: {completed.returncode}\n{combined}"

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

        files = [search_path] if search_path.is_file() else self._iter_files(search_path)
        matches = []
        for file_path in files:
            if len(matches) >= MAX_GREP_MATCHES:
                break
            if not fnmatch.fnmatch(file_path.name, include):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
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
            raise AgentToolError("Glob pattern cannot contain parent directory references.")
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

    def _validate_command_scope(self, command):
        if _has_parent_reference(command):
            raise AgentToolError("Bash command cannot contain parent directory references.")
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
            raise AgentToolError("Bash command cannot reference environment or home paths.")

    def _confirm(self, title, detail):
        answer = get_user_input(f"{title}\n{detail}\nContinue? (Y/N, Default: N): ")
        return answer.strip().lower() in {"y", "yes"}


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
    return bool(re.match(r"^[a-zA-Z]:[\\/]", text) or text.startswith("\\\\") or text.startswith("/"))


def _absolute_path_candidates(command):
    drive_paths = re.findall(r"[a-zA-Z]:[\\/][^\s\"'<>|]+", command)
    unc_paths = re.findall(r"\\\\[^\s\"'<>|]+", command)
    return drive_paths + unc_paths


def _command_risk(command):
    lowered = command.lower()
    blocked_patterns = [
        (r"(^|\s)git\s+reset\s+--hard\b", "git reset --hard is blocked"),
        (r"(^|\s)git\s+clean\b", "git clean is blocked"),
        (r"(^|\s)(format|shutdown|restart-computer|stop-computer)\b", "system-level command is blocked"),
        (r"(^|\s)(invoke-expression|iex|set-executionpolicy)\b", "dynamic PowerShell execution is blocked"),
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
        (r"(^|\s)(mkdir|md|new-item|ni|touch)\b", "directory/file creation command detected"),
        (
            r"(^|\s)(set-content|add-content|out-file|tee|tee-object)\b",
            "file write command detected",
        ),
        (r"(^|\s)sed\s+(-i|--in-place)\b", "in-place file edit command detected"),
        (r">\s*[^&|]", "shell redirection detected"),
        (r">>\s*[^&|]", "shell append redirection detected"),
        (r"(^|\s)git\s+(checkout|reset|clean|apply|am|merge|rebase|commit|add|rm|mv)\b", "mutating git command detected"),
        (r"(^|\s)(npm|pnpm|yarn)\s+(install|add|remove|update)\b", "package manager mutation detected"),
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

from dataclasses import dataclass, field


TODO_STATUS_PENDING = "pending"
TODO_STATUS_IN_PROGRESS = "in_progress"
TODO_STATUS_COMPLETED = "completed"
TODO_STATUS_BLOCKED = "blocked"
TODO_STATUS_FAILED = "failed"
VALID_TODO_STATUSES = {
    TODO_STATUS_PENDING,
    TODO_STATUS_IN_PROGRESS,
    TODO_STATUS_COMPLETED,
    TODO_STATUS_BLOCKED,
    TODO_STATUS_FAILED,
}
ACTIONABLE_TODO_STATUSES = {TODO_STATUS_PENDING, TODO_STATUS_IN_PROGRESS}

TODO_PRIORITY_P0 = "p0"
TODO_PRIORITY_P1 = "p1"
TODO_PRIORITY_P2 = "p2"
TODO_PRIORITY_P3 = "p3"
VALID_TODO_PRIORITIES = {
    TODO_PRIORITY_P0,
    TODO_PRIORITY_P1,
    TODO_PRIORITY_P2,
    TODO_PRIORITY_P3,
}
DEFAULT_TODO_PRIORITY = TODO_PRIORITY_P2
FINAL_VERIFICATION_TODO_ID = "final-verification"


STATUS_ALIASES = {
    "todo": TODO_STATUS_PENDING,
    "open": TODO_STATUS_PENDING,
    "doing": TODO_STATUS_IN_PROGRESS,
    "current": TODO_STATUS_IN_PROGRESS,
    "active": TODO_STATUS_IN_PROGRESS,
    "in-progress": TODO_STATUS_IN_PROGRESS,
    "done": TODO_STATUS_COMPLETED,
    "complete": TODO_STATUS_COMPLETED,
    "blocked": TODO_STATUS_BLOCKED,
    "block": TODO_STATUS_BLOCKED,
    "waiting": TODO_STATUS_BLOCKED,
    "failed": TODO_STATUS_FAILED,
    "fail": TODO_STATUS_FAILED,
    "error": TODO_STATUS_FAILED,
}

PRIORITY_ALIASES = {
    "critical": TODO_PRIORITY_P0,
    "highest": TODO_PRIORITY_P0,
    "high": TODO_PRIORITY_P1,
    "medium": TODO_PRIORITY_P2,
    "normal": TODO_PRIORITY_P2,
    "low": TODO_PRIORITY_P3,
}


@dataclass
class TodoItem:
    content: str
    status: str = TODO_STATUS_PENDING
    id: str = ""
    priority: str = DEFAULT_TODO_PRIORITY
    depends_on: list = field(default_factory=list)
    completion_criteria: list = field(default_factory=list)
    reason: str = ""
    verified: bool = False
    verification_note: str = ""
    system: bool = False

    def to_dict(self):
        data = {
            "id": self.id,
            "content": self.content,
            "status": self.status,
            "priority": self.priority,
        }
        if self.depends_on:
            data["depends_on"] = list(self.depends_on)
        if self.completion_criteria:
            data["completion_criteria"] = list(self.completion_criteria)
        if self.reason:
            data["reason"] = self.reason
        if self.verified:
            data["verified"] = True
        if self.verification_note:
            data["verification_note"] = self.verification_note
        if self.system:
            data["system"] = True
        return data


class TodoStore:
    def __init__(self, on_change=None):
        self.items = []
        self.revision = 0
        self.on_change = on_change

    def set_on_change(self, callback):
        self.on_change = callback
        self._notify()

    def clear(self):
        if not self.items:
            return
        self.items = []
        self.revision += 1
        self._notify()

    def update(self, todos):
        if todos is None:
            todos = []
        if not isinstance(todos, list):
            raise ValueError("todos must be an array.")

        items = []
        seen_ids = set()
        in_progress_count = 0
        for index, todo in enumerate(todos, 1):
            if not isinstance(todo, dict):
                raise ValueError(f"todos[{index}] must be an object.")
            content = str(
                todo.get("content")
                or todo.get("task")
                or todo.get("title")
                or ""
            ).strip()
            if not content:
                continue

            item_id = str(todo.get("id") or f"todo-{index}").strip()
            if item_id in seen_ids:
                raise ValueError(f"Duplicate todo id: {item_id}")
            seen_ids.add(item_id)

            status = _normalize_todo_status(todo.get("status"))
            if status == TODO_STATUS_IN_PROGRESS:
                in_progress_count += 1

            reason = str(
                todo.get("reason")
                or todo.get("block_reason")
                or todo.get("failure_reason")
                or ""
            ).strip()
            if status in {TODO_STATUS_BLOCKED, TODO_STATUS_FAILED} and not reason:
                raise ValueError(f"{status} todo '{item_id}' must include a reason.")

            completion_criteria = _string_list(
                todo.get("completion_criteria")
                or todo.get("done_when")
                or todo.get("verification")
            )
            verified = _optional_bool(todo.get("verified"), False)
            verification_note = str(todo.get("verification_note") or "").strip()
            if verified and completion_criteria and not verification_note:
                raise ValueError(
                    f"Verified todo '{item_id}' must include verification_note."
                )

            items.append(
                TodoItem(
                    id=item_id,
                    content=content,
                    status=status,
                    priority=_normalize_todo_priority(todo.get("priority")),
                    depends_on=_string_list(todo.get("depends_on")),
                    completion_criteria=completion_criteria,
                    reason=reason,
                    verified=verified if status == TODO_STATUS_COMPLETED else False,
                    verification_note=verification_note,
                    system=bool(todo.get("system")),
                )
            )

        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time.")

        if items:
            existing_ids = {item.id for item in items}
            for item in self.items:
                if item.system and item.id not in existing_ids:
                    items.append(item)

        _validate_dependencies(items)

        self.items = items
        self.revision += 1
        self._notify()

    def to_dicts(self):
        return [item.to_dict() for item in self.items]

    def ui_items(self):
        if not self.items or self.all_completed():
            return []
        return self.to_dicts()

    def all_completed(self):
        return bool(self.items) and all(
            item.status == TODO_STATUS_COMPLETED for item in self.items
        )

    def has_incomplete(self):
        return any(item.status != TODO_STATUS_COMPLETED for item in self.items)

    def has_actionable_incomplete(self):
        return any(item.status in ACTIONABLE_TODO_STATUSES for item in self.items)

    def has_unverified_completed_criteria(self):
        return any(
            item.status == TODO_STATUS_COMPLETED
            and item.completion_criteria
            and not item.verified
            for item in self.items
        )

    def incomplete_items(self):
        return [
            item
            for item in self.items
            if item.status != TODO_STATUS_COMPLETED
        ]

    def actionable_items(self):
        return [
            item
            for item in self.items
            if item.status in ACTIONABLE_TODO_STATUSES
        ]

    def status(self):
        return {
            "items": self.to_dicts(),
            "active_items": self.ui_items(),
            "has_todos": bool(self.items),
            "has_incomplete": self.has_incomplete(),
            "has_actionable_incomplete": self.has_actionable_incomplete(),
            "has_unverified_completed_criteria": self.has_unverified_completed_criteria(),
            "all_completed": self.all_completed(),
            "revision": self.revision,
        }

    def summary(self, include_completed=True):
        items = self.items if include_completed else self.incomplete_items()
        if not items:
            return "(no todos)"
        return "\n".join(_format_todo_line(item) for item in items)

    def incomplete_summary(self):
        return self.summary(include_completed=False)

    def actionable_summary(self):
        items = self.actionable_items()
        if not items:
            return "(no pending or in-progress todos)"
        return "\n".join(_format_todo_line(item) for item in items)

    def apply_final_verification(self, passed, note):
        if not self.items:
            return False

        changed = False
        note = _single_line(note, 240)
        if passed:
            changed = self._remove_final_verification_todo()
            for item in self.items:
                if (
                    item.status == TODO_STATUS_COMPLETED
                    and item.completion_criteria
                    and not item.verified
                ):
                    item.verified = True
                    item.verification_note = note or "Automatic final verification passed."
                    changed = True
        else:
            changed = self._upsert_final_verification_failure(note)

        if changed:
            self.revision += 1
            self._notify()
        return changed

    def tool_result(self):
        if not self.items:
            return "Todos cleared. Todo panel hidden."
        if self.all_completed():
            return "All todos completed. Todo panel hidden.\n" + self.summary()
        return "Todos updated:\n" + self.summary()

    def _remove_final_verification_todo(self):
        next_items = [
            item for item in self.items if item.id != FINAL_VERIFICATION_TODO_ID
        ]
        if len(next_items) == len(self.items):
            return False
        self.items = next_items
        return True

    def _upsert_final_verification_failure(self, note):
        existing = None
        for item in self.items:
            if item.id == FINAL_VERIFICATION_TODO_ID:
                existing = item
                break

        content = "Automatic final verification"
        reason = note or "Automatic final verification failed."
        criteria = ["Automatic final verification passes"]
        if existing is None:
            self.items.append(
                TodoItem(
                    id=FINAL_VERIFICATION_TODO_ID,
                    content=content,
                    status=TODO_STATUS_FAILED,
                    priority=TODO_PRIORITY_P0,
                    completion_criteria=criteria,
                    reason=reason,
                    system=True,
                )
            )
            return True

        changed = (
            existing.status != TODO_STATUS_FAILED
            or existing.priority != TODO_PRIORITY_P0
            or existing.reason != reason
            or existing.completion_criteria != criteria
            or not existing.system
        )
        existing.content = content
        existing.status = TODO_STATUS_FAILED
        existing.priority = TODO_PRIORITY_P0
        existing.reason = reason
        existing.completion_criteria = criteria
        existing.verified = False
        existing.verification_note = ""
        existing.system = True
        return changed

    def _notify(self):
        if self.on_change is None:
            return
        self.on_change(self.ui_items())


def _validate_dependencies(items):
    by_id = {item.id: item for item in items}
    for item in items:
        for dependency in item.depends_on:
            if dependency == item.id:
                raise ValueError(f"Todo '{item.id}' cannot depend on itself.")
            if dependency not in by_id:
                raise ValueError(
                    f"Todo '{item.id}' depends on unknown todo '{dependency}'."
                )

    visiting = set()
    visited = set()

    def visit(item):
        if item.id in visited:
            return
        if item.id in visiting:
            raise ValueError("Todo dependencies cannot contain cycles.")
        visiting.add(item.id)
        for dependency_id in item.depends_on:
            visit(by_id[dependency_id])
        visiting.remove(item.id)
        visited.add(item.id)

    for item in items:
        visit(item)

    for item in items:
        if item.status not in {TODO_STATUS_IN_PROGRESS, TODO_STATUS_COMPLETED}:
            continue
        unmet = [
            dependency_id
            for dependency_id in item.depends_on
            if by_id[dependency_id].status != TODO_STATUS_COMPLETED
        ]
        if unmet:
            raise ValueError(
                f"Todo '{item.id}' cannot be {item.status} until dependencies "
                f"are completed: {', '.join(unmet)}"
            )


def _normalize_todo_status(status):
    value = str(status or TODO_STATUS_PENDING).strip().lower().replace(" ", "_")
    value = STATUS_ALIASES.get(value, value)
    if value not in VALID_TODO_STATUSES:
        return TODO_STATUS_PENDING
    return value


def _normalize_todo_priority(priority):
    value = str(priority or DEFAULT_TODO_PRIORITY).strip().lower().replace(" ", "")
    value = PRIORITY_ALIASES.get(value, value)
    if value not in VALID_TODO_PRIORITIES:
        return DEFAULT_TODO_PRIORITY
    return value


def _string_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value]
    else:
        parts = [str(value).strip()]
    return [part for part in parts if part]


def _optional_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return bool(value)


def _format_todo_line(item):
    marker = "[ ]"
    if item.status == TODO_STATUS_IN_PROGRESS:
        marker = "[-]"
    elif item.status == TODO_STATUS_COMPLETED:
        marker = "[✓]"
    elif item.status == TODO_STATUS_BLOCKED:
        marker = "[!]"
    elif item.status == TODO_STATUS_FAILED:
        marker = "[x]"

    details = [f"id: {item.id}", item.priority.upper()]
    if item.depends_on:
        details.append("after: " + ", ".join(item.depends_on))
    if item.completion_criteria:
        details.append("done when: " + "; ".join(item.completion_criteria))
    if item.verified:
        details.append("verified")
    if item.reason:
        details.append("reason: " + item.reason)
    return f"{marker} {item.content} ({'; '.join(details)})"


def _single_line(text, max_chars):
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."

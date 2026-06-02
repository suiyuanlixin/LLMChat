from dataclasses import dataclass


TODO_STATUS_PENDING = "pending"
TODO_STATUS_IN_PROGRESS = "in_progress"
TODO_STATUS_COMPLETED = "completed"
VALID_TODO_STATUSES = {
    TODO_STATUS_PENDING,
    TODO_STATUS_IN_PROGRESS,
    TODO_STATUS_COMPLETED,
}


STATUS_ALIASES = {
    "todo": TODO_STATUS_PENDING,
    "open": TODO_STATUS_PENDING,
    "doing": TODO_STATUS_IN_PROGRESS,
    "current": TODO_STATUS_IN_PROGRESS,
    "active": TODO_STATUS_IN_PROGRESS,
    "in-progress": TODO_STATUS_IN_PROGRESS,
    "done": TODO_STATUS_COMPLETED,
    "complete": TODO_STATUS_COMPLETED,
}


@dataclass
class TodoItem:
    content: str
    status: str = TODO_STATUS_PENDING
    id: str = ""

    def to_dict(self):
        data = {
            "content": self.content,
            "status": self.status,
        }
        if self.id:
            data["id"] = self.id
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

            status = _normalize_todo_status(todo.get("status"))
            if status == TODO_STATUS_IN_PROGRESS:
                in_progress_count += 1
            item_id = str(todo.get("id") or "").strip()
            items.append(TodoItem(content=content, status=status, id=item_id))

        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time.")

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

    def incomplete_items(self):
        return [
            item
            for item in self.items
            if item.status != TODO_STATUS_COMPLETED
        ]

    def status(self):
        return {
            "items": self.to_dicts(),
            "active_items": self.ui_items(),
            "has_todos": bool(self.items),
            "has_incomplete": self.has_incomplete(),
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

    def tool_result(self):
        if not self.items:
            return "Todos cleared. Todo panel hidden."
        if self.all_completed():
            return "All todos completed. Todo panel hidden.\n" + self.summary()
        return "Todos updated:\n" + self.summary()

    def _notify(self):
        if self.on_change is None:
            return
        self.on_change(self.ui_items())


def _normalize_todo_status(status):
    value = str(status or TODO_STATUS_PENDING).strip().lower().replace(" ", "_")
    value = STATUS_ALIASES.get(value, value)
    if value not in VALID_TODO_STATUSES:
        return TODO_STATUS_PENDING
    return value


def _format_todo_line(item):
    marker = "[ ]"
    if item.status == TODO_STATUS_IN_PROGRESS:
        marker = "[-]"
    elif item.status == TODO_STATUS_COMPLETED:
        marker = "[✓]"
    return f"{marker} {item.content}"

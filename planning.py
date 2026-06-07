import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


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
PRIORITY_RANK = {
    TODO_PRIORITY_P0: 0,
    TODO_PRIORITY_P1: 1,
    TODO_PRIORITY_P2: 2,
    TODO_PRIORITY_P3: 3,
}

PLAN_APPROVAL_NOT_REQUIRED = "not_required"
PLAN_APPROVAL_PENDING = "pending"
PLAN_APPROVAL_APPROVED = "approved"
PLAN_APPROVAL_REJECTED = "rejected"
VALID_PLAN_APPROVAL_STATES = {
    PLAN_APPROVAL_NOT_REQUIRED,
    PLAN_APPROVAL_PENDING,
    PLAN_APPROVAL_APPROVED,
    PLAN_APPROVAL_REJECTED,
}

FINAL_VERIFICATION_TODO_ID = "final-verification"
CURRENT_PLAN_FILE = "current.json"
PLAN_EVENTS_FILE = "events.jsonl"

CRITERION_TYPE_MANUAL = "manual"
VALID_CRITERION_TYPES = {
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
    "urgent": TODO_PRIORITY_P0,
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
            data["completion_criteria"] = [
                dict(criterion) for criterion in self.completion_criteria
            ]
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
    def __init__(self, on_change=None, plan_dir=None):
        self.items = []
        self.revision = 0
        self.on_change = on_change
        self.plan_dir = None
        self.current_plan_path = None
        self.events_path = None
        self.approval_state = PLAN_APPROVAL_NOT_REQUIRED
        self.approval_note = ""
        self.plan_signature = ""
        self._loading = False
        self.set_plan_dir(plan_dir, load=True)

    def set_plan_dir(self, plan_dir, load=False):
        if plan_dir:
            self.plan_dir = Path(plan_dir)
            self.current_plan_path = self.plan_dir / CURRENT_PLAN_FILE
            self.events_path = self.plan_dir / PLAN_EVENTS_FILE
        else:
            self.plan_dir = None
            self.current_plan_path = None
            self.events_path = None

        if load:
            self._load_snapshot()

    def set_on_change(self, callback):
        self.on_change = callback
        self._notify()

    def clear(self):
        if not self.items and self.approval_state == PLAN_APPROVAL_NOT_REQUIRED:
            return
        old_items = self._copy_items()
        self.items = []
        self.approval_state = PLAN_APPROVAL_NOT_REQUIRED
        self.approval_note = ""
        self.plan_signature = ""
        self.revision += 1
        self._record_event(
            "plan_cleared",
            {"removed": [item.id for item in old_items]},
        )
        self._persist()
        self._notify()

    def update(self, todos):
        old_items = self._copy_items()
        old_approval_state = self.approval_state
        old_signature = self.plan_signature or _plan_signature(self.items)
        items = self._items_from_todos(todos)
        items = _preserve_approved_plan_items(
            items,
            old_items,
            old_approval_state,
        )

        if items:
            existing_ids = {item.id for item in items}
            for item in self.items:
                if item.system and item.id not in existing_ids:
                    items.append(copy.deepcopy(item))
        _validate_single_in_progress(items)

        new_signature = _plan_signature(items)
        structure_changed = new_signature != old_signature
        approval_required = _requires_plan_approval(
            old_items,
            items,
            old_approval_state,
            structure_changed,
        )
        _hold_unapproved_progress(
            items,
            old_items,
            old_approval_state,
            approval_required,
        )
        _validate_dependencies(items)

        self.items = items
        self.plan_signature = new_signature
        self._refresh_approval_state(approval_required)
        self.revision += 1
        self._record_update_events(old_items, old_approval_state)
        self._persist()
        self._notify()

    def approve_plan(self, note="", source="user"):
        if not self.items or self.all_completed():
            self.approval_state = PLAN_APPROVAL_NOT_REQUIRED
            self.approval_note = ""
            self._persist()
            self._notify()
            return False
        if self.approval_state == PLAN_APPROVAL_APPROVED:
            return False
        self.approval_state = PLAN_APPROVAL_APPROVED
        self.approval_note = _single_line(
            note or f"Plan approved by {source}.",
            240,
        )
        self.revision += 1
        self._record_event(
            "plan_approved",
            {"source": source, "note": self.approval_note},
        )
        self._persist()
        self._notify()
        return True

    def reject_plan(self, reason="", source="user"):
        if not self.items:
            return False
        self.approval_state = PLAN_APPROVAL_REJECTED
        self.approval_note = _single_line(
            reason or f"Plan rejected by {source}.",
            240,
        )
        self.revision += 1
        self._record_event(
            "plan_rejected",
            {"source": source, "reason": self.approval_note},
        )
        self._persist()
        self._notify()
        return True

    def retry_todo(self, todo_id, reason=""):
        item = self._find_item(todo_id)
        if item is None:
            raise ValueError(f"Unknown plan item id: {todo_id}")
        if item.status not in {TODO_STATUS_BLOCKED, TODO_STATUS_FAILED, TODO_STATUS_COMPLETED}:
            raise ValueError(
                f"Plan item '{todo_id}' is {item.status}; only blocked, failed, "
                "or completed plan items can be retried."
            )
        previous = item.status
        item.status = TODO_STATUS_PENDING
        item.reason = ""
        item.verified = False
        item.verification_note = ""
        self.approval_state = PLAN_APPROVAL_PENDING
        self.approval_note = _single_line(
            reason or f"Plan item '{todo_id}' retried from {previous}.",
            240,
        )
        self.revision += 1
        self._record_event(
            "plan_item_retried",
            {
                "id": item.id,
                "from_status": previous,
                "to_status": item.status,
                "reason": self.approval_note,
            },
        )
        self._persist()
        self._notify()
        return True

    def unblock_todo(self, todo_id, reason=""):
        item = self._find_item(todo_id)
        if item is None:
            raise ValueError(f"Unknown plan item id: {todo_id}")
        if item.status != TODO_STATUS_BLOCKED:
            raise ValueError(f"Plan item '{todo_id}' is {item.status}, not blocked.")
        item.status = TODO_STATUS_PENDING
        item.reason = ""
        self.approval_state = PLAN_APPROVAL_PENDING
        self.approval_note = _single_line(
            reason or f"Plan item '{todo_id}' unblocked.",
            240,
        )
        self.revision += 1
        self._record_event(
            "plan_item_unblocked",
            {"id": item.id, "to_status": item.status, "reason": self.approval_note},
        )
        self._persist()
        self._notify()
        return True

    def to_dicts(self):
        return [item.to_dict() for item in self.items]

    def ui_items(self):
        if not self.items or self.all_completed():
            return []
        if self.approval_state != PLAN_APPROVAL_APPROVED:
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

    def active_item(self):
        for item in self.items:
            if item.status == TODO_STATUS_IN_PROGRESS:
                return item
        return None

    def requires_active_todo(self):
        if self.approval_state != PLAN_APPROVAL_APPROVED:
            return False
        return any(
            item.status in ACTIONABLE_TODO_STATUSES and not item.system
            for item in self.items
        )

    def has_unverified_completed_criteria(self):
        return any(
            item.status == TODO_STATUS_COMPLETED
            and item.completion_criteria
            and not item.verified
            for item in self.items
        )

    def needs_action_approval(self):
        if not self.items or self.all_completed():
            return False
        if not self.has_actionable_incomplete():
            return False
        return self.approval_state in {
            PLAN_APPROVAL_PENDING,
            PLAN_APPROVAL_REJECTED,
        }

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

    def recommended_next_items(self, limit=3):
        by_id = {item.id: item for item in self.items}
        items = []
        for index, item in enumerate(self.items):
            if item.status not in ACTIONABLE_TODO_STATUSES:
                continue
            ready = _dependencies_completed(item, by_id)
            items.append((not ready, PRIORITY_RANK.get(item.priority, 9), index, item))
        items.sort()
        return [entry[-1] for entry in items[:limit]]

    def status(self, max_tool_calls=None, used_tool_calls=0):
        return {
            "items": self.to_dicts(),
            "active_items": self.ui_items(),
            "has_todos": bool(self.items),
            "has_incomplete": self.has_incomplete(),
            "has_actionable_incomplete": self.has_actionable_incomplete(),
            "has_unverified_completed_criteria": self.has_unverified_completed_criteria(),
            "all_completed": self.all_completed(),
            "revision": self.revision,
            "approval_state": self.approval_state,
            "approval_note": self.approval_note,
            "plan_path": str(self.current_plan_path) if self.current_plan_path else "",
            "events_path": str(self.events_path) if self.events_path else "",
            "quality_warnings": self.quality_warnings(
                max_tool_calls=max_tool_calls,
                used_tool_calls=used_tool_calls,
            ),
            "budget": self.budget_status(max_tool_calls, used_tool_calls),
        }

    def summary(self, include_completed=True):
        items = self.items if include_completed else self.incomplete_items()
        if not items:
            return "(no plan items)"
        return "\n".join(_format_todo_line(item) for item in items)

    def incomplete_summary(self):
        return self.summary(include_completed=False)

    def actionable_summary(self):
        items = self.actionable_items()
        if not items:
            return "(no pending or in-progress plan items)"
        return "\n".join(_format_todo_line(item) for item in items)

    def approval_summary(self):
        state = self.approval_state.replace("_", " ")
        note = f": {self.approval_note}" if self.approval_note else ""
        if not self.items:
            return "Plan approval: not required (no plan items)."
        return f"Plan approval: {state}{note}."

    def budget_status(self, max_tool_calls=None, used_tool_calls=0):
        if max_tool_calls is None:
            return {}
        max_tool_calls = max(1, int(max_tool_calls))
        used_tool_calls = max(0, int(used_tool_calls or 0))
        remaining = max(0, max_tool_calls - used_tool_calls)
        next_items = [
            {
                "id": item.id,
                "content": item.content,
                "priority": item.priority,
                "status": item.status,
            }
            for item in self.recommended_next_items()
        ]
        return {
            "max_tool_calls": max_tool_calls,
            "used_tool_calls": used_tool_calls,
            "remaining_tool_calls": remaining,
            "next": next_items,
        }

    def budget_summary(self, max_tool_calls=None, used_tool_calls=0):
        status = self.budget_status(max_tool_calls, used_tool_calls)
        if not status:
            return ""
        remaining = status["remaining_tool_calls"]
        max_calls = status["max_tool_calls"]
        lines = [f"Tool budget: {remaining}/{max_calls} calls remaining."]
        if status["next"]:
            next_text = ", ".join(
                f"{item['priority'].upper()} {item['id']}"
                for item in status["next"]
            )
            lines.append(f"Next recommended plan items: {next_text}.")
        actionable_count = len(self.actionable_items())
        if actionable_count and remaining <= actionable_count:
            lines.append(
                "Budget is tight: finish ready p0/p1 work first, and mark lower "
                "priority work blocked or pending instead of spending calls on it."
            )
        return "\n".join(lines)

    def quality_warnings(self, max_tool_calls=None, used_tool_calls=0):
        warnings = []
        if not self.items:
            return warnings

        non_system = [item for item in self.items if not item.system]
        actionable = [item for item in non_system if item.status in ACTIONABLE_TODO_STATUSES]
        in_progress = [
            item for item in non_system if item.status == TODO_STATUS_IN_PROGRESS
        ]
        if (
            actionable
            and self.approval_state == PLAN_APPROVAL_APPROVED
            and not in_progress
        ):
            warnings.append("Approved active plan has no in_progress item.")

        generated_ids = [
            item.id for item in non_system if item.id.startswith(("step-", "todo-"))
        ]
        if generated_ids:
            warnings.append(
                "Some plan items use generated ids; stable semantic ids make dependencies "
                f"and recovery safer: {', '.join(generated_ids[:5])}."
            )

        high_without_criteria = [
            item.id
            for item in non_system
            if item.priority in {TODO_PRIORITY_P0, TODO_PRIORITY_P1}
            and not item.completion_criteria
        ]
        if high_without_criteria:
            warnings.append(
                "High-priority plan items should include observable completion_criteria: "
                + ", ".join(high_without_criteria[:5])
                + "."
            )

        unverified = [
            item.id
            for item in non_system
            if item.status == TODO_STATUS_COMPLETED
            and item.completion_criteria
            and not item.verified
        ]
        if unverified:
            warnings.append(
                "Completed plan items with criteria still need verification evidence: "
                + ", ".join(unverified[:5])
                + "."
            )

        vague_blockers = [
            item.id
            for item in non_system
            if item.status in {TODO_STATUS_BLOCKED, TODO_STATUS_FAILED}
            and len(item.reason) < 8
        ]
        if vague_blockers:
            warnings.append(
                "Blocked/failed plan items should have specific reasons: "
                + ", ".join(vague_blockers[:5])
                + "."
            )

        p0_items = [item.id for item in non_system if item.priority == TODO_PRIORITY_P0]
        if len(p0_items) > 2:
            warnings.append(
                "More than two P0 plan items usually means priorities are not selective: "
                + ", ".join(p0_items[:5])
                + "."
            )

        if self.approval_state == PLAN_APPROVAL_PENDING:
            warnings.append("Plan is waiting for approval before action tools can run.")
        elif self.approval_state == PLAN_APPROVAL_REJECTED:
            warnings.append(
                "Plan was rejected; revise it or approve/retry before action tools can run."
            )

        if max_tool_calls is not None:
            budget = self.budget_status(max_tool_calls, used_tool_calls)
            remaining = budget.get("remaining_tool_calls", 0)
            if actionable and remaining <= len(actionable):
                warnings.append(
                    f"Only {remaining} tool calls remain for {len(actionable)} actionable "
                    "plan items; prioritize ready P0/P1 items."
                )

        return warnings

    def quality_report(self, max_tool_calls=None, used_tool_calls=0):
        warnings = self.quality_warnings(max_tool_calls, used_tool_calls)
        if not warnings:
            return "Plan quality: OK."
        return "Plan quality warnings:\n" + "\n".join(f"- {warning}" for warning in warnings)

    def history_tail(self, limit=20):
        limit = max(1, int(limit or 20))
        if not self.events_path or not self.events_path.is_file():
            return []
        try:
            lines = self.events_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
        except OSError:
            return []
        events = []
        for line in lines[-limit:]:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

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
            if changed:
                self._record_event(
                    "final_verification_passed",
                    {"note": note or "Automatic final verification passed."},
                )
        else:
            changed = self._upsert_final_verification_failure(note)
            if changed:
                self._record_event(
                    "final_verification_failed",
                    {"reason": note or "Automatic final verification failed."},
                )

        if changed:
            self.revision += 1
            self._persist()
            self._notify()
        return changed

    def tool_result(self, max_tool_calls=None, used_tool_calls=0):
        parts = []
        if not self.items:
            parts.append("Plan cleared. Plan panel hidden.")
        elif self.all_completed():
            parts.append("All plan items completed. Plan panel hidden.")
            parts.append(self.summary())
        else:
            parts.append("Plan updated:")
            parts.append(self.summary())

        if self.items:
            parts.append(self.approval_summary())
            budget = self.budget_summary(max_tool_calls, used_tool_calls)
            if budget:
                parts.append(budget)
            parts.append(self.quality_report(max_tool_calls, used_tool_calls))
        return "\n".join(part for part in parts if part)

    def _items_from_todos(self, todos):
        if todos is None:
            todos = []
        if not isinstance(todos, list):
            raise ValueError("items must be an array.")

        items = []
        seen_ids = set()
        for index, todo in enumerate(todos, 1):
            if not isinstance(todo, dict):
                raise ValueError(f"items[{index}] must be an object.")
            content = str(
                todo.get("content")
                or todo.get("task")
                or todo.get("title")
                or ""
            ).strip()
            if not content:
                continue

            item_id = str(todo.get("id") or f"step-{index}").strip()
            if item_id in seen_ids:
                raise ValueError(f"Duplicate plan item id: {item_id}")
            seen_ids.add(item_id)

            status = _normalize_todo_status(todo.get("status"))

            reason = str(
                todo.get("reason")
                or todo.get("block_reason")
                or todo.get("failure_reason")
                or ""
            ).strip()
            if status in {TODO_STATUS_BLOCKED, TODO_STATUS_FAILED} and not reason:
                raise ValueError(f"{status} plan item '{item_id}' must include a reason.")

            completion_criteria = _normalize_completion_criteria(
                todo.get("completion_criteria")
                or todo.get("done_when")
                or todo.get("verification")
            )
            verified = _optional_bool(todo.get("verified"), False)
            verification_note = str(todo.get("verification_note") or "").strip()
            if verified and completion_criteria and not verification_note:
                raise ValueError(
                    f"Verified plan item '{item_id}' must include verification_note."
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

        _validate_single_in_progress(items)
        return items

    def _refresh_approval_state(self, structure_changed):
        if not self.items or self.all_completed():
            self.approval_state = PLAN_APPROVAL_NOT_REQUIRED
            self.approval_note = ""
            return
        if not self.has_actionable_incomplete():
            return
        if structure_changed:
            self.approval_state = PLAN_APPROVAL_PENDING
            self.approval_note = "Plan changed and needs approval before acting."
        elif self.approval_state not in VALID_PLAN_APPROVAL_STATES:
            self.approval_state = PLAN_APPROVAL_PENDING
            self.approval_note = "Plan needs approval before acting."

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
        criteria = [
            {
                "type": "diff_check",
                "target": "git diff --check",
                "expected": "Exit code 0 and no whitespace errors",
            }
        ]
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

    def _find_item(self, todo_id):
        todo_id = str(todo_id or "").strip()
        for item in self.items:
            if item.id == todo_id:
                return item
        return None

    def _copy_items(self):
        return [copy.deepcopy(item) for item in self.items]

    def _notify(self):
        if self.on_change is None:
            return
        self.on_change(self.ui_items())

    def _snapshot(self):
        return {
            "version": 1,
            "updated_at": _utc_now(),
            "revision": self.revision,
            "approval_state": self.approval_state,
            "approval_note": self.approval_note,
            "plan_signature": self.plan_signature,
            "items": self.to_dicts(),
            "quality_warnings": self.quality_warnings(),
        }

    def _persist(self):
        if self._loading or not self.current_plan_path:
            return
        try:
            self.plan_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = self.current_plan_path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(self._snapshot(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.current_plan_path)
        except OSError:
            return

    def _load_snapshot(self):
        if not self.current_plan_path or not self.current_plan_path.is_file():
            return
        self._loading = True
        try:
            data = json.loads(
                self.current_plan_path.read_text(encoding="utf-8", errors="replace")
            )
            items = self._items_from_todos(data.get("items") or [])
            _validate_dependencies(items)
            self.items = items
            self.revision = int(data.get("revision") or 0)
            self.approval_state = _normalize_approval_state(
                data.get("approval_state")
            )
            self.approval_note = str(data.get("approval_note") or "").strip()
            self.plan_signature = str(
                data.get("plan_signature") or _plan_signature(items)
            )
        except Exception:
            self.items = []
            self.revision = 0
            self.approval_state = PLAN_APPROVAL_NOT_REQUIRED
            self.approval_note = ""
            self.plan_signature = ""
        finally:
            self._loading = False
        self._notify()

    def _record_update_events(self, old_items, old_approval_state):
        old_by_id = {item.id: item for item in old_items}
        new_by_id = {item.id: item for item in self.items}

        for item in self.items:
            old = old_by_id.get(item.id)
            if old is None:
                self._record_event("plan_item_added", {"item": item.to_dict()})
                continue
            changes = _item_changes(old, item)
            if not changes:
                continue
            event_type = (
                "plan_item_status_changed"
                if set(changes) == {"status"}
                else "plan_item_updated"
            )
            payload = {"id": item.id, "changes": changes}
            if "status" in changes:
                payload["from_status"] = changes["status"]["from"]
                payload["to_status"] = changes["status"]["to"]
            self._record_event(event_type, payload)

        for item in old_items:
            if item.id not in new_by_id:
                self._record_event("plan_item_removed", {"id": item.id, "item": item.to_dict()})

        if old_approval_state != self.approval_state:
            self._record_event(
                "plan_approval_changed",
                {
                    "from": old_approval_state,
                    "to": self.approval_state,
                    "note": self.approval_note,
                },
            )

    def _record_event(self, event_type, payload):
        if self._loading or not self.events_path:
            return
        event = {
            "ts": _utc_now(),
            "type": event_type,
            "revision": self.revision,
            "payload": payload or {},
        }
        try:
            self.plan_dir.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            return


def _validate_dependencies(items):
    by_id = {item.id: item for item in items}
    for item in items:
        for dependency in item.depends_on:
            if dependency == item.id:
                raise ValueError(f"Plan item '{item.id}' cannot depend on itself.")
            if dependency not in by_id:
                raise ValueError(
                    f"Plan item '{item.id}' depends on unknown plan item '{dependency}'."
                )

    visiting = set()
    visited = set()

    def visit(item):
        if item.id in visited:
            return
        if item.id in visiting:
            raise ValueError("Plan item dependencies cannot contain cycles.")
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
                f"Plan item '{item.id}' cannot be {item.status} until dependencies "
                f"are completed: {', '.join(unmet)}"
            )


def _requires_plan_approval(
    old_items,
    new_items,
    old_approval_state,
    structure_changed,
):
    if not structure_changed:
        return False
    return old_approval_state != PLAN_APPROVAL_APPROVED


def _preserve_approved_plan_items(items, old_items, old_approval_state):
    if old_approval_state != PLAN_APPROVAL_APPROVED:
        return items
    if not any(
        item.status in ACTIONABLE_TODO_STATUSES and not item.system
        for item in old_items
    ):
        return items

    old_non_system = [item for item in old_items if not item.system]
    if not items:
        return [copy.deepcopy(item) for item in old_non_system]

    new_by_id = {item.id: item for item in items if not item.system}
    old_positions = {
        item.id: index for index, item in enumerate(old_non_system)
    }
    incoming_active_id = ""
    for item in items:
        if not item.system and item.status == TODO_STATUS_IN_PROGRESS:
            incoming_active_id = item.id
            break

    preserved = []
    used_ids = set()
    for old_item in old_non_system:
        new_item = new_by_id.get(old_item.id)
        if new_item is not None:
            preserved.append(new_item)
            used_ids.add(new_item.id)
            continue

        restored = copy.deepcopy(old_item)
        if restored.status == TODO_STATUS_IN_PROGRESS and incoming_active_id:
            if _incoming_indicates_completed(items, restored.id, old_positions):
                restored.status = TODO_STATUS_COMPLETED
            else:
                restored.status = TODO_STATUS_PENDING
            restored.verified = False
            restored.verification_note = ""
        preserved.append(restored)
        used_ids.add(restored.id)

    for item in items:
        if item.system or item.id in used_ids:
            continue
        preserved.append(item)
        used_ids.add(item.id)
    return preserved


def _incoming_indicates_completed(items, omitted_id, old_positions):
    omitted_position = old_positions.get(omitted_id)
    for item in items:
        if item.system:
            continue
        if item.status not in {TODO_STATUS_IN_PROGRESS, TODO_STATUS_COMPLETED}:
            continue
        if omitted_id in item.depends_on:
            return True
        item_position = old_positions.get(item.id)
        if (
            omitted_position is not None
            and item_position is not None
            and item_position > omitted_position
        ):
            return True
    return False


def _validate_single_in_progress(items):
    in_progress = [
        item.id for item in items if item.status == TODO_STATUS_IN_PROGRESS
    ]
    if len(in_progress) > 1:
        raise ValueError("Only one plan item can be in_progress at a time.")


def _hold_unapproved_progress(
    items,
    old_items,
    old_approval_state,
    approval_required,
):
    if not approval_required:
        return

    old_by_id = {item.id: item for item in old_items}
    for item in items:
        if item.system or item.status not in {
            TODO_STATUS_IN_PROGRESS,
            TODO_STATUS_COMPLETED,
        }:
            continue

        old_item = old_by_id.get(item.id)
        if (
            old_approval_state == PLAN_APPROVAL_APPROVED
            and old_item is not None
            and old_item.status == TODO_STATUS_COMPLETED
        ):
            continue

        item.status = TODO_STATUS_PENDING
        item.verified = False
        item.verification_note = ""


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


def _normalize_approval_state(state):
    value = str(state or PLAN_APPROVAL_NOT_REQUIRED).strip().lower()
    if value not in VALID_PLAN_APPROVAL_STATES:
        return PLAN_APPROVAL_NOT_REQUIRED
    return value


def _normalize_completion_criteria(value):
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [part.strip() for part in value.split(";")]
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = [value]

    criteria = []
    for index, raw in enumerate(raw_items, 1):
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                continue
            criteria.append(
                {
                    "type": CRITERION_TYPE_MANUAL,
                    "target": "",
                    "expected": text,
                }
            )
            continue
        if not isinstance(raw, dict):
            text = str(raw).strip()
            if text:
                criteria.append(
                    {
                        "type": CRITERION_TYPE_MANUAL,
                        "target": "",
                        "expected": text,
                    }
                )
            continue

        criterion_type = str(
            raw.get("type") or raw.get("kind") or CRITERION_TYPE_MANUAL
        ).strip().lower().replace("-", "_")
        if criterion_type not in VALID_CRITERION_TYPES:
            raise ValueError(
                f"completion_criteria[{index}].type is invalid: {criterion_type}"
            )
        target = str(raw.get("target") or raw.get("command") or raw.get("file") or "").strip()
        expected = str(
            raw.get("expected")
            or raw.get("expect")
            or raw.get("condition")
            or raw.get("description")
            or ""
        ).strip()
        if not target and not expected:
            raise ValueError(
                f"completion_criteria[{index}] must include target or expected."
            )
        criterion = {"type": criterion_type, "target": target, "expected": expected}
        if raw.get("id"):
            criterion["id"] = str(raw.get("id")).strip()
        criteria.append(criterion)
    return criteria


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
        marker = "[" + chr(0x2713) + "]"
    elif item.status == TODO_STATUS_BLOCKED:
        marker = "[!]"
    elif item.status == TODO_STATUS_FAILED:
        marker = "[x]"

    details = [f"id: {item.id}", item.priority.upper()]
    if item.depends_on:
        details.append("after: " + ", ".join(item.depends_on))
    if item.completion_criteria:
        details.append("done when: " + _format_criteria(item.completion_criteria))
    if item.verified:
        details.append("verified")
    if item.reason:
        details.append("reason: " + item.reason)
    return f"{marker} {item.content} ({'; '.join(details)})"


def _format_criteria(criteria):
    parts = []
    for criterion in criteria:
        criterion_type = criterion.get("type") or CRITERION_TYPE_MANUAL
        target = criterion.get("target") or ""
        expected = criterion.get("expected") or ""
        if target and expected:
            parts.append(f"{criterion_type}:{target} => {expected}")
        else:
            parts.append(expected or f"{criterion_type}:{target}")
    return "; ".join(parts)


def _plan_signature(items):
    structural = []
    for item in items:
        if item.system:
            continue
        structural.append(
            {
                "id": item.id,
                "content": item.content,
                "priority": item.priority,
                "depends_on": list(item.depends_on),
                "completion_criteria": [
                    dict(criterion) for criterion in item.completion_criteria
                ],
            }
        )
    encoded = json.dumps(structural, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _dependencies_completed(item, by_id):
    for dependency_id in item.depends_on:
        dependency = by_id.get(dependency_id)
        if dependency is None or dependency.status != TODO_STATUS_COMPLETED:
            return False
    return True


def _item_changes(old, new):
    changes = {}
    old_data = old.to_dict()
    new_data = new.to_dict()
    for key in sorted(set(old_data) | set(new_data)):
        old_value = old_data.get(key)
        new_value = new_data.get(key)
        if old_value != new_value:
            changes[key] = {"from": old_value, "to": new_value}
    return changes


def _single_line(text, max_chars):
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _utc_now():
    return datetime.now(timezone.utc).isoformat()

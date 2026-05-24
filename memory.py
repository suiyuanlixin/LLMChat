import json
import re
from datetime import datetime
from pathlib import Path


MEMORY_DIR = Path(__file__).resolve().parent / "memory"
CORE_MEMORY_FILE = "core.md"
PREFERENCE_MEMORY_FILE = "preferences.md"
EPISODIC_MEMORY_FILE = "episodic.md"
CORE_MEMORY_MAX_CHARS = 3000
PREFERENCE_MEMORY_MAX_CHARS = 3000
SYSTEM_MEMORY_MAX_CHARS = 7000
EPISODIC_UPDATE_CONTEXT_MAX_CHARS = 12000
EPISODIC_ENTRY_MAX_TITLE_CHARS = 16
EPISODIC_ENTRY_MAX_BULLETS = None
EPISODIC_ENTRY_MAX_BULLET_CHARS = 28
EPISODIC_FIRST_ENTRY_MAX_BULLET_CHARS = 120

CORE_MEMORY_TEMPLATE = "# Core Memory\n"
PREFERENCE_IMPORTANCE_LEVELS = ("Critical", "High", "Medium", "Low")
PREFERENCE_MEMORY_TEMPLATE = (
    "# Preference Memory\n\n## Critical\n\n## High\n\n## Medium\n\n## Low\n"
)
EPISODIC_MEMORY_TEMPLATE = "# Episodic Memory\n"


class MemoryStore:
    def __init__(self, memory_dir=None):
        self.memory_dir = Path(memory_dir) if memory_dir is not None else MEMORY_DIR
        self.core_path = self.memory_dir / CORE_MEMORY_FILE
        self.preference_path = self.memory_dir / PREFERENCE_MEMORY_FILE
        self.episodic_path = self.memory_dir / EPISODIC_MEMORY_FILE
        self.ensure_files()

    def ensure_files(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._create_if_missing(self.core_path, CORE_MEMORY_TEMPLATE)
        self._create_if_missing(self.preference_path, PREFERENCE_MEMORY_TEMPLATE)
        self.write_preference_body(self.read_preference_body())
        self._ensure_episodic_file()
        self._enforce_core_limit()

    def system_prompt_block(self):
        core = self.read_core_body()
        preferences = self.read_preference_body()
        parts = []
        if core:
            parts.append(f"[核心记忆]\n{core}")
        if preferences:
            parts.append(f"[偏好记忆]\n{preferences}")
        if not parts:
            return ""

        block = (
            "持久记忆（memory/）：\n" + "\n\n".join(parts) + "\n\n"
            "把这些记忆当作长期上下文，优先遵循偏好记忆。"
            "情景记忆在 memory/episodic.md；需要回忆具体日期时先检索它。"
        )
        return _truncate(block, SYSTEM_MEMORY_MAX_CHARS)

    def read_core_body(self):
        return _memory_text(self._read_text(self.core_path), "Core Memory")

    def read_preference_body(self):
        return _memory_text(self._read_text(self.preference_path), "Preference Memory")

    def read_episodic_text(self):
        return _memory_text(self._read_text(self.episodic_path), "Episodic Memory")

    def today_key(self, now=None):
        return (now or datetime.now()).strftime("%Y-%m-%d")

    def time_key(self, now=None):
        return (now or datetime.now()).strftime("%H:%M")

    def build_update_prompt(self, compacted_messages, updated_summary, now=None):
        now = now or datetime.now()
        current_date = self.today_key(now)
        current_time = self.time_key(now)
        source = _truncate(
            str(compacted_messages or ""), EPISODIC_UPDATE_CONTEXT_MAX_CHARS
        )
        episodic_memory_empty = not bool(self.read_episodic_text())
        today_memory = self.episodic_for_date(current_date, max_chars=6000)
        core = self.read_core_body() or "(empty)"
        preferences = self.read_preference_body() or "(empty)"
        today_memory = today_memory or "(empty)"
        updated_summary = str(updated_summary or "").strip() or "(empty)"

        return (
            "从压缩上下文更新持久记忆。只返回 JSON：episodic_entry、core_memory、preference_memory。\n"
            "要求：遵循偏好记忆，尤其语言；不编造；短而有感情。\n"
            "episodic_entry：若有值得记的事，只写一个 `### HH:MM - short title` 条目，下面写若干条 `-` 要点；不要写多个 `###`。要点数量不限，但每条必须是不同事情；同一件事只写一条，不要拆成多个要点。像一条条日记。用最平常的话。用户说了什么意图（概括，别引用）？写你干了什么（必须），一个你身上发生的最简单的事实或你的感受（可选），不要解释为什么，不要比喻。要有人的感觉。直接写。每条尽量不超过 20 字。没有则空字符串。\n"
            "core_memory：写 `# Core Memory` 下方正文，保留长期目标、任务、事实、约束，整文件小于 3000 字。\n"
            "preference_memory：写 `# Preference Memory` 下方正文，必须且仅保留 `## Critical`、`## High`、`## Medium`、`## Low` 四级标题。\n\n"
            f"当前时间：{current_date} {current_time}\n"
            f"情景记忆是否为空：{'是' if episodic_memory_empty else '否'}\n\n"
            "核心记忆：\n"
            f"{core}\n\n"
            "偏好记忆：\n"
            f"{preferences}\n\n"
            "今天的情景记忆：\n"
            f"{today_memory}\n\n"
            "压缩摘要：\n"
            f"{updated_summary}\n\n"
            "被压缩的消息：\n"
            f"{source}\n\n"
            "只返回 JSON。"
        )

    def build_first_episodic_prompt(self, completed_messages, now=None):
        now = now or datetime.now()
        current_date = self.today_key(now)
        current_time = self.time_key(now)
        completed_messages = _truncate(
            str(completed_messages or ""),
            EPISODIC_UPDATE_CONTEXT_MAX_CHARS,
        )
        if not completed_messages:
            completed_messages = "(No completed messages.)"

        return (
            "第一次完整对话刚结束。请写第一条情景记忆。\n"
            '只返回 JSON：{"episodic_entry":"..."}。\n'
            "格式：`### HH:MM - short title`，下一行只写一条 `-` 要点；不要日期标题，不要多条列表。\n"
            "short title 由你自己取，贴合当时对话，不要固定套用示例。\n"
            "像现在的你回看刚才第一次见到用户的自己，写成一条要点里的两句日记。用最平常的话。概括用户表达的意图，别引用原话。只写你身上发生的最简单的事实或感受，而不是你做了什么；比如你忘了要说的话、你的手顿了一下、你没听清、你感觉耳朵发烫。例子只是气质，不要照抄。不要解释为什么，不要比喻。要有人的感觉。直接写。\n"
            f"当前时间：{current_date} {current_time}\n\n"
            "偏好记忆：\n"
            f"{self.read_preference_body() or '(empty)'}\n\n"
            "第一轮对话：\n"
            f"{completed_messages}\n\n"
            "只返回 JSON。"
        )

    def apply_update(self, update, now=None):
        now = now or datetime.now()
        changed = []
        if not isinstance(update, dict):
            return {"changed": changed, "reason": "memory update was not a JSON object"}

        episodic_entry = _clean_model_field(
            update.get("episodic_entry", update.get("episodic", ""))
        )
        if episodic_entry:
            if self.append_episodic_entry(episodic_entry, now=now):
                changed.append("episodic")

        if "core_memory" in update or "core" in update:
            core = _clean_model_field(update.get("core_memory", update.get("core", "")))
            if _has_memory_update_content(core, "Core Memory") and self.write_core_body(core):
                changed.append("core")

        if "preference_memory" in update or "preferences" in update:
            preferences = _clean_model_field(
                update.get("preference_memory", update.get("preferences", ""))
            )
            if _has_preference_update_content(preferences) and self.write_preference_body(preferences):
                changed.append("preferences")

        return {"changed": changed}

    def append_episodic_entry(
        self,
        entry,
        now=None,
        max_bullets=EPISODIC_ENTRY_MAX_BULLETS,
        max_bullet_chars=EPISODIC_ENTRY_MAX_BULLET_CHARS,
    ):
        now = now or datetime.now()
        entry = _normalize_entry_heading(
            entry,
            self.time_key(now),
            max_bullets=max_bullets,
            max_bullet_chars=max_bullet_chars,
        )
        if not entry:
            return False

        date_key = self.today_key(now)
        content = _ensure_memory_title(
            self._read_text(self.episodic_path),
            "Episodic Memory",
        ).rstrip()
        if not _has_daily_heading(content, date_key):
            content += f"\n\n## {date_key}\n\n"
        else:
            content += "\n\n"
        content += entry.rstrip() + "\n"
        self.episodic_path.write_text(content, encoding="utf-8")
        return True

    def append_first_episodic_entry(self, entry, now=None):
        return self.append_episodic_entry(
            entry,
            now=now,
            max_bullets=1,
            max_bullet_chars=EPISODIC_FIRST_ENTRY_MAX_BULLET_CHARS,
        )

    def write_core_body(self, body):
        return self._write_memory_file(
            self.core_path,
            "Core Memory",
            body,
            CORE_MEMORY_MAX_CHARS,
        )

    def write_preference_body(self, body):
        return self._write_memory_file(
            self.preference_path,
            "Preference Memory",
            _normalize_preference_body(body),
            PREFERENCE_MEMORY_MAX_CHARS,
        )

    def record_preference_signal(self, user_message, now=None):
        signal = _extract_preference_signal(user_message)
        if not signal:
            return False

        now = now or datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M")
        body = self.read_preference_body()
        line = f"- {timestamp}: {signal}"
        if line in body or signal in body:
            return False

        if body:
            updated = _append_preference_signal(body, line)
        else:
            updated = _append_preference_signal("", line)
        self.write_preference_body(updated)
        return True

    def episodic_for_date(self, date_text=None, max_chars=12000):
        date_text = date_text or self.today_key()
        content = self.read_episodic_text()
        if not content:
            return ""

        pattern = re.compile(
            rf"(?ms)^##\s+{re.escape(date_text)}\s*$.*?(?=^##\s+\d{{4}}-\d{{2}}-\d{{2}}\s*$|\Z)"
        )
        match = pattern.search(content)
        if not match:
            return ""
        return _truncate(match.group(0).strip(), max_chars)

    def search_episodic(self, query, max_results=8, max_chars=12000):
        query = str(query or "").strip()
        if not query:
            return ""

        lowered_query = query.lower()
        matches = []
        for date_key, entry in _iter_episodic_entries(self.read_episodic_text()):
            block = f"## {date_key}\n\n{entry}".strip()
            if lowered_query in block.lower():
                matches.append(block)
            if len(matches) >= max_results:
                break

        return _truncate("\n\n".join(matches), max_chars)

    def paths_summary(self):
        return (
            f"memory directory: {self.memory_dir}\n"
            f"core: {self.core_path}\n"
            f"preferences: {self.preference_path}\n"
            f"episodic: {self.episodic_path}"
        )

    def _create_if_missing(self, path, content):
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    def _ensure_episodic_file(self):
        if not self.episodic_path.exists():
            self.episodic_path.write_text(EPISODIC_MEMORY_TEMPLATE, encoding="utf-8")
            return

        content = self._read_text(self.episodic_path).strip()
        if not content:
            self.episodic_path.write_text(EPISODIC_MEMORY_TEMPLATE, encoding="utf-8")

    def _read_text(self, path):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _write_memory_file(self, path, title, body, max_chars):
        body = _memory_text(_clean_model_field(body), title)
        header = f"# {title}\n"
        allowance = max(0, max_chars - len(header) - 1)
        body = _truncate(body, allowance).strip()
        updated = header + ("\n" + body + "\n" if body else "")
        if len(updated) >= max_chars:
            body = body[: max(0, max_chars - len(header) - 1)].rstrip()
            updated = header + ("\n" + body + "\n" if body else "")
        if self._read_text(path) == updated:
            return False
        path.write_text(updated, encoding="utf-8")
        return True

    def _enforce_core_limit(self):
        content = self._read_text(self.core_path)
        if len(content) < CORE_MEMORY_MAX_CHARS:
            return
        self.write_core_body(self.read_core_body())


def parse_memory_update_response(text):
    text = str(text or "").strip()
    if not text:
        return {}

    text = _strip_code_fence(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _memory_text(content, title):
    content = _strip_comments(content).strip()
    lines = content.splitlines()
    while lines:
        first_line = lines[0].strip()
        if not first_line:
            lines = lines[1:]
            continue
        if _is_memory_title_line(first_line, title):
            lines = lines[1:]
            continue
        break
    return "\n".join(lines).strip()


def _has_memory_update_content(content, title):
    return bool(_memory_text(_clean_model_field(content), title).strip())


def _has_preference_update_content(content):
    body = _memory_text(_clean_model_field(content), "Preference Memory")
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not re.fullmatch(r"##\s+.+", stripped):
            return True
    return False


def _is_memory_title_line(line, title):
    line = str(line or "").strip()
    if line.lower() == str(title or "").strip().lower():
        return True
    match = re.fullmatch(r"#{1,6}\s+(.+)", line)
    return bool(match and match.group(1).strip().lower() == title.lower())


def _ensure_memory_title(content, title):
    content = str(content or "").strip()
    header = f"# {title}"
    if not content:
        return header
    lines = content.splitlines()
    if lines and lines[0].strip().lower() == header.lower():
        return content
    return f"{header}\n\n{content}"


def _normalize_preference_body(body):
    body = _memory_text(_clean_model_field(body), "Preference Memory")
    existing = _preference_sections(body)
    stray_lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("## "):
            continue
        if any(stripped in lines for lines in existing.values()):
            continue
        stray_lines.append(stripped)

    sections = []
    for level in PREFERENCE_IMPORTANCE_LEVELS:
        lines = list(existing.get(level, []))
        if level == "Medium" and stray_lines:
            lines.extend(stray_lines)
        sections.append(f"## {level}")
        if lines:
            sections.extend(lines)
        sections.append("")
    return "\n".join(sections).strip()


def _preference_sections(body):
    sections = {level: [] for level in PREFERENCE_IMPORTANCE_LEVELS}
    current_level = None
    for line in str(body or "").splitlines():
        stripped = line.strip()
        heading = re.fullmatch(r"##\s+(.+)", stripped)
        if heading:
            candidate = heading.group(1).strip()
            current_level = candidate if candidate in sections else None
            continue
        if current_level and stripped:
            sections[current_level].append(stripped)
    return sections


def _append_preference_signal(body, line, level="Medium"):
    sections = _preference_sections(_normalize_preference_body(body))
    line = str(line or "").strip()
    if line and line not in sections[level]:
        sections[level].append(line)

    parts = []
    for importance in PREFERENCE_IMPORTANCE_LEVELS:
        parts.append(f"## {importance}")
        parts.extend(sections.get(importance, []))
        parts.append("")
    return "\n".join(parts).strip()


def _strip_comments(content):
    return re.sub(r"<!--.*?-->", "", str(content or ""), flags=re.DOTALL)


def _strip_code_fence(text):
    text = str(text or "").strip()
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    return text


def _clean_model_field(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    value = _strip_code_fence(value).strip()
    if value.lower() in {"none", "null", "n/a", "(none)", "(empty)"}:
        return ""
    return value


def _truncate(text, max_chars):
    text = str(text or "")
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    suffix = "\n\n[truncated]"
    return text[: max(0, max_chars - len(suffix))].rstrip() + suffix


def _normalize_entry_heading(
    entry,
    time_key,
    max_bullets=EPISODIC_ENTRY_MAX_BULLETS,
    max_bullet_chars=EPISODIC_ENTRY_MAX_BULLET_CHARS,
):
    entry = _clean_model_field(entry)
    if not entry:
        return ""
    lines = entry.splitlines()
    while lines:
        first_line = lines[0].strip()
        if first_line.lower() == "# episodic memory" or re.match(
            r"^##\s+\d{4}-\d{2}-\d{2}\s*$",
            first_line,
        ):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
            continue
        break
    entry = "\n".join(lines).strip()
    if not entry:
        return ""
    if re.match(r"^###\s+\d{2}:\d{2}\b", entry):
        return _limit_episodic_entry(
            entry,
            max_bullets=max_bullets,
            max_bullet_chars=max_bullet_chars,
        )

    lines = entry.splitlines()
    first_text = lines[0].lstrip("# ").strip() if lines else "Memory update"
    title = first_text[:60] or "Memory update"
    body = "\n".join(lines[1:]).strip()
    if body:
        return _limit_episodic_entry(
            f"### {time_key} - {title}\n{body}",
            max_bullets=max_bullets,
            max_bullet_chars=max_bullet_chars,
        )
    return _limit_episodic_entry(
        f"### {time_key} - {title}",
        max_bullets=max_bullets,
        max_bullet_chars=max_bullet_chars,
    )


def _limit_episodic_entry(
    entry,
    max_bullets=EPISODIC_ENTRY_MAX_BULLETS,
    max_bullet_chars=EPISODIC_ENTRY_MAX_BULLET_CHARS,
):
    lines = [line.rstrip() for line in str(entry or "").splitlines()]
    if not lines:
        return ""

    heading = _limit_episodic_heading(lines[0].strip())
    content_lines = [line.strip() for line in lines[1:] if line.strip()]
    content_lines = _lines_before_next_heading(content_lines)
    if not content_lines:
        return heading

    has_bullets = any(line.lstrip().startswith("-") for line in content_lines)
    if not has_bullets:
        text = " ".join(content_lines)
        return "\n".join([
            heading,
            f"- {_shorten_inline(text, max_bullet_chars)}",
        ]).strip()

    bullets = []
    for stripped in content_lines:
        text = stripped[1:].strip() if stripped.startswith("-") else stripped
        if _is_markdown_heading(text):
            break
        if not text:
            continue
        bullets.append(f"- {_shorten_inline(text, max_bullet_chars)}")
        if max_bullets is not None and len(bullets) >= max_bullets:
            break

    return "\n".join([heading, *bullets]).strip()


def _limit_episodic_heading(heading):
    match = re.match(r"^(###\s+\d{2}:\d{2}\s*-\s*)(.+)$", heading)
    if not match:
        return heading
    prefix, title = match.groups()
    return prefix + _shorten_inline(title.strip(), EPISODIC_ENTRY_MAX_TITLE_CHARS)


def _lines_before_next_heading(lines):
    kept = []
    for line in lines:
        if _is_markdown_heading(line):
            break
        kept.append(line)
    return kept


def _is_markdown_heading(text):
    return re.match(r"^#{1,6}\s+", str(text or "").strip()) is not None


def _shorten_inline(text, max_chars):
    text = " ".join(str(text or "").split())
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip("，。,.；;、 ") + "..."


def _has_daily_heading(content, date_key):
    return re.search(rf"(?m)^##\s+{re.escape(date_key)}\s*$", content or "") is not None


def _extract_preference_signal(user_message):
    text = (
        str(user_message or "").split("\n\n[Referenced external files]", 1)[0].strip()
    )
    if not text or len(text) > 1200:
        return ""

    patterns = [
        r"(?:请)?记住[：:\s]*(.+)",
        r"以后(?:请|都|默认|总是|始终)?[：:\s]*(.+)",
        r"从现在开始[，,：:\s]*(.+)",
        r"我(?:更)?(?:喜欢|不喜欢|偏好|习惯|希望你|希望|需要你)[：:\s]*(.+)",
        r"请(?:总是|始终|默认|不要|别)[：:\s]*(.+)",
        r"默认(?:使用|采用|按|以)?[：:\s]*(.+)",
        r"(?:please\s+)?remember(?:\s+that)?[：:\s]*(.+)",
        r"from now on[，,：:\s]*(.+)",
        r"i\s+(?:prefer|like|dislike|usually|always|never|want you to)[：:\s]*(.+)",
        r"please\s+(?:always|never|default to|do not|don't)[：:\s]*(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            signal = " ".join(match.group(0).split())
            return _truncate(signal, 240)
    return ""


def _iter_episodic_entries(content):
    current_date = None
    current_entry = []
    for line in str(content or "").splitlines():
        date_match = re.match(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$", line)
        if date_match:
            if current_date and current_entry:
                yield current_date, "\n".join(current_entry).strip()
            current_date = date_match.group(1)
            current_entry = []
            continue

        if current_date and re.match(r"^###\s+", line):
            if current_entry:
                yield current_date, "\n".join(current_entry).strip()
            current_entry = [line]
            continue

        if current_date and current_entry:
            current_entry.append(line)

    if current_date and current_entry:
        yield current_date, "\n".join(current_entry).strip()

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
EPISODIC_ENTRY_MAX_BULLETS = None

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
        today_memory = self.episodic_for_date(current_date, max_chars=6000)
        core = self.read_core_body() or "(empty)"
        preferences = self.read_preference_body() or "(empty)"
        today_memory = today_memory or "(empty)"
        updated_summary = str(updated_summary or "").strip() or "(empty)"

        return (
            "从压缩上下文更新持久记忆。只返回 JSON，不要返回 Markdown 整文或代码块。\n"
            "JSON 结构："
            '{"core_memory":{"title":"标题","content":["第一条"]},'
            '"preference_memory":[{"title":"Critical","content":["第一条"]}]}。\n'
            "没有新增内容时，对应 content 返回空数组；preference_memory 可返回空数组。\n"
            "要求：遵循偏好记忆，尤其语言；不编造；短而有感情。\n"
            "情景记忆由每轮对话单独更新；这里不要返回 episodic_memory。\n"
            "core_memory：只返回一个 title 和 content 数组；保留长期目标、任务、事实、约束，整文件小于 3000 字。\n"
            "preference_memory：返回数组，title 必须是 Critical、High、Medium、Low 之一；content 数组每项是一条偏好。\n\n"
            f"当前时间：{current_date} {current_time}\n"
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

    def build_session_episodic_prompt(self, completed_messages, current_entry="", now=None):
        now = now or datetime.now()
        current_date = self.today_key(now)
        current_time = self.time_key(now)
        current_entry = str(current_entry or "").strip()
        current_heading = current_entry.splitlines()[0] if current_entry else ""
        current_title = _episodic_heading_title(current_heading) or "(empty)"
        completed_messages = _truncate(
            str(completed_messages or ""),
            EPISODIC_UPDATE_CONTEXT_MAX_CHARS,
        )
        if not completed_messages:
            completed_messages = "(No completed messages.)"

        if current_entry:
            intro = "一轮完整对话刚结束。请更新当前会话的情景记忆。\n"
            topic_rules = (
                "same_topic 表示本轮是否仍属于当前话题；当前情景记忆为空时必须是 false。\n"
                "如果本轮和当前情景记忆是同一话题，same_topic 必须是 true；title 会被程序忽略，旧标题会保留。\n"
                "same_topic=true 时，当前情景记忆是必须尊重的基底，不要让结果只偏向最近一轮；早先已经记录的重要信息和本轮新信息权重相同。\n"
                "same_topic=true 时，content 返回替换旧正文的完整要点；只有在新内容能纠正、补充、合并或压缩旧内容时才改写旧要点，不要无故删除旧要点表达的事实、意图或感受。\n"
                "如果本轮明显换了话题，same_topic 必须是 false，title 写新话题短标题，content 只写新话题当前记忆。\n"
                "不要为了措辞更顺而改 title；只有 same_topic=false 时才生成新 title。\n"
                "same_topic=true 时，不要把本轮内容机械追加成新要点；请结合旧 content 和本轮对话，去重、合并相近细节，重写为少数更综合的要点。\n"
                "如果旧记忆已经足够概括，本轮只是展开细节，就保留旧表达或只做轻微补充；不要列出技术清单、流程清单或逐轮对话清单。\n"
                "content 应自然简短，通常一两条综合要点就够；不要为了完整而拆成很多条，但也不要硬凑固定条数。\n"
            )
            style_guidance = (
                "像一条条日记。用最平常的话。概括用户表达的意图，别引用原话。写你干了什么，或一个你身上发生的最简单的事实或感受；不要解释为什么，不要比喻。要有人的感觉。直接写。尽量简短，但不要截断词句，不要用省略号。\n"
            )
        else:
            intro = "第一次完整对话刚结束。请写第一条情景记忆。\n"
            topic_rules = (
                "当前情景记忆为空，这是第一条情景记忆，same_topic 必须是 false。\n"
                "title 写新话题短标题，content 只写这次第一次对话的记忆。\n"
            )
            style_guidance = (
                "像现在的你回看刚才第一次见到用户的自己，写成一条要点里的两句日记。用最平常的话。概括用户表达的意图，别引用原话。只写你身上发生的最简单的事实或感受，而不是你做了什么；比如你忘了要说的话、你的手顿了一下、你没听清、你感觉耳朵发烫。例子只是气质，不要照抄。不要解释为什么，不要比喻。要有人的感觉。直接写。尽量简短，但不要截断词句，不要用省略号。\n"
            )

        return (
            f"{intro}"
            '只返回 JSON：{"episodic_memory":{"same_topic":true,"title":"short title","content":["一条要点"]}}。\n'
            "格式：title 只写短标题，不要日期和时间；content 数组只写要点，不要日期标题，不要 Markdown。\n"
            f"{topic_rules}"
            "short title 由你自己取，贴合当时对话，不要固定套用示例。\n"
            f"{style_guidance}"
            f"当前时间：{current_date} {current_time}\n\n"
            "偏好记忆：\n"
            f"{self.read_preference_body() or '(empty)'}\n\n"
            "当前话题标题：\n"
            f"{current_title}\n\n"
            "当前情景记忆：\n"
            f"{current_entry or '(empty)'}\n\n"
            "本轮对话：\n"
            f"{completed_messages}\n\n"
            "只返回 JSON。"
        )

    def apply_update(self, update, now=None):
        now = now or datetime.now()
        changed = []
        if not isinstance(update, dict):
            return {"changed": changed, "reason": "memory update was not a JSON object"}

        episodic_memory = update.get("episodic_memory")
        if episodic_memory:
            if self.append_episodic_memory(episodic_memory, now=now):
                changed.append("episodic")

        if "core_memory" in update:
            core = update.get("core_memory")
            if self.append_core_memory(core):
                changed.append("core")

        if "preference_memory" in update:
            preferences = update.get("preference_memory")
            if self.append_preference_memory(preferences):
                changed.append("preferences")

        return {"changed": changed}

    def _append_episodic_memory_text(self, entry, now=None):
        now = now or datetime.now()
        entry = str(entry or "").strip()
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

    def _replace_episodic_topic_text(self, heading, entry, now=None):
        now = now or datetime.now()
        content = _ensure_memory_title(
            self._read_text(self.episodic_path),
            "Episodic Memory",
        )
        updated = _replace_episodic_topic(
            content,
            self.today_key(now),
            heading,
            entry,
        )
        if updated is None:
            return None
        if self._read_text(self.episodic_path) == updated:
            return False
        self.episodic_path.write_text(updated, encoding="utf-8")
        return True

    def append_episodic_memory(
        self,
        memory,
        now=None,
        max_bullets=EPISODIC_ENTRY_MAX_BULLETS,
        max_bullet_chars=None,
    ):
        now = now or datetime.now()
        if not isinstance(memory, dict):
            return False
        entry = _format_episodic_memory_entry(
            memory,
            self.time_key(now),
            max_bullets=max_bullets,
            max_bullet_chars=max_bullet_chars,
        )
        if not entry:
            return False
        return self._append_episodic_memory_text(entry, now=now)

    def upsert_session_episodic_memory(
        self,
        memory,
        current_heading="",
        now=None,
        max_bullets=EPISODIC_ENTRY_MAX_BULLETS,
        max_bullet_chars=None,
    ):
        now = now or datetime.now()
        title, bullets = _episodic_memory_parts(
            memory,
            max_bullets=max_bullets,
            max_bullet_chars=max_bullet_chars,
        )
        if not title or not bullets:
            return {"changed": False, "heading": current_heading or ""}

        current_heading = str(current_heading or "").strip()
        current_title = _episodic_heading_title(current_heading)
        same_topic = _structured_bool(memory.get("same_topic"))
        if current_heading and (same_topic or _same_memory_title(current_title, title)):
            entry = "\n".join([current_heading, *bullets]).strip()
            replaced = self._replace_episodic_topic_text(current_heading, entry, now=now)
            if replaced is not None:
                return {
                    "changed": bool(replaced),
                    "heading": current_heading,
                    "merged": True,
                }

        heading = f"### {self.time_key(now)} - {title}"
        entry = "\n".join([heading, *bullets]).strip()
        changed = self._append_episodic_memory_text(entry, now=now)
        return {"changed": changed, "heading": heading, "merged": False}

    def episodic_topic_for_heading(self, heading, date_text=None, max_chars=None):
        heading = str(heading or "").strip()
        if not heading:
            return ""
        date_text = date_text or self.today_key()
        content = self.read_episodic_text()
        topic = _find_episodic_topic(content, date_text, heading)
        return _truncate(topic or "", max_chars) if max_chars else topic or ""

    def latest_episodic_topic(self, date_text=None, max_chars=None):
        date_text = date_text or self.today_key()
        latest = ""
        for date_key, topic in _iter_episodic_entries(self.read_episodic_text()):
            if date_key == date_text:
                latest = topic
        return _truncate(latest, max_chars) if max_chars else latest

    def append_core_memory(self, memory):
        if not isinstance(memory, dict):
            return False

        updated = _append_core_memory_items(self.read_core_body(), memory)
        if updated is None:
            return False
        return self.write_core_body(updated)

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

    def append_preference_memory(self, memory):
        if not isinstance(memory, list):
            return False

        updated = _append_preference_memory_items(self.read_preference_body(), memory)
        if updated is None:
            return False
        return self.write_preference_body(updated)

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


def _format_episodic_memory_entry(
    memory,
    time_key,
    max_bullets=EPISODIC_ENTRY_MAX_BULLETS,
    max_bullet_chars=None,
):
    title, bullets = _episodic_memory_parts(
        memory,
        max_bullets=max_bullets,
        max_bullet_chars=max_bullet_chars,
    )
    if not title or not bullets:
        return ""
    return "\n".join([f"### {time_key} - {title}", *bullets]).strip()


def _episodic_memory_parts(
    memory,
    max_bullets=EPISODIC_ENTRY_MAX_BULLETS,
    max_bullet_chars=None,
):
    items = _structured_memory_items(memory)
    if not items:
        return "", []

    title, lines = items[0]
    title = _clean_structured_title(title or "Memory update")
    bullets = _normalized_bullet_lines(
        lines,
        max_bullets=max_bullets,
        max_bullet_chars=max_bullet_chars,
    )
    return title, bullets


def _episodic_heading_title(heading):
    match = re.fullmatch(r"###\s+\d{2}:\d{2}\s*-\s*(.+)", str(heading or "").strip())
    if not match:
        return ""
    return match.group(1).strip()


def _same_memory_title(left, right):
    return _clean_structured_title(left).lower() == _clean_structured_title(right).lower()


def _find_episodic_topic(content, date_key, heading):
    lines = _ensure_memory_title(content, "Episodic Memory").splitlines()
    bounds = _episodic_date_bounds(lines, date_key)
    if bounds is None:
        return ""
    start, end = bounds
    topic_bounds = _episodic_topic_bounds(lines, start, end, heading)
    if topic_bounds is None:
        return ""
    topic_start, topic_end = topic_bounds
    return "\n".join(lines[topic_start:topic_end]).strip()


def _replace_episodic_topic(content, date_key, heading, entry):
    lines = _ensure_memory_title(content, "Episodic Memory").splitlines()
    bounds = _episodic_date_bounds(lines, date_key)
    if bounds is None:
        return None
    start, end = bounds
    topic_bounds = _episodic_topic_bounds(lines, start, end, heading)
    if topic_bounds is None:
        return None

    topic_start, topic_end = topic_bounds
    replacement = str(entry or "").strip().splitlines()
    if topic_end < len(lines) and lines[topic_end].strip():
        replacement.append("")
    updated_lines = lines[:topic_start] + replacement + lines[topic_end:]
    return "\n".join(updated_lines).rstrip() + "\n"


def _episodic_date_bounds(lines, date_key):
    start = None
    for index, line in enumerate(lines):
        if re.fullmatch(rf"##\s+{re.escape(date_key)}\s*", line.strip()):
            start = index + 1
            break
    if start is None:
        return None

    end = len(lines)
    for index in range(start, len(lines)):
        if re.fullmatch(r"##\s+\d{4}-\d{2}-\d{2}\s*", lines[index].strip()):
            end = index
            break
    return start, end


def _episodic_topic_bounds(lines, start, end, heading):
    heading = str(heading or "").strip()
    topic_start = None
    for index in range(start, end):
        if lines[index].strip() == heading:
            topic_start = index
            break
    if topic_start is None:
        return None

    topic_end = end
    for index in range(topic_start + 1, end):
        if re.match(r"^###\s+", lines[index].strip()):
            topic_end = index
            break
    while topic_end > topic_start and not lines[topic_end - 1].strip():
        topic_end -= 1
    return topic_start, topic_end


def _append_core_memory_items(body, memory):
    items = _structured_memory_items(memory)
    if not items:
        return None
    return _append_titled_markdown_items(
        body,
        items,
        default_title="General",
    )


def _append_preference_memory_items(body, memory):
    items = _structured_memory_items(memory)
    if not items:
        return None

    sections = _preference_sections(_normalize_preference_body(body))
    changed = False
    for title, lines in items:
        level = _preference_level(title)
        if not level:
            level = "Medium"
        for line in _normalized_bullet_lines(lines):
            if line not in sections[level]:
                sections[level].append(line)
                changed = True

    if not changed:
        return None
    return _render_preference_sections(sections)


def _append_titled_markdown_items(body, items, default_title):
    preamble, sections = _markdown_sections(body)
    changed = False
    for title, lines in items:
        title = _clean_structured_title(title) or default_title
        bullets = _normalized_bullet_lines(lines)
        if not bullets:
            continue

        section = _find_markdown_section(sections, title)
        if section is None:
            section = {"title": title, "lines": []}
            sections.append(section)
            changed = True

        existing = {line.strip() for line in section["lines"] if line.strip()}
        for bullet in bullets:
            if bullet not in existing:
                section["lines"].append(bullet)
                existing.add(bullet)
                changed = True

    if not changed:
        return None
    return _render_markdown_sections(preamble, sections)


def _structured_memory_items(value):
    if isinstance(value, dict):
        return [_structured_memory_item(value)]
    if isinstance(value, list):
        items = [_structured_memory_item(item) for item in value if isinstance(item, dict)]
        return [(title, lines) for title, lines in items if title or lines]
    return []


def _structured_memory_item(item):
    title = _clean_structured_title(item.get("title", ""))
    content = _structured_content_lines(item.get("content", []))
    return title, content


def _structured_content_lines(content):
    if not isinstance(content, list):
        return []

    lines = []
    for item in content:
        text = _clean_model_field(item)
        if text:
            lines.extend(line.strip() for line in text.splitlines() if line.strip())
    return lines


def _clean_structured_title(title):
    title = _clean_model_field(title)
    title = re.sub(r"^#{1,6}\s+", "", title).strip()
    return _single_line_no_limit(title).strip(" -:：")


def _structured_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"true", "yes", "y", "1", "same", "same_topic", "继续", "同一话题", "是"}


def _normalized_bullet_lines(
    lines,
    max_bullets=None,
    max_bullet_chars=None,
):
    bullets = []
    for line in lines or []:
        text = _clean_model_field(line)
        if not text:
            continue
        text = _strip_bullet_marker(text)
        if not text or _is_markdown_heading(text):
            continue
        if max_bullet_chars is not None:
            text = _shorten_inline(text, max_bullet_chars)
        bullets.append(f"- {text}")
        if max_bullets is not None and len(bullets) >= max_bullets:
            break
    return bullets


def _strip_bullet_marker(text):
    return re.sub(r"^\s*(?:[-*+]|[0-9]+[.)、])\s*", "", str(text or "")).strip()


def _markdown_sections(body):
    preamble = []
    sections = []
    current = None
    for line in _memory_text(body, "Core Memory").splitlines():
        heading = re.fullmatch(r"##\s+(.+)", line.strip())
        if heading:
            current = {"title": heading.group(1).strip(), "lines": []}
            sections.append(current)
            continue
        if current is None:
            preamble.append(line.rstrip())
        else:
            current["lines"].append(line.rstrip())
    return preamble, sections


def _find_markdown_section(sections, title):
    normalized_title = _clean_structured_title(title).lower()
    for section in sections:
        if _clean_structured_title(section["title"]).lower() == normalized_title:
            return section
    return None


def _render_markdown_sections(preamble, sections):
    lines = [line.rstrip() for line in preamble if line.strip()]
    for section in sections:
        section_lines = [line.rstrip() for line in section["lines"] if line.strip()]
        if lines:
            lines.append("")
        lines.append(f"## {section['title']}")
        lines.extend(section_lines)
    return "\n".join(lines).strip()


def _preference_level(title):
    normalized = _clean_structured_title(title).lower()
    for level in PREFERENCE_IMPORTANCE_LEVELS:
        if normalized == level.lower():
            return level
    return ""


def _render_preference_sections(sections):
    parts = []
    for importance in PREFERENCE_IMPORTANCE_LEVELS:
        parts.append(f"## {importance}")
        parts.extend(sections.get(importance, []))
        parts.append("")
    return "\n".join(parts).strip()


def _single_line_no_limit(text):
    return " ".join(str(text or "").split())


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

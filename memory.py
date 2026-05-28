import json
import re
from datetime import datetime
from pathlib import Path


MEMORY_DIR = Path(__file__).resolve().parent / "memory"
CORE_MEMORY_FILE = "core.md"
PREFERENCE_MEMORY_FILE = "preferences.md"
EPISODIC_DIR_NAME = "episodes"
LEGACY_EPISODIC_FILE = "episodic.md"
LEGACY_EPISODIC_MIGRATED_FILE = "episodic.migrated.md"
HISTORY_FILE = "history.jsonl"
MEMORY_UPDATE_DIAGNOSTICS_FILE = "memory_update_diagnostics.jsonl"
CORE_MEMORY_MAX_CHARS = 3000
PREFERENCE_MEMORY_MAX_CHARS = 3000
SYSTEM_MEMORY_MAX_CHARS = 7000
EPISODIC_UPDATE_CONTEXT_MAX_CHARS = 12000
EPISODIC_ENTRY_MAX_BULLETS = None

CORE_MEMORY_TEMPLATE = ""
PREFERENCE_IMPORTANCE_LEVELS = ("Critical", "High", "Medium", "Low")
PREFERENCE_MEMORY_TEMPLATE = (
    "# Critical\n\n# High\n\n# Medium\n\n# Low\n"
)

class MemoryStore:
    def __init__(self, memory_dir=None):
        self.memory_dir = Path(memory_dir) if memory_dir is not None else MEMORY_DIR
        self.core_path = self.memory_dir / CORE_MEMORY_FILE
        self.preference_path = self.memory_dir / PREFERENCE_MEMORY_FILE
        self.episodic_dir = self.memory_dir / EPISODIC_DIR_NAME
        self.legacy_episodic_path = self.memory_dir / LEGACY_EPISODIC_FILE
        self.legacy_episodic_migrated_path = self.memory_dir / LEGACY_EPISODIC_MIGRATED_FILE
        self.history_path = self.memory_dir / HISTORY_FILE
        self.update_diagnostics_path = self.memory_dir / MEMORY_UPDATE_DIAGNOSTICS_FILE
        self.ensure_files()

    def ensure_files(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.episodic_dir.mkdir(parents=True, exist_ok=True)
        self._create_if_missing(self.core_path, CORE_MEMORY_TEMPLATE)
        self._create_if_missing(self.preference_path, PREFERENCE_MEMORY_TEMPLATE)
        self._create_if_missing(self.history_path, "")
        self.write_core_body(self.read_core_body())
        self.write_preference_body(self.read_preference_body())
        self._ensure_episodic_files()
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
            "情景记忆在 memory/episodes/YYYY-MM-DD.md；需要回忆具体日期时先检索它。"
        )
        return _truncate(block, SYSTEM_MEMORY_MAX_CHARS)

    def read_core_body(self):
        return _clean_memory_body(self._read_text(self.core_path))

    def read_preference_body(self):
        return _clean_memory_body(self._read_text(self.preference_path))

    def read_episodic_text(self):
        blocks = []
        for path in self._episode_files():
            date_key = path.stem
            body = self._episode_body(path)
            if body:
                blocks.append(f"## {date_key}\n\n{body}")
        return "\n\n".join(blocks).strip()

    def episode_path(self, date_text=None, now=None):
        date_text = date_text or self.today_key(now)
        if not _valid_date_key(date_text):
            return None
        return self.episodic_dir / f"{date_text}.md"

    def append_history(self, role, content, extra=None, now=None):
        now = now or datetime.now()
        row = {
            "ts": now.isoformat(timespec="seconds"),
            "role": str(role or ""),
            "content": _json_safe(content),
        }
        if isinstance(extra, dict):
            for key, value in _json_safe(extra).items():
                if key not in row:
                    row[key] = value
        try:
            with self.history_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
            return True
        except OSError:
            return False

    def history_tail(self, limit=20):
        limit = max(1, int(limit or 20))
        rows = []
        try:
            lines = self.history_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return rows
        for line in lines[-limit:]:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def history_stats(self):
        rows = 0
        try:
            with self.history_path.open("r", encoding="utf-8") as file:
                for line in file:
                    if line.strip():
                        rows += 1
        except OSError:
            rows = 0
        size = self.history_path.stat().st_size if self.history_path.exists() else 0
        return {"path": str(self.history_path), "rows": rows, "bytes": size}

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
            initial_entry_locked = self._is_initial_episodic_heading(
                current_heading,
                now=now,
            )
            intro = "一轮完整对话刚结束。请更新当前会话的情景记忆。\n"
            if initial_entry_locked:
                topic_rules = (
                    "same_topic 表示本轮是否仍属于当前话题；当前情景记忆为空时必须是 false。\n"
                    "当前情景记忆是第一次见面的初始记忆，不能修改、重写、合并、复述或删除。\n"
                    "如果本轮和初始记忆是同一话题，same_topic 必须是 true；title 会被程序忽略，旧标题会保留。\n"
                    "same_topic=true 时，content 只写本轮需要新增的记忆；程序会在同标题下新开条目，不会覆盖初始记忆。\n"
                    "如果本轮明显换了话题，same_topic 必须是 false，title 写新话题短标题，content 只写新话题当前记忆。\n"
                    "不要为了措辞更顺而改 title；只有 same_topic=false 时才生成新 title。\n"
                    "content 应自然简短，通常一条要点就够；不要为了完整而拆成很多条，但也不要硬凑固定条数。\n"
                )
            else:
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
        path = self.episode_path(date_key)
        if path is None:
            return False
        content = self._episode_body(path).rstrip()
        content = (content + "\n\n" if content else "") + entry.rstrip() + "\n"
        self._write_text(path, content)
        return True

    def _replace_episodic_topic_text(self, heading, entry, now=None):
        now = now or datetime.now()
        date_key = self.today_key(now)
        path = self.episode_path(date_key)
        if path is None:
            return None
        body = self._episode_body(path)
        updated = _replace_episodic_topic(body, heading, entry)
        if updated is None:
            return None
        updated_daily = updated.strip() + "\n"
        if self._read_text(path) == updated_daily:
            return False
        self._write_text(path, updated_daily)
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
            if self._is_initial_episodic_heading(current_heading, now=now):
                title = current_title or title
            else:
                entry = "\n".join([current_heading, *bullets]).strip()
                replaced = self._replace_episodic_topic_text(current_heading, entry, now=now)
                if replaced is not None:
                    return {
                        "changed": bool(replaced),
                        "heading": current_heading,
                        "merged": True,
                    }

        heading = self._unique_episodic_heading(title, now=now)
        entry = "\n".join([heading, *bullets]).strip()
        changed = self._append_episodic_memory_text(entry, now=now)
        return {"changed": changed, "heading": heading, "merged": False}

    def _initial_episodic_topic_info(self):
        for path in self._episode_files():
            for entry in _iter_episode_topics(self._episode_body(path)):
                heading = _episodic_entry_heading(entry)
                if heading:
                    return {"date": path.stem, "heading": heading, "entry": entry}
        return {}

    def _is_initial_episodic_heading(self, heading, now=None):
        heading = str(heading or "").strip()
        if not heading:
            return False
        initial = self._initial_episodic_topic_info()
        return (
            bool(initial)
            and initial.get("date") == self.today_key(now)
            and initial.get("heading") == heading
        )

    def _unique_episodic_heading(self, title, now=None):
        now = now or datetime.now()
        title = str(title or "").strip()
        path = self.episode_path(self.today_key(now))
        body = self._episode_body(path) if path else ""
        candidates = [
            f"# {self.time_key(now)} - {title}",
            f"# {now.strftime('%H:%M:%S')} - {title}",
        ]
        for heading in candidates:
            if not _find_episodic_topic(body, heading):
                return heading

        index = 2
        while True:
            heading = f"# {now.strftime('%H:%M:%S')} - {title} ({index})"
            if not _find_episodic_topic(body, heading):
                return heading
            index += 1

    def episodic_topic_for_heading(self, heading, date_text=None, max_chars=None):
        heading = str(heading or "").strip()
        if not heading:
            return ""
        date_text = date_text or self.today_key()
        path = self.episode_path(date_text)
        topic = _find_episodic_topic(self._episode_body(path) if path else "", heading)
        return _truncate(topic or "", max_chars) if max_chars else topic or ""

    def latest_episodic_topic(self, date_text=None, max_chars=None):
        date_text = date_text or self.today_key()
        latest = ""
        path = self.episode_path(date_text)
        if path:
            for topic in _iter_episode_topics(self._episode_body(path)):
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
            _normalize_core_body(body),
            CORE_MEMORY_MAX_CHARS,
            "core",
        )

    def write_preference_body(self, body):
        return self._write_memory_file(
            self.preference_path,
            _normalize_preference_body(body),
            PREFERENCE_MEMORY_MAX_CHARS,
            "preferences",
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

    def tidy_preference_memory(self):
        before = self.read_preference_body()
        after = _normalize_preference_body(before)
        changed = self.write_preference_body(after)
        return {"changed": changed, "removed_duplicates": _preference_duplicate_count(before)}

    def remove_preference(self, query):
        query_key = _preference_match_key(query)
        if not query_key:
            return {"changed": False, "removed": []}
        sections = _preference_sections(self.read_preference_body())
        removed = []
        for level, lines in sections.items():
            kept = []
            for line in lines:
                if query_key in _preference_match_key(line):
                    removed.append(f"{level}: {line}")
                else:
                    kept.append(line)
            sections[level] = kept
        if not removed:
            return {"changed": False, "removed": []}
        return {
            "changed": self.write_preference_body(_render_preference_sections(sections)),
            "removed": removed,
        }

    def set_preference_level(self, query, level):
        level = _preference_level(level)
        query_key = _preference_match_key(query)
        if not level or not query_key:
            return {"changed": False, "moved": []}
        sections = _preference_sections(self.read_preference_body())
        moved = []
        for current_level, lines in sections.items():
            kept = []
            for line in lines:
                if query_key in _preference_match_key(line):
                    moved.append(f"{current_level}: {line}")
                    if current_level == level:
                        kept.append(line)
                    elif line not in sections[level]:
                        sections[level].append(line)
                else:
                    kept.append(line)
            sections[current_level] = kept
        if not moved:
            return {"changed": False, "moved": []}
        return {
            "changed": self.write_preference_body(_render_preference_sections(sections)),
            "moved": moved,
            "level": level,
        }

    def episodic_for_date(self, date_text=None, max_chars=12000):
        date_text = date_text or self.today_key()
        if not _valid_date_key(date_text):
            return ""
        path = self.episode_path(date_text)
        if path and path.exists():
            body = self._episode_body(path)
            if not body:
                return ""
            return _truncate(body.strip(), max_chars)
        return ""

    def search_episodic(self, query, max_results=8, max_chars=12000):
        query = str(query or "").strip()
        if not query:
            return ""

        matches = []
        for order, (date_key, entry) in enumerate(_iter_episodic_entries(self.read_episodic_text())):
            score, snippet = _episodic_search_score(query, date_key, entry)
            if score <= 0:
                continue
            matches.append(
                {
                    "score": score,
                    "date": date_key,
                    "entry": entry,
                    "snippet": snippet,
                    "order": order,
                }
            )

        matches.sort(
            key=lambda item: (
                item["score"],
                _date_sort_key(item["date"]),
                item["order"],
            ),
            reverse=True,
        )

        rendered = []
        for item in matches[:max_results]:
            block = f"## {item['date']}\n\n{item['entry']}".strip()
            if item["snippet"]:
                block += f"\n\nMatch: {item['snippet']}"
            block += f"\n\nScore: {item['score']}"
            rendered.append(block)

        return _truncate("\n\n".join(rendered), max_chars)

    def paths_summary(self):
        return (
            f"memory directory: {self.memory_dir}\n"
            f"core: {self.core_path}\n"
            f"preferences: {self.preference_path}\n"
            f"episodes: {self.episodic_dir}\n"
            f"history: {self.history_path}\n"
            f"diagnostics: {self.update_diagnostics_path}"
        )

    def _create_if_missing(self, path, content):
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    def _episode_files(self):
        if not self.episodic_dir.exists():
            return []
        return sorted(
            path
            for path in self.episodic_dir.glob("*.md")
            if _valid_date_key(path.stem)
        )

    def _episode_body(self, path):
        return _normalize_episode_body(self._read_text(path))

    def _ensure_episodic_files(self):
        self.episodic_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_episodic_file()
        self._delete_legacy_episodic_migrated_file()
        for path in self._episode_files():
            raw = self._read_text(path)
            normalized = _normalize_episode_body(raw)
            normalized_file = normalized + ("\n" if normalized else "")
            if raw != normalized_file:
                self._write_text(path, normalized_file)

    def _migrate_legacy_episodic_file(self):
        if not self.legacy_episodic_path.exists():
            return

        legacy_text = self._read_text(self.legacy_episodic_path)
        migrated = False
        for date_key, body in _iter_legacy_episodic_date_blocks(legacy_text):
            normalized = _normalize_episode_body(body)
            if not normalized:
                continue
            path = self.episode_path(date_key)
            if path is None:
                continue
            existing = _normalize_episode_body(self._read_text(path))
            merged = _merge_episode_bodies(existing, normalized)
            self._write_text(path, merged + ("\n" if merged else ""))
            migrated = True

        if migrated or not legacy_text.strip():
            try:
                self.legacy_episodic_path.unlink()
            except OSError:
                pass

    def _delete_legacy_episodic_migrated_file(self):
        if not self.legacy_episodic_migrated_path.exists():
            return
        try:
            self.legacy_episodic_migrated_path.unlink()
        except OSError:
            pass

    def _read_text(self, path):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _write_text(self, path, content):
        path = Path(path)
        existing = self._read_text(path)
        if existing == content:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True

    def _write_memory_file(self, path, body, max_chars, reason_name):
        body = _clean_memory_body(_clean_model_field(body))
        allowance = max(0, max_chars - 1)
        body = _truncate(body, allowance).strip()
        updated = body + ("\n" if body else "")
        if len(updated) >= max_chars:
            body = body[: max(0, max_chars - 1)].rstrip()
            updated = body + ("\n" if body else "")
        if self._read_text(path) == updated:
            return False
        return self._write_text(path, updated)

    def _enforce_core_limit(self):
        content = self._read_text(self.core_path)
        if len(content) < CORE_MEMORY_MAX_CHARS:
            return
        self.write_core_body(self.read_core_body())

    def record_update_diagnostic(
        self,
        source,
        raw_response,
        error,
        repair_response=None,
        now=None,
    ):
        now = now or datetime.now()
        payload = {
            "ts": now.isoformat(timespec="seconds"),
            "source": str(source or ""),
            "error": str(error or ""),
            "raw_response": _truncate(str(raw_response or ""), 4000),
        }
        if repair_response is not None:
            payload["repair_response"] = _truncate(str(repair_response or ""), 4000)
        try:
            with self.update_diagnostics_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return True
        except OSError:
            return False


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


def _clean_memory_body(content):
    lines = _strip_comments(content).strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and _is_memory_wrapper_heading(lines[0]):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(line.rstrip() for line in lines).strip()


def _is_memory_wrapper_heading(line):
    heading = _heading_text(line).lower()
    if heading in {"core memory", "preference memory", "episodic memory"}:
        return True
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+episodic memory", heading) is not None


def _heading_text(line):
    match = re.fullmatch(r"#{1,6}\s+(.+)", str(line or "").strip())
    return match.group(1).strip() if match else ""


def _normalize_preference_body(body):
    body = _clean_memory_body(_clean_model_field(body))
    existing = _preference_sections(body)

    sections = []
    seen = set()
    for level in PREFERENCE_IMPORTANCE_LEVELS:
        lines = []
        for line in existing.get(level, []):
            key = _preference_match_key(line)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            lines.append(line)
        sections.append(f"# {level}")
        if lines:
            sections.extend(lines)
        sections.append("")
    return "\n".join(sections).strip()


def _normalize_core_body(body):
    body = _clean_memory_body(_clean_model_field(body))
    preamble, sections = _markdown_sections(body)
    if not sections:
        return "\n".join(line.rstrip() for line in preamble).strip()
    return _render_markdown_sections(preamble, sections)


def _preference_sections(body):
    sections = {level: [] for level in PREFERENCE_IMPORTANCE_LEVELS}
    current_level = None
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+", stripped):
            heading = re.fullmatch(r"#{1,6}\s+(.+)", stripped)
            candidate = heading.group(1).strip() if heading else ""
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
        parts.append(f"# {importance}")
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
    return "\n".join([f"# {time_key} - {title}", *bullets]).strip()


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


def _normalize_episode_body(body):
    lines = []
    for line in _clean_memory_body(body).splitlines():
        stripped = line.strip()
        if _legacy_episodic_date_key(stripped):
            continue
        topic = re.fullmatch(r"#{1,6}\s+(\d{2}:\d{2}(?::\d{2})?\s*-\s*.+)", stripped)
        if topic:
            lines.append(f"# {topic.group(1).strip()}")
        else:
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _merge_episode_bodies(existing, incoming):
    existing = _normalize_episode_body(existing)
    incoming = _normalize_episode_body(incoming)
    if not existing:
        return incoming
    if not incoming or incoming in existing:
        return existing
    return f"{existing.rstrip()}\n\n{incoming.strip()}"


def _iter_legacy_episodic_date_blocks(content):
    current_date = None
    current_lines = []
    for line in _clean_memory_body(content).splitlines():
        date_key = _legacy_episodic_date_key(line)
        if date_key:
            if current_date:
                yield current_date, "\n".join(current_lines).strip()
            current_date = date_key
            current_lines = []
            continue
        if current_date:
            current_lines.append(line.rstrip())
    if current_date:
        yield current_date, "\n".join(current_lines).strip()


def _legacy_episodic_date_key(line):
    match = re.fullmatch(
        r"#{1,6}\s+(\d{4}-\d{2}-\d{2})(?:\s+Episodic Memory)?\s*",
        str(line or "").strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    date_key = match.group(1)
    return date_key if _valid_date_key(date_key) else ""


def _episodic_entry_heading(entry):
    for line in str(entry or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _episodic_heading_title(heading):
    match = re.fullmatch(
        r"#{1,6}\s+\d{2}:\d{2}(?::\d{2})?\s*-\s*(.+)",
        str(heading or "").strip(),
    )
    if not match:
        return ""
    return match.group(1).strip()


def _same_memory_title(left, right):
    return _clean_structured_title(left).lower() == _clean_structured_title(right).lower()


def _find_episodic_topic(content, heading):
    lines = _normalize_episode_body(content).splitlines()
    topic_bounds = _episodic_topic_bounds(lines, 0, len(lines), heading)
    if topic_bounds is None:
        return ""
    topic_start, topic_end = topic_bounds
    return "\n".join(lines[topic_start:topic_end]).strip()


def _replace_episodic_topic(content, heading, entry):
    lines = _normalize_episode_body(content).splitlines()
    topic_bounds = _episodic_topic_bounds(lines, 0, len(lines), heading)
    if topic_bounds is None:
        return None

    topic_start, topic_end = topic_bounds
    replacement = str(entry or "").strip().splitlines()
    if topic_end < len(lines) and lines[topic_end].strip():
        replacement.append("")
    updated_lines = lines[:topic_start] + replacement + lines[topic_end:]
    return "\n".join(updated_lines).rstrip() + "\n"


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
        if re.match(r"^#{1,6}\s+", lines[index].strip()):
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
    for line in _clean_memory_body(body).splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+", stripped):
            heading = re.fullmatch(r"#{1,6}\s+(.+)", stripped)
            if heading:
                current = {"title": heading.group(1).strip(), "lines": []}
                sections.append(current)
            else:
                current = None
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
        lines.append(f"# {section['title']}")
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
        parts.append(f"# {importance}")
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


def _valid_date_key(value):
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "").strip()) is not None


def _is_episodic_topic_heading(line):
    return (
        re.match(
            r"^#{1,6}\s+\d{2}:\d{2}(?::\d{2})?\s*-\s+",
            str(line or "").strip(),
        )
        is not None
    )


def _json_safe(value):
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        pass
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if hasattr(value, "__dict__"):
        return {
            str(key): _json_safe(item)
            for key, item in value.__dict__.items()
            if not str(key).startswith("_")
        }
    return str(value)


def _preference_match_key(text):
    text = _strip_bullet_marker(str(text or ""))
    text = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _preference_duplicate_count(body):
    seen = set()
    duplicates = 0
    for lines in _preference_sections(body).values():
        for line in lines:
            key = _preference_match_key(line)
            if not key:
                continue
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
    return duplicates


def _episodic_search_score(query, date_key, entry):
    lowered_query = query.lower()
    lowered_entry = entry.lower()
    heading = entry.splitlines()[0] if entry else ""
    title = _episodic_heading_title(heading) or heading
    lowered_title = title.lower()
    terms = _search_terms(query)
    score = 0

    if lowered_query and lowered_query in lowered_title:
        score += 14
    if lowered_query and lowered_query in lowered_entry:
        score += 8
    if lowered_query and lowered_query in date_key:
        score += 10

    entry_terms = _search_terms(entry)
    title_terms = _search_terms(title)
    for term in terms:
        if term in title_terms:
            score += 5
        if term in entry_terms:
            score += 2
        elif len(term) >= 2 and term in lowered_entry:
            score += 1

    if score <= 0:
        return 0, ""
    score += min(3, max(0, _date_sort_key(date_key) - 730000) // 365)
    return score, _search_snippet(entry, [lowered_query, *terms])


def _search_terms(text):
    text = str(text or "").lower()
    terms = set(re.findall(r"[a-z0-9_+-]{2,}", text))
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(chunk) <= 4:
            terms.add(chunk)
        for size in (2, 3):
            if len(chunk) >= size:
                for index in range(0, len(chunk) - size + 1):
                    terms.add(chunk[index : index + size])
    return {term for term in terms if term.strip()}


def _search_snippet(text, terms, radius=80):
    text = str(text or "").strip()
    lowered = text.lower()
    best_index = -1
    best_term = ""
    for term in terms:
        term = str(term or "").lower().strip()
        if not term:
            continue
        index = lowered.find(term)
        if index >= 0 and (best_index < 0 or index < best_index):
            best_index = index
            best_term = term
    if best_index < 0:
        return ""
    start = max(0, best_index - radius)
    end = min(len(text), best_index + len(best_term) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + " ".join(text[start:end].split()) + suffix


def _date_sort_key(date_key):
    try:
        return datetime.strptime(str(date_key), "%Y-%m-%d").toordinal()
    except ValueError:
        return 0


def _iter_episode_topics(content):
    current_entry = []
    for line in str(content or "").splitlines():
        if _is_episodic_topic_heading(line):
            if current_entry:
                yield "\n".join(current_entry).strip()
            current_entry = [line]
            continue
        if re.match(r"^#{1,6}\s+", line.strip()):
            if current_entry:
                yield "\n".join(current_entry).strip()
            current_entry = []
            continue
        if current_entry:
            current_entry.append(line)
    if current_entry:
        yield "\n".join(current_entry).strip()


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

        if current_date and _is_episodic_topic_heading(line):
            if current_entry:
                yield current_date, "\n".join(current_entry).strip()
            current_entry = [line]
            continue

        if current_date and re.match(r"^#{1,6}\s+", line.strip()):
            if current_entry:
                yield current_date, "\n".join(current_entry).strip()
            current_entry = []
            continue

        if current_date and current_entry:
            current_entry.append(line)

    if current_date and current_entry:
        yield current_date, "\n".join(current_entry).strip()

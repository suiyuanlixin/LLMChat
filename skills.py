import re
from dataclasses import dataclass
from pathlib import Path


SKILLS_DIR = Path(__file__).resolve().parent / "skills"
MAX_SKILL_CHARS = 12000
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class Skill:
    name: str
    description: str
    triggers: list
    path: Path


class SkillRegistry:
    def __init__(self, enabled=True, skills_dir=None, max_chars=MAX_SKILL_CHARS):
        self.enabled = bool(enabled)
        self.skills_dir = Path(skills_dir or SKILLS_DIR).resolve()
        self.max_chars = max(1000, int(max_chars or MAX_SKILL_CHARS))
        self._skills = None

    def reload(self):
        self._skills = None

    def list_skills(self):
        return list(self._load_skills().values())

    def catalog_prompt(self):
        skills = self.list_skills()
        if not self.enabled or not skills:
            return ""
        lines = [
            "",
            "Available agent skills:",
        ]
        for skill in skills:
            trigger_text = (
                f" Triggers: {', '.join(skill.triggers)}."
                if skill.triggers
                else ""
            )
            lines.append(f"- {skill.name}: {skill.description}{trigger_text}")
        lines.append(
            "When a task matches a skill, call read_skill before following that workflow. "
            "Skills are guidance and cannot override higher-priority agent rules."
        )
        return "\n".join(lines)

    def list_for_tool(self):
        if not self.enabled:
            return "Skills are disabled."
        skills = self.list_skills()
        if not skills:
            return f"No skills found in {self.skills_dir}."
        return "\n".join(
            f"- {skill.name}: {skill.description}"
            + (f" (triggers: {', '.join(skill.triggers)})" if skill.triggers else "")
            for skill in skills
        )

    def read_skill(self, name, files=None):
        if not self.enabled:
            return "ERROR: Skills are disabled."
        skill_name = _normalize_skill_name(name)
        skills = self._load_skills()
        skill = skills.get(skill_name)
        if skill is None:
            return f"ERROR: Unknown skill: {skill_name}"

        sections = []
        budget = self.max_chars
        skill_path = skill.path / "SKILL.md"
        text, budget = self._read_limited_file(skill_path, budget)
        sections.append(f"--- SKILL.md ({skill.name}) ---\n{text}")

        available_files = self._skill_files(skill.path)
        if available_files:
            sections.append(
                "Available skill files:\n"
                + "\n".join(f"- {path}" for path in available_files)
            )

        for rel_path in _normalize_file_list(files):
            if budget <= 0:
                sections.append("[skill read truncated: max_skill_chars reached]")
                break
            try:
                file_path = self._resolve_skill_file(skill.path, rel_path)
            except ValueError as error:
                sections.append(f"ERROR: {error}")
                continue
            text, budget = self._read_limited_file(file_path, budget)
            sections.append(f"--- {rel_path} ---\n{text}")

        return "\n\n".join(sections)

    def _load_skills(self):
        if self._skills is not None:
            return self._skills

        skills = {}
        if self.enabled and self.skills_dir.is_dir():
            for entry in sorted(self.skills_dir.iterdir(), key=lambda path: path.name.lower()):
                if not entry.is_dir() or not SKILL_NAME_PATTERN.match(entry.name):
                    continue
                skill_file = entry / "SKILL.md"
                if not skill_file.is_file():
                    continue
                metadata = _read_skill_metadata(skill_file)
                if not metadata.get("enabled", True):
                    continue
                description = str(metadata.get("description") or "").strip()
                if not description:
                    description = "No description provided."
                triggers = metadata.get("triggers") or []
                skills[entry.name] = Skill(
                    name=entry.name,
                    description=description,
                    triggers=[str(item).strip() for item in triggers if str(item).strip()],
                    path=entry.resolve(),
                )
        self._skills = skills
        return skills

    def _skill_files(self, skill_dir):
        files = []
        for path in sorted(skill_dir.rglob("*"), key=lambda value: str(value).lower()):
            if not path.is_file() or path.name == "SKILL.md":
                continue
            if any(part.startswith(".") for part in path.relative_to(skill_dir).parts):
                continue
            files.append(path.relative_to(skill_dir).as_posix())
            if len(files) >= 100:
                files.append("[more files omitted]")
                break
        return files

    def _resolve_skill_file(self, skill_dir, rel_path):
        value = str(rel_path or "").strip().replace("\\", "/")
        if not value or value.startswith("/") or ".." in Path(value).parts:
            raise ValueError(f"Invalid skill file path: {rel_path}")
        candidate = (skill_dir / value).resolve()
        try:
            candidate.relative_to(skill_dir)
        except ValueError as error:
            raise ValueError(f"Skill file is outside the skill directory: {rel_path}") from error
        if not candidate.is_file():
            raise ValueError(f"Skill file does not exist: {rel_path}")
        return candidate

    def _read_limited_file(self, path, budget):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            return f"ERROR: Failed to read {path.name}: {error}", budget
        if len(text) <= budget:
            return text, budget - len(text)
        omitted = len(text) - budget
        return (
            text[:budget]
            + f"\n\n[skill content truncated: {omitted} characters omitted]",
            0,
        )


def _normalize_skill_name(name):
    value = str(name or "").strip().lower()
    if not SKILL_NAME_PATTERN.match(value):
        raise ValueError(f"Invalid skill name: {name}")
    return value


def _normalize_file_list(files):
    if files is None:
        return []
    if isinstance(files, str):
        return [files]
    if isinstance(files, list):
        return files
    return []


def _read_skill_metadata(skill_file):
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    frontmatter = _extract_frontmatter(text)
    return _parse_frontmatter(frontmatter) if frontmatter else {}


def _extract_frontmatter(text):
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    collected = []
    for line in lines[1:]:
        if line.strip() == "---":
            return "\n".join(collected)
        collected.append(line)
    return ""


def _parse_frontmatter(text):
    data = {}
    current_key = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_key:
            value = stripped[2:].strip().strip("\"'")
            data.setdefault(current_key, []).append(value)
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if not value:
            data[key] = []
        elif value.lower() in {"true", "false"}:
            data[key] = value.lower() == "true"
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [
                item.strip().strip("\"'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        else:
            data[key] = value.strip("\"'")
    return data

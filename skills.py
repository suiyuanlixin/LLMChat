import re
from dataclasses import dataclass
from pathlib import Path


APP_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
WORKSPACE_SKILLS_RELATIVE_DIR = Path(".omniagent") / "skills"
MAX_SKILL_CHARS = 12000
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SKILL_SOURCES = {"app", "workspace"}


@dataclass
class Skill:
    name: str
    key: str
    source: str
    description: str
    triggers: list
    path: Path


class SkillRegistry:
    def __init__(
        self,
        enabled=True,
        app_enabled=True,
        workspace_enabled=False,
        workspace_dir=None,
        auto_catalog=True,
        max_chars=MAX_SKILL_CHARS,
        app_skills_dir=None,
    ):
        self.enabled = bool(enabled)
        self.app_enabled = bool(app_enabled)
        self.workspace_enabled = bool(workspace_enabled)
        self.auto_catalog = bool(auto_catalog)
        self.app_skills_dir = Path(app_skills_dir or APP_SKILLS_DIR).resolve()
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir else None
        self.max_chars = max(1000, int(max_chars or MAX_SKILL_CHARS))
        self._skills = None
        self._ensure_enabled_workspace_skills_dir()

    @property
    def workspace_skills_dir(self):
        if not self.workspace_dir:
            return None
        return (self.workspace_dir / WORKSPACE_SKILLS_RELATIVE_DIR).resolve()

    def configure(
        self,
        enabled=None,
        app_enabled=None,
        workspace_enabled=None,
        workspace_dir=None,
        auto_catalog=None,
        max_chars=None,
    ):
        if enabled is not None:
            self.enabled = bool(enabled)
        if app_enabled is not None:
            self.app_enabled = bool(app_enabled)
        if workspace_enabled is not None:
            self.workspace_enabled = bool(workspace_enabled)
        if workspace_dir is not None:
            self.workspace_dir = (
                Path(workspace_dir).resolve() if workspace_dir else None
            )
        if auto_catalog is not None:
            self.auto_catalog = bool(auto_catalog)
        if max_chars is not None:
            self.max_chars = max(1000, int(max_chars or MAX_SKILL_CHARS))
        self._ensure_enabled_workspace_skills_dir()
        self.reload()

    def reload(self):
        self._skills = None

    def list_skills(self):
        return list(self._load_skills().values())

    def catalog_prompt(self):
        skills = self.list_skills()
        if not self.enabled or not self.auto_catalog or not skills:
            return ""
        lines = [
            "",
            "Available agent skills:",
        ]
        for skill in skills:
            trigger_text = (
                f" Triggers: {', '.join(skill.triggers)}." if skill.triggers else ""
            )
            lines.append(
                f"- {skill.key} [{skill.source}]: {skill.description}{trigger_text}"
            )
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
            return (
                "No skills found in enabled skill sources.\n" + self._source_summary()
            )
        return "\n".join(
            f"- {skill.key} [{skill.source}]: {skill.description}"
            + (f" (triggers: {', '.join(skill.triggers)})" if skill.triggers else "")
            for skill in skills
        )

    def read_skill(self, name, files=None):
        if not self.enabled:
            return "ERROR: Skills are disabled."
        skill_key = _normalize_skill_key(name)
        skill = self._resolve_skill(skill_key)
        if skill is None:
            return f"ERROR: Unknown skill: {skill_key}"
        if isinstance(skill, list):
            choices = ", ".join(item.key for item in skill)
            return f"ERROR: Ambiguous skill name: {skill_key}. Use one of: {choices}"

        sections = []
        budget = self.max_chars
        skill_path = skill.path / "SKILL.md"
        text, budget = self._read_limited_file(skill_path, budget)
        sections.append(f"--- SKILL.md ({skill.key}) ---\n{text}")

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

    def status(self):
        skills = self.list_skills()
        by_source = {"app": 0, "workspace": 0}
        for skill in skills:
            by_source[skill.source] = by_source.get(skill.source, 0) + 1
        workspace_dir = self.workspace_skills_dir
        return {
            "enabled": self.enabled,
            "sources": {
                "app": self.app_enabled,
                "workspace": self.workspace_enabled,
            },
            "auto_catalog": self.auto_catalog,
            "max_skill_chars": self.max_chars,
            "count": len(skills),
            "counts": by_source,
            "directories": {
                "app": str(self.app_skills_dir),
                "workspace": str(workspace_dir) if workspace_dir else "",
            },
        }

    def _resolve_skill(self, skill_key):
        skills = self._load_skills()
        if skill_key in skills:
            return skills[skill_key]
        if "/" in skill_key:
            source, name = skill_key.split("/", 1)
            for skill in skills.values():
                if skill.source == source and skill.name == name:
                    return skill
            return None
        matches = [skill for skill in skills.values() if skill.name == skill_key]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return matches
        return None

    def _load_skills(self):
        if self._skills is not None:
            return self._skills

        loaded = []
        if self.enabled:
            for source, directory in self._source_dirs():
                loaded.extend(self._load_source_skills(source, directory))

        name_counts = {}
        for skill in loaded:
            name_counts[skill.name] = name_counts.get(skill.name, 0) + 1

        skills = {}
        for skill in loaded:
            skill.key = (
                f"{skill.source}/{skill.name}"
                if name_counts.get(skill.name, 0) > 1
                else skill.name
            )
            skills[skill.key] = skill

        self._skills = skills
        return skills

    def _source_dirs(self):
        if self.app_enabled:
            yield "app", self.app_skills_dir
        workspace_dir = self.workspace_skills_dir
        if self.workspace_enabled and workspace_dir:
            self._ensure_workspace_skills_dir(workspace_dir)
            yield "workspace", workspace_dir

    def _ensure_workspace_skills_dir(self, workspace_dir):
        try:
            workspace_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _ensure_enabled_workspace_skills_dir(self):
        workspace_dir = self.workspace_skills_dir
        if self.workspace_enabled and workspace_dir:
            self._ensure_workspace_skills_dir(workspace_dir)

    def _source_summary(self):
        workspace_dir = self.workspace_skills_dir
        lines = [
            f"app: {'on' if self.app_enabled else 'off'} ({self.app_skills_dir})",
            "workspace: "
            + (
                f"{'on' if self.workspace_enabled else 'off'} ({workspace_dir})"
                if workspace_dir
                else f"{'on' if self.workspace_enabled else 'off'} (no workspace)"
            ),
        ]
        return "\n".join(lines)

    def _load_source_skills(self, source, skills_dir):
        skills = []
        if not skills_dir.is_dir():
            return skills
        for entry in sorted(skills_dir.iterdir(), key=lambda path: path.name.lower()):
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
            skills.append(
                Skill(
                    name=entry.name,
                    key=entry.name,
                    source=source,
                    description=description,
                    triggers=[
                        str(item).strip() for item in triggers if str(item).strip()
                    ],
                    path=entry.resolve(),
                )
            )
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
            raise ValueError(
                f"Skill file is outside the skill directory: {rel_path}"
            ) from error
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


def _normalize_skill_key(name):
    value = str(name or "").strip().lower().replace("\\", "/")
    if "/" in value:
        source, skill_name = value.split("/", 1)
        if source not in SKILL_SOURCES or not SKILL_NAME_PATTERN.match(skill_name):
            raise ValueError(f"Invalid skill name: {name}")
        return f"{source}/{skill_name}"
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

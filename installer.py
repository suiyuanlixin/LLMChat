import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePosixPath


DEFAULT_CLAWHUB_REGISTRY = "https://clawhub.ai"
DEFAULT_SKILLHUB_REGISTRY = "https://skillhub.space"
CLAWHUB_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
SKILLHUB_SLUG_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9_-]{0,127}(?:/[a-z0-9][a-z0-9_-]{0,127})?$"
)
PROVIDERS = {
    "clawhub": {
        "label": "ClawHub",
        "default_registry": DEFAULT_CLAWHUB_REGISTRY,
        "registry_env": "CLAWHUB_REGISTRY",
        "token_env": "CLAWHUB_TOKEN",
        "pattern": CLAWHUB_SLUG_PATTERN,
        "origin_dir": ".clawhub",
    },
    "skillhub": {
        "label": "SkillHub",
        "default_registry": DEFAULT_SKILLHUB_REGISTRY,
        "registry_env": "SKILLHUB_REGISTRY",
        "token_env": "SKILLHUB_TOKEN",
        "pattern": SKILLHUB_SLUG_PATTERN,
        "origin_dir": ".skillhub",
    },
}
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 500
MAX_ARCHIVE_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_PREVIEW_CHARS = 6000
TEXT_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".mjs",
    ".md",
    ".ps1",
    ".psd1",
    ".psm1",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SCRIPT_EXTENSIONS = {".bat", ".cmd", ".js", ".mjs", ".ps1", ".psm1", ".py", ".sh", ".ts"}
SUSPICIOUS_PATTERNS = [
    (re.compile(r"\b(?:process\.env|os\.environ|getenv)\b", re.I), "reads environment variables"),
    (re.compile(r"\b(?:\.env|id_rsa|ssh[/\\]|appdata|credential|token)\b", re.I), "references local secrets or credentials"),
    (re.compile(r"\b(?:curl|wget|invoke-webrequest|irm|fetch|requests\.)\b", re.I), "performs network requests"),
    (re.compile(r"\b(?:eval|exec|invoke-expression|iex)\b", re.I), "uses dynamic code execution"),
]


class SkillInstallError(Exception):
    pass


@dataclass
class SkillInstallResult:
    slug: str
    target: str
    install_dir: Path
    version: str
    files: list
    warnings: list
    dry_run: bool = False


def normalize_clawhub_slug(value):
    return normalize_provider_slug("clawhub", value)


def normalize_skillhub_slug(value):
    return normalize_provider_slug("skillhub", value)


def normalize_provider_slug(provider, value):
    provider = _normalize_provider(provider)
    text = str(value or "").strip().lower().replace("\\", "/")
    prefix = f"{provider}:"
    if text.startswith(prefix):
        text = text.split(":", 1)[1]
    if not PROVIDERS[provider]["pattern"].match(text):
        raise SkillInstallError(
            f"Invalid {PROVIDERS[provider]['label']} skill slug: {value}"
        )
    return text


def clawhub_search(query, limit=10, registry=None):
    return registry_search("clawhub", query, limit=limit, registry=registry)


def skillhub_search(query, limit=10, registry=None):
    return registry_search("skillhub", query, limit=limit, registry=registry)


def registry_search(provider, query, limit=10, registry=None):
    provider = _normalize_provider(provider)
    query = str(query or "").strip()
    if not query:
        raise SkillInstallError("Search query cannot be empty.")
    params = {"q": query}
    data = _http_json(
        _api_url(provider, registry, "/api/v1/search", params),
        provider,
    )
    items = _extract_items(data)
    rows = []
    for item in items[: max(1, int(limit or 10))]:
        slug = _field(item, "slug", "name", "id")
        if not slug:
            continue
        title = _field(item, "displayName", "display_name", "title", "name") or slug
        owner = _field(item, "owner", "ownerHandle", "publisher", "author")
        description = _field(item, "description", "summary", "readmeSummary") or ""
        downloads = _field(item, "downloads", "installCount", "installs", "score")
        rows.append(
            {
                "slug": str(slug),
                "title": str(title),
                "owner": str(owner or ""),
                "description": _single_line(description, 180),
                "downloads": downloads,
            }
        )
    return rows


def clawhub_inspect(slug, version=None, registry=None):
    return registry_inspect("clawhub", slug, version=version, registry=registry)


def skillhub_inspect(slug, version=None, registry=None):
    return registry_inspect("skillhub", slug, version=version, registry=registry)


def registry_inspect(provider, slug, version=None, registry=None):
    provider = _normalize_provider(provider)
    slug = normalize_provider_slug(provider, slug)
    params = {}
    if version and version != "latest":
        params["version"] = str(version)
    data = _http_json(
        _api_url(provider, registry, f"/api/v1/skills/{_url_path_slug(slug)}", params),
        provider,
    )
    skill = data.get("skill") if isinstance(data, dict) and isinstance(data.get("skill"), dict) else data
    if not isinstance(skill, dict):
        raise SkillInstallError(f"Invalid {PROVIDERS[provider]['label']} response for {slug}.")

    tag_or_version = str(version or "latest")
    skill_md = _fetch_skill_file(provider, slug, "SKILL.md", tag_or_version, registry)
    files = _extract_file_list(skill)
    if "SKILL.md" not in files:
        files.insert(0, "SKILL.md")
    return {
        "slug": slug,
        "version": _field(skill, "version", "latestVersion", "latest_version") or tag_or_version,
        "title": _field(skill, "displayName", "display_name", "title", "name") or slug,
        "owner": _field(skill, "owner", "ownerHandle", "publisher", "author") or "",
        "description": _field(skill, "description", "summary", "readmeSummary") or "",
        "homepage": _field(skill, "homepage", "url", "sourceUrl", "sourceRepo") or "",
        "files": files,
        "skill_md": skill_md,
        "warnings": _security_warnings_from_texts({"SKILL.md": skill_md}, files),
        "raw": skill,
    }


def install_clawhub_skill(
    slug,
    skills_dir,
    target="workspace",
    version=None,
    registry=None,
    force=False,
    dry_run=False,
):
    return install_registry_skill(
        "clawhub",
        slug,
        skills_dir,
        target=target,
        version=version,
        registry=registry,
        force=force,
        dry_run=dry_run,
    )


def install_skillhub_skill(
    slug,
    skills_dir,
    target="workspace",
    version=None,
    registry=None,
    force=False,
    dry_run=False,
):
    return install_registry_skill(
        "skillhub",
        slug,
        skills_dir,
        target=target,
        version=version,
        registry=registry,
        force=force,
        dry_run=dry_run,
    )


def install_registry_skill(
    provider,
    slug,
    skills_dir,
    target="workspace",
    version=None,
    registry=None,
    force=False,
    dry_run=False,
):
    provider = _normalize_provider(provider)
    slug = normalize_provider_slug(provider, slug)
    local_name = _local_skill_name(provider, slug)
    skills_dir = Path(skills_dir).resolve()
    install_dir = (skills_dir / local_name).resolve()
    _ensure_child(skills_dir, install_dir)

    archive = _download_skill_archive(provider, slug, version, registry)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    bundle = _validated_skill_archive(archive)
    warnings = _security_warnings_from_texts(bundle["texts"], bundle["files"])
    resolved_version = str(version or bundle.get("version") or "latest")

    if dry_run:
        return SkillInstallResult(
            slug=slug,
            target=target,
            install_dir=install_dir,
            version=resolved_version,
            files=bundle["files"],
            warnings=warnings,
            dry_run=True,
        )

    if install_dir.exists() and not force:
        raise SkillInstallError(
            f"Skill already exists at {install_dir}. Use --force to replace it."
        )

    skills_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{local_name}.install-", dir=str(skills_dir)))
    try:
        for rel_path, content in bundle["contents"].items():
            target_path = temp_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(content)
        _write_origin(
            temp_dir,
            provider,
            slug,
            local_name,
            target,
            registry,
            resolved_version,
            archive_sha256,
            bundle["files"],
        )
        if install_dir.exists():
            shutil.rmtree(install_dir)
        temp_dir.replace(install_dir)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    _update_lock(
        skills_dir,
        provider,
        slug,
        local_name,
        target,
        registry,
        resolved_version,
        archive_sha256,
    )
    return SkillInstallResult(
        slug=slug,
        target=target,
        install_dir=install_dir,
        version=resolved_version,
        files=bundle["files"],
        warnings=warnings,
        dry_run=False,
    )


def _api_url(provider, registry, path, params=None):
    provider = _normalize_provider(provider)
    base = str(
        registry
        or os.getenv(PROVIDERS[provider]["registry_env"])
        or PROVIDERS[provider]["default_registry"]
    ).rstrip("/")
    query = urllib.parse.urlencode(params or {})
    return f"{base}{path}" + (f"?{query}" if query else "")


def _http_json(url, provider):
    try:
        with urllib.request.urlopen(_request(url, provider), timeout=20) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else ""
        raise SkillInstallError(
            f"{PROVIDERS[provider]['label']} request failed ({error.code}): "
            f"{_single_line(detail, 240)}"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise SkillInstallError(f"{PROVIDERS[provider]['label']} request failed: {error}") from error


def _http_bytes(url, max_bytes, provider):
    try:
        with urllib.request.urlopen(_request(url, provider), timeout=30) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise SkillInstallError(f"Download is too large ({content_length} bytes).")
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else ""
        raise SkillInstallError(
            f"{PROVIDERS[provider]['label']} download failed ({error.code}): "
            f"{_single_line(detail, 240)}"
        ) from error
    except OSError as error:
        raise SkillInstallError(f"{PROVIDERS[provider]['label']} download failed: {error}") from error
    if len(data) > max_bytes:
        raise SkillInstallError(f"Download exceeds {max_bytes} bytes.")
    return data


def _request(url, provider):
    provider = _normalize_provider(provider)
    headers = {"User-Agent": "LLMChat-skill-installer"}
    token = os.getenv(PROVIDERS[provider]["token_env"])
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(
        url,
        headers=headers,
    )


def _fetch_skill_file(provider, slug, path, version, registry):
    params = {"path": path}
    if version and version != "latest":
        params["version"] = version
    else:
        params["tag"] = "latest"
    try:
        data = _http_bytes(
            _api_url(
                provider,
                registry,
                f"/api/v1/skills/{_url_path_slug(slug)}/file",
                params,
            ),
            200000,
            provider,
        )
    except SkillInstallError:
        return ""
    return data.decode("utf-8", errors="replace")


def _download_skill_archive(provider, slug, version, registry):
    params = {"slug": slug}
    if version and version != "latest":
        params["version"] = str(version)
    else:
        params["tag"] = "latest"

    urls = [
        _api_url(provider, registry, "/api/v1/download", params),
        _api_url(provider, registry, f"/api/v1/download/{urllib.parse.quote(slug, safe='')}", None),
        _api_url(provider, registry, f"/api/v1/download/{_url_path_slug(slug)}", None),
    ]
    if provider == "skillhub":
        urls.extend(
            [
                _api_url(provider, registry, f"/skills/{_url_path_slug(slug)}/download", None),
                _api_url(provider, registry, f"/api/skills/{_url_path_slug(slug)}/download", None),
            ]
        )

    errors = []
    for url in urls:
        try:
            return _http_bytes(url, MAX_DOWNLOAD_BYTES, provider)
        except SkillInstallError as error:
            errors.append(str(error))
    raise SkillInstallError(errors[-1] if errors else "Skill download failed.")


def _normalize_provider(provider):
    value = str(provider or "").strip().lower()
    if value not in PROVIDERS:
        raise SkillInstallError(f"Unsupported skill registry: {provider}")
    return value


def _local_skill_name(provider, slug):
    if provider == "skillhub":
        return slug.rsplit("/", 1)[-1]
    return slug


def _url_path_slug(slug):
    return urllib.parse.quote(str(slug or ""), safe="/")


def _validated_skill_archive(data):
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as error:
        raise SkillInstallError("Downloaded skill is not a valid zip archive.") from error

    infos = [info for info in archive.infolist() if not info.is_dir()]
    if len(infos) > MAX_ARCHIVE_FILES:
        raise SkillInstallError(f"Skill archive has too many files ({len(infos)}).")
    total_size = sum(info.file_size for info in infos)
    if total_size > MAX_ARCHIVE_TOTAL_BYTES:
        raise SkillInstallError(f"Skill archive is too large ({total_size} bytes).")

    skill_roots = []
    for info in infos:
        rel = _safe_zip_path(info.filename)
        if rel.name == "SKILL.md":
            skill_roots.append(rel.parent)
    if not skill_roots:
        raise SkillInstallError("Skill archive does not contain SKILL.md.")
    unique_roots = sorted({str(root) for root in skill_roots})
    if len(unique_roots) != 1:
        raise SkillInstallError("Skill archive contains multiple SKILL.md files.")
    root = PurePosixPath(unique_roots[0])

    contents = {}
    texts = {}
    files = []
    for info in infos:
        rel = _safe_zip_path(info.filename)
        try:
            out_rel = rel.relative_to(root) if str(root) != "." else rel
        except ValueError:
            continue
        if not str(out_rel) or str(out_rel) == ".":
            continue
        _validate_skill_file(out_rel, info)
        content = archive.read(info)
        key = out_rel.as_posix()
        contents[key] = content
        files.append(key)
        if _is_text_file(out_rel):
            texts[key] = content[:MAX_TEXT_PREVIEW_CHARS].decode("utf-8", errors="replace")

    if "SKILL.md" not in contents:
        raise SkillInstallError("Skill archive root does not contain SKILL.md.")
    return {
        "contents": contents,
        "texts": texts,
        "files": sorted(files),
        "version": _version_from_skill_md(texts.get("SKILL.md", "")),
    }


def _safe_zip_path(name):
    value = str(name or "").replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or re.match(r"^[a-zA-Z]:", value)
    ):
        raise SkillInstallError(f"Unsafe archive path: {name}")
    return path


def _validate_skill_file(path, info):
    if info.file_size > MAX_ARCHIVE_FILE_BYTES:
        raise SkillInstallError(f"Skill file is too large: {path}")
    if not _is_text_file(path):
        raise SkillInstallError(f"Skill archive contains unsupported non-text file: {path}")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        raise SkillInstallError(f"Skill archive contains a symlink: {path}")


def _is_text_file(path):
    if path.name == "SKILL.md":
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS


def _security_warnings_from_texts(texts, files):
    warnings = []
    script_files = [path for path in files if Path(path).suffix.lower() in SCRIPT_EXTENSIONS]
    if script_files:
        warnings.append("contains script files: " + ", ".join(script_files[:8]))
    for path, text in texts.items():
        for pattern, reason in SUSPICIOUS_PATTERNS:
            if pattern.search(text):
                warning = f"{path}: {reason}"
                if warning not in warnings:
                    warnings.append(warning)
    return warnings


def _write_origin(
    skill_dir,
    provider,
    slug,
    local_name,
    target,
    registry,
    version,
    archive_sha256,
    files,
):
    provider = _normalize_provider(provider)
    origin_dir = skill_dir / PROVIDERS[provider]["origin_dir"]
    origin_dir.mkdir(parents=True, exist_ok=True)
    origin = {
        "source": provider,
        "registry": _registry_value(provider, registry),
        "slug": slug,
        "local_name": local_name,
        "target": target,
        "version": version,
        "archive_sha256": archive_sha256,
        "installed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "files": files,
    }
    (origin_dir / "origin.json").write_text(
        json.dumps(origin, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _update_lock(
    skills_dir,
    provider,
    slug,
    local_name,
    target,
    registry,
    version,
    archive_sha256,
):
    provider = _normalize_provider(provider)
    lock_dir = _lock_dir_for_skills_dir(skills_dir, provider)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        lock = {"skills": {}}
    skills = lock.setdefault("skills", {})
    skills[slug] = {
        "source": provider,
        "registry": _registry_value(provider, registry),
        "local_name": local_name,
        "target": target,
        "version": version,
        "archive_sha256": archive_sha256,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    lock_path.write_text(json.dumps(lock, indent=2, ensure_ascii=False), encoding="utf-8")


def _registry_value(provider, registry):
    provider = _normalize_provider(provider)
    return str(
        registry
        or os.getenv(PROVIDERS[provider]["registry_env"])
        or PROVIDERS[provider]["default_registry"]
    )


def _lock_dir_for_skills_dir(skills_dir, provider="clawhub"):
    provider = _normalize_provider(provider)
    skills_dir = Path(skills_dir)
    if skills_dir.name.lower() == "skills":
        return skills_dir.parent / PROVIDERS[provider]["origin_dir"]
    return skills_dir / PROVIDERS[provider]["origin_dir"]


def _ensure_child(parent, child):
    try:
        child.relative_to(parent)
    except ValueError as error:
        raise SkillInstallError(f"Install path is outside skills directory: {child}") from error


def _extract_items(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "results", "skills", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _extract_file_list(skill):
    for key in ("files", "filePaths", "file_paths"):
        value = skill.get(key)
        if isinstance(value, list):
            output = []
            for item in value:
                if isinstance(item, str):
                    output.append(item)
                elif isinstance(item, dict):
                    path = _field(item, "path", "name")
                    if path:
                        output.append(str(path))
            return output
    return []


def _field(data, *names):
    if not isinstance(data, dict):
        return ""
    for name in names:
        if name in data and data[name] not in {None, ""}:
            return data[name]
    for value in data.values():
        if isinstance(value, dict):
            found = _field(value, *names)
            if found not in {None, ""}:
                return found
    return ""


def _version_from_skill_md(text):
    in_frontmatter = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                return ""
            in_frontmatter = True
            continue
        if in_frontmatter and stripped.startswith("version:"):
            return stripped.split(":", 1)[1].strip().strip("\"'")
    return ""


def _single_line(text, max_chars):
    value = " ".join(str(text or "").split())
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "..."

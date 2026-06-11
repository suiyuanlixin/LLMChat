import sys
import base64
import mimetypes
import re
from pathlib import Path

from ui import (
    console,
    print_success,
    print_error,
    print_warn,
    print_thinking,
    get_user_input,
    run_tui,
    show_dashboard,
    start_tui,
    print_stream_thinking_continue,
    clean_and_print_stream_response,
    clean_display_text,
)
from config import load_config
from chat import OmniAgent
from commands import process_command
from tools import MAX_READ_CHARS, normalize_workspace_dir


FILE_REFERENCE_PATTERN = re.compile(r"\[([^\[\]\r\n]+)\]")
IMAGE_MEDIA_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}
VIDEO_MEDIA_TYPES = {
    "video/mp4",
    "video/x-msvideo",
    "video/quicktime",
    "video/x-matroska",
}
IMAGE_FILE_MAX_BYTES = 10 * 1024 * 1024
VIDEO_FILE_MAX_BYTES = 50 * 1024 * 1024
MULTIMODAL_REQUEST_MAX_BYTES = 64 * 1024 * 1024


def _clean_text(text):
    return clean_display_text(text)


def stream_print_thinking(content):
    if content == "\n":
        print_stream_thinking_continue("\n")
    elif content:
        print_stream_thinking_continue(content)


def stream_print_response(content):
    if content == "\n":
        clean_and_print_stream_response("\n")
    elif content:
        clean_and_print_stream_response(content)


def _should_print_unstreamed_thinking(response, stream_mode, thinking_mode):
    return (
        response.get("thinking")
        and thinking_mode
        and (stream_mode or response.get("response_streamed"))
        and response.get("thinking_streamed") is False
    )


def handle_response(response, model_name, stream_mode=False, thinking_mode=False):
    if not response:
        print_error(
            "Failed to get response, please check your APIKey and network connection."
        )
        return

    if _should_print_unstreamed_thinking(response, stream_mode, thinking_mode):
        print_thinking(_clean_text(response.get("thinking")))

    if stream_mode:
        console.print()
        return

    if response.get("response_streamed"):
        console.print()
        return

    if response.get("agent_stopped"):
        return

    thinking = response.get("thinking")
    if thinking and thinking_mode:
        print_thinking(_clean_text(thinking))

    if response.get("thinking_needs_separator") and not response.get("agent_stopped"):
        console.print()

    reply = _clean_text(response["response"])
    print_success(f"{model_name.upper()}: {reply}")


def get_startup_workspace(argv):
    if len(argv) < 2:
        return None, None

    workspace = normalize_workspace_dir(argv[1])
    if workspace is None:
        return None, f"Invalid workspace directory: {argv[1]}"
    return str(workspace), None


def _is_file_reference_path(path_text):
    value = str(path_text or "").strip()
    if not value:
        return False
    if value.startswith(("/", "\\", "~")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    return value.startswith(("./", ".\\", "../", "..\\"))


def _external_file_references(user_input):
    references = []
    seen = set()
    for match in FILE_REFERENCE_PATTERN.finditer(user_input):
        path_text = match.group(1).strip()
        if not _is_file_reference_path(path_text):
            continue
        if path_text in seen:
            continue
        seen.add(path_text)
        references.append(path_text)
    return references


def _resolve_external_file_reference(path_text):
    try:
        path = Path(path_text).expanduser().resolve(strict=True)
    except OSError as error:
        raise ValueError(f"Referenced file does not exist: {path_text}") from error

    if not path.is_file():
        raise ValueError(f"Referenced path is not a file: {path}")
    return path


def _guess_media_type_from_header(path):
    try:
        with path.open("rb") as file:
            header = file.read(32)
    except OSError:
        return None, None

    if header.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image", "image/gif"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image", "image/webp"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return "video", "video/x-msvideo"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "video/x-matroska"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        suffix = path.suffix.lower()
        if suffix == ".mov":
            return "video", "video/quicktime"
        return "video", "video/mp4"
    return None, None


def _detect_reference_media_type(path):
    kind, mime_type = _guess_media_type_from_header(path)
    if kind and mime_type:
        return kind, mime_type

    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type in IMAGE_MEDIA_TYPES:
        return "image", mime_type
    if mime_type in VIDEO_MEDIA_TYPES:
        return "video", mime_type
    return "text", ""


def _read_external_file_reference(path_text):
    path = _resolve_external_file_reference(path_text)

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ValueError(f"Failed to read referenced file: {path}") from error

    truncated = content[:MAX_READ_CHARS]
    if len(content) > MAX_READ_CHARS:
        truncated += (
            f"\n\n[referenced file truncated after {MAX_READ_CHARS} characters]"
        )
    return path, truncated


def _read_external_media_reference(path_text, encoded_bytes_before=0):
    path = _resolve_external_file_reference(path_text)
    kind, mime_type = _detect_reference_media_type(path)
    if kind not in {"image", "video"}:
        return None

    size = path.stat().st_size
    max_bytes = IMAGE_FILE_MAX_BYTES if kind == "image" else VIDEO_FILE_MAX_BYTES
    if size > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise ValueError(
            f"Referenced {kind} file is too large for MiniMax-M3 base64 input "
            f"({size} bytes > {limit_mb} MB): {path}"
        )

    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as error:
        raise ValueError(f"Failed to read referenced media file: {path}") from error

    encoded_bytes = len(data.encode("ascii"))
    total_encoded = encoded_bytes_before + encoded_bytes
    if total_encoded > MULTIMODAL_REQUEST_MAX_BYTES:
        limit_mb = MULTIMODAL_REQUEST_MAX_BYTES // (1024 * 1024)
        raise ValueError(
            f"Referenced media files exceed MiniMax-M3 request body budget "
            f"({total_encoded} encoded bytes > {limit_mb} MB)."
        )

    return {
        "path": str(path),
        "kind": kind,
        "mime_type": mime_type,
        "data": data,
        "bytes": size,
        "encoded_bytes": encoded_bytes,
        "detail": "default",
    }


def attach_external_file_references(user_input):
    user_input, _media_references = attach_external_file_references_with_media(
        user_input
    )
    return user_input


def attach_external_file_references_with_media(user_input):
    references = _external_file_references(user_input)
    if not references:
        return user_input, []

    blocks = [
        (
            "[Referenced external files]\n"
            "The user explicitly attached these read-only file contents. "
            "They do not grant access to directories or other external files."
        )
    ]
    media_references = []
    encoded_media_bytes = 0
    for path_text in references:
        media_reference = _read_external_media_reference(
            path_text,
            encoded_media_bytes,
        )
        if media_reference:
            media_references.append(media_reference)
            encoded_media_bytes += media_reference["encoded_bytes"]
            blocks.append(
                f"--- {media_reference['kind'].title()}: "
                f"{media_reference['path']} "
                f"({media_reference['mime_type']}, "
                f"{media_reference['bytes']} bytes) ---\n"
                "Attached as multimodal input for MiniMax-M3-capable APIs.\n"
                f"--- End {media_reference['kind']}: "
                f"{media_reference['path']} ---"
            )
            continue

        path, content = _read_external_file_reference(path_text)
        blocks.append(f"--- File: {path} ---\n{content}\n--- End file: {path} ---")

    return f"{user_input}\n\n" + "\n\n".join(blocks), media_references


def run_chat_loop(
    config, workspace_dir, workspace_error=None, agent_auto_disabled=False
):
    if workspace_error:
        print_error(workspace_error)
    if agent_auto_disabled:
        print_warn(
            "Agent mode requires a startup workspace directory and has been turned off."
        )
    try:
        chat = OmniAgent(
            model=config.model,
            api_key=config.api_key,
            api_type=config.api_type,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            context_window_tokens=config.context_window_tokens,
            temperature=config.temperature,
            stream_mode=config.stream_mode,
            thinking_mode=config.thinking_mode,
            reasoning_effort=config.reasoning_effort,
            agent_mode=config.agent_mode,
            workspace_dir=workspace_dir,
            max_agent_rounds=config.max_agent_rounds,
            max_agent_tool_calls=config.max_agent_tool_calls,
            agent_approval_mode=config.agent_approval_mode,
            agent_show_thinking=config.agent_show_thinking,
            agent_summary_model=config.agent_summary_model,
            skills_enabled=config.skills_enable,
            skills_source_app=config.skills_source_app,
            skills_source_workspace=config.skills_source_workspace,
            skills_auto_catalog=config.skills_auto_catalog,
            skills_max_chars=config.skills_max_chars,
            compaction_enable=config.compaction_enable,
            compaction_trigger_ratio=config.compaction_trigger_ratio,
            compaction_keep_recent_messages=config.compaction_keep_recent_messages,
            compaction_compact_model=config.compaction_compact_model,
            memory_model=config.memory_model,
            debug=config.debug,
            web_search_enabled=config.web_search_enable,
            web_search_provider=config.web_search_provider,
            web_search_api_key=config.web_search_api_key,
            web_search_max_results=config.web_search_max_results,
            web_search_depth=config.web_search_depth,
            web_search_topic=config.web_search_topic,
            agent_plan_enabled=config.agent_plan_enable,
        )
    except Exception as error:
        print_error(f"Failed to initialize client: {error}")
        return

    while True:
        try:
            user_input = get_user_input("You: ", multiline=True)

            if not user_input:
                print_error("Please enter a non-empty message.")
                continue

            if user_input.startswith("/"):
                should_continue = process_command(user_input, chat)
                if should_continue is False:
                    break
                if should_continue is True:
                    continue

            try:
                user_input, media_references = (
                    attach_external_file_references_with_media(user_input)
                )
            except ValueError as error:
                print_error(str(error))
                continue

            response = chat.send_message(
                user_input,
                stream_print_thinking,
                stream_print_response,
                media_references=media_references,
            )
            handle_response(
                response,
                chat.model,
                chat.stream_mode and not chat.agent_mode,
                chat.thinking_mode,
            )
            if response and not response.get("agent_stopped"):
                chat.update_session_episodic_memory()

        except KeyboardInterrupt:
            console.print()
            print_success("Conversation interrupted, goodbye!")
            break
        except EOFError:
            console.print()
            print_success("Conversation interrupted, goodbye!")
            break
        except Exception as error:
            print_error(f"Error occurred: {error}")


def main():
    config = load_config()
    workspace_dir, workspace_error = get_startup_workspace(sys.argv)
    agent_auto_disabled = False
    if config.agent_mode and not workspace_dir:
        config.agent_mode = False
        agent_auto_disabled = True

    if sys.stdin.isatty() and sys.stdout.isatty():
        start_tui(
            config.model,
            workspace_dir,
            config.api_type,
            config.thinking_mode,
            config.reasoning_effort,
        )
        run_tui(
            lambda: run_chat_loop(
                config,
                workspace_dir,
                workspace_error,
                agent_auto_disabled,
            )
        )
        return

    show_dashboard(
        config.model,
        workspace_dir,
        config.api_type,
        config.thinking_mode,
        config.reasoning_effort,
    )
    run_chat_loop(config, workspace_dir, workspace_error, agent_auto_disabled)


if __name__ == "__main__":
    main()

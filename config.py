import os
import json
from dataclasses import dataclass

from ui import print_error, print_success, print_warn, print_info, get_user_input
from search import (
    DEFAULT_WEB_SEARCH_DEPTH,
    DEFAULT_WEB_SEARCH_ENABLE,
    DEFAULT_WEB_SEARCH_MAX_RESULTS,
    DEFAULT_WEB_SEARCH_PROVIDER,
    DEFAULT_WEB_SEARCH_TOPIC,
    TAVILY_SEARCH_DEPTHS,
    TAVILY_TOPICS,
    WEB_SEARCH_PROVIDERS,
)

CONFIG_FILE = "config.json"
API_TYPE_GLM = "glm"
API_TYPE_ANTHROPIC = "anthropic"
API_TYPE_OPENAI = "openai"
API_TYPE_OLLAMA = "ollama"
DEFAULT_API_TYPE = API_TYPE_GLM
DEFAULT_BASE_URL = ""
DEFAULT_MODEL = "glm-4.7"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7
DEFAULT_STREAM_MODE = False
DEFAULT_THINKING_MODE = False
DEFAULT_AGENT_MODE = False
DEFAULT_MAX_AGENT_ROUNDS = 12
DEFAULT_MAX_AGENT_TOOL_CALLS = 40
DEFAULT_AGENT_APPROVAL_MODE = "confirm"
DEFAULT_AGENT_SUMMARY_MODEL = ""
DEFAULT_COMPACTION_ENABLE = True
DEFAULT_COMPACTION_MAX_CHARS = 60000
DEFAULT_COMPACTION_KEEP_RECENT_MESSAGES = 12
DEFAULT_COMPACTION_COMPACT_MODEL = ""
AGENT_THINKING_OFF = "off"
AGENT_THINKING_SUMMARY = "summary"
AGENT_THINKING_FULL = "full"
DEFAULT_AGENT_SHOW_THINKING = AGENT_THINKING_SUMMARY
AGENT_APPROVAL_MODES = {"confirm", "auto"}
AGENT_THINKING_MODES = {AGENT_THINKING_OFF, AGENT_THINKING_SUMMARY, AGENT_THINKING_FULL}
SUPPORTED_API_TYPES = {
    API_TYPE_GLM,
    API_TYPE_ANTHROPIC,
    API_TYPE_OPENAI,
    API_TYPE_OLLAMA,
}


@dataclass
class AppConfig:
    api_type: str = DEFAULT_API_TYPE
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE
    stream_mode: bool = DEFAULT_STREAM_MODE
    thinking_mode: bool = DEFAULT_THINKING_MODE
    agent_mode: bool = DEFAULT_AGENT_MODE
    max_agent_rounds: int = DEFAULT_MAX_AGENT_ROUNDS
    max_agent_tool_calls: int = DEFAULT_MAX_AGENT_TOOL_CALLS
    agent_approval_mode: str = DEFAULT_AGENT_APPROVAL_MODE
    agent_show_thinking: str = DEFAULT_AGENT_SHOW_THINKING
    agent_summary_model: str = DEFAULT_AGENT_SUMMARY_MODEL
    compaction_enable: bool = DEFAULT_COMPACTION_ENABLE
    compaction_max_chars: int = DEFAULT_COMPACTION_MAX_CHARS
    compaction_keep_recent_messages: int = DEFAULT_COMPACTION_KEEP_RECENT_MESSAGES
    compaction_compact_model: str = DEFAULT_COMPACTION_COMPACT_MODEL
    web_search_enable: bool = DEFAULT_WEB_SEARCH_ENABLE
    web_search_provider: str = DEFAULT_WEB_SEARCH_PROVIDER
    web_search_api_key: str = ""
    web_search_max_results: int = DEFAULT_WEB_SEARCH_MAX_RESULTS
    web_search_depth: str = DEFAULT_WEB_SEARCH_DEPTH
    web_search_topic: str = DEFAULT_WEB_SEARCH_TOPIC

    def to_flat_dict(self):
        return {
            "api_type": self.api_type,
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream_mode": self.stream_mode,
            "thinking_mode": self.thinking_mode,
            "agent_mode": self.agent_mode,
            "max_agent_rounds": self.max_agent_rounds,
            "max_agent_tool_calls": self.max_agent_tool_calls,
            "agent_approval_mode": self.agent_approval_mode,
            "agent_show_thinking": self.agent_show_thinking,
            "agent_summary_model": self.agent_summary_model,
            "compaction_enable": self.compaction_enable,
            "compaction_max_chars": self.compaction_max_chars,
            "compaction_keep_recent_messages": self.compaction_keep_recent_messages,
            "compaction_compact_model": self.compaction_compact_model,
            "web_search_enable": self.web_search_enable,
            "web_search_provider": self.web_search_provider,
            "web_search_api_key": self.web_search_api_key,
            "web_search_max_results": self.web_search_max_results,
            "web_search_depth": self.web_search_depth,
            "web_search_topic": self.web_search_topic,
        }

    def to_dict(self):
        return {
            "api_type": self.api_type,
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream_mode": self.stream_mode,
            "thinking_mode": self.thinking_mode,
            "agent_mode": {
                "enable": self.agent_mode,
                "max_rounds": self.max_agent_rounds,
                "max_tool_calls": self.max_agent_tool_calls,
                "approve": self.agent_approval_mode,
                "show_thinking": self.agent_show_thinking,
                "summary_model": self.agent_summary_model,
            },
            "auto_compact": {
                "enable": self.compaction_enable,
                "max_chars": self.compaction_max_chars,
                "keep_recent_messages": self.compaction_keep_recent_messages,
                "compact_model": self.compaction_compact_model,
            },
            "web_search": {
                "enable": self.web_search_enable,
                "provider": self.web_search_provider,
                "api_key": self.web_search_api_key,
                "max_results": self.web_search_max_results,
                "search_depth": self.web_search_depth,
                "topic": self.web_search_topic,
            },
        }


def normalize_api_type(api_type):
    return str(api_type or DEFAULT_API_TYPE).strip().lower()


def requires_api_key(api_type):
    return normalize_api_type(api_type) != API_TYPE_OLLAMA


def _normalize_base_url(api_type, base_url):
    if normalize_api_type(api_type) == API_TYPE_GLM:
        return ""
    return str(base_url or "").strip()


def _default_config():
    return {
        "api_type": DEFAULT_API_TYPE,
        "base_url": DEFAULT_BASE_URL,
        "model": DEFAULT_MODEL,
        "api_key": "",
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": DEFAULT_TEMPERATURE,
        "stream_mode": DEFAULT_STREAM_MODE,
        "thinking_mode": DEFAULT_THINKING_MODE,
        "agent_mode": DEFAULT_AGENT_MODE,
        "max_agent_rounds": DEFAULT_MAX_AGENT_ROUNDS,
        "max_agent_tool_calls": DEFAULT_MAX_AGENT_TOOL_CALLS,
        "agent_approval_mode": DEFAULT_AGENT_APPROVAL_MODE,
        "agent_show_thinking": DEFAULT_AGENT_SHOW_THINKING,
        "agent_summary_model": DEFAULT_AGENT_SUMMARY_MODEL,
        "auto_compact": {
            "enable": DEFAULT_COMPACTION_ENABLE,
            "max_chars": DEFAULT_COMPACTION_MAX_CHARS,
            "keep_recent_messages": DEFAULT_COMPACTION_KEEP_RECENT_MESSAGES,
            "compact_model": DEFAULT_COMPACTION_COMPACT_MODEL,
        },
        "web_search": {
            "enable": DEFAULT_WEB_SEARCH_ENABLE,
            "provider": DEFAULT_WEB_SEARCH_PROVIDER,
            "api_key": "",
            "max_results": DEFAULT_WEB_SEARCH_MAX_RESULTS,
            "search_depth": DEFAULT_WEB_SEARCH_DEPTH,
            "topic": DEFAULT_WEB_SEARCH_TOPIC,
        },
    }


def _parse_positive_integer(value, label):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be an integer.") from error

    if parsed <= 0:
        raise ValueError(f"{label} must be greater than 0.")
    return parsed


def parse_max_tokens(value):
    return _parse_positive_integer(value, "Max tokens")


def parse_agent_rounds(value):
    return _parse_positive_integer(value, "Agent max rounds")


def parse_agent_tool_calls(value):
    return _parse_positive_integer(value, "Agent max tool calls")


def parse_compaction_max_chars(value):
    return _parse_positive_integer(value, "Auto compact max chars")


def parse_compaction_keep_recent_messages(value):
    return _parse_positive_integer(value, "Auto compact keep recent messages")


def parse_web_search_max_results(value):
    parsed = _parse_positive_integer(value, "Web search max results")
    if parsed > 20:
        raise ValueError("Web search max results must be between 1 and 20.")
    return parsed


def parse_web_search_provider(value):
    provider = str(value or DEFAULT_WEB_SEARCH_PROVIDER).strip().lower()
    if provider not in WEB_SEARCH_PROVIDERS:
        raise ValueError("Web search provider must be tavily.")
    return provider


def parse_web_search_depth(value):
    depth = str(value or DEFAULT_WEB_SEARCH_DEPTH).strip().lower()
    if depth not in TAVILY_SEARCH_DEPTHS:
        raise ValueError("Web search depth must be basic, fast, ultra-fast, or advanced.")
    return depth


def parse_web_search_topic(value):
    topic = str(value or DEFAULT_WEB_SEARCH_TOPIC).strip().lower()
    if topic not in TAVILY_TOPICS:
        raise ValueError("Web search topic must be general, news, or finance.")
    return topic


def parse_agent_approval_mode(value):
    if value is None:
        mode = DEFAULT_AGENT_APPROVAL_MODE
    elif isinstance(value, bool):
        raise ValueError("Agent approval mode must be confirm or auto.")
    else:
        mode = str(value).strip().lower() or DEFAULT_AGENT_APPROVAL_MODE
    if mode not in AGENT_APPROVAL_MODES:
        raise ValueError("Agent approval mode must be confirm or auto.")
    return mode


def parse_agent_show_thinking(value):
    if isinstance(value, bool):
        return AGENT_THINKING_SUMMARY if value else AGENT_THINKING_OFF
    if value is None:
        return DEFAULT_AGENT_SHOW_THINKING

    mode = str(value).strip().lower()
    if mode in AGENT_THINKING_MODES:
        return mode
    if mode in {"true", "ture", "1", "yes", "on"}:
        return AGENT_THINKING_SUMMARY
    if mode in {"false", "0", "no", "off", "none", "hide", "hidden"}:
        return AGENT_THINKING_OFF
    if mode in {"summary", "brief", "short", "summarize", "summarized"}:
        return AGENT_THINKING_SUMMARY
    if mode in {"full", "raw", "verbose", "all"}:
        return AGENT_THINKING_FULL
    raise ValueError("Agent thinking display must be summary, full, or off.")


def parse_temperature(value):
    try:
        temperature = float(str(value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError("Temperature must be a number.") from error

    if temperature < 0 or temperature > 1:
        raise ValueError("Temperature must be between 0 and 1.")
    return temperature


def _parse_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "ture", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _extract_agent_config(config):
    raw_agent_config = config.get("agent_mode", DEFAULT_AGENT_MODE)
    if isinstance(raw_agent_config, dict):
        agent_config = dict(raw_agent_config)
    else:
        agent_config = {"enable": raw_agent_config}

    aliases = {
        "enabled": "enable",
        "max_agent_rounds": "max_rounds",
        "max_agent_tool_calls": "max_tool_calls",
        "agent_approval_mode": "approve",
        "approval_mode": "approve",
        "approval": "approve",
        "agent_show_thinking": "show_thinking",
        "agent_summary_model": "summary_model",
        "thinking_summary_model": "summary_model",
    }
    for source, target in aliases.items():
        if target not in agent_config and source in agent_config:
            agent_config[target] = agent_config[source]

    flat_aliases = {
        "max_agent_rounds": "max_rounds",
        "max_agent_tool_calls": "max_tool_calls",
        "agent_approval_mode": "approve",
        "agent_show_thinking": "show_thinking",
        "agent_summary_model": "summary_model",
        "thinking_summary_model": "summary_model",
    }
    for source, target in flat_aliases.items():
        if target not in agent_config and source in config:
            agent_config[target] = config[source]

    return agent_config


def _extract_compaction_config(config):
    raw_compaction_config = config.get("auto_compact", {})
    if isinstance(raw_compaction_config, dict):
        compaction_config = dict(raw_compaction_config)
    else:
        compaction_config = {"enable": raw_compaction_config}

    aliases = {
        "enabled": "enable",
        "max_context_chars": "max_chars",
        "context_max_chars": "max_chars",
        "keep_recent": "keep_recent_messages",
        "recent_messages": "keep_recent_messages",
        "auto_compact_model": "compact_model",
    }
    for source, target in aliases.items():
        if target not in compaction_config and source in compaction_config:
            compaction_config[target] = compaction_config[source]

    flat_aliases = {
        "auto_compact_enable": "enable",
        "auto_compact_max_chars": "max_chars",
        "auto_compact_keep_recent_messages": "keep_recent_messages",
        "auto_compact_compact_model": "compact_model",
    }
    for source, target in flat_aliases.items():
        if target not in compaction_config and source in config:
            compaction_config[target] = config[source]

    return compaction_config


def _extract_web_search_config(config):
    raw_web_search_config = config.get("web_search", {})
    if isinstance(raw_web_search_config, dict):
        web_search_config = dict(raw_web_search_config)
    else:
        web_search_config = {"enable": raw_web_search_config}

    aliases = {
        "enabled": "enable",
        "apiKey": "api_key",
        "key": "api_key",
        "max": "max_results",
        "results": "max_results",
        "depth": "search_depth",
        "searchDepth": "search_depth",
    }
    for source, target in aliases.items():
        if target not in web_search_config and source in web_search_config:
            web_search_config[target] = web_search_config[source]

    flat_aliases = {
        "web_search_enable": "enable",
        "web_search_provider": "provider",
        "web_search_api_key": "api_key",
        "web_search_max_results": "max_results",
        "web_search_depth": "search_depth",
        "web_search_topic": "topic",
    }
    for source, target in flat_aliases.items():
        if target not in web_search_config and source in config:
            web_search_config[target] = config[source]

    return web_search_config


def _sanitize_config(config):
    agent_config = _extract_agent_config(config)
    compaction_config = _extract_compaction_config(config)
    web_search_config = _extract_web_search_config(config)

    config["api_type"] = normalize_api_type(config.get("api_type"))
    if config["api_type"] not in SUPPORTED_API_TYPES:
        print_warn(f"Unsupported API type: {config['api_type']}. Fallback to {DEFAULT_API_TYPE}.")
        config["api_type"] = DEFAULT_API_TYPE

    config["base_url"] = _normalize_base_url(config["api_type"], config.get("base_url"))
    config["model"] = str(config.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    config["api_key"] = str(config.get("api_key") or "").strip()

    try:
        config["max_tokens"] = parse_max_tokens(config.get("max_tokens", DEFAULT_MAX_TOKENS))
    except ValueError as error:
        print_warn(f"Invalid max_tokens in {CONFIG_FILE}: {error} Fallback to {DEFAULT_MAX_TOKENS}.")
        config["max_tokens"] = DEFAULT_MAX_TOKENS

    try:
        config["temperature"] = parse_temperature(config.get("temperature", DEFAULT_TEMPERATURE))
    except ValueError as error:
        print_warn(f"Invalid temperature in {CONFIG_FILE}: {error} Fallback to {DEFAULT_TEMPERATURE}.")
        config["temperature"] = DEFAULT_TEMPERATURE

    config["stream_mode"] = _parse_bool(config.get("stream_mode"), DEFAULT_STREAM_MODE)
    config["thinking_mode"] = _parse_bool(config.get("thinking_mode"), DEFAULT_THINKING_MODE)
    config["agent_mode"] = _parse_bool(agent_config.get("enable"), DEFAULT_AGENT_MODE)
    config["agent_summary_model"] = str(
        agent_config.get("summary_model", DEFAULT_AGENT_SUMMARY_MODEL) or ""
    ).strip()
    try:
        config["agent_show_thinking"] = parse_agent_show_thinking(
            agent_config.get("show_thinking", DEFAULT_AGENT_SHOW_THINKING)
        )
    except ValueError as error:
        print_warn(
            f"Invalid agent_mode.show_thinking in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_AGENT_SHOW_THINKING}."
        )
        config["agent_show_thinking"] = DEFAULT_AGENT_SHOW_THINKING
    try:
        config["agent_approval_mode"] = parse_agent_approval_mode(
            agent_config.get("approve", DEFAULT_AGENT_APPROVAL_MODE)
        )
    except ValueError as error:
        print_warn(
            f"Invalid agent_mode.approve in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_AGENT_APPROVAL_MODE}."
        )
        config["agent_approval_mode"] = DEFAULT_AGENT_APPROVAL_MODE

    try:
        config["max_agent_rounds"] = parse_agent_rounds(
            agent_config.get("max_rounds", DEFAULT_MAX_AGENT_ROUNDS)
        )
    except ValueError as error:
        print_warn(
            f"Invalid agent_mode.max_rounds in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_MAX_AGENT_ROUNDS}."
        )
        config["max_agent_rounds"] = DEFAULT_MAX_AGENT_ROUNDS

    try:
        config["max_agent_tool_calls"] = parse_agent_tool_calls(
            agent_config.get("max_tool_calls", DEFAULT_MAX_AGENT_TOOL_CALLS)
        )
    except ValueError as error:
        print_warn(
            f"Invalid agent_mode.max_tool_calls in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_MAX_AGENT_TOOL_CALLS}."
        )
        config["max_agent_tool_calls"] = DEFAULT_MAX_AGENT_TOOL_CALLS

    config["compaction_enable"] = _parse_bool(
        compaction_config.get("enable"),
        DEFAULT_COMPACTION_ENABLE,
    )
    try:
        config["compaction_max_chars"] = parse_compaction_max_chars(
            compaction_config.get("max_chars", DEFAULT_COMPACTION_MAX_CHARS)
        )
    except ValueError as error:
        print_warn(
            f"Invalid auto_compact.max_chars in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_COMPACTION_MAX_CHARS}."
        )
        config["compaction_max_chars"] = DEFAULT_COMPACTION_MAX_CHARS
    try:
        config["compaction_keep_recent_messages"] = parse_compaction_keep_recent_messages(
            compaction_config.get(
                "keep_recent_messages",
                DEFAULT_COMPACTION_KEEP_RECENT_MESSAGES,
            )
        )
    except ValueError as error:
        print_warn(
            f"Invalid auto_compact.keep_recent_messages in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_COMPACTION_KEEP_RECENT_MESSAGES}."
        )
        config["compaction_keep_recent_messages"] = DEFAULT_COMPACTION_KEEP_RECENT_MESSAGES
    config["compaction_compact_model"] = str(
        compaction_config.get("compact_model", DEFAULT_COMPACTION_COMPACT_MODEL) or ""
    ).strip()

    config["web_search_enable"] = _parse_bool(
        web_search_config.get("enable"),
        DEFAULT_WEB_SEARCH_ENABLE,
    )
    try:
        config["web_search_provider"] = parse_web_search_provider(
            web_search_config.get("provider", DEFAULT_WEB_SEARCH_PROVIDER)
        )
    except ValueError as error:
        print_warn(
            f"Invalid web_search.provider in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_WEB_SEARCH_PROVIDER}."
        )
        config["web_search_provider"] = DEFAULT_WEB_SEARCH_PROVIDER
    config["web_search_api_key"] = str(web_search_config.get("api_key") or "").strip()
    try:
        config["web_search_max_results"] = parse_web_search_max_results(
            web_search_config.get("max_results", DEFAULT_WEB_SEARCH_MAX_RESULTS)
        )
    except ValueError as error:
        print_warn(
            f"Invalid web_search.max_results in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_WEB_SEARCH_MAX_RESULTS}."
        )
        config["web_search_max_results"] = DEFAULT_WEB_SEARCH_MAX_RESULTS
    try:
        config["web_search_depth"] = parse_web_search_depth(
            web_search_config.get("search_depth", DEFAULT_WEB_SEARCH_DEPTH)
        )
    except ValueError as error:
        print_warn(
            f"Invalid web_search.search_depth in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_WEB_SEARCH_DEPTH}."
        )
        config["web_search_depth"] = DEFAULT_WEB_SEARCH_DEPTH
    try:
        config["web_search_topic"] = parse_web_search_topic(
            web_search_config.get("topic", DEFAULT_WEB_SEARCH_TOPIC)
        )
    except ValueError as error:
        print_warn(
            f"Invalid web_search.topic in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_WEB_SEARCH_TOPIC}."
        )
        config["web_search_topic"] = DEFAULT_WEB_SEARCH_TOPIC

    return AppConfig(**{key: config[key] for key in AppConfig().to_flat_dict()})


def _prompt_api_type(current_api_type):
    prompt = f"API type (glm/anthropic/openai/ollama, Current: {current_api_type}): "
    value = get_user_input(prompt).strip()
    if not value:
        return current_api_type

    api_type = normalize_api_type(value)
    if api_type not in SUPPORTED_API_TYPES:
        print_warn(f"Unsupported API type: {value}. Keep current: {current_api_type}.")
        return current_api_type
    return api_type


def _prompt_base_url(api_type, current_base_url):
    if api_type == API_TYPE_GLM:
        return ""

    current = current_base_url or "None"
    if api_type == API_TYPE_OLLAMA:
        return (
            get_user_input(
                f"Base URL (Current: {current}, empty uses local Ollama): "
            ).strip()
            or current_base_url
        )
    return get_user_input(f"Base URL (Current: {current}): ").strip() or current_base_url


def _prompt_max_tokens(prompt, default_value):
    while True:
        value = get_user_input(prompt).strip()
        if not value:
            return default_value

        try:
            return parse_max_tokens(value)
        except ValueError as error:
            print_error(str(error))


def _prompt_agent_rounds(prompt, default_value):
    while True:
        value = get_user_input(prompt).strip()
        if not value:
            return default_value

        try:
            return parse_agent_rounds(value)
        except ValueError as error:
            print_error(str(error))


def _prompt_agent_tool_calls(prompt, default_value):
    while True:
        value = get_user_input(prompt).strip()
        if not value:
            return default_value

        try:
            return parse_agent_tool_calls(value)
        except ValueError as error:
            print_error(str(error))


def _prompt_agent_approval_mode(prompt, default_value):
    while True:
        value = get_user_input(prompt).strip()
        if not value:
            return default_value

        try:
            return parse_agent_approval_mode(value)
        except ValueError as error:
            print_error(str(error))


def _prompt_temperature(prompt, default_value):
    while True:
        value = get_user_input(prompt).strip()
        if not value:
            return default_value

        try:
            return parse_temperature(value)
        except ValueError as error:
            print_error(str(error))


def load_config():
    config = _load_existing_config()
    if config.api_key or not requires_api_key(config.api_type):
        return config

    print_warn("Configuration file does not exist or API Key is empty!")

    api_type = _prompt_api_type(config.api_type)
    base_url = _prompt_base_url(api_type, config.base_url)
    model = (
        get_user_input(
            f"Please enter the model name (Default: {config.model}): "
        ).strip()
        or config.model
    )
    api_key_prompt = "Please enter your API Key: "
    if not requires_api_key(api_type):
        api_key_prompt = "Please enter your API Key (optional for Ollama local): "
    api_key = get_user_input(api_key_prompt).strip()
    max_tokens = _prompt_max_tokens(
        f"Please enter the maximum tokens (Default: {DEFAULT_MAX_TOKENS}): ",
        DEFAULT_MAX_TOKENS,
    )
    temperature = _prompt_temperature(
        f"Please enter the temperature (Default: {DEFAULT_TEMPERATURE}): ",
        DEFAULT_TEMPERATURE,
    )

    config = AppConfig(
        api_type=api_type,
        base_url=_normalize_base_url(api_type, base_url),
        model=model,
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=temperature,
        stream_mode=DEFAULT_STREAM_MODE,
        thinking_mode=DEFAULT_THINKING_MODE,
        agent_mode=DEFAULT_AGENT_MODE,
        max_agent_rounds=DEFAULT_MAX_AGENT_ROUNDS,
        max_agent_tool_calls=DEFAULT_MAX_AGENT_TOOL_CALLS,
        agent_approval_mode=DEFAULT_AGENT_APPROVAL_MODE,
        agent_show_thinking=DEFAULT_AGENT_SHOW_THINKING,
        agent_summary_model=DEFAULT_AGENT_SUMMARY_MODEL,
        compaction_enable=DEFAULT_COMPACTION_ENABLE,
        compaction_max_chars=DEFAULT_COMPACTION_MAX_CHARS,
        compaction_keep_recent_messages=DEFAULT_COMPACTION_KEEP_RECENT_MESSAGES,
        compaction_compact_model=DEFAULT_COMPACTION_COMPACT_MODEL,
        web_search_enable=DEFAULT_WEB_SEARCH_ENABLE,
        web_search_provider=DEFAULT_WEB_SEARCH_PROVIDER,
        web_search_api_key="",
        web_search_max_results=DEFAULT_WEB_SEARCH_MAX_RESULTS,
        web_search_depth=DEFAULT_WEB_SEARCH_DEPTH,
        web_search_topic=DEFAULT_WEB_SEARCH_TOPIC,
    )
    _save_config(config)

    return config


def reload_config():
    return _load_existing_config()


def _persist_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as file:
            json.dump(config.to_dict(), file, indent=4, ensure_ascii=False)
        print_success(f"Configuration saved to {CONFIG_FILE}")
    except Exception as error:
        print_error(f"Failed to save configuration file: {error}")


def _save_config(config):
    _persist_config(_sanitize_config(config.to_dict()))


def save_config_field(key, value):
    save_config_fields({key: value})


def save_config_fields(fields):
    config = _load_existing_config().to_flat_dict()
    for key in fields:
        if key not in config:
            raise ValueError(f"Unknown config key: {key}")
    config.update(fields)
    _persist_config(_sanitize_config(config))


def _load_existing_config():
    config = _default_config()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                existing = json.load(file)
                if "base_url" not in existing and "url" in existing:
                    existing["base_url"] = existing.get("url", "")
                config.update(existing)
        except Exception as error:
            print_warn(f"Failed to parse {CONFIG_FILE}: {error}. Using defaults.")
    return _sanitize_config(config)


def update_config():
    config = _load_existing_config()

    print_info("Enter new configuration (Enter to keep current)")

    new_api_type = _prompt_api_type(config.api_type)
    new_base_url = _prompt_base_url(new_api_type, config.base_url)
    new_model = (
        get_user_input(f"Model name (Current: {config.model}): ").strip()
        or config.model
    )
    masked_key = config.api_key[:10] + "..." if config.api_key else "None"
    api_key_label = "API Key"
    if not requires_api_key(new_api_type):
        api_key_label = "API Key (optional for Ollama local)"
    new_api_key = (
        get_user_input(f"{api_key_label} (Current: {masked_key}): ").strip() or config.api_key
    )
    new_max_tokens = _prompt_max_tokens(
        f"Max tokens (Current: {config.max_tokens}): ",
        config.max_tokens,
    )
    new_temperature = _prompt_temperature(
        f"Temperature (Current: {config.temperature}): ",
        config.temperature,
    )
    new_max_agent_rounds = _prompt_agent_rounds(
        f"Agent max rounds (Current: {config.max_agent_rounds}): ",
        config.max_agent_rounds,
    )
    new_max_agent_tool_calls = _prompt_agent_tool_calls(
        f"Agent max tool calls (Current: {config.max_agent_tool_calls}): ",
        config.max_agent_tool_calls,
    )
    new_agent_approval_mode = _prompt_agent_approval_mode(
        f"Agent approval mode confirm/auto (Current: {config.agent_approval_mode}): ",
        config.agent_approval_mode,
    )
    new_agent_summary_model = (
        get_user_input(
            f"Agent summary model (Current: {config.agent_summary_model or 'None'}): "
        ).strip()
        or config.agent_summary_model
    )
    new_compaction_compact_model = (
        get_user_input(
            f"Auto compact model (Current: {config.compaction_compact_model or 'Current model'}): "
        ).strip()
        or config.compaction_compact_model
    )

    new_config = AppConfig(
        api_type=new_api_type,
        base_url=_normalize_base_url(new_api_type, new_base_url),
        model=new_model,
        api_key=new_api_key,
        max_tokens=new_max_tokens,
        temperature=new_temperature,
        stream_mode=config.stream_mode,
        thinking_mode=config.thinking_mode,
        agent_mode=config.agent_mode,
        max_agent_rounds=new_max_agent_rounds,
        max_agent_tool_calls=new_max_agent_tool_calls,
        agent_approval_mode=new_agent_approval_mode,
        agent_show_thinking=config.agent_show_thinking,
        agent_summary_model=new_agent_summary_model,
        compaction_enable=config.compaction_enable,
        compaction_max_chars=config.compaction_max_chars,
        compaction_keep_recent_messages=config.compaction_keep_recent_messages,
        compaction_compact_model=new_compaction_compact_model,
        web_search_enable=config.web_search_enable,
        web_search_provider=config.web_search_provider,
        web_search_api_key=config.web_search_api_key,
        web_search_max_results=config.web_search_max_results,
        web_search_depth=config.web_search_depth,
        web_search_topic=config.web_search_topic,
    )
    _save_config(new_config)
    return new_config

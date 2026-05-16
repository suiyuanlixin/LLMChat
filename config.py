import os
import json
from dataclasses import dataclass

from ui import print_error, print_success, print_warn, print_info, get_user_input

CONFIG_FILE = "config.json"
API_TYPE_GLM = "glm"
API_TYPE_ANTHROPIC = "anthropic"
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
AGENT_APPROVAL_MODES = {"confirm", "auto"}
SUPPORTED_API_TYPES = {API_TYPE_GLM, API_TYPE_ANTHROPIC}
API_TYPE_ALIASES = {
    "zhipu": API_TYPE_GLM,
    "zhipuai": API_TYPE_GLM,
    "bigmodel": API_TYPE_GLM,
    "claude": API_TYPE_ANTHROPIC,
    "anthropic-compatible": API_TYPE_ANTHROPIC,
    "anthropic_compatible": API_TYPE_ANTHROPIC,
    "deepseek": API_TYPE_ANTHROPIC,
    "minimax": API_TYPE_ANTHROPIC,
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
            "agent_mode": self.agent_mode,
            "max_agent_rounds": self.max_agent_rounds,
            "max_agent_tool_calls": self.max_agent_tool_calls,
            "agent_approval_mode": self.agent_approval_mode,
        }


def normalize_api_type(api_type):
    value = str(api_type or DEFAULT_API_TYPE).strip().lower()
    return API_TYPE_ALIASES.get(value, value)


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


def parse_agent_approval_mode(value):
    mode = str(value or DEFAULT_AGENT_APPROVAL_MODE).strip().lower()
    if mode not in AGENT_APPROVAL_MODES:
        raise ValueError("Agent approval mode must be confirm or auto.")
    return mode


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
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _sanitize_config(config):
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
    config["agent_mode"] = _parse_bool(config.get("agent_mode"), DEFAULT_AGENT_MODE)
    try:
        config["agent_approval_mode"] = parse_agent_approval_mode(
            config.get("agent_approval_mode", DEFAULT_AGENT_APPROVAL_MODE)
        )
    except ValueError as error:
        print_warn(
            f"Invalid agent_approval_mode in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_AGENT_APPROVAL_MODE}."
        )
        config["agent_approval_mode"] = DEFAULT_AGENT_APPROVAL_MODE

    try:
        config["max_agent_rounds"] = parse_agent_rounds(
            config.get("max_agent_rounds", DEFAULT_MAX_AGENT_ROUNDS)
        )
    except ValueError as error:
        print_warn(
            f"Invalid max_agent_rounds in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_MAX_AGENT_ROUNDS}."
        )
        config["max_agent_rounds"] = DEFAULT_MAX_AGENT_ROUNDS

    try:
        config["max_agent_tool_calls"] = parse_agent_tool_calls(
            config.get("max_agent_tool_calls", DEFAULT_MAX_AGENT_TOOL_CALLS)
        )
    except ValueError as error:
        print_warn(
            f"Invalid max_agent_tool_calls in {CONFIG_FILE}: {error} "
            f"Fallback to {DEFAULT_MAX_AGENT_TOOL_CALLS}."
        )
        config["max_agent_tool_calls"] = DEFAULT_MAX_AGENT_TOOL_CALLS

    return AppConfig(**{key: config[key] for key in _default_config()})


def _prompt_api_type(current_api_type):
    prompt = f"API type (glm/anthropic, Current: {current_api_type}): "
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
    if config.api_key:
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
    api_key = get_user_input("Please enter your API Key: ").strip()
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
    )
    _save_config(config)

    return config


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
    config = _load_existing_config().to_dict()
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
    new_api_key = (
        get_user_input(f"API Key (Current: {masked_key}): ").strip() or config.api_key
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
    )
    _save_config(new_config)
    return new_config

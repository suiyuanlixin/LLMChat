import os
import json

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


def normalize_api_type(api_type):
    value = (api_type or DEFAULT_API_TYPE).strip().lower()
    return API_TYPE_ALIASES.get(value, value)


def _normalize_base_url(api_type, base_url):
    if normalize_api_type(api_type) == API_TYPE_GLM:
        return ""
    return (base_url or "").strip()


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
    }


def _config_to_tuple(config):
    return (
        config["api_type"],
        config["base_url"],
        config["model"],
        config["api_key"],
        config["max_tokens"],
        config["temperature"],
        config["stream_mode"],
        config["thinking_mode"],
    )


def _sanitize_config(config):
    config["api_type"] = normalize_api_type(config.get("api_type"))
    if config["api_type"] not in SUPPORTED_API_TYPES:
        print_warn(f"Unsupported API type: {config['api_type']}. Fallback to {DEFAULT_API_TYPE}.")
        config["api_type"] = DEFAULT_API_TYPE

    config["base_url"] = _normalize_base_url(config["api_type"], config.get("base_url"))
    config["model"] = (config.get("model") or DEFAULT_MODEL).strip()
    config["api_key"] = (config.get("api_key") or "").strip()
    config["max_tokens"] = config.get("max_tokens", DEFAULT_MAX_TOKENS)
    config["temperature"] = config.get("temperature", DEFAULT_TEMPERATURE)
    config["stream_mode"] = config.get("stream_mode", DEFAULT_STREAM_MODE)
    config["thinking_mode"] = config.get("thinking_mode", DEFAULT_THINKING_MODE)
    return config


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


def load_config():
    config = _load_existing_config()
    if config["api_key"]:
        return _config_to_tuple(config)

    print_warn("Configuration file does not exist or API Key is empty!")

    api_type = _prompt_api_type(config["api_type"])
    base_url = _prompt_base_url(api_type, config["base_url"])
    model = (
        get_user_input(
            f"Please enter the model name (Default: {config['model']}): "
        ).strip()
        or config["model"]
    )
    api_key = get_user_input("Please enter your API Key: ")
    max_tokens_str = get_user_input(
        f"Please enter the maximum tokens (Default: {DEFAULT_MAX_TOKENS}): "
    )
    max_tokens = int(max_tokens_str) if max_tokens_str else DEFAULT_MAX_TOKENS
    temp_str = get_user_input(
        f"Please enter the temperature (Default: {DEFAULT_TEMPERATURE}): "
    )
    temperature = float(temp_str) if temp_str else DEFAULT_TEMPERATURE

    _save_config(api_type, base_url, model, api_key, max_tokens, temperature, False, DEFAULT_THINKING_MODE)

    return api_type, base_url, model, api_key, max_tokens, temperature, DEFAULT_STREAM_MODE, DEFAULT_THINKING_MODE


def _persist_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=4, ensure_ascii=False)
        print_success(f"Configuration saved to {CONFIG_FILE}")
    except Exception as error:
        print_error(f"Failed to save configuration file: {error}")


def _save_config(api_type, base_url, model, api_key, max_tokens, temperature, stream_mode=False, thinking_mode=False):
    api_type = normalize_api_type(api_type)
    if api_type not in SUPPORTED_API_TYPES:
        api_type = DEFAULT_API_TYPE

    _persist_config({
        "api_type": api_type,
        "base_url": _normalize_base_url(api_type, base_url),
        "model": model,
        "api_key": api_key,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream_mode": stream_mode,
        "thinking_mode": thinking_mode,
    })


def save_config_field(key, value):
    config = _load_existing_config()
    if key not in config:
        raise ValueError(f"Unknown config key: {key}")
    config[key] = value
    _persist_config(config)


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

    new_api_type = _prompt_api_type(config["api_type"])
    new_base_url = _prompt_base_url(new_api_type, config["base_url"])
    new_model = (
        get_user_input(f"Model name (Current: {config['model']}): ").strip()
        or config["model"]
    )
    masked_key = config["api_key"][:10] + "..." if config["api_key"] else "None"
    new_api_key = (
        get_user_input(f"API Key (Current: {masked_key}): ") or config["api_key"]
    )
    new_max_tokens_str = get_user_input(f"Max tokens (Current: {config['max_tokens']}): ")
    new_max_tokens = (
        int(new_max_tokens_str) if new_max_tokens_str else config["max_tokens"]
    )
    new_temp_str = get_user_input(f"Temperature (Current: {config['temperature']}): ")
    new_temperature = float(new_temp_str) if new_temp_str else config["temperature"]

    _save_config(
        new_api_type,
        new_base_url,
        new_model,
        new_api_key,
        new_max_tokens,
        new_temperature,
        config["stream_mode"],
        config["thinking_mode"],
    )

    return (
        new_api_type,
        _normalize_base_url(new_api_type, new_base_url),
        new_model,
        new_api_key,
        new_max_tokens,
        new_temperature,
        config["stream_mode"],
        config["thinking_mode"],
    )

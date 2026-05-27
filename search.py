import httpx


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_WEB_SEARCH_PROVIDER = "tavily"
DEFAULT_WEB_SEARCH_ENABLE = True
DEFAULT_WEB_SEARCH_MAX_RESULTS = 5
DEFAULT_WEB_SEARCH_DEPTH = "basic"
DEFAULT_WEB_SEARCH_TOPIC = "general"
DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS = 15
MAX_WEB_SEARCH_OUTPUT_CHARS = 12000
MAX_SEARCH_RESULT_CONTENT_CHARS = 1200

WEB_SEARCH_PROVIDERS = {"tavily"}
TAVILY_SEARCH_DEPTHS = {"basic", "fast", "ultra-fast", "advanced"}
TAVILY_TOPICS = {"general", "news", "finance"}
TAVILY_TIME_RANGES = {"day", "week", "month", "year", "d", "w", "m", "y"}


class WebSearchError(Exception):
    pass


def resolve_tavily_api_key(api_key=""):
    key = str(api_key or "").strip()
    if key:
        return key, "config"
    return "", ""


def is_web_search_configured(provider="tavily", api_key=""):
    provider = normalize_web_search_provider(provider)
    if provider != DEFAULT_WEB_SEARCH_PROVIDER:
        return False
    key, _source = resolve_tavily_api_key(api_key)
    return bool(key)


def normalize_web_search_provider(value):
    provider = str(value or DEFAULT_WEB_SEARCH_PROVIDER).strip().lower()
    return provider if provider in WEB_SEARCH_PROVIDERS else DEFAULT_WEB_SEARCH_PROVIDER


def normalize_tavily_search_depth(value):
    depth = str(value or DEFAULT_WEB_SEARCH_DEPTH).strip().lower()
    return depth if depth in TAVILY_SEARCH_DEPTHS else DEFAULT_WEB_SEARCH_DEPTH


def normalize_tavily_topic(value):
    topic = str(value or DEFAULT_WEB_SEARCH_TOPIC).strip().lower()
    return topic if topic in TAVILY_TOPICS else DEFAULT_WEB_SEARCH_TOPIC


def normalize_tavily_time_range(value):
    time_range = str(value or "").strip().lower()
    return time_range if time_range in TAVILY_TIME_RANGES else ""


def search_tavily(
    query,
    api_key="",
    max_results=DEFAULT_WEB_SEARCH_MAX_RESULTS,
    search_depth=DEFAULT_WEB_SEARCH_DEPTH,
    topic=DEFAULT_WEB_SEARCH_TOPIC,
    time_range="",
    include_answer=False,
    include_raw_content=False,
    include_domains=None,
    exclude_domains=None,
    country="",
    timeout=DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS,
):
    query = str(query or "").strip()
    if not query:
        raise WebSearchError("Search query cannot be empty.")

    key, key_source = resolve_tavily_api_key(api_key)
    if not key:
        raise WebSearchError(
            "Tavily API key is missing. Add web_search.api_key in config.json "
            "or run /search key <api-key>."
        )

    max_results = _bounded_int(max_results, DEFAULT_WEB_SEARCH_MAX_RESULTS, 1, 20)
    search_depth = normalize_tavily_search_depth(search_depth)
    topic = normalize_tavily_topic(topic)
    time_range = normalize_tavily_time_range(time_range)

    payload = {
        "query": query,
        "search_depth": search_depth,
        "topic": topic,
        "max_results": max_results,
        "include_answer": _normalize_include_answer(include_answer),
        "include_raw_content": _normalize_include_raw_content(include_raw_content),
        "include_images": False,
        "include_usage": True,
    }
    if time_range:
        payload["time_range"] = time_range
    domains = _string_list(include_domains)
    if domains:
        payload["include_domains"] = domains
    domains = _string_list(exclude_domains)
    if domains:
        payload["exclude_domains"] = domains
    country = str(country or "").strip().lower()
    if country and topic == "general":
        payload["country"] = country

    try:
        response = httpx.post(
            TAVILY_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=float(timeout or DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS),
        )
    except httpx.TimeoutException as error:
        raise WebSearchError("Tavily search timed out.") from error
    except httpx.HTTPError as error:
        raise WebSearchError(f"Tavily search request failed: {error}") from error

    if response.status_code >= 400:
        raise WebSearchError(_tavily_error_message(response))

    try:
        data = response.json()
    except ValueError as error:
        raise WebSearchError("Tavily returned a non-JSON response.") from error

    return format_tavily_response(data, query=query, key_source=key_source)


def format_tavily_response(data, query="", key_source=""):
    if not isinstance(data, dict):
        return "Tavily web search returned an unexpected response."

    results = data.get("results") or []
    usage = data.get("usage") or {}
    credits = usage.get("credits")
    request_id = data.get("request_id") or ""
    response_time = data.get("response_time")

    header = [f"Tavily web search results for: {query or data.get('query') or ''}"]
    if key_source:
        header.append(f"api_key: {key_source}")
    if credits is not None:
        header.append(f"credits: {credits}")
    if response_time is not None:
        header.append(f"response_time: {response_time}s")
    if request_id:
        header.append(f"request_id: {request_id}")

    lines = ["\n".join(header).strip()]

    answer = str(data.get("answer") or "").strip()
    if answer:
        lines.append("Answer:\n" + _truncate(answer, MAX_SEARCH_RESULT_CONTENT_CHARS))

    if not results:
        lines.append("No results found.")
        return "\n\n".join(lines)

    result_lines = []
    for index, item in enumerate(results, 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "(untitled)").strip()
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()
        raw_content = str(item.get("raw_content") or "").strip()
        score = item.get("score")

        block = [f"[{index}] {title}"]
        if url:
            block.append(f"URL: {url}")
        if score is not None:
            block.append(f"Score: {score}")
        if content:
            block.append("Content: " + _truncate(" ".join(content.split()), MAX_SEARCH_RESULT_CONTENT_CHARS))
        if raw_content:
            block.append(
                "Raw content: "
                + _truncate(" ".join(raw_content.split()), MAX_SEARCH_RESULT_CONTENT_CHARS)
            )
        result_lines.append("\n".join(block))

    lines.append("Results:\n" + "\n\n".join(result_lines))
    return _truncate("\n\n".join(lines).strip(), MAX_WEB_SEARCH_OUTPUT_CHARS)


def _tavily_error_message(response):
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        detail = (
            payload.get("detail")
            or payload.get("error")
            or payload.get("message")
            or ""
        )
    if not detail:
        detail = response.text.strip()
    detail = _truncate(detail, 500) if detail else response.reason_phrase
    return f"Tavily search failed ({response.status_code}): {detail}"


def _normalize_include_answer(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"basic", "advanced"}:
            return normalized
        return normalized in {"true", "1", "yes", "on"}
    return bool(value)


def _normalize_include_raw_content(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"markdown", "text"}:
            return normalized
        return normalized in {"true", "1", "yes", "on"}
    return bool(value)


def _bounded_int(value, default, minimum, maximum):
    if isinstance(value, bool):
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _string_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        items = re_split_list(value)
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def re_split_list(value):
    return [item for item in str(value or "").replace(";", ",").split(",")]


def _truncate(text, max_chars):
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "...[truncated]"

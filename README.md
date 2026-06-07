# OmniAgent

OmniAgent 是一个基于 Python 和 Rich 的终端 AI Agent 工作台。它面向本地多步骤任务，支持 GLM / ZhipuAI、Anthropic Messages API、OpenAI Chat Completions API、Gemini API 以及 Ollama 本地 / 云端模型，可在限定工作目录内读文件、搜索代码、编辑文件和执行命令，同时保留普通交互、会话保存、流式输出、推理内容展示和持久记忆能力。

## 功能亮点

- 支持五类 API：`glm` 使用 ZAI SDK，`anthropic` 使用 Anthropic SDK，`openai` 使用 OpenAI SDK，`gemini` 使用 Gemini OpenAI 兼容端点，`ollama` 使用 Ollama Python SDK。
- 支持自定义 `base_url`，可接入 Anthropic Messages API、OpenAI Chat Completions 兼容端点、Gemini OpenAI 兼容端点或 Ollama 服务端。
- 支持普通输出与流式输出，流式模式会实时打印模型返回内容。
- 支持展示模型返回的推理 / 思考内容，例如 GLM `reasoning_content`、Anthropic thinking block、Gemini thought summary 或 MiniMax OpenAI 兼容接口的 `reasoning_details`。
- 使用 Rich 渲染终端界面，包含启动 dashboard、渐变文本和最近历史记录预览。
- 支持会话保存与加载，记录以 JSON 文件保存到 `record/`。
- 支持运行时调整 `max_tokens`、`temperature`、输出模式和思考模式。
- 支持会话上下文自动压缩和 `/comp` 手动压缩，使用分层摘要 + 最近消息窗口延续长任务和长对话。
- 支持 Tavily 网络搜索，普通交互可按需自动检索，Agent 模式会注入 `web_search` 工具。
- 支持持久记忆、偏好记忆、情景记忆和热历史，记忆更新可使用独立模型。
- 支持本地文件 agent 模式，可让模型在限定工作目录内读文件、搜索代码、编辑文件和执行命令。
- 支持 Agent plan 审批、状态恢复和最终校验，适合多步骤本地任务。
- 支持 agent skills，可从程序目录、工作目录、ClawHub 或 SkillHub 加载本机专用工作流指令。
- 对写文件、编辑文件和高风险命令执行用户确认，避免模型无提示修改本地文件。

## 环境要求

- Python 3.10+
- 可访问目标模型服务的 API Key

核心依赖：

- `rich`
- `zai-sdk`
- `anthropic`
- `openai`
- `ollama`
- `httpx`
- `prompt_toolkit`

## 安装

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 中包含运行所需的完整 Python 依赖；如果只想手动安装核心包，可安装 `rich prompt_toolkit zai-sdk anthropic openai ollama httpx`。

## 配置

首次运行时，如果 `config.json` 不存在或 `api_key` 为空，程序会在终端中引导你输入配置。

也可以手动创建或编辑 `config.json`：

```json
{
  "api_type": "glm",
  "base_url": "",
  "model": "glm-4.7",
  "api_key": "YOUR_API_KEY",
  "max_tokens": 4096,
  "temperature": 0.7,
  "stream_mode": false,
  "thinking_mode": false,
  "reasoning_effort": "",
  "context_window_tokens": 128000,
  "debug": false,
  "agent_mode": {
    "enable": false,
    "max_rounds": 12,
    "max_tool_calls": 40,
    "approve": "confirm",
    "show_thinking": "summary",
    "summary_model": "",
    "plan_mode": true
  },
  "skills": {
    "enable": true,
    "sources": {
      "app": true,
      "workspace": false
    },
    "auto_catalog": true,
    "max_skill_chars": 12000
  },
  "auto_compact": {
    "enable": true,
    "trigger_ratio": 0.75,
    "keep_recent_messages": 12,
    "compact_model": ""
  },
  "memory_system": {
    "memory_model": ""
  },
  "web_search": {
    "enable": true,
    "provider": "tavily",
    "api_key": "YOUR_TAVILY_API_KEY",
    "max_results": 5,
    "search_depth": "basic",
    "topic": "general"
  }
}
```

其他接口只需要替换对应的连接字段：

```json
{
  "api_type": "openai",
  "base_url": "https://api.minimaxi.com/v1",
  "model": "MiniMax-M2.7",
  "api_key": "YOUR_API_KEY"
}
```

```json
{
  "api_type": "gemini",
  "base_url": "",
  "model": "gemini-3.5-flash",
  "api_key": "YOUR_GEMINI_API_KEY"
}
```

```json
{
  "api_type": "ollama",
  "base_url": "",
  "model": "qwen3",
  "api_key": ""
}
```

```json
{
  "api_type": "ollama",
  "base_url": "https://ollama.com",
  "model": "gpt-oss:120b",
  "api_key": "YOUR_OLLAMA_API_KEY"
}
```

如果希望通过本地 Ollama 调用云端模型，先在命令行执行 `ollama signin`，再 `ollama pull gpt-oss:120b-cloud`。项目配置保持 `"api_type": "ollama"`、`"base_url": ""`、`"api_key": ""`，把 `"model"` 改成 `gpt-oss:120b-cloud` 即可。

配置字段说明：

| 字段 | 说明 |
| --- | --- |
| `api_type` | API 类型，只支持 `glm`、`anthropic`、`openai`、`gemini` 或 `ollama`。 |
| `base_url` | 自定义 API 地址。`glm` 类型会自动忽略该字段；`anthropic` 和 `openai` 类型可留空使用 SDK 默认地址，也可填写兼容端点。`gemini` 留空时自动使用 `https://generativelanguage.googleapis.com/v1beta/openai/`。MiniMax OpenAI 兼容接口填写 `https://api.minimaxi.com/v1`。`ollama` 留空时使用本地默认服务，通常是 `http://localhost:11434`；直连 Ollama 云端时填写 `https://ollama.com`。 |
| `model` | 模型名称，按服务商要求填写。 |
| `api_key` | API Key。Gemini 使用 Google AI Studio 创建的 Gemini API Key。Ollama 本地模型和通过本地 Ollama 代理调用云端模型可留空；直连 `https://ollama.com` 时填写 Ollama API Key，也可以使用 `OLLAMA_API_KEY` 环境变量。请只保存在本地，不要提交到仓库。 |
| `max_tokens` | 单次回复的最大 token 数。 |
| `temperature` | 采样温度，范围为 `0` 到 `1`。 |
| `stream_mode` | 是否默认启用流式输出。 |
| `thinking_mode` | 是否默认展示模型返回的推理 / 思考内容。 |
| `reasoning_effort` | 推理 / 思考强度。留空时使用服务商默认值；可填写 `none`、`minimal`、`low`、`medium`、`high`、`xhigh` 或 `max`。不同 API 会按官方支持范围自动映射，例如 Gemini 的 `xhigh/max` 会降为 `high`，DeepSeek 的 `low/medium` 会映射为 `high`。 |
| `context_window_tokens` | 当前模型的上下文窗口 token 数，用于按比例计算自动压缩阈值。不同模型请按服务商文档填写。 |
| `debug` | 全局调试开关，默认 `false`。开启后会写入 `memory/memory_update_diagnostics.jsonl` 等诊断信息。 |
| `agent_mode.enable` | 是否默认启用 agent 模式。没有启动工作目录时会自动关闭。 |
| `agent_mode.max_rounds` | 每次用户请求中，agent 最多执行多少轮工具循环。 |
| `agent_mode.max_tool_calls` | 每次用户请求中，agent 最多调用多少次工具。 |
| `agent_mode.approve` | Agent 写操作审批模式，只支持 `confirm` 或 `auto`。 |
| `agent_mode.show_thinking` | Agent thinking 显示方式，支持 `summary`、`full`、`off`。`true` 等同于 `summary`，`false` 等同于 `off`。它不改变模型 thinking 是否开启。 |
| `agent_mode.summary_model` | Agent thinking 概括模型。留空时使用本地规则生成一句话；填写轻量模型名时，会复用当前 API 配置发起单独的流式请求来生成一句话概括。 |
| `agent_mode.plan_mode` | 是否启用 Agent plan 系统。默认 `true`；关闭后不注入 `update_plan`，也不会强制计划审批或显示 Plan 面板。 |
| `skills.enable` | Agent skills 总开关。关闭后不会注入 `list_skills` 和 `read_skill`。 |
| `skills.sources.app` | 是否加载程序目录 `skills/`，适合放所有项目通用的本机 skills。 |
| `skills.sources.workspace` | 是否加载工作目录 `.omniagent/skills/`，默认关闭；开启后如果目录不存在会自动创建。 |
| `skills.auto_catalog` | 是否把已加载 skills 的名称、描述和触发词自动加入 agent system prompt。 |
| `skills.max_skill_chars` | 单次 `read_skill` 读取 `SKILL.md` 和附加文件的最大字符数，超出会截断。 |
| `auto_compact.enable` | 是否启用自动上下文压缩。开启后普通交互和 Agent 模式都会在上下文超过阈值时自动压缩。 |
| `auto_compact.trigger_ratio` | 自动压缩触发比例，默认 `0.75` 表示上一轮请求的输入 token 达到 `context_window_tokens * 0.75` 时压缩。 |
| `auto_compact.keep_recent_messages` | 压缩时保留最近多少条原始消息，其余更早消息会折叠进压缩摘要。 |
| `auto_compact.compact_model` | 执行压缩摘要的模型。留空时使用当前模型；填写模型名时统一使用该模型做自动和手动压缩。 |
| `memory_system.memory_model` | 执行持久记忆和情景记忆更新的模型。留空时使用当前模型。 |
| `web_search.enable` | 是否开启网络搜索功能。开启且配置 Tavily key 后，普通交互和 Agent 都可以使用搜索。 |
| `web_search.provider` | 网络搜索提供商，目前支持 `tavily`。 |
| `web_search.api_key` | Tavily API Key，也可以通过 `/search key <tavily-api-key>` 写入。 |
| `web_search.max_results` | 每次搜索最多返回多少条结果，范围 `1` 到 `20`。 |
| `web_search.search_depth` | Tavily 搜索深度，支持 `basic`、`fast`、`ultra-fast`、`advanced`。 |
| `web_search.topic` | Tavily 搜索主题，支持 `general`、`news`、`finance`。 |

`config.json` 已被 `.gitignore` 忽略，适合存放本地密钥和个人配置。

## 运行

普通交互：

```bash
python main.py
```

指定工作目录后运行：

```bash
python main.py <workspace>
```

启动后会显示 dashboard，包含当前模型、工作目录和最近历史记录。未指定工作目录时，dashboard 会显示 `No workspace directory`。

## 外部文件引用

在消息中使用 `[/path/to/your/file]` 可以把工作目录外的单个文件内容附加给模型阅读。该功能只支持文件，不支持文件夹；引用目录或不存在的路径会取消本次发送并显示错误。

Windows 路径同样支持，例如：

```text
请总结这个文件：[D:\Notes\report.md]
```

文件内容会作为只读上下文附加到本次消息中，不会放开 Agent 工具对工作目录外路径的读写限制。单个引用文件最多附加 `60000` 个字符，超出部分会被截断。

## 自定义提示词

首次运行时会自动创建 `prompt.md`，可以在其中写入自定义提示词、人格、回复风格和个人偏好。程序会在每次模型请求前读取 `prompt.md`，普通交互和 Agent 模式都会生效。

`prompt.md` 已被 `.gitignore` 忽略，适合存放只在本机使用的个人提示词。默认生成的说明内容在 Markdown 注释中，不会发送给模型；把真实提示词写在注释外即可。上下文压缩和 Agent thinking 概括仍使用项目内置提示词，不会附加该文件内容。

## 网络搜索

网络搜索使用 Tavily。开启并配置 key 后，普通交互会在需要近期、外部或容易变化的信息时让模型调用搜索；Agent 模式也会获得 `web_search` 工具。搜索结果会包含来源 URL，回答需要引用搜索结果中的链接。

常用命令：

```text
/search
/search on
/search key tvly-your-api-key
/search provider tavily
/search max 5
/search depth basic
/search topic general
```

当前 provider 只支持 `tavily`。`search_depth` 支持 `basic`、`fast`、`ultra-fast`、`advanced`，`topic` 支持 `general`、`news`、`finance`。如果未配置 `web_search.api_key`，`/search` 会显示 `missing key`，普通交互和 Agent 都不会真正执行网络搜索。

普通交互默认会给模型注入 `read_program_docs` 和 `web_fetch` 只读工具。`read_program_docs` 只能读取程序内置 `README.md`，用于回答“这个程序怎么用”、命令说明、配置说明、Agent 模式和 skills 等学习类问题；`web_fetch` 可读取用户给出的公开 HTTP/HTTPS 网页链接。它们不会授予普通聊天读取工作目录任意文件的权限。

## Agent 模式

Agent 模式类似 Claude Code 的本地工具调用流程：模型可以请求工具，客户端执行工具并把结果返回给模型，直到模型输出最终回复。

当前支持的工具：

| 工具 | 功能 |
| --- | --- |
| `update_plan` | 维护当前 Agent plan，支持优先级、依赖、完成标准、blocked/failed 原因和校验状态。 |
| `ask_user` | 向用户提出一个单选问题，用户可用方向键、数字键和 Enter 选择，结果会回传给模型；Agent 需要澄清多个选项时应使用该工具，而不是在正文里等待用户回复。 |
| `list_dir` | 列出工作目录内文件和目录，支持递归深度限制。 |
| `read_file` | 读取工作目录内的 UTF-8 文本文件，支持按行号范围读取。 |
| `read_program_docs` | 读取程序内置 `README.md`，帮助用户学习命令、配置和使用方式；普通交互也默认可用。 |
| `web_fetch` | 读取指定公开 HTTP/HTTPS 网页链接，支持正文提取或原始 HTML/text。 |
| `write_file` | 创建或覆盖工作目录内文件，并按审批模式确认或自动批准。 |
| `edit_file` | 基于精确字符串替换编辑文件，并按审批模式确认或自动批准。 |
| `apply_patch` | 按 1-based 行号范围替换文件内容，并展示 unified diff 预览。 |
| `apply_unified_patch` | 对单个文件应用 unified diff，并在写入前校验上下文。 |
| `bash` | 在工作目录内执行 shell 命令。明显会修改或删除文件的命令需要用户确认。 |
| `local_http_check` | 临时启动 Python 静态服务，检查一个或多个本地路径的 HTTP 状态，然后自动关闭服务。 |
| `git_status` | 查看工作目录的 `git status --short`，只读且不需要确认。 |
| `git_diff` | 查看完整 diff、diff stat 或 `git diff --check`，只读且不需要确认。 |
| `grep` | 使用正则搜索工作目录内文件内容。 |
| `glob` | 使用 glob 模式匹配工作目录内文件名。 |
| `web_search` | 使用 Tavily 搜索公开网页。仅在网络搜索开启且配置 key 后注入。 |
| `list_skills` | 列出已启用来源中的 agent skills。 |
| `read_skill` | 读取匹配 skill 的 `SKILL.md` 和可选附加文件。 |

开启 agent 模式：

```text
/agent on
```

关闭 agent 模式：

```text
/agent off
```

查看 agent 状态：

```text
/agent
```

设置 agent 预算：

```text
/agent budget 12 40
```

设置 agent 审批模式：

```text
/agent approve confirm
/agent approve auto
```

设置 agent thinking 显示：

```text
/agent show-thinking summary
/agent show-thinking full
/agent show-thinking off
/agent plan on
/agent plan off
```

查看、批准或恢复 Agent plan：

```text
/plan
/plan check
/plan approve
/plan reject
/plan retry <item-id>
/plan unblock <item-id>
/plan history
/plan clear
```

多步骤任务中，Agent 会先用 `update_plan` 创建计划。`agent_mode.approve=confirm` 时，执行非计划工具前会要求确认；`auto` 会自动批准工作目录内的低风险流程。计划和事件日志保存在 `<workspace>/.omniagent/plans/`，该目录已被 `.gitignore` 忽略。可用 `agent_mode.plan_mode=false` 或 `/agent plan off` 关闭 plan 系统；关闭后 Agent 不会强制计划审批，也不会显示 Plan 面板。

设置 agent skills：

```text
/skills on
/skills off
/skills reload
/skills app on
/skills workspace on
/skills catalog on
/skills max-chars 12000
```

Agent skills 只在 agent 模式中可用，支持两个来源：程序目录 `skills/` 和工作目录 `.omniagent/skills/`。工作目录来源默认关闭，开启后如果目录不存在会自动创建。每个 skill 使用一个目录，目录名只能包含小写字母、数字、`-` 和 `_`：

```text
skills/
  git-commit/
    SKILL.md
    references/

<workspace>/.omniagent/skills/
  project-review/
    SKILL.md
```

`SKILL.md` 可以包含 frontmatter：

```md
---
description: Git commit message workflow.
triggers:
  - git commit
enabled: true
---

# Instructions
...
```

Skills 只提供工作流指导，不会执行 skill 里的脚本，也不能覆盖 agent 安全规则、工作目录限制、审批设置或工具限制。程序目录 `skills/` 已被 `.gitignore` 忽略，适合放本机专用指令。

从 ClawHub 或 SkillHub 安装 skills：

```text
/skills search clawhub git --limit 5
/skills inspect clawhub:git-commit
/skills install clawhub:git-commit --workspace
/skills install skillhub:owner/name --app
```

默认安装到工作目录 `.omniagent/skills/`；使用 `--app` 会安装到程序目录 `skills/`，对所有开启程序目录来源的工作区生效。安装前会展示预览并要求确认，可用 `--dry-run` 只预览、`--force` 覆盖已有 skill、`--yes` 跳过确认、`--version <version>` 指定版本、`--registry <url>` 指定 registry。安装来源和 lock 文件分别写入 `.clawhub/` 或 `.skillhub/`，这些目录已被 `.gitignore` 忽略。

安全限制：

- 必须通过启动参数传入工作目录后才能开启 agent 模式。
- 所有工具只能访问工作目录及其子目录。
- 普通交互默认可用的 `read_program_docs` 只读取程序内置 `README.md`，`web_fetch` 只读取公开 HTTP/HTTPS 网页；二者不开放工作目录文件读取权限。
- `web_fetch` 会阻止 localhost、内网、回环、link-local、保留地址等非公开地址。
- 路径中不允许使用父级目录引用。
- 写入类工具会展示 unified diff。`agent_mode.approve=auto` 时会自动批准工作目录内的文件编辑和低风险命令。
- `bash` 在工作目录内执行，并拦截明显越界路径。
- 删除、包安装、变更 git 历史等高风险命令仍会要求用户确认。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示所有可用命令。 |
| `/quit` | 退出程序。 |
| `/clear` | 清空当前会话上下文。 |
| `/save` | 将当前会话保存到 `record/`。 |
| `/load` | 从 `record/` 选择并加载历史会话。 |
| `/conf` | 重新配置 API 类型、地址、模型、密钥和参数；配置成功后会重新初始化客户端并清空当前上下文。 |
| `/conf reload` | 从 `config.json` 重新加载配置并应用到当前会话。 |
| `/token` | 查看当前 `max_tokens`。 |
| `/token <num>` | 设置 `max_tokens`，例如 `/token 8192`。 |
| `/temp` | 查看当前 `temperature`。 |
| `/temp <val>` | 设置 `temperature`，例如 `/temp 0.7`。 |
| `/mode` | 查看当前输出模式。 |
| `/mode normal` | 切换到普通输出模式。 |
| `/mode stream` | 切换到流式输出模式。 |
| `/think` | 查看当前推理 / 思考内容展示模式。 |
| `/think on` | 开启推理 / 思考内容展示。 |
| `/think off` | 关闭推理 / 思考内容展示。 |
| `/comp` | 立即按当前 auto_compact 配置压缩会话上下文；消息数不超过 `keep_recent_messages` 时会取消压缩。 |
| `/plan` | 查看当前 Agent plan、审批状态、预算和计划文件路径。 |
| `/plan check` | 查看当前 plan 质量检查结果。 |
| `/plan approve [note]` / `/plan reject [reason]` | 手动批准或拒绝当前 Agent 计划。 |
| `/plan retry <item-id>` / `/plan unblock <item-id>` | 将 failed 或 blocked 的计划项恢复为可继续执行。 |
| `/plan history [limit]` / `/plan clear` | 查看计划事件日志或清空当前 plan。 |
| `/memory` | 查看持久记忆文件、记忆模型、debug 状态和主要记忆内容。 |
| `/memory core` / `/memory prefs` / `/memory today` | 查看核心记忆、偏好记忆或当天情景记忆。 |
| `/memory date YYYY-MM-DD` / `/memory search <query>` | 查看指定日期情景记忆或搜索情景记忆。 |
| `/memory history` / `/memory path` | 查看热历史摘要或持久记忆文件路径。 |
| `/memory prefs tidy` / `/memory prefs remove <text>` / `/memory prefs level <level> <text>` | 整理、删除或调整偏好记忆等级。 |
| `/search` / `/search status` | 查看网络搜索状态、provider、结果数、深度和 topic。 |
| `/search on` / `/search off` | 开启或关闭网络搜索。 |
| `/search key <tavily-api-key>` | 将 Tavily API Key 写入 `config.json`。 |
| `/search provider tavily` | 设置网络搜索 provider。当前只支持 `tavily`。 |
| `/search max <1-20>` / `/search depth basic|fast|ultra-fast|advanced` / `/search topic general|news|finance` | 调整网络搜索返回数量、搜索深度和主题。 |
| `/agent` | 查看当前 agent 状态。 |
| `/agent on` | 开启 agent 模式。 |
| `/agent off` | 关闭 agent 模式。 |
| `/agent stop` | 请求停止当前正在运行的 agent 任务。 |
| `/agent budget <rounds> <tool-calls>` | 设置每次请求的 agent 工具循环轮数和工具调用次数上限。 |
| `/agent approve confirm` / `/agent approve auto` | 设置 Agent 写操作审批模式。 |
| `/agent show-thinking summary` / `/agent show-thinking full` / `/agent show-thinking off` | 只控制 agent thinking 的显示，不改变模型 thinking 是否开启。`summary` 只显示一句话概括。 |
| `/agent plan on` / `/agent plan off` | 开启或关闭 Agent plan 系统。关闭后不注入 `update_plan`，也不会强制计划审批。 |
| `/skills` | 查看 agent skills 状态、来源和读取限制。 |
| `/skills on` / `/skills off` / `/skills reload` | 开启、关闭或重新加载 agent skills。 |
| `/skills app on` / `/skills app off` | 开启或关闭程序目录 skills 来源。 |
| `/skills workspace on` / `/skills workspace off` | 开启或关闭工作目录 `.omniagent/skills/` 来源，开启时目录不存在会自动创建。 |
| `/skills catalog on` / `/skills catalog off` | 控制是否把 skills 摘要自动加入 Agent system prompt。 |
| `/skills max-chars <num>` | 设置单次读取 skill 文件的最大字符数。 |
| `/skills search clawhub|skillhub <query>` | 从 ClawHub 或 SkillHub 搜索可安装 skills。 |
| `/skills inspect clawhub:<slug>` / `/skills inspect skillhub:<owner>/<name>` | 查看远程 skill 元数据、文件列表、警告和安装目标。 |
| `/skills install clawhub:<slug>` / `/skills install skillhub:<owner>/<name>` | 安装远程 skill，支持 `--workspace`、`--app`、`--dry-run`、`--force`、`--yes`、`--version`、`--registry`。 |
| `Ctrl+C` | 中断并退出程序。 |

## 输出模式

普通模式会等待模型完整返回后再显示结果。如果服务商返回推理内容，程序会先显示 Thinking，再显示最终回答。

流式模式会实时显示模型返回的 thinking delta 和 text delta。实际是否能看到推理内容取决于模型和服务商是否返回对应字段。`reasoning_effort` 可用于调节支持该能力的模型思考强度；Gemini 在 `thinking_mode=true` 时会请求 thought summary；Ollama thinking 模型会读取 `message.thinking` 字段；`gpt-oss` 系列开启 thinking 且未配置 `reasoning_effort` 时默认使用 `medium` 级别。

Anthropic Agent 模式使用 streaming API 读取工具调用事件和 thinking delta，避免长请求触发 SDK 的非流式限制。GLM / OpenAI / Gemini Agent 模式使用 Chat Completions 工具循环，Ollama Agent 模式使用 `/api/chat` 工具调用循环，并会在每轮响应返回后立即显示可用的 thinking。

Agent 运行时会隐藏 round 编号、工具调用编号、工具结果摘要和 final check 摘要等中间噪音，只保留必要的确认、警告、错误和最终回复。Thinking、确认框和最终回复之间会自动保持一行间距。

Agent 模式在 `show_thinking=summary` 时只显示一句话概括，避免完整 thinking 中的大段代码影响阅读。配置 `agent_mode.summary_model` 后，会使用轻量模型单独发起流式请求生成概括；未配置时使用本地规则伪流式显示。开启流式模式时，Agent 最终回复会以伪流式方式显示。

## 上下文压缩

上下文压缩使用“压缩摘要 + 最近消息窗口”的策略。自动压缩开启后，普通交互和 Agent 模式都会根据上一轮模型调用返回的输入 token 判断是否超过 `context_window_tokens * auto_compact.trigger_ratio`，并用当前历史的本地 token 粗估补上本轮新增消息。超过阈值时，将较早消息和已有压缩摘要交给 `auto_compact.compact_model` 生成新的连续性摘要，并保留最近 `auto_compact.keep_recent_messages` 条原始消息。如果服务端没有返回 usage，客户端会临时使用本地 token 粗估作为兜底。

`auto_compact.compact_model` 留空时使用当前模型；填写模型名时，自动压缩和 `/comp` 手动压缩都会统一使用该模型。手动执行 `/comp` 时，如果当前消息数不超过 `keep_recent_messages`，会提示取消压缩。

持久记忆和情景记忆更新使用 `memory_system.memory_model`。该字段留空时使用当前模型；填写后只影响记忆写入，不影响压缩摘要模型。

## 持久记忆

程序会在 `memory/` 下维护本机持久记忆：

```text
memory/
  core.md
  preferences.md
  episodes/
    YYYY-MM-DD.md
  history.jsonl
```

`core.md` 保存长期事实、目标和约束，`preferences.md` 保存偏好，`episodes/YYYY-MM-DD.md` 保存按日期归档的情景记忆，`history.jsonl` 保存热历史。使用 `/memory` 可以查看路径和状态，使用 `/memory prefs tidy`、`/memory prefs remove <text>`、`/memory prefs level Critical|High|Medium|Low <text>` 可以整理偏好记忆。

全局 `debug` 默认为 `false`。只有开启后，记忆更新才会额外写入 `memory/memory_update_diagnostics.jsonl`，用于排查模型返回的记忆 JSON、解析结果和写入状态。`memory/` 已被 `.gitignore` 忽略，默认不会提交到仓库。

## 会话记录

使用 `/save` 后，程序会在 `record/` 下生成类似下面的文件：

```text
record/2026-04-25-22-33.json
```

记录文件结构：

```json
{
  "version": "3.0.0",
  "model": "model-name",
  "created_at": "2026-04-25T22:33:00",
  "conversation": [
    {
      "role": "user",
      "content": "Hello"
    },
    {
      "role": "assistant",
      "content": "Hi!"
    }
  ]
}
```

Agent 模式会话可能包含 Anthropic `tool_use` / `tool_result` content block 或 GLM tool 消息。保存和加载时会尽量按可读文本展示。

Agent 运行结束后，中间工具消息会折叠成摘要写入会话历史，避免后续对话继承大量内部 tool 结果。

`record/` 已被 `.gitignore` 忽略，默认不会提交到仓库。

## 项目结构

```text
OmniAgent/
├── record/                  # 会话记录目录
├── chat.py                  # OmniAgent 客户端、API 调用、流式处理和 agent loop
├── commands.py              # 命令解析、分发与 handler
├── config.py                # 配置读取、校验、交互式更新和持久化
├── installer.py             # ClawHub / SkillHub skills 搜索、检查和安装
├── main.py                  # 程序入口、启动参数、事件循环和 UI 组装
├── memory.py                # 持久记忆、偏好记忆、情景记忆和热历史
├── planning.py              # Agent plan、审批状态、事件日志和质量检查
├── search.py                # Tavily 网络搜索封装和结果格式化
├── session.py               # 会话保存与加载
├── skills.py                # Agent skills 加载、索引和读取
├── tools.py                 # Agent 工具 schema、本地执行器和安全限制
└── ui.py                    # Rich 终端 UI、dashboard、流式输出和确认界面
```

## 常见问题

**提示 `No module named 'zai'`**

安装 ZAI SDK：

```bash
pip install zai-sdk
```

**提示 `No module named 'anthropic'`**

安装 Anthropic SDK：

```bash
pip install anthropic
```

**提示 `No module named 'openai'`**

安装 OpenAI SDK：

```bash
pip install openai
```

**提示 `No module named 'ollama'`**

安装 Ollama Python SDK：

```bash
pip install ollama
```

**Ollama 本地模型无法连接**

确认本机 Ollama 已安装并运行，且已经拉取模型：

```bash
ollama pull qwen3
ollama run qwen3
```

配置中 `api_type` 使用 `ollama`，`base_url` 留空即可走本地默认服务。如果 Ollama 服务运行在其他机器或端口，填写对应地址，例如 `http://192.168.1.10:11434`。

**无法开启 agent 模式**

请确认启动时传入了工作目录：

```bash
python main.py D:\Code
```

未传入工作目录时，`/agent on` 会被拒绝，并显示 `No workspace directory`。

**网络搜索显示 `missing key`**

先写入 Tavily API Key：

```text
/search key tvly-your-api-key
/search on
```

当前网络搜索只支持 Tavily。配置成功后，`/search` 会显示 `available`。

**请求失败或无响应**

检查 `api_key`、`base_url`、`model` 是否与服务商要求一致，并确认当前网络可以访问对应 API。

**终端显示异常**

建议使用支持 UTF-8 和 ANSI 颜色的现代终端，例如 Windows Terminal、PowerShell 7、iTerm2 或 GNOME Terminal。

## 许可证

GNU 通用公共许可证 v3.0

# LLMChat

LLMChat 是一个基于 Python 和 Rich 的终端大模型聊天客户端。它支持 GLM / ZhipuAI、Anthropic Messages API 以及兼容 Anthropic Messages API 的服务，并提供会话保存、流式输出、推理内容展示和本地文件 agent 模式。

## 功能亮点

- 支持两类 API：`glm` 使用 ZAI SDK，`anthropic` 使用 Anthropic SDK。
- 支持自定义 `base_url`，可接入 Anthropic Messages API 兼容端点。
- 支持普通输出与流式输出，流式模式会实时打印模型返回内容。
- 支持展示模型返回的推理 / 思考内容，例如 GLM `reasoning_content` 或 Anthropic thinking block。
- 使用 Rich 渲染终端界面，包含启动 dashboard、渐变文本和最近历史记录预览。
- 支持会话保存与加载，记录以 JSON 文件保存到 `record/`。
- 支持运行时调整 `max_tokens`、`temperature`、输出模式和思考模式。
- 支持本地文件 agent 模式，可让模型在限定工作目录内读文件、搜索代码、编辑文件和执行命令。
- 对写文件、编辑文件和高风险命令执行用户确认，避免模型无提示修改本地文件。

## 环境要求

- Python 3.10+
- 可访问目标模型服务的 API Key

核心依赖：

- `rich`
- `zai-sdk`
- `anthropic`

## 安装

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install rich zai-sdk anthropic
```

macOS / Linux：

```bash
python -m venv .venv
source .venv/bin/activate
pip install rich zai-sdk anthropic
```

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
  "agent_mode": {
    "enable": false,
    "max_rounds": 12,
    "max_tool_calls": 40,
    "approve": "confirm",
    "show_thinking": "summary",
    "summary_model": ""
  }
}
```

配置字段说明：

| 字段 | 说明 |
| --- | --- |
| `api_type` | API 类型，支持 `glm` 或 `anthropic`。也支持部分别名，如 `zhipu`、`zhipuai`、`claude`、`minimax`、`deepseek`。 |
| `base_url` | 自定义 API 地址。`glm` 类型会自动忽略该字段；`anthropic` 类型可留空使用默认地址，也可填写兼容端点。 |
| `model` | 模型名称，按服务商要求填写。 |
| `api_key` | API Key。请只保存在本地，不要提交到仓库。 |
| `max_tokens` | 单次回复的最大 token 数。 |
| `temperature` | 采样温度，范围为 `0` 到 `1`。 |
| `stream_mode` | 是否默认启用流式输出。 |
| `thinking_mode` | 是否默认展示模型返回的推理 / 思考内容。 |
| `agent_mode.enable` | 是否默认启用 agent 模式。没有启动工作目录时会自动关闭。 |
| `agent_mode.max_rounds` | 每次用户请求中，agent 最多执行多少轮工具循环。 |
| `agent_mode.max_tool_calls` | 每次用户请求中，agent 最多调用多少次工具。 |
| `agent_mode.approve` | Agent 写操作审批模式，只支持 `confirm` 或 `auto`。 |
| `agent_mode.show_thinking` | Agent thinking 显示方式，支持 `summary`、`full`、`off`。`true` 等同于 `summary`，`false` 等同于 `off`。它不改变模型 thinking 是否开启。 |
| `agent_mode.summary_model` | Agent thinking 概括模型。留空时使用本地规则生成一句话；填写轻量模型名时，会复用当前 API 配置发起单独的流式请求来生成一句话概括。 |

`config.json` 已被 `.gitignore` 忽略，适合存放本地密钥和个人配置。

## 运行

普通聊天：

```bash
python main.py
```

指定工作目录后运行：

```bash
python main.py <workspace>
```

启动后会显示 dashboard，包含当前模型、工作目录和最近历史记录。未指定工作目录时，dashboard 会显示 `No workspace directory`。

## Agent 模式

Agent 模式类似 Claude Code 的本地工具调用流程：模型可以请求工具，客户端执行工具并把结果返回给模型，直到模型输出最终回复。

当前支持的工具：

| 工具 | 功能 |
| --- | --- |
| `list_dir` | 列出工作目录内文件和目录，支持递归深度限制。 |
| `read_file` | 读取工作目录内的 UTF-8 文本文件，支持按行号范围读取。 |
| `write_file` | 创建或覆盖工作目录内文件，并按审批模式确认或自动批准。 |
| `edit_file` | 基于精确字符串替换编辑文件，并按审批模式确认或自动批准。 |
| `apply_patch` | 按 1-based 行号范围替换文件内容，并展示 unified diff 预览。 |
| `apply_unified_patch` | 对单个文件应用 unified diff，并在写入前校验上下文。 |
| `bash` | 在工作目录内执行 shell 命令。明显会修改或删除文件的命令需要用户确认。 |
| `git_status` | 查看工作目录的 `git status --short`，只读且不需要确认。 |
| `git_diff` | 查看完整 diff、diff stat 或 `git diff --check`，只读且不需要确认。 |
| `grep` | 使用正则搜索工作目录内文件内容。 |
| `glob` | 使用 glob 模式匹配工作目录内文件名。 |

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
```

安全限制：

- 必须通过启动参数传入工作目录后才能开启 agent 模式。
- 所有工具只能访问工作目录及其子目录。
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
| `/agent` | 查看当前 agent 状态。 |
| `/agent on` | 开启 agent 模式。 |
| `/agent off` | 关闭 agent 模式。 |
| `/agent stop` | 请求停止当前正在运行的 agent 任务。 |
| `/agent budget <rounds> <tool-calls>` | 设置每次请求的 agent 工具循环轮数和工具调用次数上限。 |
| `/agent approve confirm` / `/agent approve auto` | 设置 Agent 写操作审批模式。 |
| `/agent show-thinking summary` / `/agent show-thinking full` / `/agent show-thinking off` | 只控制 agent thinking 的显示，不改变模型 thinking 是否开启。`summary` 只显示一句话概括。 |
| `Ctrl+C` | 中断并退出程序。 |

## 输出模式

普通模式会等待模型完整返回后再显示结果。如果服务商返回推理内容，程序会先显示 Thinking，再显示最终回答。

流式模式会实时显示模型返回的 thinking delta 和 text delta。实际是否能看到推理内容取决于模型和服务商是否返回对应字段。

Anthropic Agent 模式使用 streaming API 读取工具调用事件和 thinking delta，避免长请求触发 SDK 的非流式限制。GLM Agent 模式仍使用非流式工具循环，但会在每轮响应返回后立即显示 thinking。

Agent 运行时会隐藏 round 编号、工具调用编号、工具结果摘要和 final check 摘要等中间噪音，只保留必要的确认、警告、错误和最终回复。Thinking、确认框和最终回复之间会自动保持一行间距。

Agent 模式在 `show_thinking=summary` 时只显示一句话概括，避免完整 thinking 中的大段代码影响阅读。配置 `agent_mode.summary_model` 后，会使用轻量模型单独发起流式请求生成概括；未配置时使用本地规则伪流式显示。开启流式模式时，Agent 最终回复会以伪流式方式显示。

## 会话记录

使用 `/save` 后，程序会在 `record/` 下生成类似下面的文件：

```text
record/2026-04-25-22-33.json
```

记录文件结构：

```json
{
  "version": "2.5.0",
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
LLMChat/
├── record/                  # 会话记录目录
├── chat.py                  # LLMChat 客户端、API 调用、流式处理和 agent loop
├── commands.py              # 命令解析、分发与 handler
├── config.py                # 配置读取、校验、交互式更新和持久化
├── main.py                  # 程序入口、启动参数、事件循环和 UI 组装
├── session.py               # 会话保存与加载
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

**无法开启 agent 模式**

请确认启动时传入了工作目录：

```bash
python main.py D:\Code
```

未传入工作目录时，`/agent on` 会被拒绝，并显示 `No workspace directory`。

**请求失败或无响应**

检查 `api_key`、`base_url`、`model` 是否与服务商要求一致，并确认当前网络可以访问对应 API。

**终端显示异常**

建议使用支持 UTF-8 和 ANSI 颜色的现代终端，例如 Windows Terminal、PowerShell 7、iTerm2 或 GNOME Terminal。

## 许可证

GNU 通用公共许可证 v3.0

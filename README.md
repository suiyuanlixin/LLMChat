# LLMChat

LLMChat 是一个基于 Python 和 Rich 的终端大模型聊天客户端。它把模型配置、会话记录、流式输出和推理内容展示整合在一个轻量命令行工具里，适合在终端中快速使用 GLM、Claude 或其他 Anthropic Messages API 兼容服务。

## 功能亮点

- 支持两类 API：`glm` 使用 ZAI / 智谱 SDK，`anthropic` 使用 Anthropic SDK。
- 支持自定义 `base_url`，可接入 Anthropic Messages API 兼容端点。
- 支持普通输出与流式输出，流式模式会实时打印模型返回内容。
- 可展示模型返回的推理 / 思考内容，例如 `reasoning_content` 或 Anthropic thinking block。
- 使用 Rich 渲染终端界面，包含启动面板、渐变文字、最近历史记录预览。
- 支持会话保存与加载，记录以 JSON 文件保存在 `record/`。
- 支持运行时调整 `max_tokens`、`temperature` 和输出模式。

## 环境要求

- Python 3.10+（当前项目虚拟环境使用 Python 3.14）
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
  "stream_mode": false
}
```

配置字段说明：

| 字段 | 说明 |
| --- | --- |
| `api_type` | API 类型，支持 `glm` 或 `anthropic`。也支持部分别名，如 `zhipu`、`claude`、`minimax`、`deepseek`。 |
| `base_url` | 自定义 API 地址。`glm` 类型会自动忽略该字段；`anthropic` 类型可留空使用默认地址，也可填写兼容端点。 |
| `model` | 模型名称，按服务商要求填写。 |
| `api_key` | API Key。请只保存在本地，不要提交到仓库。 |
| `max_tokens` | 单次回复的最大 token 数。 |
| `temperature` | 采样温度，范围为 `0` 到 `1`。 |
| `stream_mode` | 是否默认启用流式输出。 |

`config.json` 已被 `.gitignore` 忽略，适合存放本地密钥和个人配置。

## 运行

```bash
python main.py
```

启动后会显示一个终端面板，包含当前模型、工作目录和最近的会话记录。输入自然语言即可开始对话；输入 `/help` 可查看命令。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示所有可用命令。 |
| `/quit` | 退出程序。 |
| `/clear` | 清空当前会话上下文。 |
| `/save` | 将当前会话保存到 `record/`。 |
| `/load` | 从 `record/` 选择并加载历史会话。 |
| `/conf` | 重新配置 API 类型、地址、模型、密钥和参数；配置成功后会重新初始化客户端并清空当前上下文。 |
| `/token` | 查看当前 `max_tokens`。 |
| `/token <num>` | 设置 `max_tokens`，例如 `/token 8192`。 |
| `/temp` | 查看当前 `temperature`。 |
| `/temp <val>` | 设置 `temperature`，例如 `/temp 0.7`。 |
| `/mode` | 查看当前输出模式。 |
| `/mode normal` | 切换到普通输出模式。 |
| `/mode stream` | 切换到流式输出模式。 |
| `Ctrl+C` | 强制中断并退出程序。 |

## 输出模式

普通模式会等待模型完整返回后再显示结果。如果服务商返回推理内容，程序会先显示 Thinking，再显示最终回答。

流式模式会实时显示模型返回的 thinking delta 和 text delta。实际是否能看到推理内容取决于模型和服务商是否返回对应字段。

## 会话记录

使用 `/save` 后，程序会在 `record/` 下生成类似下面的文件：

```text
record/2026-04-25-22-33.json
```

记录文件结构：

```json
{
  "version": "1.0",
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

`record/` 已被 `.gitignore` 忽略，默认不会提交到仓库。

## 项目结构

```text
LLMChat/
├─ record/       # 会话记录目录，已被 git 忽略
├─ config.json   # 本地配置文件，已被 git 忽略
├─ config.py     # 配置读取、校验、交互式更新和持久化
├─ main.py       # 程序入口、聊天客户端、命令处理、会话保存/加载
├─ README.md     # 项目说明
└─ ui.py         # Rich 终端 UI、渐变文本、启动面板、流式输出渲染
```

## 常见问题

**提示 `No module named 'zai'`**

安装 ZAI SDK：

```bash
pip install zai-sdk
```

**请求失败或无响应**

检查 `api_key`、`base_url`、`model` 是否与服务商要求一致，并确认当前网络可访问对应 API。

**终端显示异常**

建议使用支持 UTF-8 和 ANSI 颜色的现代终端，例如 Windows Terminal、PowerShell 7、iTerm2 或 GNOME Terminal。

## License

GNU General Public License v3.0

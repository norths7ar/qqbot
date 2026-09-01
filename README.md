# qqbot

本地运行的私人 QQ 群聊机器人。产品定位是一个能承接多人对话、认识群友并按需回忆往事的“赛博群友”，不是靠大量斜杠命令驱动的 QQ 工具箱。

## 链路

```text
QQ → LLOneBot → OneBot V11 反向 WebSocket → NoneBot2
   → 消息与身份解析 → 共享短期上下文 / 长期记忆 / 只读工具
   → 单一 OpenAI-compatible 多模态模型 → QQ 回复
```

文字、引用和图片在同一次模型请求中处理。项目不再维护“文本模型 + 视觉模型 caption”的双模型链路；当前使用哪个供应商只由 `LLM_BASE_URL`、`LLM_MODEL` 和 `LLM_API_KEY` 决定，代码里没有 MiMo、DeepSeek、Qwen 等供应商分支。

## 目录

- `bot.py`：进程锁、NoneBot 初始化和启动入口。
- `qqbot/plugins/`：matcher 注册、配置读取和依赖装配。
- `qqbot/chat/`：聊天编排、提示词、模型工具和后台记忆任务。
- `qqbot/messaging/`：OneBot 消息、图片、说话者和引用结构解析。
- `qqbot/memory/`：统一身份、claim 长期记忆、证据、提取与审阅。
- `qqbot/integrations/`：通用模型、图片输入、Tavily 和外部内容客户端。
- `qqbot/storage/`：群消息数据库和 SQLite schema。
- `qqbot/runtime/`：项目路径、进程状态和结构化审计日志。
- `qqbot/identity.py`：QQ 账号、群名片与统一真人身份映射。
- `tests/`：不访问外部服务的测试。

## 初始化

项目使用 Python 3.12 和 uv，依赖安装在项目自己的 `.venv`：

```powershell
uv sync
Copy-Item .env.example .env
```

至少填写：

```dotenv
LLM_API_KEY=...
LLM_BASE_URL=https://example.com/v1
LLM_MODEL=your-multimodal-model
ALLOWED_GROUPS=[123456789]
SUPERUSERS=["你的QQ号"]
```

`TAVILY_API_KEY` 可选；未配置时联网搜索工具会明确返回不可用。长期身份映射写在不会提交到 Git 的 `data/people.yaml`，格式参考 `data/people.example.yaml`。

## 运行

```powershell
.\scripts\qqbot.ps1 start
.\scripts\qqbot.ps1 status
.\scripts\qqbot.ps1 logs
.\scripts\qqbot.ps1 restart
.\scripts\qqbot.ps1 stop
```

脚本固定使用项目 `.venv`。运行时数据库和日志路径都锚定仓库根目录，不受启动命令当前目录影响。`status` 会显示运行实例的 Git commit、启动时间、PID、Python 环境、记忆 schema 和 ready 状态。

LLOneBot 需要启用 OneBot 11 反向 WebSocket，地址为 `ws://127.0.0.1:8080/onebot/v11/ws`。如果启用 token，两端必须保持一致。

## 聊天与功能边界

- 只观察 `ALLOWED_GROUPS` 中的消息，主要在被 `@` 时回复。
- 普通群消息进入带统一说话者身份的共享短期上下文；图片只随当前轮直接送入同一个多模态模型。
- 模型可按需调用联网搜索、长期记忆、群友身份、群成员名单和最近群聊工具。
- “刚刚聊了什么”直接用自然语言询问；模型读取最近群聊后回答，没有 `/总结`。
- 不提供清空个人上下文、清空全群上下文或让机器人自行删除长期记忆的能力。
- B 站链接自动解析由 `BILIBILI_AUTO_PARSE_ENABLED` 控制，默认关闭。
- “历史上的今天”保留为未来主动发言方向，由 `HISTORY_TODAY_ENABLED` 预留开关；目前没有斜杠入口，也尚未指定主动发送时间。

公开的斜杠功能命令已经移除。当前只保留三个确定性管理员命令：

- `/记住 @群友 内容`
- `/查看记忆 @群友`
- `/删除记忆 编号`

它们只允许 `SUPERUSERS` 使用，且直接操作正式 claim 记忆。

## 记忆系统

`data/bot_memory.db` 中的 claim 是唯一正式长期记忆后端：

- 人类消息按批次异步提取，BOT 回复永远不作为人物事实证据。
- 每条自动记忆保存人物或群范围、事实谓词、来源类型、置信度、状态、有效期和消息证据。
- 本人明确陈述可以形成有效 profile/preference；第三方说法默认保持候选状态。
- 临时群事件有默认有效期；替代旧事实时保留 supersede 链。
- 旧 V1 记忆在 schema 升级时一次性迁移为可信 `legacy_v1` claim，之后不再双写。
- 旧实验 `shadow_v2` 数据不会删除，但正式检索和后续提取都明确排除它。

`data/group_tools.db` 保存群消息和正式提取游标。两个数据库都不提交到 Git。

本地确定性审阅入口：

```powershell
uv run python -m qqbot.memory_admin status
uv run python -m qqbot.memory_admin recent --limit 20
uv run python -m qqbot.memory_admin candidates
uv run python -m qqbot.memory_admin conflicts
uv run python -m qqbot.memory_admin batches
uv run python -m qqbot.memory_admin show 记忆编号
```

### 待决策

- [ ] 未提取消息达到每群保留上限时，优先保证消息留存，还是优先保证长期记忆最终完整？
- [ ] 群聊低活跃时，是否应在空闲一段时间后提取不足一个批次的尾部消息？需要权衡模型调用成本与记忆及时性。
- [ ] 候选记忆是否继续允许模型按需召回？如果允许，需要通过群聊回放确认模型会稳定保留不确定性。

## 日志与验证

运行日志位于 `data/logs/`，结构化审计日志为 `data/logs/qqbot-audit.jsonl`。同一条聊天链路使用 `trace_id` 关联消息解析、工具、模型、回复和后台记忆任务；API Key、图片二进制和 base64 不写入日志。

```powershell
uv run python -m unittest discover -s tests -v
uv run ruff check bot.py qqbot tests
uv run ruff format --check bot.py qqbot tests
```

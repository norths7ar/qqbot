# qqbot

本地运行的私人 QQ 群聊机器人。项目重点是多人对话连续性、统一身份和可审计的群聊长期记忆，不再扩展提醒、天气、热榜等功能机器人能力。

## 链路

```text
QQ → LLOneBot → OneBot V11 反向 WebSocket → NoneBot2/FastAPI → 消息解析与身份映射 → 群聊上下文/长期记忆/工具 → DeepSeek 或 MiMo → QQ 回复
```

## 目录

- `bot.py`：NoneBot 启动入口。
- `qqbot/plugins/`：matcher 注册、依赖装配和事件转交。
- `qqbot/chat/`：聊天编排、提示词、工具执行和异步记忆任务。
- `qqbot/messaging/`：OneBot 消息解析、发言者与引用协议、提示注入检查和命令路由。
- `qqbot/memory/`：V1 记忆、V2 shadow claims、提取器、回放和审阅工具。
- `qqbot/integrations/`：DeepSeek、MiMo 和 Web 服务客户端。
- `qqbot/storage/`：群消息数据库和 SQLite schema。
- `qqbot/runtime/`：进程状态与审计日志。
- `qqbot/identity.py`：QQ 账号、群名片和统一真人身份映射。
- `tests/`：不访问外部服务的测试。

## 初始化

项目使用 Python 3.12 和 uv。

```powershell
uv sync
Copy-Item .env.example .env
```

在 `.env` 中填写 `DEEPSEEK_API_KEY`、`SUPERUSERS` 及需要启用的 Tavily、MiMo 配置。长期身份映射写在不会提交到 Git 的 `data/people.yaml`，格式参考 `data/people.example.yaml`。

## 运行

```powershell
.\scripts\qqbot.ps1 start
.\scripts\qqbot.ps1 status
.\scripts\qqbot.ps1 logs
.\scripts\qqbot.ps1 restart
.\scripts\qqbot.ps1 stop
```

脚本使用项目 `.venv`，不会自动启动或自动修改端口。`status` 会显示运行实例的 Git commit、启动时间、PID、Python 环境、数据库 schema 和 ready 状态。

LLOneBot 需要启用 OneBot 11 反向 WebSocket，地址为 `ws://127.0.0.1:8080/onebot/v11/ws`。如果启用 token，两端必须保持一致。

## 聊天行为

机器人只处理配置群中的消息，主要在被 `@` 或收到明确命令时回复。普通群消息会进入带说话者标签的共享上下文；当前发言者、引用对象、BOT/真人/未知身份和引用正文通过结构化协议分别传递。

图片由 MiMo 生成临时观察，再交给 DeepSeek 结合群聊语境回复。图片观察不写入长期记忆，API Key、图片二进制和 base64 不写入日志。

常用命令：

- `/菜单`
- `/搜索 关键词`
- `/历史上的今天`，默认禁用
- `/我的记忆`

超级用户还可以使用 `/清空群聊记录 确认`、`/记住 @群友 内容`、`/查看记忆 @群友` 和 `/删除记忆 编号`。

## 记忆系统

- V1 是当前正式记忆，参与 `recall_memory` 和机器人回复。
- V2 保存 claim、evidence、冲突和 shadow batch，目前不参与回复。
- 人类消息按批次异步提取；BOT 回复不作为人物事实证据。
- `data/bot_memory.db` 保存长期记忆，`data/group_tools.db` 保存群消息，两者都不提交到 Git。

本地审阅 V2：

```powershell
uv run python -m qqbot.memory_admin status
uv run python -m qqbot.memory_admin recent --limit 20
uv run python -m qqbot.memory_admin candidates
uv run python -m qqbot.memory_admin conflicts
uv run python -m qqbot.memory_admin batches
```

隔离回放方法和 V2 替换 V1 前的检查项见 `docs/memory-v2-readiness.md`。

## 日志与测试

运行日志位于 `data/logs/`，结构化审计日志为 `data/logs/qqbot-audit.jsonl`。同一条聊天链路使用 `trace_id` 关联消息解析、工具、模型、回复和后台记忆任务。

```powershell
uv run python -m unittest discover -s tests -v
```

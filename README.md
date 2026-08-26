# qqbot

本地运行的私人 QQ 群机器人。LLOneBot 提供 OneBot V11 协议接口，
NoneBot2 使用 FastAPI 驱动并负责上层逻辑。

## 项目结构

- `pyproject.toml`：Python 3.12、NoneBot2、FastAPI 驱动和 OneBot V11
  适配器的项目配置。
- `bot.py`：NoneBot 的显式运行入口，供 `nb run` 和后台脚本共用。
- `.env`：本地运行配置与密钥，不提交到 Git。
- `.env.example`：需要手工填写的配置项模板。
- `qqbot/plugins/`：NoneBot 插件入口，只负责 matcher 注册、依赖装配和事件转交。
- `qqbot/chat/`：聊天编排、提示词、工具执行和异步记忆任务。
- `qqbot/messaging/`：OneBot 消息解析、当前发言者与引用上下文协议、提示注入检查和显式命令路由。
- `qqbot/memory/`：V1 记忆存储与提取、V2 shadow claims、审查 CLI 和运行时单例。
- `qqbot/integrations/`：DeepSeek、MiMo 和 Web 服务客户端。
- `qqbot/storage/`：群消息 SQLite 仓库、schema 辅助函数和共享运行实例。
- `qqbot/runtime/`：进程身份、运行状态与结构化审计日志。
- `qqbot/identity.py` 与 `qqbot/menu.py`：群友身份展示和菜单领域逻辑。
- `qqbot/memory_admin.py`、`qqbot/memory_v2.py` 与 `qqbot/group_data.py`：为现有 CLI 或旧导入保留的兼容入口。
- `tests/`：不访问外部服务的单元测试。

## 本地运行

```powershell
conda activate qqbot
python -m pip install -e .
nb run
```

首次使用时，为当前 Windows 用户启用本地 PowerShell 脚本（无需管理员
权限）：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

之后日常直接使用后台管理脚本，不需要保持 PowerShell 窗口：

```powershell
# 后台启动
.\scripts\qqbot.ps1 start

# 查看状态或最近日志
.\scripts\qqbot.ps1 status
.\scripts\qqbot.ps1 logs

# 重启
.\scripts\qqbot.ps1 restart

# 停止
.\scripts\qqbot.ps1 stop
```

脚本默认使用 `miniconda3\envs\qqbot` 环境，运行状态和日志分别保存到
`data/runtime/` 和 `data/logs/`，不会提交到 Git。这种方式不会自动随
Windows 启动；需要机器人时手动执行 `start` 即可。`bot.py` 进程会自己
持有项目级运行锁并写入 PID、启动时间、Python 路径和入口路径；
`start`、`stop`、`restart` 和 `status` 根据这些进程身份信息管理实例，
不会仅凭 8080 端口判断机器人是否运行。每次启动使用独立日志文件，
避免新实例覆盖仍被旧进程持有的日志。若升级脚本前遗留的实例没有状态文件，
脚本会同时核对配置端口的监听 PID 和 qqbot Conda 环境的精确 Python 路径，
将唯一匹配的实例标为 `legacy unmanaged`；随后可用 `restart` 安全替换。

首次启动前请填写 `.env`。`SUPERUSERS` 可填写为 QQ 号组成的 JSON 数组，
例如 `["123456789"]`。
项目固定只读取这一份本地 `.env`；`ENVIRONMENT=local` 仅用于让启动日志明确显示
本地环境，不会再加载 `.env.prod` 等环境覆盖文件。

如需让聊天机器人查询实时信息，还需要申请 Tavily API Key，并在 `.env` 中填写
`TAVILY_API_KEY`。没有填写时，人物记忆、本地群聊上下文、B站解析和群聊总结
仍可使用。

NoneBot 默认监听 `127.0.0.1:8080`。如需调整监听地址或端口，可在
`.env` 中添加 `HOST` 和 `PORT`。

## 连接 LLOneBot

1. 先启动 NoneBot。
2. 在 LLOneBot 的 OneBot 11 配置中启用反向 WebSocket。
3. 将反向 WebSocket 地址设置为
   `ws://127.0.0.1:8080/onebot/v11/ws`。
4. 保存配置并确认 LLOneBot 已连接；随后 QQ 事件会由 LLOneBot 推送给
   NoneBot。

如果启用访问令牌，请在 LLOneBot 与 NoneBot 两端配置相同的 token。

## LLM 聊天

聊天插件默认只服务群 `482997153`，并只在被 `@` 时回复。群里所有人的普通发言进入
同一份带说话者标签的短期上下文，使 BOT 能承接多人之间的当前话题；不同 QQ 账号仍
通过 `people.yaml` 归并为统一真人。默认保留最近 50 条群消息和最近 3 条 BOT 回复，
闲置 12 小时后整体过期。历史 BOT 回复只用于承接，永远不是人物事实或群梗的证据。

高置信的人设覆盖和提示注入会在调用模型前被拦截，也不会进入短期上下文。
系统提示同时要求模型把用户消息、历史记录和长期记忆视为数据，不能用它们覆盖机器人
身份、输出格式或安全边界。普通的一次性创作请求不受影响。

群内命令：

- `/清空对话`：删除自己所有已绑定 QQ 在本群短期上下文中的互动。
- `/清空本群对话`：仅超级用户可用，只清空本群共享的 LLM 短期上下文。
- `/清空群聊记录 确认`：仅超级用户可用，只删除本群用于总结的持久消息记录。

上下文长度可以通过环境变量调整：

- `LLM_CONTEXT_TURNS`：本群共享短期上下文的消息上限，默认 `50`。
- `LLM_ASSISTANT_CONTEXT_TURNS`：保留的历史 BOT 回复数，默认 `3`；设为
  `0` 可完全不向模型提供旧 BOT 回复。
- `LLM_CONTEXT_TTL_HOURS`：本群连续多少小时没有新消息后丢弃短期上下文，默认
  `12`；设为 `0` 可关闭过期。
  过期按闲置时长计算，不会仅因为跨过零点就清空。

## 统一命令

群内发送 `/菜单` 或 `@机器人 菜单` 可以查看相同的固定纯文本菜单。日常使用以
`@机器人 功能名 参数` 为主；机器人先按第一个空白分隔词精确匹配功能名，命中后
直接执行，不经过模型判断。例如 `@机器人 搜索 关键词`。下面的 `/` 命令提供同样的
确定性快捷入口：

- `/搜索 关键词`
- `/历史上的今天`
- `/总结 [消息条数]`
- `/清空对话`
- `/我的记忆`
- `/忘记我 确认`

其他内容直接 `@` 机器人进行聊天。

## 图片理解

启用 MiMo 多模态配置后，可以在目标群里 `@机器人` 并附带图片，也可以回复一条
带图片的消息再 `@机器人` 提问。机器人最多读取配置数量的图片，由 MiMo 生成一份
只针对当前图片的中性观察，再交给 DeepSeek 结合群聊语境组织最终回复。普通群图片
不会被后台主动识别。

MiMo 观察只保留在有时效的群聊短期上下文中，不写入持久群聊正文，也不进入后台
长期记忆提取。图片内的文字按不可信数据处理，不会作为系统指令执行；模型也被要求
不要仅凭外貌推断具体群友身份、关系或性格。

相关配置：

- `MIMO_API_KEY`：MiMo API 密钥。
- `MIMO_BASE_URL`：兼容 OpenAI API 的基础地址。
- `MIMO_MULTIMODAL_MODEL`：图片理解模型名称。
- `MIMO_MULTIMODAL_ENABLED`：是否启用图片理解。
- `MIMO_MAX_IMAGES`：单条请求最多处理的图片数，默认 `4`。
- `MIMO_MAX_IMAGE_BYTES`：每张图片下载大小上限，默认 `10485760` 字节。
- `MIMO_TIMEOUT_SECONDS`：图片下载及 MiMo 请求超时，默认 `45` 秒。

支持按文件签名验证 JPEG、PNG、GIF、WebP 和 BMP；仅接受 OneBot 消息段提供的
HTTP 或 HTTPS 图片地址。图片服务或 MiMo 暂时失败时，机器人不会猜测图片内容。
成功的 MiMo 观察文本以及失败响应中的状态、`finish_reason`、`refusal` 和错误信息
会写入本地运行日志；API Key、图片二进制和 base64 不会写入日志。

## 审计日志

聊天链路另写一份结构化 JSONL 审计日志到
`data/logs/qqbot-audit.jsonl`。同一条 @ 消息使用稳定的 `trace_id` 串联输入解析、
路由、MiMo、DeepSeek、Tool Calling 和最终生成回复。工具事件包含工具名、参数、
耗时、结果摘要或异常，因此可以区分“模型没有调用搜索”“搜索调用失败”和“搜索成功
后模型如何作答”。后台记忆提取也会记录批次结果。

审计日志自动遮蔽名称中含 key、token、secret、password、authorization 或 cookie
的字段，也不会记录图片二进制和 base64。默认单文件 5 MiB、保留 3 份轮转文件；
`qqbot.ps1 logs` 会同时显示最新运行日志和最近 80 条审计事件。

相关配置：

- `AUDIT_LOG_ENABLED`：是否启用审计日志，默认 `true`。
- `AUDIT_LOG_MAX_BYTES`：单个审计日志文件的轮转阈值，默认 `5242880`。
- `AUDIT_LOG_BACKUP_COUNT`：保留的旧日志数量，默认 `3`。
- `AUDIT_LOG_TEXT_LIMIT`：单个文本字段最多记录字符数，默认 `4000`。

如果第一个词没有精确命中注册功能，消息才作为普通聊天交给模型；模型仍可通过
Tool Calling 自行判断是否需要联网搜索、历史事件、长期记忆、群成员、群友
称呼或群聊记录工具，但这属于自然语言能力，不是要求群友学习的菜单入口。
当一个词可能是群友姓名、别名、群名片或 QQ 昵称，且身份会影响回答时，模型可按需
查询当前群身份；只有精确且唯一的匹配才能确认，普通词义、候选或同名不会强行归人。
路由层不会再根据
“天气”等零散关键词猜测意图或维护特殊语句黑名单。

机器人会把目标群的普通文字消息保存在本机 `data/group_tools.db`，用于共享短期
语境、`/总结` 和后台记忆提取。该数据库不会提交到 Git，最多保留最近 5000 条文字
消息。超级用户可以使用
`/清空群聊记录 确认` 删除本群用于总结的记录；该命令不会清除 LLM 短期上下文。
发送 B 站视频链接、`b23.tv` 短链接或 BV 号时，机器人会自动回复视频标题、UP 主、
时长、播放量、简介和规范链接。

## 群友长期记忆

短期对话重启后清空；群友身份和结构化长期记忆保存在本地
`data/bot_memory.db`，该文件不会提交到 Git。旧的 `/记住` 数据会自动迁移为
`admin_config` 来源的有效人物资料。

项目已创建本地 `data/people.yaml`。参考 `data/people.example.yaml`，为每个真人
设置内部 ID、名称、别名和一个或多个 QQ 号：

```yaml
people:
  zhangsan:
    name: 张三
    qq_ids:
      - "111111111"
      - "222222222"
    aliases:
      - 老张
```

`data/people.yaml` 包含私人账号映射，也不会提交到 Git。修改映射后重启机器人即可
同步；原先由单个 QQ 创建的记忆会迁移到统一身份。

超级用户可使用：

- `/记住 @群友 内容`
- `/查看记忆 @群友`
- `/删除记忆 编号`

群友可用 `/我的记忆` 查看当前群可用的长期记忆，使用 `/忘记我 确认` 删除自己的
全部长期记忆。身份与 QQ 绑定不会随之删除。

后台每累计一批人类消息，会异步提取两类内容：人物资料候选，以及有过期时间的群聊
事件。本人明确陈述的稳定资料可以成为有效记忆；第三方转述、关系陈述和长期群梗先
进入候选状态。BOT 回复不会送入提取器。回复依赖过去信息时，模型通过
`recall_memory` 按需检索，不再把某人的全部记忆永久塞进提示词。

相关配置：

- `MEMORY_AUTO_EXTRACT_ENABLED`：是否启用后台自动提取，默认 `true`。
- `MEMORY_EXTRACT_BATCH_SIZE`：每批处理的人类消息数，默认 `20`。
- `MEMORY_EPISODE_TTL_HOURS`：普通群聊事件的有效期，默认 `72` 小时。
- `MEMORY_V2_SHADOW_ENABLED`：是否启用 V2 影子提取，默认 `false`。影子结果
  只写入独立 claim 表，不参与 BOT 回答。
- `MEMORY_V2_SHADOW_BATCH_SIZE`：V2 每次影子整理的人类消息数，默认 `20`。
- `MEMORY_V2_SHADOW_BACKFILL_EXISTING`：首次启用影子提取时是否回放已有群聊，
  默认 `false`，即从启用后的新消息开始，避免突然处理数千条旧记录。
- `HISTORY_TODAY_ENABLED`：是否开放“历史上的今天”，目前默认 `false`；设为
  `true` 后才会出现在菜单和 LLM 工具中。

V2 本地审阅工具只面向机器人维护者，不暴露为群命令：

```powershell
uv run python -m qqbot.memory_admin status
uv run python -m qqbot.memory_admin recent --limit 20
uv run python -m qqbot.memory_admin candidates
uv run python -m qqbot.memory_admin show 记忆编号
uv run python -m qqbot.memory_admin confirm 记忆编号
uv run python -m qqbot.memory_admin reject 记忆编号
uv run python -m qqbot.memory_admin dispute 记忆编号
uv run python -m qqbot.memory_admin conflicts
uv run python -m qqbot.memory_admin batches
```

`show` 会同时显示证据消息原文；`batches` 会显示每批操作数量、实际写入数量，以及
未通过验证的具体原因。影子 claim 不参与当前 `recall_memory`，因此审阅操作不会改变
BOT 的正式回答。V2 表与 V1 表位于同一个本地数据库，但迁移只新增表；V1 后续写入
会镜像到兼容 claim，关闭影子开关即可停止额外模型调用。

天气、运势、新闻和提醒已经移除。历史上的今天暂时保留代码，但默认禁用。

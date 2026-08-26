# V2 影子记忆就绪边界

当前 V2 仍是 shadow-only，不能替代 V1，也没有生产切换或迁移授权。V1 继续负责正式回复和用户可见语义。

## 必须保持的 V1 兼容面

- `people/people.yaml`、`MemoryStore` 的 QQ 账号到 `person_id` 身份 API，以及同一真人多个账号的统一归属。
- 人物记忆、群记忆、证据、过期和用户可见 memory ID/删除语义。
- `prompt_context`、`search_context`、群命令，以及 `recall_memory`、`my_memories`、`forget_me`。

V2 的 claim、evidence、assertor、冲突和 supersession 只能先作为评估材料；不能静默改变 V1 的检索、回复、删除或权限行为。

## 当前评估入口与维度

`python -m qqbot.memory_replay --source <source.db> --output <temporary.db>` 会用 SQLite `mode=ro` 打开源库，拒绝源/目标相同或覆盖已有目标，然后只在副本上初始化 schema 并汇总 shadow batch。若同时传入 `--group-source <group_tools.db>`，群聊库也会以只读方式复制到旁边的显式副本，并用 `GroupDataStore` 检查 evidence 消息是否仍可解析。报告会暴露 raw response 的 operation 解析、批次状态、rejection/duplicate、claim status、evidence 数量和 assertor；没有人工标注 ground truth 时不声称 precision/recall。

后续评估至少需要覆盖：

- 身份归属与 speaker/subject/assertor 区分；
- evidence 可追溯性、BOT/他人引用隔离和来源消息完整性；
- candidate/active/rejected/disputed/superseded 与重复、冲突处理；
- 检索相关性、V1 兼容命令/API、用户可见 ID 和删除语义；
- 回放可重复性、备份恢复以及错误批次回滚。

## 未来切换前的操作顺序

1. 固定版本和 schema，做只读快照并验证备份可恢复。
2. 在隔离副本完成带人工标注的回放评估，明确准入阈值和已知失败类型。
3. 设计可见、可拒绝、可回滚的 V1→V2 迁移；先小范围 shadow 对照，再由人工批准切换。
4. 保留 V1 表、身份 API、命令和删除路径，验证回滚后回复与用户可见数据一致。

本阶段不执行上述迁移、生产数据库写入、V1/V2 serving 切换或自动重启。

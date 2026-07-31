from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptGuardResult:
    blocked: bool
    score: int


_WEIGHTED_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (
        re.compile(
            r"忽略.{0,12}(之前|以上|原有|系统).{0,8}(指令|提示|规则)"
            r"|ignore.{0,20}(previous|prior|system).{0,12}(instruction|prompt)",
            re.IGNORECASE | re.DOTALL,
        ),
        4,
    ),
    (
        re.compile(
            r"(泄露|输出|显示|复述).{0,10}(系统提示|system prompt|开发者消息)"
            r"|reveal.{0,12}(system prompt|developer message)",
            re.IGNORECASE | re.DOTALL,
        ),
        4,
    ),
    (
        re.compile(
            r"(你现在|从现在开始|接下来).{0,10}(就是|扮演|模拟|作为)"
            r"|please (act|roleplay) as|you are now",
            re.IGNORECASE | re.DOTALL,
        ),
        2,
    ),
    (
        re.compile(
            r"(如果|若).{0,8}(明白|理解).{0,8}(只|仅).{0,4}(回答|回复|输出)"
            r"|if you understand.{0,12}(only reply|respond only)",
            re.IGNORECASE | re.DOTALL,
        ),
        2,
    ),
    (
        re.compile(
            r"(虚拟|假想|角色扮演).{0,20}(不受|无需|忽略|允许).{0,12}(法律|规则|限制)"
            r"|fictional.{0,20}(no rules|unrestricted|allowed)",
            re.IGNORECASE | re.DOTALL,
        ),
        2,
    ),
    (
        re.compile(
            r"(维护|记录).{0,8}(变量|状态|好感度)|\[(debug|system)\]|【debug】",
            re.IGNORECASE | re.DOTALL,
        ),
        2,
    ),
    (
        re.compile(
            r"(不得|不许|必须|每一句|所有回复).{0,16}(格式|结尾|回答|回复|输出)"
            r"|every response.{0,16}(must|end with|format)",
            re.IGNORECASE | re.DOTALL,
        ),
        1,
    ),
    (
        re.compile(r"\b(jailbreak|DAN mode|developer mode)\b", re.IGNORECASE),
        4,
    ),
)

_BLOCK_THRESHOLD = 4

_BLOCK_REPLIES = (
    "角色覆盖脚本收到了，但接管失败。我还是我，有事直接聊。",
    "这么长的设定我看完了，结论是不接管人设。换个正常问题吧。",
    "好感度变量创建失败，我还是原来的群友机器人。想玩梗可以短一点。",
)


def inspect_prompt(text: str) -> PromptGuardResult:
    score = sum(
        weight for pattern, weight in _WEIGHTED_PATTERNS if pattern.search(text)
    )
    if text.count("补充要求") >= 2:
        score += 2
    return PromptGuardResult(blocked=score >= _BLOCK_THRESHOLD, score=score)


def blocked_reply(group_id: int, user_id: int, prompt: str) -> str:
    index = (group_id + user_id + len(prompt)) % len(_BLOCK_REPLIES)
    return _BLOCK_REPLIES[index]

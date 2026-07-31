from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from qqbot.memory import MemoryStore, Person

_ROLE_LABELS = {
    "owner": "群主",
    "admin": "管理员",
    "member": "成员",
}
_ROLE_PRIORITY = {
    "owner": 3,
    "admin": 2,
    "member": 1,
}


@dataclass(slots=True)
class _RosterEntry:
    person: Person | None
    fallback_name: str
    account_count: int = 0
    observed_names: list[str] = field(default_factory=list)
    role: str = "member"
    is_bot: bool = False


def canonical_speaker_name(
    memory_store: MemoryStore,
    user_id: int | str,
    observed_name: str,
) -> str:
    person = memory_store.get_person_by_qq(user_id)
    if person is not None:
        return person.display_name
    return observed_name.strip() or str(user_id)


def format_group_roster(
    memory_store: MemoryStore,
    members: Sequence[Mapping[str, object]],
    *,
    bot_user_id: int | str | None = None,
) -> str:
    entries: dict[str, _RosterEntry] = {}
    normalized_bot_id = str(bot_user_id) if bot_user_id is not None else None

    for member in members:
        user_id = str(member.get("user_id", "")).strip()
        if not user_id:
            continue
        person = memory_store.get_person_by_qq(user_id)
        observed_name = _member_name(member, user_id)
        key = f"person:{person.person_id}" if person else f"qq:{user_id}"
        entry = entries.setdefault(
            key,
            _RosterEntry(
                person=person,
                fallback_name=observed_name,
            ),
        )
        entry.account_count += 1
        if observed_name not in entry.observed_names:
            entry.observed_names.append(observed_name)
        role = str(member.get("role", "member"))
        if _ROLE_PRIORITY.get(role, 0) > _ROLE_PRIORITY.get(entry.role, 0):
            entry.role = role
        entry.is_bot = entry.is_bot or user_id == normalized_bot_id

    if not entries:
        return "未能读取当前群成员名单。"

    lines = ["当前群成员名单（已根据本地身份映射合并同一真人的多个 QQ 账号）："]
    for index, entry in enumerate(entries.values(), start=1):
        lines.append(f"{index}. {_format_roster_entry(entry)}")
    lines.append(
        "以上是实时群成员资料；统一名称和别名来自管理员配置，"
        "群名片和群角色来自当前 QQ 群。不要把同一条中的多个名称当成不同的人。"
    )
    return "\n".join(lines)


def _member_name(member: Mapping[str, object], user_id: str) -> str:
    card = str(member.get("card", "")).strip()
    nickname = str(member.get("nickname", "")).strip()
    return card or nickname or user_id


def _format_roster_entry(entry: _RosterEntry) -> str:
    if entry.person is None:
        name = entry.fallback_name
        details = ["尚未配置统一身份"]
    else:
        name = entry.person.display_name
        details = []
        if entry.person.aliases:
            details.append(f"别名：{'、'.join(entry.person.aliases)}")
        observed = [
            value
            for value in entry.observed_names
            if value != name and value not in entry.person.aliases
        ]
        if observed:
            details.append(f"当前群名片：{'、'.join(observed)}")
        if entry.account_count > 1:
            details.append(f"已合并 {entry.account_count} 个群内 QQ 账号")

    details.append(f"群角色：{_ROLE_LABELS.get(entry.role, '成员')}")
    if entry.is_bot:
        details.append("机器人账号")
    return f"{name}（{'；'.join(details)}）"

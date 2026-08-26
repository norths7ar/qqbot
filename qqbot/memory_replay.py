"""Compatibility entrypoint for isolated shadow-memory replay."""

from qqbot.memory.replay import (
    ShadowReplayReport,
    build_parser,
    build_shadow_replay_report,
    copy_sqlite_readonly,
    main,
)

__all__ = [
    "ShadowReplayReport",
    "build_parser",
    "build_shadow_replay_report",
    "copy_sqlite_readonly",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())

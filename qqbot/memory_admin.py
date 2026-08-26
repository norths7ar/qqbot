"""Compatibility entrypoint for the memory review CLI."""

from qqbot.memory.admin import main


if __name__ == "__main__":
    raise SystemExit(main())

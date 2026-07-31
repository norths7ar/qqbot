from pathlib import Path

from qqbot.memory import MemoryStore

memory_store = MemoryStore(
    database_path=Path("data/bot_memory.db"),
    people_path=Path("data/people.yaml"),
)
memory_store.initialize()

from functools import cache

from qqbot.memory.store import MemoryStore
from qqbot.runtime.paths import PROJECT_ROOT


@cache
def get_memory_store() -> MemoryStore:
    """Create the process-wide store on first explicit use."""
    store = MemoryStore(
        database_path=PROJECT_ROOT / "data" / "bot_memory.db",
        people_path=PROJECT_ROOT / "data" / "people.yaml",
    )
    store.initialize()
    return store

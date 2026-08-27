from functools import cache

from qqbot.runtime.paths import PROJECT_ROOT
from qqbot.storage.group_data import GroupDataStore


@cache
def get_group_data_store() -> GroupDataStore:
    """Create the process-wide group store on first explicit use."""
    store = GroupDataStore(PROJECT_ROOT / "data" / "group_tools.db")
    store.initialize()
    return store

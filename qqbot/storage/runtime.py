from pathlib import Path

from qqbot.storage.group_data import GroupDataStore

group_data_store = GroupDataStore(Path("data/group_tools.db"))
group_data_store.initialize()

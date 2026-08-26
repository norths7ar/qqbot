from pathlib import Path

from qqbot.memory.v2 import ClaimStore

claim_store = ClaimStore(Path("data/bot_memory.db"))
claim_store.initialize()

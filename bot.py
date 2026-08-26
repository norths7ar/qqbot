import nonebot
from nonebot import logger
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from qqbot.memory.v2_runtime import claim_store
from qqbot.runtime.paths import PROJECT_ROOT
from qqbot.runtime.process import BotAlreadyRunningError, BotProcessGuard


def main() -> None:
    guard = BotProcessGuard(PROJECT_ROOT / "data" / "runtime", PROJECT_ROOT / "bot.py")
    try:
        guard.acquire()
    except BotAlreadyRunningError as error:
        raise SystemExit(str(error)) from error

    try:
        nonebot.init(_env_file=(str(PROJECT_ROOT / ".env"),))
        driver = nonebot.get_driver()
        driver.register_adapter(OneBotV11Adapter)
        nonebot.load_from_toml(str(PROJECT_ROOT / "pyproject.toml"))
        # The plugin loader has already imported the process-wide runtime store.
        # Read that same instance after all plugins initialize; do not create a
        # second ClaimStore just for readiness metadata.

        runtime_state = guard.mark_ready(
            memory_schema_version=claim_store.schema_version()
        )
        logger.info(
            "qqbot runtime ready commit={} pid={} started={} python={} "
            "prefix={} memory_schema={}",
            runtime_state["git_commit"],
            runtime_state["pid"],
            runtime_state["started_at"],
            runtime_state["python"],
            runtime_state["python_prefix"],
            runtime_state["memory_schema_version"],
        )
        nonebot.run()
    finally:
        guard.release()


if __name__ == "__main__":
    main()

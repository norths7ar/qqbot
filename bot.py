from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from qqbot.process_runtime import BotAlreadyRunningError, BotProcessGuard


def main() -> None:
    project_root = Path(__file__).resolve().parent
    guard = BotProcessGuard(project_root / "data" / "runtime", Path(__file__))
    try:
        guard.acquire()
    except BotAlreadyRunningError as error:
        raise SystemExit(str(error)) from error

    try:
        nonebot.init(_env_file=(".env",))
        driver = nonebot.get_driver()
        driver.register_adapter(OneBotV11Adapter)
        nonebot.load_from_toml("pyproject.toml")
        nonebot.run()
    finally:
        guard.release()


if __name__ == "__main__":
    main()

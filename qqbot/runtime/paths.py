from pathlib import Path

# This project intentionally treats the repository root as a runtime boundary:
# configuration, persistent data, and the script entrypoint all live beneath it.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

"""TwinGuide 命令行在 Blender 中的启动脚本。"""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parent / "src"
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from twin_guide.cli import main  # noqa: E402

if __name__ == "__main__":
    main()

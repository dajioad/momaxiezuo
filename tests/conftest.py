# tests/conftest.py
"""pytest 公共配置：把项目根加入 sys.path，让测试能 import utils / ui。"""
import sys
from pathlib import Path

# conftest.py 在 tests/ 下，其父目录就是上两级目录
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
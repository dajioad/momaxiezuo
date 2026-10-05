# _bump_version.py - 改 ui/constants.py 里的 APP_VERSION
# 用 Python 读写，彻底避开 PowerShell 5.1 的 Get-Content/Set-Content 编码坑
import re
import sys
import pathlib

if len(sys.argv) < 2:
    print("用法: _bump_version.py <version>")
    sys.exit(1)

version = sys.argv[1].strip()
p = pathlib.Path("ui/constants.py")
if not p.exists():
    print(f"[ERROR] 找不到 {p}")
    sys.exit(1)

s = p.read_text(encoding="utf-8")
new_s, n = re.subn(
    r'APP_VERSION\s*=\s*"[^"]+"',
    f'APP_VERSION = "{version}"',
    s,
)
if n == 0:
    print("[ERROR] ui/constants.py 里没找到 APP_VERSION")
    sys.exit(1)

p.write_text(new_s, encoding="utf-8")
print(f"OK 版本号已改为 {version}")
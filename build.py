#!/usr/bin/env python3
"""墨码写作 - 绿色便携版打包脚本

产物结构：
    dist/墨码写作/
    ├── 墨码写作.exe      主程序
    ├── updater.exe       自动更新器（独立进程，负责替换文件 + 重启）
    ├── 墨码写作.ico
    ├── LICENSE.txt
    ├── README.md
    ├── 用户手册.md
    └── _internal/        主程序依赖
        └── ui/pet/       墨灵宠物图片素材
            └── hanfu/    （含 6 个动作子目录）

依赖收敛：PySide6 + QtAwesome + requests + cryptography + Pillow + pypinyin，
显式排除 torch/easyocr/PDF/Excel 等历史重依赖，目标体积 <= 200MB。

打包前请先安装开发依赖：
    pip install -r requirements-dev.txt
"""
import os
import sys
import shutil
import subprocess

os.chdir(os.path.dirname(os.path.abspath(__file__)))

APP_NAME = "墨码写作"
ICON = "墨码写作.ico"

print("=" * 60)
print(f"{APP_NAME} - 绿色便携版打包脚本")
print("=" * 60)

# 0. 前置检查：PyInstaller 是否可用
print("\n[Step 0] 检查 PyInstaller ...")
try:
    import PyInstaller  # noqa: F401
except ImportError:
    print("[ERROR] 未安装 PyInstaller。请先运行：")
    print("        pip install -r requirements-dev.txt")
    sys.exit(1)
print(f"[OK] PyInstaller {PyInstaller.__version__}")
print(f"[OK] Python {sys.version.split()[0]}")

# 1. 清理旧产物
print("\n[Step 1] 清理旧的 build/dist/spec ...")
for d in ['build', 'dist', 'temp_src']:
    if os.path.exists(d):
        shutil.rmtree(d, ignore_errors=True)
for spec_file in [f"{APP_NAME}.spec", "updater.spec"]:
    if os.path.exists(spec_file):
        os.remove(spec_file)

# 2. 清理 __pycache__
print("[Step 2] 清理缓存目录 ...")
for root, dirs, _ in os.walk('.'):
    for d in list(dirs):
        if d == '__pycache__':
            shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            dirs.remove(d)

# 3. 图标准备
print("[Step 3] 检查应用图标 ...")
if not os.path.exists(ICON):
    fallback = "256x256.ico"
    if os.path.exists(fallback):
        shutil.copy(fallback, ICON)
        print(f"[OK] 已由 {fallback} 生成 {ICON}")
    else:
        print("[WARN] 未找到任何 ico 文件，将使用默认图标")

# 5. 打包主程序
print("\n[Step 4] 打包主程序（约需数分钟）...")

# 历史重依赖 / 开发工具一律排除，避免本机环境残留被间接收集。
#
# ⚠ 关于 unittest / pydoc：
#   这两个是 Python 标准库，很多第三方库会在 import 阶段触碰 unittest.mock。
#   一旦被排除，运行期会 ModuleNotFoundError 直接崩溃。**不要排除它们**。
#
# ⚠ 关于 PyQt5 / PyQtWebEngine / py5：
#   PyQt5 是 GPL v3，混入产物会让"闭源商业软件"声明失效。显式排除是双保险。
EXCLUDES = [
    # 深度学习 / 科学计算
    "torch", "torchvision", "easyocr", "scipy", "numpy", "pandas",
    # 图像 / 视频
    "cv2", "matplotlib", "skimage", "imageio", "moviepy",
    # PDF 处理
    "fitz", "pymupdf", "pypdf", "pdfplumber", "pdf2image", "poppler",
    # Office 文档
    "docx", "openpyxl",
    # 格式化 / 静态分析工具
    "black", "jsbeautifier", "autopep8", "pycodestyle",
    # HTML / XML 解析
    "bs4", "lxml", "soupsieve",
    # 中文分词（一致性检查用 pypinyin 不是 jieba）
    "jieba",
    # GUI 框架（只留 PySide6）
    "tkinter",
    # GPL 污染源（PyQt5 全家 / py5）—— 必须显式排除
    "PyQt5", "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtWidgets",
    "PyQt5.QtWebEngine", "PyQt5.QtWebEngineWidgets",
    "PyQtWebEngine", "py5",
    # 服务端框架（客户端用不到，避免本机环境残留被间接收集）
    "flask", "flask_cors", "flask_jwt_extended", "flask_sqlalchemy",
    "jinja2", "sqlalchemy", "werkzeug", "itsdangerous",
    # 测试 / 交互式环境
    "pytest", "IPython", "notebook", "jupyter",
    # 其他实验残留
    "keyboard", "mouse", "line_profiler", "stackprinter",
    "tqdm", "regex", "pytesseract",
]

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--windowed",
    "--noconfirm",
    "--name", APP_NAME,
    "--icon", ICON,
    # 宠物图片素材。PyInstaller 的 --collect-submodules 只收集 .py，
    # 不会自动带上 .png；缺这一行，打包后猫娘会 fallback 到
    # "画个圆脸"的 _paint_minimal 模式，用户看不到立绘。
    # 用 os.pathsep 兼容 Windows(;) / macOS、Linux(:)。
    "--add-data", f"ui/pet{os.pathsep}ui/pet",
    # 仅收集运行期真实需要的三方包
    "--collect-all", "qtawesome",
    "--collect-all", "cryptography",
    "--collect-all", "requests",
    "--collect-all", "urllib3",
    "--collect-all", "certifi",
    "--collect-all", "charset_normalizer",
    "--collect-all", "PIL",
    "--collect-all", "idna",
    # 一致性检查的拼音通道
    "--collect-all", "pypinyin",
    "--collect-submodules", "ui",
    "--collect-submodules", "utils",
]
for mod in EXCLUDES:
    cmd += ["--exclude-module", mod]
cmd.append("main.py")

try:
    subprocess.run(cmd, check=True)
except subprocess.CalledProcessError as e:
    print(f"\n[ERROR] 主程序打包失败: {e}")
    sys.exit(1)

# 6. 打包 updater.exe（独立更新器）
print("\n[Step 5] 打包 updater.exe ...")
if not os.path.exists("updater.py"):
    print("[WARN] 未找到 updater.py，跳过更新器打包（不生成自动更新功能）")
else:
    final_dir = os.path.join("dist", APP_NAME)
    if not os.path.isdir(final_dir):
        print("[ERROR] 主程序未成功打包，无法继续")
        sys.exit(1)

    updater_cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                              # 单文件，便于分发
        "--windowed",                             # 无黑窗
        "--noconfirm",
        "--name", "updater",
        "--distpath", final_dir,                  # 直接产出到主程序目录
        "--workpath", os.path.join("build", "updater"),
        "--specpath", os.path.join("build", "updater"),
    ]
    # updater 只用标准库，把所有项目依赖全部排除，减小体积
    UPDATER_EXCLUDES = [
        "PySide6", "shiboken6", "QtAwesome", "QtPy",
        "requests", "urllib3", "certifi", "charset_normalizer", "idna",
        "cryptography", "cffi", "pycparser",
        "PIL", "pypinyin",
        "numpy", "pandas", "scipy",
        "tkinter",
    ]
    for mod in UPDATER_EXCLUDES:
        updater_cmd += ["--exclude-module", mod]
    updater_cmd.append("updater.py")

    try:
        subprocess.run(updater_cmd, check=True)
        updater_exe = os.path.join(final_dir, "updater.exe")
        if os.path.exists(updater_exe):
            size_mb = os.path.getsize(updater_exe) / (1024 * 1024)
            print(f"[OK] updater.exe 已生成（{size_mb:.1f} MB）")
        else:
            print("[WARN] updater.exe 未生成，自动更新将不可用")
    except subprocess.CalledProcessError as e:
        print(f"[WARN] updater 打包失败: {e}")
        print("       主程序不受影响，但自动更新功能将不可用。")

# 7. 后处理
print("\n[Step 6] 后处理 ...")
final_dir = os.path.join("dist", APP_NAME)
if not os.path.isdir(final_dir):
    print("[ERROR] 未找到打包输出目录！")
    sys.exit(1)

internal_dir = os.path.join(final_dir, "_internal")

# 图标 / 许可协议放到 exe 同级目录
if os.path.exists(ICON):
    shutil.copy(ICON, os.path.join(final_dir, ICON))
if os.path.exists("LICENSE.txt"):
    shutil.copy("LICENSE.txt", os.path.join(final_dir, "LICENSE.txt"))

# 说明文档随包分发
for doc in ("README.md", "用户手册.md"):
    if os.path.exists(doc):
        shutil.copy(doc, os.path.join(final_dir, doc))

# 清理 pyc / pycache / 杂项
for root, dirs, files in os.walk(internal_dir):
    for d in list(dirs):
        if d == "__pycache__":
            shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            dirs.remove(d)
    for f in files:
        if f.endswith('.pyc') or f.startswith('.') or f == "Thumbs.db":
            try:
                os.remove(os.path.join(root, f))
            except OSError:
                pass

# 8. GPL 污染自检
print("\n[Step 7] 扫描产物是否有 GPL 污染 ...")
GPL_BAD_DIRS = [
    "PyQt5", "PyQtWebEngine", "py5",
    "PySide6/QtWebEngineWidgets",
]
GPL_BAD_FILES = [
    "PyQt5.QtCore.pyd", "PyQt5.QtGui.pyd", "PyQt5.QtWidgets.pyd",
]
contaminated = []
for bad in GPL_BAD_DIRS:
    p = os.path.join(internal_dir, bad)
    if os.path.exists(p):
        contaminated.append(p)
for bad in GPL_BAD_FILES:
    p = os.path.join(internal_dir, bad)
    if os.path.exists(p):
        contaminated.append(p)

if contaminated:
    print("[ERROR] 产物中含 GPL 污染，无法发布！命中：")
    for p in contaminated:
        print(f"        {p}")
    print("        请在 .venv 里卸载 PyQt5 / PyQtWebEngine / py5 后重新打包。")
    sys.exit(1)
print("[OK] 未检测到 GPL 污染")

# 9. 体积统计
exe_path = os.path.join(final_dir, f"{APP_NAME}.exe")
updater_path = os.path.join(final_dir, "updater.exe")
total_size = 0
for root, _, files in os.walk(final_dir):
    for f in files:
        try:
            total_size += os.path.getsize(os.path.join(root, f))
        except OSError:
            pass
total_mb = total_size / (1024 * 1024)

print("\n" + "=" * 60)
if os.path.exists(exe_path):
    print("[SUCCESS] 打包完成！")
    print(f"  输出目录 : {os.path.abspath(final_dir)}")
    print(f"  主程序   : {os.path.getsize(exe_path) / (1024 * 1024):.1f} MB")
    if os.path.exists(updater_path):
        print(f"  更新器   : {os.path.getsize(updater_path) / (1024 * 1024):.1f} MB")
    else:
        print("  更新器   : 未生成（自动更新不可用）")
    print(f"  总体积   : {total_mb:.1f} MB（目标 <= 200MB）")
    if total_mb > 200:
        print("  [WARNING] 体积超标，请检查是否有重依赖被误收集。")

    print("\n  下一步：")
    print("    1. 制作更新包 zip（重要：不要带顶层目录名！）：")
    print(f"         cd dist\\{APP_NAME}")
    print(f"         Compress-Archive -Path * -DestinationPath ..\\update.zip")
    print("       （错误示范：Compress-Archive -Path '..\\%s'，"
          "会多套一层目录）" % APP_NAME)
    print()
    print("    2. 用 Inno Setup 编译 打包.iss 生成安装包；")
    print(f"       或直接把 dist\\{APP_NAME}\\ 目录打包成 zip 分发。")
else:
    print("[ERROR] EXE 未生成！")
    sys.exit(1)
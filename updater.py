# updater.py
"""墨码写作 - 自动更新器（独立进程）

关键设计：
1. 从程序目录启动时，先自我拷贝到 %TEMP%\\moma_update\\ 再运行，
   这样原位置的 updater.exe 就能被安全覆盖。
2. 拷贝后通过环境变量 MOMA_UPDATER_RELOCATED=1 防止无限递归。
3. 主程序退出后多等 2 秒再替换，等文件句柄释放。
4. 解压失败自动重试 3 次。

用法：
    updater.exe --pid <主程序PID> --zip <更新包路径> --target <程序目录>
"""
import argparse
import os
import sys
import time
import shutil
import zipfile
import subprocess
import ctypes
from pathlib import Path


_NO_WINDOW = (
    subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
)


def log(msg: str):
    try:
        log_dir = os.path.join(os.environ.get("TEMP", "."), "moma_update")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "updater.log"), "a",
                  encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def wait_for_process_exit(pid: int, timeout: float = 30.0) -> bool:
    if not pid:
        return True
    try:
        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return True
        try:
            result = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            return result == 0
        finally:
            kernel32.CloseHandle(handle)
    except Exception as e:
        log(f"wait_for_process_exit 异常: {e}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=_NO_WINDOW)
                if str(pid) not in (r.stdout or ""):
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False


def extract_zip_once(zip_path: str, target_dir: str) -> bool:
    """解压 zip 到目标目录，跳过正在运行的 updater.exe。"""
    skip = {"updater.exe"}
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name.startswith("/") or ".." in Path(name).parts:
                    log(f"危险路径，拒绝解压: {name}")
                    return False
                base = os.path.basename(name)
                if base in skip:
                    log(f"跳过: {name}")
                    continue
                zf.extract(name, target_dir)
        return True
    except PermissionError as e:
        log(f"解压失败（权限）: {e}")
        return False
    except Exception as e:
        log(f"解压失败: {e}")
        return False


def extract_zip(zip_path: str, target_dir: str, max_retries: int = 3) -> bool:
    for i in range(max_retries):
        if extract_zip_once(zip_path, target_dir):
            return True
        if i < max_retries - 1:
            log(f"第 {i + 1} 次失败，1.5 秒后重试…")
            time.sleep(1.5)
    return False


def backup_critical_files(target_dir: str, backup_dir: str) -> bool:
    try:
        os.makedirs(backup_dir, exist_ok=True)
        for name in ("墨码写作.exe",):
            src = os.path.join(target_dir, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(backup_dir, name))
        return True
    except Exception as e:
        log(f"备份失败: {e}")
        return False


def launch_new_version(target_dir: str, exe_name: str) -> bool:
    exe_path = os.path.join(target_dir, exe_name)
    if not os.path.exists(exe_path):
        log(f"新版 exe 不存在: {exe_path}")
        return False
    try:
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            [exe_path], cwd=target_dir,
            creationflags=DETACHED_PROCESS | _NO_WINDOW,
            close_fds=True,
        )
        return True
    except Exception as e:
        log(f"启动新版失败: {e}")
        return False


def cleanup_temp(zip_path: str):
    try:
        temp_dir = os.path.dirname(zip_path)
        if os.path.basename(temp_dir) == "moma_update":
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:
        log(f"清理临时文件失败: {e}")


def self_delete():
    try:
        current = sys.executable if getattr(sys, "frozen", False) else __file__
        subprocess.Popen(
            f'cmd /c timeout /t 2 /nobreak > nul & del "{current}"',
            shell=True,
            creationflags=0x08000000 | _NO_WINDOW,
        )
    except Exception:
        pass


def relocate_self(args) -> bool:
    """如果自己就在目标目录里，拷贝到临时目录重新启动。"""
    if os.environ.get("MOMA_UPDATER_RELOCATED") == "1":
        return False
    if not getattr(sys, "frozen", False):
        return False

    current_exe = sys.executable
    current_dir = os.path.normpath(os.path.dirname(current_exe)).lower()
    target_dir = os.path.normpath(args.target).lower()

    if current_dir != target_dir:
        return False

    temp_dir = os.path.join(os.environ.get("TEMP", "."), "moma_update")
    os.makedirs(temp_dir, exist_ok=True)
    temp_exe = os.path.join(temp_dir, "updater.exe")

    try:
        if os.path.exists(temp_exe):
            try:
                os.remove(temp_exe)
            except OSError:
                pass
        shutil.copy2(current_exe, temp_exe)
        log(f"自我拷贝到: {temp_exe}")
    except Exception as e:
        log(f"自我拷贝失败: {e}")
        return False

    env = os.environ.copy()
    env["MOMA_UPDATER_RELOCATED"] = "1"
    DETACHED_PROCESS = 0x00000008
    try:
        subprocess.Popen(
            [temp_exe,
             "--pid", str(args.pid),
             "--zip", args.zip,
             "--target", args.target,
             "--exe", args.exe],
            env=env,
            creationflags=DETACHED_PROCESS | _NO_WINDOW,
            close_fds=True,
        )
        log("已从临时副本重新启动")
        return True
    except Exception as e:
        log(f"启动临时副本失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--zip", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--exe", default="墨码写作.exe")
    args = parser.parse_args()

    log("=" * 50)
    log(f"updater 启动: pid={args.pid} zip={args.zip} target={args.target}")

    if relocate_self(args):
        return 0

    if not wait_for_process_exit(args.pid, timeout=30.0):
        log("等待主程序退出超时")
        return 1
    log("主程序已退出")

    time.sleep(2.0)

    backup_dir = os.path.join(os.environ.get("TEMP", "."),
                              "moma_update_backup")
    backup_critical_files(args.target, backup_dir)
    log(f"备份完成: {backup_dir}")

    if not extract_zip(args.zip, args.target):
        log("解压失败（已重试），尝试回滚")
        for name in ("墨码写作.exe",):
            src = os.path.join(backup_dir, name)
            if os.path.exists(src):
                try:
                    shutil.copy2(src, os.path.join(args.target, name))
                except Exception:
                    pass
        launch_new_version(args.target, args.exe)
        return 2
    log("解压完成")

    if not launch_new_version(args.target, args.exe):
        log("启动新版失败")
        return 3
    log("新版已启动")

    time.sleep(1.5)
    cleanup_temp(args.zip)
    self_delete()
    return 0


if __name__ == "__main__":
    sys.exit(main())
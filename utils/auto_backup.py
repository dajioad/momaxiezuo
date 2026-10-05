# utils/auto_backup.py
"""墨码写作 - 自动备份（纯逻辑 + 后台线程，无对话框依赖）。

与手动「备份所有作品」同一份打包清单，但：
- 落盘到数据目录下的 auto_backups/，不弹文件对话框；
- 按「每天 / 每周」频率到期才跑；
- 自动清理，只保留最近 N 份；
- 在 QThread 里压缩，几百 MB 的书也不卡 UI。

备份内容刻意不含 ai.dat / logs：
API Key 密钥文件绑定本机硬件指纹，带走也无法解密。
"""
from __future__ import annotations

import os
import zipfile
from datetime import date, datetime
from typing import List, Tuple

from PySide6.QtCore import QThread, Signal

from .logger import get_logger

logger = get_logger()

# 与 MainWindow._backup_all 的清单保持一致
BACKUP_ITEMS = [
    "works",
    "versions",
    "stats.json",
    "proof_dict.json",
    "ai_presets.json",
    "publish_targets.json",
]

INTERVAL_DAILY = "每天"
INTERVAL_WEEKLY = "每周"


def backup_dir_for(data_root: str) -> str:
    return os.path.join(data_root, "auto_backups")


def backup_due(last_date: str, interval: str) -> bool:
    """今天是否该备份。last_date 为 'YYYY-MM-DD' 或空串。"""
    today = date.today()
    if not last_date:
        return True
    try:
        last = datetime.strptime(last_date.strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return True
    gap = (today - last).days
    if interval == INTERVAL_WEEKLY:
        return gap >= 7
    return gap >= 1   # 默认按每天处理


class BackupCancelled(Exception):
    """取消标志置位时中断打包（退出程序场景）。"""


def create_backup_zip(data_root: str, dest_dir: str = None,
                      now: datetime = None, cancel_check=None) -> Tuple[str, int]:
    """同步打包（在线程里调用）。返回 (zip 路径, 字节数)。

    没有任何可备份内容时返回 ("", 0)。
    cancel_check: 可选回调，返回 True 时立即中止并清理临时包。
    先写 .tmp 再原子改名，避免崩溃/被杀线程留下半截 zip
    （半截包会被 prune 计入保留份数，最坏挤掉唯一的好备份）。
    """
    now = now or datetime.now()
    dest_dir = dest_dir or backup_dir_for(data_root)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(
        dest_dir, f"自动备份_{now.strftime('%Y%m%d_%H%M%S')}.zip")
    tmp_path = path + ".tmp"

    wrote_any = False
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in BACKUP_ITEMS:
                if cancel_check is not None and cancel_check():
                    raise BackupCancelled()
                full = os.path.join(data_root, name)
                if os.path.isdir(full):
                    for root, _dirs, files in os.walk(full):
                        for f in files:
                            if cancel_check is not None and cancel_check():
                                raise BackupCancelled()
                            fp = os.path.join(root, f)
                            arc = os.path.relpath(fp, data_root)
                            try:
                                zf.write(fp, arc)
                                wrote_any = True
                            except OSError as e:
                                logger.warning(f"自动备份打包失败 {fp}: {e}")
                elif os.path.isfile(full):
                    try:
                        zf.write(full, name)
                        wrote_any = True
                    except OSError as e:
                        logger.warning(f"自动备份打包失败 {full}: {e}")

        if not wrote_any:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return "", 0

        os.replace(tmp_path, path)
        return path, os.path.getsize(path)
    except BackupCancelled:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        logger.info("自动备份已取消（程序退出中）")
        return "", 0
    except Exception:
        # 任何异常都不留半截临时包
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def prune_backups(dest_dir: str, keep: int) -> List[str]:
    """只保留最近 keep 份自动备份 zip，返回被删除的路径列表。"""
    if keep < 1:
        keep = 1
    removed: List[str] = []
    if not os.path.isdir(dest_dir):
        return removed
    zips = []
    for fn in os.listdir(dest_dir):
        if fn.startswith("自动备份_") and fn.lower().endswith(".zip"):
            fp = os.path.join(dest_dir, fn)
            if not os.path.isfile(fp):
                continue
            # 先剔除损坏包（旧版本非原子写 + 退出杀线程可能留下半截
            # zip）；否则坏包会被计入 keep 份数，keep=1 时唯一保留的
            # 可能就是打不开的那份。
            try:
                with zipfile.ZipFile(fp, "r") as zf:
                    if zf.testzip() is not None:
                        raise zipfile.BadZipFile("CRC 校验失败")
            except (zipfile.BadZipFile, OSError) as e:
                logger.warning(f"发现损坏的自动备份，已删除: {fn} ({e})")
                try:
                    os.remove(fp)
                    removed.append(fp)
                except OSError:
                    pass
                continue
            # 以文件名时间戳为主序：同一秒内生成的多个备份 mtime 相同，
            # 只按 mtime 排序会让删除顺序不确定；YYYYMMDD_HHMMSS 文件名
            # 天然按时间字典序排列。
            zips.append((fp, os.path.getmtime(fp), fn))
    zips.sort(key=lambda x: (x[2], x[1]))   # 旧 → 新
    for fp, _m, _fn in zips[:max(0, len(zips) - keep)]:
        try:
            os.remove(fp)
            removed.append(fp)
        except OSError as e:
            logger.warning(f"清理旧自动备份失败 {fp}: {e}")
    return removed


class AutoBackupWorker(QThread):
    """后台打 zip。finished_ok(路径, 字节数)；无内容时路径为空串。"""

    finished_ok = Signal(str, int)
    failed = Signal(str)

    def __init__(self, data_root: str, parent=None):
        super().__init__(parent)
        self._data_root = data_root
        self._stop = False

    def stop(self):
        """请求取消：run 内每个文件写入前检查，尽快结束。"""
        self._stop = True

    def run(self):
        try:
            path, size = create_backup_zip(
                self._data_root, cancel_check=lambda: self._stop)
            # 取消（无产物）时不发 finished_ok，避免界面误报"备份成功"
            if not self._stop or path:
                self.finished_ok.emit(path, size)
        except Exception as e:
            logger.error(f"自动备份失败: {e}", exc_info=True)
            self.failed.emit(str(e))

# utils/update_worker.py
"""墨码写作 - 更新检查工作线程（避免阻塞 UI）"""
from PySide6.QtCore import QThread, Signal

from utils.updater import check_for_update
from utils.logger import get_logger

logger = get_logger()


class UpdateCheckWorker(QThread):
    """检查更新，完成后发 finished_ok(dict)。

    无更新时发空 dict（信号参数不能是 None，用 {} 表示"无事发生"）；
    网络/解析失败发 failed(str)，由调用方区分提示（不再把失败误报成
    "已是最新版本"）。
    """
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UpdateCheckWorker")
        self._stop = False

    def stop(self):
        """请求尽快退出。requests 阻塞中无法立刻中断，
        stop 后 wait 会超时，调用方需要容忍。"""
        self._stop = True

    def run(self):
        if self._stop:
            self.finished_ok.emit({})
            return
        try:
            info = check_for_update() or {}
        except Exception as e:
            logger.warning(f"更新检查异常: {e}")
            if not self._stop:
                self.failed.emit(str(e))
            return
        if self._stop:
            return
        self.finished_ok.emit(info)
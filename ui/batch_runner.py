# ui/batch_runner.py
"""墨码写作 - 多章节批量 AI 任务顺序执行器（墨灵试读 / 批量关系提取共用）。

- 任务按顺序一章一章跑，单章失败不影响后续章节；
- 确定性进度条（第 i/n 章），可随时取消（协作式，取消当前请求后停止）；
- worker 挂在 owner 上，避免 QThread 运行中被 GC 导致进程 abort；
- 模态 exec()，结束后从 results 取 [(ok, text_or_error), ...]。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple, Union

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
)
from PySide6.QtCore import Qt, QTimer

from utils.ai_client import AIWorker, load_ai_config
from ui.theme_style import apply_dialog_theme


@dataclass
class BatchTask:
    label: str
    # 直接给消息列表，或给一个无参 callable（可依赖前几章结果动态构造）
    messages: Union[List[dict], Callable[[], List[dict]]]
    temperature: float = 0.3
    timeout: float = 120.0


class BatchAIProgress(QDialog):
    def __init__(self, tasks: List[BatchTask], parent=None,
                 owner=None, title: str = "批量处理",
                 hint: str = "逐章处理中，可随时取消"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self._tasks = list(tasks or [])
        self._owner = owner if owner is not None else (parent or self)
        self.results: List[Tuple[bool, str]] = []
        self.cancelled = False

        self._index = 0
        self._worker: Optional[AIWorker] = None
        self._cfg = load_ai_config()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 14)
        lay.setSpacing(10)

        self._title = QLabel(title)
        lay.addWidget(self._title)

        self._status = QLabel("准备中…")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, max(1, len(self._tasks)))
        self._bar.setValue(0)
        self._bar.setFixedHeight(10)
        self._bar.setTextVisible(True)
        lay.addWidget(self._bar)

        hint_lab = QLabel(hint)
        hint_lab.setWordWrap(True)
        hint_lab.setObjectName("batchHint")
        lay.addWidget(hint_lab)

        row = QHBoxLayout()
        row.addStretch()
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self._cancel_btn)
        lay.addLayout(row)

        apply_dialog_theme(self)
        try:
            muted = self.palette().placeholderText().color().name()
        except Exception:
            muted = "#928a7c"
        hint_lab.setStyleSheet(
            f"#batchHint {{ color: {muted}; font-size: 12px; }}")

        # showEvent 后再启动，保证对话框先画出来
        self._started = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._started:
            self._started = True
            QTimer.singleShot(0, self._advance)

    # ---------- 状态机 ----------

    def _advance(self):
        if self.cancelled:
            self.reject()
            return
        if self._index >= len(self._tasks):
            self._bar.setValue(len(self._tasks))
            self.accept()
            return

        task = self._tasks[self._index]
        self._status.setText(
            f"正在处理第 {self._index + 1}/{len(self._tasks)} 项：{task.label}")
        self._bar.setValue(self._index)

        try:
            messages = (task.messages() if callable(task.messages)
                        else task.messages)
        except Exception as e:
            self.results.append((False, f"构造请求失败：{e}"))
            self._index += 1
            QTimer.singleShot(0, self._advance)
            return

        worker = AIWorker(
            messages,
            self._cfg["api_key"],
            self._cfg["base_url"],
            self._cfg["model"],
            temperature=task.temperature,
            stream=True,
            timeout=task.timeout,
        )
        self._worker = worker
        try:
            setattr(self._owner, "_batch_ai_worker", worker)
        except Exception:
            pass
        worker.finished_ok.connect(self._on_ok)
        worker.failed.connect(self._on_fail)
        worker.start()

    def _retire_worker(self):
        w = self._worker
        if w is None:
            return
        for sig, slot in ((w.finished_ok, self._on_ok),
                          (w.failed, self._on_fail)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:
            w.deleteLater()
        except RuntimeError:
            pass
        try:
            if getattr(self._owner, "_batch_ai_worker", None) is w:
                setattr(self._owner, "_batch_ai_worker", None)
        except Exception:
            pass
        self._worker = None

    def _on_ok(self, text: str):
        self.results.append((True, text or ""))
        self._retire_worker()
        self._index += 1
        QTimer.singleShot(0, self._advance)

    def _on_fail(self, msg: str):
        # 单章失败：记录后继续后续章节，不整批中断
        self.results.append((False, msg or "请求失败"))
        self._retire_worker()
        self._index += 1
        QTimer.singleShot(0, self._advance)

    def _on_cancel(self):
        self.cancelled = True
        self._status.setText("正在取消…")
        w = self._worker
        if w is not None:
            try:
                w.stop()
            except RuntimeError:
                pass
        # 等当前 worker 回调里走 reject；兜底 2 秒
        QTimer.singleShot(2000, self._ensure_closed)

    def _detach_running_worker(self):
        """对话框关闭前把在途 worker 断信号、摘 owner 引用并交给 Reaper。

        覆盖取消按钮 2 秒兜底、Esc、标题栏 X 三条路径，避免：
        1) 对话框已关但状态机继续向后台发起下一章请求；
        2) QThread 运行中析构导致退出崩溃。
        """
        w = self._worker
        if w is None:
            return
        for sig, slot in ((w.finished_ok, self._on_ok),
                          (w.failed, self._on_fail)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:
            if getattr(self._owner, "_batch_ai_worker", None) is w:
                setattr(self._owner, "_batch_ai_worker", None)
        except Exception:
            pass
        try:
            from utils.ai_client import retire_worker
            retire_worker(w)
        except Exception:
            pass
        self._worker = None

    def reject(self):
        # Esc / 标题栏关闭 / 取消兜底统一收口
        self.cancelled = True
        self._detach_running_worker()
        super().reject()

    def _ensure_closed(self):
        if self.isVisible():
            self.reject()

    def success_count(self) -> int:
        return sum(1 for ok, _ in self.results if ok)

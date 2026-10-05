# ui/batch_progress.py
"""墨码写作 - 批量处理进度与取消（对话框复用）

给需要「逐章/逐项处理」的对话框提供统一的：
- 内嵌进度条（不是模态 QProgressDialog，不挡界面）
- 取消按钮
- 分块执行：每处理 N 项让出一次事件循环，界面不冻结

设计取舍：
- **不用 QThread**：Work 不是线程安全的（写 book.json、改 meta
  字典），挪到子线程会引入数据竞争。分块执行在保持单线程语义
  的前提下同样能让 UI 保持可响应——单块控制在十几项内，
  耗时通常 <100ms。
- **取消语义交给调用方**：预览可以整体丢弃；应用则保留已写入的
  章节（每章都已单独打快照，可恢复）。混入类只负责停。

用法：
    class MyDialog(BatchProgressMixin, QDialog):
        def _build_ui(self):
            ...
            self._build_progress_row(root)   # 放在按钮行之前

        def _do_apply(self):
            items = [...]
            self._start_batch(items, self._apply_one, "应用", self._apply_done)

    调用方提供两个回调：
        step(item)              处理单项，抛异常即中止整批
        done(cancelled, error)  收尾；cancelled=True 表示用户中途取消
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)


class BatchProgressMixin:
    """分块执行 + 进度条 + 取消。"""

    # 每块处理多少项。太大 UI 会顿，太小调度开销高。
    # 子类可覆写：预览是纯内存变换可以大些，写盘（含 fsync）小些更跟手。
    _batch_chunk = 10

    # ---------- 构建 ----------

    def _build_progress_row(self, root_layout):
        """在 root_layout 末尾追加一行（进度文字 + 条 + 取消），默认隐藏。"""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("batchProgressLabel")
        lay.addWidget(self.progress_label, 0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setRange(0, 100)
        lay.addWidget(self.progress_bar, 1)

        self.cancel_batch_btn = QPushButton("取消")
        self.cancel_batch_btn.setObjectName("batchCancelBtn")
        self.cancel_batch_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_batch_btn.clicked.connect(self._on_cancel_batch)
        lay.addWidget(self.cancel_batch_btn, 0)

        row.setVisible(False)
        root_layout.addWidget(row)

        self._batch_row = row
        self._batch_gen = 0  # 代际号：旧块的回调靠它失效
        self._batch_cancelled = False
        return row

    # ---------- 生命周期 ----------

    def _abort_batch(self):
        """关闭对话框时调用：让当前批次立即失效。"""
        self._batch_gen += 1
        self._batch_cancelled = True
        self._batch_hide()

    def _invalidate_batch(self):
        """静默作废当前批次（不触发 on_done）。

        场景：参数/范围改变，正在跑的预览已经过时——直接丢弃，
        不要让它的收尾逻辑去改 stats_label。
        """
        self._batch_gen += 1
        self._batch_hide()

    def accept(self):
        self._abort_batch()
        super().accept()

    def reject(self):
        self._abort_batch()
        super().reject()

    # ---------- 内部 ----------

    def _on_cancel_batch(self):
        self._batch_cancelled = True
        try:
            self.cancel_batch_btn.setEnabled(False)
            self.progress_label.setText("正在取消…")
        except RuntimeError:
            pass

    def _batch_show(self, text, total):
        try:
            self.progress_label.setText(text)
            self.progress_bar.setMaximum(max(1, total))
            self.progress_bar.setValue(0)
            self.cancel_batch_btn.setEnabled(True)
            self._batch_row.setVisible(True)
        except RuntimeError:
            pass

    def _batch_hide(self):
        try:
            self._batch_row.setVisible(False)
        except RuntimeError:
            pass

    # ---------- 执行 ----------

    def _start_batch(self, items, step, verb, on_done, chunk=None):
        """分块执行 items。

        items    待处理项（list，顺序即处理顺序）
        step     处理单项的回调；抛异常即中止整批
        verb     进度文案动词，如 "预览" / "应用" / "替换"
        on_done  on_done(cancelled: bool, error: Exception | None)
        chunk    每块项数，默认取 self._batch_chunk
        """
        chunk = max(1, int(chunk or self._batch_chunk))
        n = len(items)
        if n == 0:
            QTimer.singleShot(0, lambda: on_done(False, None))
            return

        self._batch_gen += 1
        gen = self._batch_gen
        self._batch_cancelled = False
        self._batch_show(f"正在{verb} 0/{n} …", n)

        state = {"i": 0}

        def run():
            if gen != self._batch_gen:
                return  # 已被新批次或关闭取代
            i = state["i"]
            end = min(i + chunk, n)
            try:
                while i < end:
                    if self._batch_cancelled:
                        self._batch_hide()
                        on_done(True, None)
                        return
                    step(items[i])
                    i += 1
                    state["i"] = i
            except Exception as e:  # noqa: BLE001 — 兜底交回调用方
                from utils.logger import get_logger

                get_logger().error(f"{verb}失败: {e}", exc_info=True)
                self._batch_hide()
                on_done(False, e)
                return
            try:
                self.progress_bar.setValue(i)
                self.progress_label.setText(f"正在{verb} {i}/{n} …")
            except RuntimeError:
                return
            if i < n:
                QTimer.singleShot(0, run)
            else:
                self._batch_hide()
                on_done(False, None)

        QTimer.singleShot(0, run)

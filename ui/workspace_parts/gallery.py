# ui/workspace_parts/gallery.py
"""墨码写作 - 画廊子系统（Mixin）

从 workspace.py 拆出的画廊卡片、拖拽排序、作品操作、导出相关逻辑。
按 Mixin 方式合并到 WritingWorkspace，所有方法直接访问 self.*。
"""

import os
import json
import hashlib
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QHBoxLayout,
    QMenu,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QPlainTextEdit,
    QDialog,
    QGraphicsDropShadowEffect,
    QApplication,
)
from PySide6.QtCore import (
    Qt,
    QTimer,
    QSize,
    QEvent,
    QRect,
    QRectF,
    QPoint,
    QPropertyAnimation,
    QEasingCurve,
    QAbstractAnimation,
)
from PySide6.QtGui import (
    QPixmap,
    QPainter,
    QPainterPath,
    QColor,
    QLinearGradient,
    QFont,
    QCursor,
    QPalette,
    QShortcut,
    QKeySequence,
)
import qtawesome as qta

from ui.theme_style import qss_alpha
from ui.constants import modern_scrollbar_qss
from ui.workspace_parts.export_options_dialog import _ExportOptionsDialog
from utils.work_store import Work, CHAPTER_STATUS_DONE
from utils.logger import get_logger

logger = get_logger()


# ============================================================
# 封面 / 悬停卡片（模块级 widget）
# ============================================================


# ▼▼▼ 第二步粘贴位置 ▼▼▼
class _CoverLabel(QLabel):
    """网文封面（3:4 竖图）。全自绘。"""

    def __init__(self, cover_path: str, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("workCover")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(150)
        self.setAutoFillBackground(False)
        self._title = title or ""
        # 是否按宽度自动撑到 4:3 高度。hero 卡片默认 True（配合固定尺寸）；
        # 网格卡片应关掉——高度由 _measure_card_size 统一算，否则
        # setFixedHeight 会把卡片撑到失控。
        self._auto_height = True
        pix = (
            QPixmap(cover_path)
            if cover_path and os.path.exists(cover_path)
            else QPixmap()
        )
        self._src = None if pix.isNull() else pix
        # 状态标记 key（ongoing / completed / paused / planning），
        # 空串表示不绘制
        self._work_status = ""
        # 缩放结果单槽缓存：画廊滚动时每个封面每次重绘都做一次
        # SmoothTransformation 全图缩放（几百 ms 级卡顿来源）。
        # key=(源图 cacheKey, 目标宽, 目标高)，尺寸不变即零缩放。
        self._cover_cache_key = None
        self._cover_cache_pix = None

    def set_work_status(self, status_key: str):
        """设置封面上的状态标记。"" 表示不绘制。"""
        self._work_status = status_key or ""
        self.update()

    # 状态 → (文字, 颜色)
    _STATUS_META = {
        "ongoing": ("连载中", (76, 175, 80)),
        "completed": ("已完结", (91, 127, 229)),
        "paused": ("暂停更新", (230, 162, 60)),
        "planning": ("构思中", (146, 138, 124)),
    }

    def _paint_status_marker(self, p, rect):
        """在封面上绘制状态标记：右上角彩点 + 左下角文字标签。"""
        meta = self._STATUS_META.get(self._work_status)
        if meta is None:
            return
        text, (r, g, b) = meta
        from PySide6.QtGui import QColor as _QC

        color = _QC(r, g, b)

        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        # ---- 右上角圆点 ----
        dot_size = 10
        dot_x = rect.width() - dot_size - 12
        dot_y = 12
        p.setPen(Qt.NoPen)
        p.setBrush(_QC(255, 255, 255, 240))
        p.drawEllipse(dot_x - 3, dot_y - 3, dot_size + 6, dot_size + 6)
        p.setBrush(color)
        p.drawEllipse(dot_x, dot_y, dot_size, dot_size)

        # ---- 左下角文字标签 ----
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        p.setFont(font)
        fm = p.fontMetrics()
        text_w = fm.horizontalAdvance(text)
        pad_h = 9
        pad_v = 4
        label_w = text_w + pad_h * 2
        label_h = fm.height() + pad_v * 2
        label_x = 10
        label_y = rect.height() - label_h - 10

        p.setBrush(_QC(255, 255, 255, 240))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(label_x, label_y, label_w, label_h, label_h / 2, label_h / 2)

        p.setPen(color.darker(140))
        p.drawText(
            QRectF(label_x, label_y, label_w, label_h),
            Qt.AlignCenter,
            text,
        )
        p.restore()

    def set_auto_height(self, enabled: bool):
        self._auto_height = bool(enabled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._auto_height:
            w = max(60, self.width())
            h = int(w * 4 / 3 + 0.5)
            if self.height() != h:
                self.setFixedHeight(h)
        self.update()

    def paintEvent(self, event):
        rect = self.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        radius = min(10.0, rect.width() / 18.0)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        p.setClipPath(path)
        if self._src is not None:
            dpr = self.devicePixelRatioF() or 1.0
            tw = max(1, int(rect.width() * dpr + 0.5))
            th = max(1, int(rect.height() * dpr + 0.5))
            cache_key = (self._src.cacheKey(), tw, th)
            if self._cover_cache_key == cache_key and \
                    self._cover_cache_pix is not None:
                scaled = self._cover_cache_pix
            else:
                scaled = self._src.scaled(
                    tw, th,
                    Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                )
                if scaled.width() > tw or scaled.height() > th:
                    sx = max(0, (scaled.width() - tw) // 2)
                    sy = max(0, (scaled.height() - th) // 2)
                    scaled = scaled.copy(sx, sy, tw, th)
                self._cover_cache_key = cache_key
                self._cover_cache_pix = scaled
            p.drawPixmap(rect, scaled)
        else:
            self._paint_fallback(p, rect)

        # 状态标记
        if self._work_status:
            self._paint_status_marker(p, rect)

        p.end()

    def _paint_fallback(self, painter: QPainter, rect):
        c1, c2 = GalleryMixin._cover_gradient_colors(self._title)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(c1))
        grad.setColorAt(1.0, QColor(c2))
        painter.fillRect(rect, grad)
        text = self._title[0] if self._title else "？"
        size = max(26, int(rect.width() * 0.42))
        f = QFont(self.font())
        f.setPixelSize(size)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(rect, Qt.AlignCenter, text)


class _CoverSlot(QFrame):
    """新建占位卡的封面区。

    高度完全由父 layout 分配（v.addWidget(cover_box, 1) 给了 stretch=1）。
    不再用 resizeEvent 主动 setMinimumHeight——那会和 layout 分配冲突，
    导致卡片高度溢出。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("newWorkCoverBox")


class _HoverCard(QFrame):
    """画廊卡片：鼠标悬浮时阴影加深、视觉上浮。"""

    def __init__(self, object_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self._hover_shadow = QGraphicsDropShadowEffect(self)
        self._hover_shadow.setBlurRadius(0)
        self._hover_shadow.setOffset(0, 2)
        self._hover_shadow.setColor(QColor(0, 0, 0, 0))
        self.setGraphicsEffect(self._hover_shadow)
        self._anim_blur = QPropertyAnimation(self._hover_shadow, b"blurRadius", self)
        self._anim_blur.setDuration(160)
        self._anim_blur.setEasingCurve(QEasingCurve.OutCubic)
        self._anim_y = QPropertyAnimation(self._hover_shadow, b"yOffset", self)
        self._anim_y.setDuration(160)
        self._anim_y.setEasingCurve(QEasingCurve.OutCubic)

    def _animate_shadow(self, hover: bool):
        # 拖动中：阴影由拖拽逻辑接管，hover 动画不插手
        if getattr(self, "_drag_active", False):
            return
        try:
            self._hover_shadow.setColor(QColor(0, 0, 0, 80 if hover else 0))
        except RuntimeError:
            # effect 的 C++ 对象已被销毁（例如被 setGraphicsEffect 替换过），
            # 直接放弃动画，不让异常冒泡到 QApplication.notify
            return
        self._anim_blur.stop()
        self._anim_y.stop()
        self._anim_blur.setEndValue(26 if hover else 0)
        self._anim_y.setEndValue(10 if hover else 2)
        self._anim_blur.start()
        self._anim_y.start()

    def enterEvent(self, event):
        self._animate_shadow(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_shadow(False)
        super().leaveEvent(event)


# ============================================================
# 画廊 Mixin
# ============================================================


class GalleryMixin:
    """画廊子系统。合并到 WritingWorkspace，所有方法直接访问 self.*"""

    # ▼▼▼ 第三步粘贴位置 ▼▼▼
    def _measure_card_size(self, card_w: int):
        """给定网格卡片宽度，算卡片应有的 (宽, 高)。

        新版卡片布局：封面（3:4）+ 信息区两行（书名 / 章数·字数）。
        """
        # 封面比例：3 宽 : 4 高
        cover_h = int(card_w * 4 / 3 + 0.5)
        # 信息区：上下边距 14 + 书名 22 + 3 + 副标 18 + 底边距 14 = 71
        info_h = 71
        card_h = cover_h + info_h
        return card_w, card_h

    def _layout_gallery_cards(self, animate: bool = False):
        """两层布局：
        - 第一层：hero 卡片（若存在），横跨整个宽度
        - 第二层：网格卡片。列数按宽度 + 卡片数双重约束，整体水平居中

        列数算法（关键改动）：
          1) 按宽度算能放几列（IDEAL 200px）
          2) 若卡片数 < 宽度能放的列数 → 用卡片数当列数
             （卡片少时居中显示，不强行拉宽填满）
          3) 卡片宽度上限 220px
        例：3 张卡 + 宽 1200 → 3 列居中；8 张卡 + 宽 1200 → 5 列紧凑填满
        """
        if not self._gallery_cards:
            return

        vw = max(0, self._gallery_scroll.viewport().width())
        gap = self._gallery_gap

        old_rects = {}
        if animate:
            for c in self._gallery_cards:
                try:
                    old_rects[c] = c.geometry()
                except RuntimeError:
                    pass

        # ---- 找 hero 卡片 ----
        hero_card = None
        for c in self._gallery_cards:
            if getattr(c, "_card_kind", None) == "hero":
                hero_card = c
                break

        # ---- 列数计算 ----
        IDEAL_CARD_W = 200
        MAX_CARD_W = 220
        MAX_COLS = 8

        # 会走网格布局的卡片数（不含 hero 和 empty）
        grid_count = sum(
            1
            for c in self._gallery_cards
            if c is not hero_card and getattr(c, "_card_kind", "grid") != "empty"
        )

        # 按宽度能放几列
        max_by_width = max(1, (vw + gap) // (IDEAL_CARD_W + gap))
        max_by_width = min(max_by_width, MAX_COLS)

        # 列数 = min(宽度上限, 卡片数)
        if grid_count > 0:
            cols = min(max_by_width, grid_count)
        else:
            cols = max_by_width
        cols = max(1, cols)

        # 卡片宽度：按列数分配，超 MAX_CARD_W 就封顶
        if vw > 0:
            grid_card_w = (vw - (cols - 1) * gap) // cols
        else:
            grid_card_w = IDEAL_CARD_W
        if grid_card_w > MAX_CARD_W:
            grid_card_w = MAX_CARD_W
        grid_card_w, grid_card_h = self._measure_card_size(grid_card_w)

        # 左对齐：不做水平居中偏移
        x_offset = 0

        # ---- 第一层：hero ----
        y_cursor = 0
        if hero_card is not None:
            hero_rect = QRect(0, 0, vw, self._hero_height)
            self._animate_or_set(hero_card, hero_rect, old_rects, animate)
            y_cursor = hero_rect.bottom() + 1 + self._hero_gap

        # ---- 第二层：网格 ----
        grid_index = 0
        for c in self._gallery_cards:
            if c is hero_card:
                continue
            kind = getattr(c, "_card_kind", "grid")

            if kind == "empty":
                try:
                    w = c.width() or 480
                    h = c.height() or 380
                    x = max(0, (vw - w) // 2)
                    self._animate_or_set(
                        c, QRect(x, y_cursor + 40, w, h), old_rects, animate
                    )
                except RuntimeError:
                    pass
                continue

            row = grid_index // cols
            col = grid_index % cols
            x = x_offset + col * (grid_card_w + gap)
            y = y_cursor + row * (grid_card_h + gap)
            target = QRect(x, y, grid_card_w, grid_card_h)
            self._animate_or_set(c, target, old_rects, animate)
            grid_index += 1

        # ---- 容器高度 ----
        grid_rows = (grid_count + cols - 1) // cols if grid_count else 0
        grid_total_h = grid_rows * (grid_card_h + gap) if grid_rows else 0
        needed_h = y_cursor + grid_total_h
        # empty 场景：empty 卡片自身有固定高度
        for c in self._gallery_cards:
            if getattr(c, "_card_kind", None) == "empty":
                needed_h = max(needed_h, y_cursor + 40 + (c.height() or 380))
        self._gallery_container.setMinimumHeight(needed_h)
        self._gallery_container.setMinimumWidth(vw)

    def _animate_or_set(self, card, target_rect, old_rects, animate):
        """把卡片移到目标位置：animate=True 时平滑过渡，否则直接 setGeometry。"""
        try:
            old_anim = getattr(card, "_geom_anim", None)
            if old_anim is not None:
                try:
                    old_anim.stop()
                except RuntimeError:
                    pass
                card._geom_anim = None

            if animate and card in old_rects and old_rects[card] != target_rect:
                anim = QPropertyAnimation(card, b"geometry", card)
                anim.setDuration(220)
                anim.setStartValue(old_rects[card])
                anim.setEndValue(target_rect)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                anim.start(QAbstractAnimation.DeleteWhenStopped)
                card._geom_anim = anim
            else:
                card.setGeometry(target_rect)
        except RuntimeError:
            pass

    def _clear_gallery_cards(self):
        """销毁所有卡片（切换排序/搜索/数据变化时用）。"""
        for c in self._gallery_cards:
            try:
                c.setParent(None)
                c.deleteLater()
            except RuntimeError:
                pass
        self._gallery_cards = []

    def _refresh_gallery(self, *, preserve_scroll: bool = False):
        sb = self._gallery_scroll.verticalScrollBar()
        saved_y = sb.value()
        self._clear_gallery_cards()

        all_works = self.store.list_works()
        works = self._filtered_works()

        # ---- 更新顶部身份区统计 ----
        n_works = len(all_works)
        total_words = sum(int(w.get("words", 0)) for w in all_works)
        if n_works == 0:
            self.gallery_stats_label.setText("还没有作品")
        else:
            self.gallery_stats_label.setText(
                f"{n_works} 部作品 · 合计 {total_words:,} 字"
            )

        # ---- 空状态 ----
        if not all_works:
            empty = self._make_empty_card()
            empty.setParent(self._gallery_container)
            empty.show()
            self._gallery_cards.append(empty)
            self._layout_gallery_cards(animate=False)
            if preserve_scroll:
                QTimer.singleShot(0, lambda: sb.setValue(min(saved_y, sb.maximum())))
            else:
                QTimer.singleShot(0, lambda: sb.setValue(0))
            return

        if not works:
            # 搜索无结果
            miss = QLabel("没有匹配的作品")
            miss.setAlignment(Qt.AlignCenter)
            miss.setObjectName("galleryEmptyTip")
            miss._card_kind = "empty"
            miss.setFixedSize(480, 120)
            self._gallery_cards.append(miss)
            miss.setParent(self._gallery_container)
            miss.show()
            self._layout_gallery_cards(animate=False)
            QTimer.singleShot(0, lambda: sb.setValue(min(saved_y, sb.maximum())))
            return

        # ---- 决定 hero 卡片：不搜索时显示；搜索时隐藏 ----
        hero_info = None
        grid_works = list(works)

        keyword_active = bool((self._gallery_keyword or "").strip())
        if not keyword_active and len(works) >= 1:
            # 优先用"最近打开的作品"（配置记录）；没有就用列表第一个
            recent_path = self._recent_work_path()
            hero_info = None
            if recent_path:
                for w in works:
                    if os.path.normcase(w.get("path", "")) == os.path.normcase(
                        recent_path
                    ):
                        hero_info = w
                        break
            if hero_info is None:
                hero_info = works[0]
            # 从网格列表里移除 hero 那一本
            grid_works = [w for w in works if w is not hero_info]

        # ---- 渲染 hero ----
        if hero_info is not None:
            hero = self._make_hero_card(hero_info)
            hero.setParent(self._gallery_container)
            hero.show()
            self._gallery_cards.append(hero)

        # ---- 渲染网格 ----
        for w_info in grid_works:
            card = self._make_grid_card(w_info)
            card.setParent(self._gallery_container)
            card.show()
            self._gallery_cards.append(card)

        # 新建卡（网格区最后一张）
        new_card = self._make_new_card()
        new_card.setParent(self._gallery_container)
        new_card.show()
        self._gallery_cards.append(new_card)

        self._layout_gallery_cards(animate=False)

        if preserve_scroll:
            QTimer.singleShot(0, lambda: sb.setValue(min(saved_y, sb.maximum())))
        else:
            QTimer.singleShot(0, lambda: sb.setValue(0))

    def _recent_work_path(self) -> str:
        """读取"最近打开的作品"路径（持久化在 config）。"""
        if not self.cfg:
            return ""
        try:
            return self.cfg.get_config("Gallery", "recent_work", "") or ""
        except Exception:
            return ""

    def _save_recent_work_path(self, path: str):
        if not self.cfg:
            return
        try:
            self.cfg.set_config("Gallery", "recent_work", path or "", validate=False)
        except Exception as e:
            logger.warning(f"保存最近作品失败: {e}")

    def _filtered_works(self) -> list:
        works = self.store.list_works()
        kw = (self._gallery_keyword or "").strip().lower()
        if kw:
            works = [
                w
                for w in works
                if kw in w.get("title", "").lower() or kw in w.get("author", "").lower()
            ]
        mode = self._gallery_sort
        if mode == "created":
            works.sort(key=lambda w: w.get("created_at", ""), reverse=True)
        elif mode == "words":
            works.sort(key=lambda w: w.get("words", 0), reverse=True)
        elif mode == "title":
            works.sort(key=lambda w: w.get("title", ""))
        elif mode == "custom":
            # 用户拖拽后的自定义顺序；没记录的排最后
            order = self._load_gallery_order()
            idx = {name: i for i, name in enumerate(order)}
            works.sort(key=lambda w: idx.get(w.get("dir", ""), 999999))
        return works

    # ---------- O1：手动排序落盘 ----------
    def _gallery_order_path(self) -> str:
        return os.path.join(self.store.data_root, "works", ".order")

    def _load_gallery_order(self) -> list:
        path = self._gallery_order_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(x) for x in data]
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        return []

    def _save_gallery_order(self, order: list):
        path = self._gallery_order_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(list(order), f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"保存作品排序失败: {e}")

    # ---------- 拖拽：拿起 / 移动 / 放下 ----------

    def _begin_card_drag(self, card):
        """进入拖拽：源卡片浮到最上层 + 阴影加深 + 抓鼠标。

        grabMouse 保证鼠标快速移出卡片范围也还能收到 mouseMove——
        否则卡片会"掉队"。

        防御：hero 卡片本不该走到这里（_apply_card_interaction 没给它
        绑 mouseMoveEvent），但加一道判断保证万无一失。
        """
        if getattr(card, "_card_kind", None) == "hero":
            return
        card._drag_active = True
        QApplication.setOverrideCursor(Qt.ClosedHandCursor)
        self._drag_src_card = card
        try:
            self._drag_insert_idx = self._gallery_cards.index(card)
        except ValueError:
            self._drag_insert_idx = 0

        card.raise_()
        # 加深阴影（不新建 effect，只调参数——新建会删掉 _hover_shadow）
        try:
            card._hover_shadow.setBlurRadius(30)
            card._hover_shadow.setColor(QColor(0, 0, 0, 120))
            card._hover_shadow.setOffset(0, 12)
        except RuntimeError:
            pass
        try:
            card.grabMouse()
        except RuntimeError:
            pass

        # Esc 取消拖拽（应用级快捷键，grabMouse 不影响键盘）
        if self._drag_esc_shortcut is None:
            sc = QShortcut(QKeySequence("Escape"), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(self._cancel_card_drag)
            self._drag_esc_shortcut = sc
        # Alt+Tab / Win+L 锁屏 / 远程桌面断开等会触发 Windows 取消鼠标
        # 抓取且不产生 release 事件；监听应用失焦自动收尾，防止
        # 闭合手型光标残留、卡片停在错位。只连接一次。
        if not self._app_state_connected:
            app = QApplication.instance()
            if app is not None:
                app.applicationStateChanged.connect(self._on_app_state_during_drag)
                self._app_state_connected = True

    def _on_app_state_during_drag(self, state):
        if self._drag_src_card is not None and state != Qt.ApplicationActive:
            self._cancel_card_drag()

    def _cancel_card_drag(self):
        """拖拽取消（Esc / 应用失焦）：放弃新顺序，卡片全部回到原位。

        与 _end_card_drag 的区别：不重排 _gallery_cards、不存盘，
        只释放鼠标抓取/光标并按旧顺序重新布局（卡片动画滑回原槽位）。
        """
        card = self._drag_src_card
        if card is None:
            return
        QApplication.restoreOverrideCursor()
        try:
            card.releaseMouse()
        except RuntimeError:
            pass
        try:
            card._hover_shadow.setBlurRadius(0)
            card._hover_shadow.setColor(QColor(0, 0, 0, 0))
            card._hover_shadow.setOffset(0, 2)
        except RuntimeError:
            pass
        self._drag_src_card = None
        self._drag_insert_idx = 0
        self._layout_gallery_cards(animate=True)
        if self._drag_esc_shortcut is not None:
            try:
                self._drag_esc_shortcut.setEnabled(False)
                self._drag_esc_shortcut.deleteLater()
            except RuntimeError:
                pass
            self._drag_esc_shortcut = None

    def _move_card_with_mouse(self, card):
        """源卡片跟随鼠标 + 让其他卡片实时让位。"""
        offset = card._drag_offset
        if offset is None:
            return
        global_pos = QCursor.pos()
        container_pos = self._gallery_container.mapFromGlobal(global_pos)
        target_x = container_pos.x() - offset.x()
        target_y = container_pos.y() - offset.y()
        # 边界：不能拖出容器
        max_x = max(0, self._gallery_container.width() - card.width())
        target_x = max(0, min(target_x, max_x))
        target_y = max(0, target_y)
        card.move(target_x, target_y)

        new_idx = self._compute_drag_insert_index(card)
        if new_idx is not None:
            self._drag_insert_idx = new_idx
        self._layout_others_during_drag(card, self._drag_insert_idx)

    def _compute_drag_insert_index(self, src_card):
        """鼠标悬停在哪个网格卡片上 → 插到它前面或后面。

        只统计 _card_kind == "grid" 的卡片；hero 卡片不参与。
        鼠标不在任何卡片上时返回 None（保持上一次的位置）。
        """
        mouse_pos = self._gallery_container.mapFromGlobal(QCursor.pos())
        others = [
            c
            for c in self._gallery_cards
            if c is not src_card and getattr(c, "_card_kind", None) == "grid"
        ]
        for i, c in enumerate(others):
            try:
                rect = c.geometry()
            except RuntimeError:
                continue
            if not rect.contains(mouse_pos):
                continue
            if mouse_pos.x() < rect.center().x():
                return i
            return i + 1
        return None

    def _layout_others_during_drag(self, src_card, insert_idx):
        """拖拽实时让位：网格卡重排，hero 固定不动。

        列数 / 居中偏移算法必须和 _layout_gallery_cards 完全一致——
        否则松手后卡片会跳一下。
        """
        others = [
            c
            for c in self._gallery_cards
            if c is not src_card and getattr(c, "_card_kind", None) == "grid"
        ]

        # hero 卡片偏移：与 _layout_gallery_cards 保持一致
        # （_hero_height + _hero_gap，不要额外加 40）
        hero_offset = 0
        for c in self._gallery_cards:
            if getattr(c, "_card_kind", None) == "hero":
                hero_offset = self._hero_height + self._hero_gap
                break

        vw = max(0, self._gallery_scroll.viewport().width())
        gap = self._gallery_gap
        IDEAL_CARD_W = 200
        MAX_CARD_W = 220
        MAX_COLS = 8

        # 网格卡总数：others + 源卡片本身
        grid_count = len(others) + 1
        max_by_width = max(1, (vw + gap) // (IDEAL_CARD_W + gap))
        max_by_width = min(max_by_width, MAX_COLS)
        cols = min(max_by_width, grid_count)
        cols = max(1, cols)

        if vw > 0:
            card_w = (vw - (cols - 1) * gap) // cols
        else:
            card_w = IDEAL_CARD_W
        if card_w > MAX_CARD_W:
            card_w = MAX_CARD_W
        card_w, card_h = self._measure_card_size(card_w)

        # 左对齐：不做水平居中偏移（与 _layout_gallery_cards 保持一致）
        x_offset = 0

        slot = 0
        for c in others:
            if slot == insert_idx:
                slot += 1  # 跳过源卡片占的槽位
            x = x_offset + (slot % cols) * (card_w + gap)
            y = hero_offset + (slot // cols) * (card_h + gap)
            self._animate_card_to(c, QRect(x, y, card_w, card_h))
            slot += 1

        rows = (slot + cols - 1) // cols if slot else 0
        self._gallery_container.setMinimumHeight(hero_offset + rows * (card_h + gap))
        self._gallery_container.setMinimumWidth(vw)

    def _animate_card_to(self, card, target_rect):
        """让卡片平滑滑到目标位置。已在目标位置时不动。"""
        try:
            current = card.geometry()
        except RuntimeError:
            return
        if current == target_rect:
            return
        old_anim = getattr(card, "_geom_anim", None)
        if old_anim is not None:
            try:
                old_anim.stop()
            except RuntimeError:
                pass
            card._geom_anim = None
        anim = QPropertyAnimation(card, b"geometry", card)
        anim.setDuration(150)  # 让位动画要短，否则拖拽会显迟钝
        anim.setStartValue(current)
        anim.setEndValue(target_rect)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        card._geom_anim = anim

    def _end_card_drag(self, card):
        """松手：源卡片放下，其他网格卡片按新顺序归位。

        hero 卡片不动——它通过 _recent_work_path（配置）决定显示哪本，
        与网格排序无关。
        """
        QApplication.restoreOverrideCursor()
        try:
            card.releaseMouse()
        except RuntimeError:
            pass
        try:
            card._hover_shadow.setBlurRadius(0)
            card._hover_shadow.setColor(QColor(0, 0, 0, 0))
            card._hover_shadow.setOffset(0, 2)
        except RuntimeError:
            pass

        # 只有网格卡片参与重排
        others = [
            c
            for c in self._gallery_cards
            if c is not card and getattr(c, "_card_kind", None) == "grid"
        ]
        insert_idx = max(0, min(self._drag_insert_idx, len(others)))
        new_order = []
        for i, c in enumerate(others):
            if i == insert_idx:
                new_order.append(card)
            new_order.append(c)
        if insert_idx >= len(others):
            new_order.append(card)

        # 分离 hero 卡片和非网格杂项（empty 等）
        hero_card = None
        others_non_grid = []
        for c in self._gallery_cards:
            kind = getattr(c, "_card_kind", None)
            if kind == "hero":
                hero_card = c
            elif kind != "grid" and c is not card:
                others_non_grid.append(c)

        # 重组列表：[hero] + [网格（新顺序）] + [其他非网格项]
        result = []
        if hero_card is not None:
            result.append(hero_card)
        result.extend(new_order)
        result.extend(others_non_grid)
        self._gallery_cards = result

        # 存盘：只存网格区顺序（hero 那本按 _recent_work_path 决定）
        dirs = []
        for c in new_order:
            path = c.property("work_path")
            if path:
                dirs.append(os.path.basename(path.rstrip("/\\")))
        self._save_gallery_order(dirs)
        self._gallery_sort = "custom"
        self._save_gallery_sort_pref("custom")

        self._drag_src_card = None
        self._drag_insert_idx = 0
        if self._drag_esc_shortcut is not None:
            try:
                self._drag_esc_shortcut.setEnabled(False)
                self._drag_esc_shortcut.deleteLater()
            except RuntimeError:
                pass
            self._drag_esc_shortcut = None

        self._layout_gallery_cards(animate=True)

    def _move_work_in_order(self, path: str, direction: str):
        """手动排序模式下：把某作品在 .order 里上移 / 下移 / 置顶。

        direction: "up" / "down" / "top"
        """
        dir_name = os.path.basename(path.rstrip("/\\"))
        all_names = [w.get("dir", "") for w in self.store.list_works()]
        order = self._load_gallery_order()
        # 补全缺失的名字（新作品默认排最后）
        for name in all_names:
            if name and name not in order:
                order.append(name)
        if dir_name not in order:
            return
        idx = order.index(dir_name)
        if direction == "top":
            order.insert(0, order.pop(idx))
        elif direction == "up":
            if idx > 0:
                order[idx], order[idx - 1] = order[idx - 1], order[idx]
        elif direction == "down":
            if idx < len(order) - 1:
                order[idx], order[idx + 1] = order[idx + 1], order[idx]
        self._save_gallery_order(order)
        self._refresh_gallery(preserve_scroll=True)

    def _on_gallery_search(self, _text=""):
        """搜索框变化：只更新关键词，不动排序状态。"""
        self._gallery_keyword = self.gallery_search.text()
        self._refresh_gallery()

    def _on_gallery_sort_changed(self, _idx=0):
        """用户从 combo 选择了排序方式：切回自动排序。"""
        new_sort = self.gallery_sort.currentData() or "updated"
        if new_sort == self._gallery_sort:
            return
        self._gallery_sort = new_sort
        self._save_gallery_sort_pref(new_sort)
        self._refresh_gallery()

    def _load_gallery_sort_pref(self) -> str:
        """从 config 读画廊排序偏好。读不到 / 值非法时回退 'updated'。"""
        if not self.cfg:
            return "updated"
        try:
            v = self.cfg.get_config("Gallery", "sort_by", "updated") or "updated"
        except Exception:
            return "updated"
        # 白名单：防止旧配置或手改文件写进非法值
        if v in ("updated", "created", "words", "title", "custom"):
            return v
        return "updated"

    def _save_gallery_sort_pref(self, value: str):
        if not self.cfg:
            return
        try:
            self.cfg.set_config("Gallery", "sort_by", value, validate=False)
        except Exception as e:
            logger.warning(f"保存画廊排序偏好失败: {e}")

    def _make_new_card(self) -> QWidget:
        card = _HoverCard("newWorkCard")
        # 关键：标记为网格卡，否则 _layout_gallery_cards 会把它当
        # "空状态"处理并居中成一条窄条
        card._card_kind = "grid"
        card.setCursor(Qt.PointingHandCursor)
        card.mousePressEvent = lambda e: (
            self.create_new_work() if e.button() == Qt.LeftButton else None
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 12, 12, 10)
        v.setSpacing(8)
        cover_box = _CoverSlot()
        cb = QVBoxLayout(cover_box)
        cb.setContentsMargins(0, 0, 0, 0)
        plus = QLabel("＋")
        plus.setAlignment(Qt.AlignCenter)
        plus.setObjectName("newWorkPlus")
        plus.setStyleSheet("font-size: 46px;")
        hint = QLabel("新建作品")
        hint.setAlignment(Qt.AlignCenter)
        hint.setObjectName("newWorkHint")
        hint.setStyleSheet("font-size: 14px;")
        cb.addStretch(1)
        cb.addWidget(plus)
        cb.addWidget(hint)
        cb.addStretch(1)
        # stretch=1：封面区吃掉卡片剩余高度（和 _make_grid_card 一致）。
        # 否则封面区被压扁、下方留白。
        v.addWidget(cover_box, 1)
        spacer = QLabel("")
        spacer.setStyleSheet("background: transparent;")
        spacer.setFixedHeight(83)
        v.addWidget(spacer)
        return card

    def _reflow_gallery(self):
        """窗口大小变化：重算布局（列数/位置）。"""
        self._layout_gallery_cards(animate=False)

    # ---------- 卡片工厂：hero / 网格 / 空状态 ----------

    def _apply_card_interaction(self, card, w_info):
        """给卡片绑定交互。

        hero 卡片（_card_kind == "hero"）只支持单击打开——它是焦点，
        位置固定，不参与拖拽排序。
        网格卡片（grid）支持单击打开 + 拖拽排序。
        """
        card.setCursor(Qt.PointingHandCursor)
        card.setProperty("work_path", w_info["path"])
        card.setContextMenuPolicy(Qt.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda pos, c=card: self._show_card_menu(c, c.mapToGlobal(pos))
        )

        card._drag_start_pos = None
        card._drag_active = False
        card._drag_offset = None

        # hero 卡片：只绑单击打开
        if getattr(card, "_card_kind", None) == "hero":

            def _on_release_only(e, p=w_info["path"]):
                if e.button() == Qt.LeftButton:
                    self._open_work_by_path(p)

            card.mouseReleaseEvent = _on_release_only
            return card

        # 网格卡片：完整交互
        def _on_press(e, c=card):
            if e.button() == Qt.LeftButton:
                c._drag_start_pos = e.pos()
                c._drag_active = False
                c._drag_offset = e.pos()

        def _on_move(e, c=card):
            if c._drag_start_pos is None:
                return
            if not c._drag_active:
                if (
                    e.pos() - c._drag_start_pos
                ).manhattanLength() < QApplication.startDragDistance():
                    return
                self._begin_card_drag(c)
            self._move_card_with_mouse(c)

        def _on_release(e, c=card, p=w_info["path"]):
            if e.button() != Qt.LeftButton:
                return
            was_drag = c._drag_active
            c._drag_start_pos = None
            c._drag_active = False
            c._drag_offset = None
            if was_drag:
                self._end_card_drag(c)
            else:
                self._open_work_by_path(p)

        card.mousePressEvent = _on_press
        card.mouseMoveEvent = _on_move
        card.mouseReleaseEvent = _on_release
        return card

    def _make_hero_card(self, w_info: dict) -> QWidget:
        """焦点区卡片：三栏布局。

        左栏：封面（200×267）
        中栏：书名 / 元信息 / 简介 / [弹性] / 继续写 / 统计 / 按钮
        右栏：最近 5 章（可点击跳章）
        """
        card = _HoverCard("heroCard")
        card._card_kind = "hero"
        self._apply_card_interaction(card, w_info)
        card.setFixedHeight(self._hero_height)

        lay = QHBoxLayout(card)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(32)

        # ============ 左栏：封面 ============
        cover = _CoverLabel(w_info.get("cover", ""), w_info.get("title", "？"))
        cover.setObjectName("heroCover")
        cover.setFixedWidth(200)
        cover.setFixedHeight(267)
        lay.addWidget(cover, 0, Qt.AlignVCenter)

        # ============ 中栏：信息 ============
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(0)

        # 书名 + 状态 pill
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(12)
        title_lbl = QLabel(w_info.get("title", "未命名"))
        title_lbl.setObjectName("heroBookTitle")
        title_row.addWidget(title_lbl)
        status = self._work_status(w_info)
        pill = QLabel(self._status_label(status))
        pill.setObjectName(f"heroStatusPill_{status}")
        pill.setAlignment(Qt.AlignCenter)
        title_row.addWidget(pill, 0, Qt.AlignVCenter)
        title_row.addStretch()
        info.addLayout(title_row)

        # 元信息（作者 · 章节 · 字数 · 更新时间）
        meta_parts = []
        author = (w_info.get("author") or "").strip()
        if author:
            meta_parts.append(author)
        meta_parts.append(
            f"{w_info.get('chapter_count', 0)} 章 · " f"{w_info.get('words', 0):,} 字"
        )
        updated_str = self._time_ago(w_info.get("updated_at", ""))
        if updated_str:
            meta_parts.append(updated_str)
        meta_lbl = QLabel("  ·  ".join(meta_parts))
        meta_lbl.setObjectName("heroBookMeta")
        info.addWidget(meta_lbl)

        info.addSpacing(14)

        # 简介（限宽 + 超 150 字截断）
        desc = (w_info.get("description") or "").strip()
        if not desc:
            desc_lbl = QLabel("还没有写简介。右键作品卡片 →「编辑简介」添加。")
            desc_lbl.setObjectName("heroBookDescEmpty")
        else:
            if len(desc) > 150:
                desc = desc[:150] + "…"
            desc_lbl = QLabel(desc)
            desc_lbl.setObjectName("heroBookDesc")
        desc_lbl.setWordWrap(True)
        # 关键：限制宽度。58 中文字 ≈ 620px，眼睛扫一行不累。
        desc_lbl.setMaximumWidth(620)
        info.addWidget(desc_lbl)

        # 弹性：把下面的内容推到卡片底部
        info.addStretch(1)

        # 继续写（章节名超 40 字截断）
        cont_row = QHBoxLayout()
        cont_row.setContentsMargins(0, 0, 0, 0)
        cont_row.setSpacing(10)
        arrow = QLabel("▶")
        arrow.setObjectName("heroArrow")
        cont_row.addWidget(arrow)
        lab = QLabel("继续写")
        lab.setObjectName("heroContinueLabel")
        cont_row.addWidget(lab)
        ch_title = self._recent_chapter_title(w_info)
        if len(ch_title) > 40:
            ch_title = ch_title[:40] + "…"
        ch_lbl = QLabel(ch_title)
        ch_lbl.setObjectName("heroChapter")
        cont_row.addWidget(ch_lbl)
        cont_row.addStretch()
        info.addLayout(cont_row)

        info.addSpacing(14)

        # 今日统计条
        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(0)
        stats_frame = QFrame()
        stats_frame.setObjectName("heroTodayStats")
        sl = QHBoxLayout(stats_frame)
        sl.setContentsMargins(14, 8, 14, 8)
        sl.setSpacing(14)

        today = self.stats.today_words() if self.stats else 0
        streak = self.stats.streak_days() if self.stats else 0
        yesterday = self.stats.words_of_day(1) if self.stats else 0

        def _stat(num, label):
            w = QWidget()
            w.setAttribute(Qt.WA_StyledBackground, False)
            l = QHBoxLayout(w)
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(4)
            n_lab = QLabel(f"{num:,}")
            n_lab.setObjectName("heroStatNum")
            l.addWidget(n_lab)
            t_lab = QLabel(label)
            t_lab.setObjectName("heroStatLabel")
            l.addWidget(t_lab)
            return w

        sl.addWidget(_stat(today, "字 今天"))
        sep1 = QFrame()
        sep1.setObjectName("heroStatSep")
        sep1.setFixedSize(1, 14)
        sl.addWidget(sep1)
        sl.addWidget(_stat(streak, "天 连续"))
        if yesterday > 0:
            sep2 = QFrame()
            sep2.setObjectName("heroStatSep")
            sep2.setFixedSize(1, 14)
            sl.addWidget(sep2)
            sl.addWidget(_stat(yesterday, "字 昨天"))
        sl.addStretch()
        stats_row.addWidget(stats_frame)
        stats_row.addStretch()
        info.addLayout(stats_row)

        info.addSpacing(16)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(10)
        btn_start = QPushButton("开始写作")
        btn_start.setObjectName("heroPrimaryBtn")
        btn_start.setCursor(Qt.PointingHandCursor)
        btn_start.setFixedHeight(36)
        btn_start.setMinimumWidth(110)
        btn_start.clicked.connect(
            lambda _=False, p=w_info["path"]: self._open_work_by_path(p)
        )
        btn_row.addWidget(btn_start)

        btn_history = QPushButton("历史快照")
        btn_history.setObjectName("heroGhostBtn")
        btn_history.setCursor(Qt.PointingHandCursor)
        btn_history.setFixedHeight(36)
        btn_history.clicked.connect(
            lambda _=False, p=w_info["path"]: self._open_work_history(p)
        )
        btn_row.addWidget(btn_history)
        btn_row.addStretch()
        info.addLayout(btn_row)

        lay.addLayout(info, 1)

        # ============ 右栏：最近章节 ============
        recent_col = self._make_hero_recent_column(w_info)
        lay.addWidget(recent_col, 0)

        return card

    def _make_hero_recent_column(self, w_info: dict) -> QWidget:
        """hero 卡片右栏：最近 5 章列表 + 底部"查看全部"。"""
        col = QFrame()
        col.setObjectName("heroRecentCol")
        col.setFixedWidth(240)

        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # 标题行
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(0)
        head_lbl = QLabel("最近章节")
        head_lbl.setObjectName("heroRecentHead")
        head.addWidget(head_lbl)
        head.addStretch()
        total_ch = int(w_info.get("chapter_count", 0) or 0)
        count_lbl = QLabel(f"{total_ch} 章")
        count_lbl.setObjectName("heroRecentCount")
        head.addWidget(count_lbl)
        v.addLayout(head)

        v.addSpacing(10)

        # 章节列表（7 条填满 hero 右栏高度）
        chapters = self._recent_chapters(w_info, n=7)
        if not chapters:
            empty = QLabel("（还没有章节）")
            empty.setObjectName("heroRecentEmpty")
            empty.setAlignment(Qt.AlignCenter)
            v.addWidget(empty)
            v.addStretch(1)
        else:
            work_path = w_info["path"]
            for num, title, wc, cid in chapters:
                item = self._make_recent_chapter_item(work_path, num, title, wc, cid)
                v.addWidget(item)
            v.addStretch(1)

        # 底部"查看全部"
        foot = QPushButton("查看全部 →")
        foot.setObjectName("heroRecentFoot")
        foot.setCursor(Qt.PointingHandCursor)
        foot.clicked.connect(
            lambda _=False, p=w_info["path"]: self._open_work_by_path(p)
        )
        v.addWidget(foot)

        return col

    def _make_recent_chapter_item(
        self, work_path: str, num: int, title: str, word_count: int, chapter_id: str
    ) -> QWidget:
        """右栏的单条章节。整行可点击 → 打开作品 + 跳到该章。"""
        item = QFrame()
        item.setObjectName("heroRecentItem")
        item.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(item)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(8)

        # 章号
        num_lbl = QLabel(f"{num:02d}")
        num_lbl.setObjectName("heroRecentNum")
        num_lbl.setFixedWidth(26)
        num_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(num_lbl)

        # 标题（长标题自动截断）
        if len(title) > 12:
            title = title[:12] + "…"
        name_lbl = QLabel(title)
        name_lbl.setObjectName("heroRecentName")
        row.addWidget(name_lbl, 1)

        # 字数
        wc_lbl = QLabel(f"{word_count:,}")
        wc_lbl.setObjectName("heroRecentWc")
        row.addWidget(wc_lbl)

        # 点击整行跳转
        def _on_click(e, p=work_path, cid=chapter_id):
            if e.button() == Qt.LeftButton:
                self._open_work_chapter(p, cid)

        item.mousePressEvent = _on_click
        return item

    def _make_grid_card(self, w_info: dict) -> QWidget:
        """网格区卡片：封面 + 书名 + 章数（无作者/时间）。"""
        card = _HoverCard("workCard")
        card._card_kind = "grid"
        self._apply_card_interaction(card, w_info)

        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # 封面（无内边距，让封面顶到卡片边缘）
        cover = _CoverLabel(w_info.get("cover", ""), w_info.get("title", "？"))
        # 关键：关掉封面自动撑高——网格卡片高度由 _measure_card_size
        # 统一控制，封面只负责填满上半部分
        cover.set_auto_height(False)
        cover.setMinimumHeight(0)
        # 状态标记（右上角彩点 + 左下角文字标签，绘制在封面内部）
        cover.set_work_status(self._work_status(w_info))
        # stretch=1：封面吃掉卡片剩余高度。
        # _CoverLabel 内部不通过 setPixmap 显示图片，sizeHint 返回极小值；
        # 不给 stretch 的话 QVBoxLayout 按 sizeHint 分高度——封面被压扁、
        # 底部留一大片空白。
        v.addWidget(cover, 1)

        # 信息区
        info = QWidget()
        info.setObjectName("gridCardInfo")
        il = QVBoxLayout(info)
        il.setContentsMargins(14, 12, 14, 14)
        il.setSpacing(3)

        title_lbl = QLabel(w_info.get("title", "未命名"))
        title_lbl.setObjectName("workCardTitle")
        il.addWidget(title_lbl)

        sub_lbl = QLabel(
            f"{w_info.get('chapter_count', 0)} 章 · " f"{w_info.get('words', 0):,} 字"
        )
        sub_lbl.setObjectName("workCardSub")
        il.addWidget(sub_lbl)

        v.addWidget(info, 0)
        return card

    def _make_empty_card(self) -> QWidget:
        """空状态引导卡（新用户第一次打开）。"""
        card = QFrame()
        card.setObjectName("galleryEmptyCard")
        card._card_kind = "empty"
        card.setCursor(Qt.PointingHandCursor)
        card.setFixedSize(480, 380)
        card.mousePressEvent = lambda e: (
            self.create_new_work() if e.button() == Qt.LeftButton else None
        )

        v = QVBoxLayout(card)
        v.setContentsMargins(40, 52, 40, 48)
        v.setSpacing(0)
        v.setAlignment(Qt.AlignCenter)

        icon = QLabel("墨")
        icon.setObjectName("galleryEmptyIcon")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(68, 68)
        v.addWidget(icon, 0, Qt.AlignHCenter)

        v.addSpacing(28)

        title = QLabel("你的第一部作品")
        title.setObjectName("galleryEmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        v.addWidget(title, 0, Qt.AlignHCenter)

        v.addSpacing(14)

        desc = QLabel("给它起个名字，写下第一句话。\n" "剩下的路，我们陪你慢慢走。")
        desc.setObjectName("galleryEmptyDesc")
        desc.setAlignment(Qt.AlignCenter)
        v.addWidget(desc, 0, Qt.AlignHCenter)

        v.addSpacing(32)

        btn = QPushButton("开始创作")
        btn.setObjectName("heroPrimaryBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(38)
        btn.setMinimumWidth(140)
        btn.clicked.connect(self.create_new_work)
        v.addWidget(btn, 0, Qt.AlignHCenter)

        return card

    def _recent_chapters(self, w_info: dict, n: int = 5) -> list:
        """返回作品最近的 N 章（倒序）：[(章号, 标题, 字数, chapter_id), ...]

        用于 hero 卡片右栏"最近章节"。
        章号取 book.json 数组里的位置（1-based）；标题过长时截断由调用方处理。
        """
        try:
            work_obj = self.store.open_work(w_info["path"])
            if work_obj is None or not work_obj.chapters:
                return []
            chapters = work_obj.chapters
            # 取尾部 N 章，倒序显示（最新在最上）
            tail = chapters[-n:] if len(chapters) > n else chapters
            total = len(chapters)
            start_idx = total - len(tail) + 1
            result = []
            for i, c in enumerate(tail):
                num = start_idx + i
                title = (c.get("title") or "").strip() or "未命名"
                wc = int(c.get("word_count", 0) or 0)
                cid = c.get("id", "")
                result.append((num, title, wc, cid))
            result.reverse()
            return result
        except Exception:
            return []

    def _open_work_chapter(self, work_path: str, chapter_id: str):
        """打开作品并直接跳到指定章节（hero 右栏"最近章节"用）。

        实现方式：先把 chapter_id 存到 _pending_chapter_id，
        _open_work_by_path 触发 _on_work_switched 时会优先打开它。
        """
        if not chapter_id:
            self._open_work_by_path(work_path)
            return
        self._pending_chapter_id = chapter_id
        self._open_work_by_path(work_path)

    def _recent_chapter_title(self, w_info: dict) -> str:
        """取作品的"最近编辑章节"标题，用于 hero 卡片"继续写"行。"""
        try:
            work_obj = self.store.open_work(w_info["path"])
            if work_obj is None or not work_obj.chapters:
                return "（还没有章节）"
            chapters = work_obj.chapters
            with_time = [c for c in chapters if c.get("updated_at")]
            if with_time:
                recent = max(with_time, key=lambda c: c.get("updated_at", ""))
            else:
                recent = chapters[-1]
            title = (recent.get("title") or "").strip()
            return title or "（未命名章节）"
        except Exception:
            return "（无法读取）"

    def _open_work_history(self, path: str):
        """hero 卡片"历史快照"按钮：打开作品的快照浏览器。"""
        work_obj = self.store.open_work(path)
        if work_obj is None:
            return
        # 临时把 workspace.work 切到该作品（快照浏览器依赖 work 对象）
        # 但因为 workspace.work 会被改动，走完对话后恢复
        prev_work = self.work
        try:
            self.work = work_obj
            from ui.version_browser_dialog import VersionBrowserDialog

            dlg = VersionBrowserDialog(work_obj, self)
            dlg.exec()
        finally:
            self.work = prev_work

    # ---------- 作品状态辅助 ----------
    _STATUS_LABELS = {
        "ongoing": "连载中",
        "completed": "已完结",
        "paused": "暂停更新",
        "planning": "构思中",
    }

    def _work_status(self, w_info: dict) -> str:
        """从 w_info 推算作品状态。

        w_info 里若有 status 字段就用它；否则按章节数推断
        （有章节 → 连载中；没章节 → 构思中）。
        """
        s = (w_info.get("status") or "").strip()
        if s in self._STATUS_LABELS:
            return s
        return "ongoing" if int(w_info.get("chapter_count", 0) or 0) > 0 else "planning"

    def _status_label(self, status: str) -> str:
        return self._STATUS_LABELS.get(status, "未知")

    def _set_work_status(self, work_obj: Work, status: str):
        """右键菜单 → 修改作品状态。"""
        from utils.work_store import VALID_WORK_STATUSES

        if status not in VALID_WORK_STATUSES:
            return
        work_obj.set_status(status)
        self._refresh_gallery(preserve_scroll=True)

    @staticmethod
    def _cover_gradient_colors(seed: str):
        import hashlib

        palettes = [
            ("#667eea", "#764ba2"),
            ("#f093fb", "#f5576c"),
            ("#4facfe", "#00f2fe"),
            ("#43e97b", "#38f9d7"),
            ("#fa709a", "#fee140"),
            ("#a18cd1", "#fbc2eb"),
            ("#ff9a9e", "#fecfef"),
            ("#ffecd2", "#fcb69f"),
            ("#30cfd0", "#330867"),
            ("#a1c4fd", "#c2e9fb"),
        ]
        h = int(hashlib.md5((seed or "").encode()).hexdigest(), 16)
        return palettes[h % len(palettes)]

    @staticmethod
    def _time_ago(time_str: str) -> str:
        if not time_str:
            return ""
        try:
            t = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            delta = datetime.now() - t
            secs = delta.total_seconds()
            if secs < 60:
                return "刚刚更新"
            if secs < 3600:
                return f"{int(secs // 60)} 分钟前"
            if secs < 86400:
                return f"{int(secs // 3600)} 小时前"
            if secs < 86400 * 7:
                return f"{int(secs // 86400)} 天前"
            return time_str[:10]
        except Exception:
            return time_str[:10] if len(time_str) >= 10 else ""

    def _open_work_by_path(self, path: str):
        work = self.store.open_work(path)
        if work:
            # 记录"最近打开的作品"（持久化到 config），
            # 下次启动 / 回画廊时 hero 卡片会显示它
            self._save_recent_work_path(path)
            self.bookshelf.load_work(work)
            self.bookshelf.refresh_works(path)
            self.bookshelf.work_switched.emit(path)

    def _show_card_menu(self, card: QFrame, global_pos: QPoint):
        parent_path = card.property("work_path")
        if not parent_path:
            return
        work_obj = self.store.open_work(parent_path)
        if not work_obj:
            return
        menu = QMenu(self)
        act_open = menu.addAction("打开作品")
        act_rename = menu.addAction("重命名")
        act_edit_desc = menu.addAction("编辑简介…")
        menu.addSeparator()

        # 作品状态子菜单：当前状态灰显，其余可点
        status_menu = menu.addMenu("作品状态")
        current_status = self._work_status(
            {
                "status": work_obj.meta.get("status", ""),
                "chapter_count": len(work_obj.chapters),
            }
        )
        status_actions = {}
        for key in ("ongoing", "completed", "paused", "planning"):
            label = self._STATUS_LABELS[key]
            if key == current_status:
                act = status_menu.addAction(f"● {label}")
                act.setEnabled(False)
            else:
                act = status_menu.addAction(label)
                status_actions[act] = key

        menu.addSeparator()
        act_cover = menu.addAction("上传封面…")
        act_remove = menu.addAction("移除封面")
        if not work_obj.has_cover():
            act_remove.setEnabled(False)
        menu.addSeparator()
        act_export = menu.addAction("导出文档…")
        menu.addSeparator()

        # O1：手动排序模式下才有上移 / 下移 / 置顶
        act_move_top = act_move_up = act_move_down = None
        if self._gallery_sort == "custom":
            act_move_top = menu.addAction("置顶")
            act_move_up = menu.addAction("上移")
            act_move_down = menu.addAction("下移")
            menu.addSeparator()

        act_delete = menu.addAction("移到回收站")

        chosen = menu.exec(global_pos)
        # 关键：用户取消菜单时 chosen 是 None，必须提前返回。
        # 否则下面的 act_move_xxx（非手动模式时也是 None）会被 None == None 命中。
        if chosen is None:
            return

        if chosen == act_open:
            self._open_work_by_path(parent_path)
        elif chosen == act_rename:
            self._rename_work(work_obj, parent_path)
        elif chosen == act_edit_desc:
            self._edit_work_description(work_obj, parent_path)
        elif chosen in status_actions:
            self._set_work_status(work_obj, status_actions[chosen])
        elif chosen == act_cover:
            src, _ = QFileDialog.getOpenFileName(
                self, "选择封面图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.webp)"
            )
            if src:
                try:
                    work_obj.set_cover(src)
                    self._refresh_gallery()
                    self._refresh_gallery_theme()
                except Exception as e:
                    QMessageBox.warning(self, "封面失败", str(e))
        elif chosen == act_remove:
            work_obj.remove_cover()
            self._refresh_gallery()
            self._refresh_gallery_theme()
        elif chosen == act_export:
            self._export_work_obj(work_obj)
        # 移动分支：act_xxx is not None 双重保险
        elif act_move_top is not None and chosen == act_move_top:
            self._move_work_in_order(parent_path, "top")
        elif act_move_up is not None and chosen == act_move_up:
            self._move_work_in_order(parent_path, "up")
        elif act_move_down is not None and chosen == act_move_down:
            self._move_work_in_order(parent_path, "down")
        elif chosen == act_delete:
            if (
                QMessageBox.question(
                    self,
                    "移到回收站",
                    f"确定把《{work_obj.title}》移到回收站吗？\n"
                    "可在「回收站」里恢复或彻底删除。",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                == QMessageBox.Yes
            ):
                if self.work and self.work.root_dir == parent_path:
                    self.work = None
                    self.stack.setCurrentIndex(0)
                try:
                    self.store.delete_work(parent_path)
                except Exception as e:
                    QMessageBox.critical(self, "移动失败", str(e))
                    return
                self._refresh_gallery()
                self._refresh_gallery_theme()

    def _rename_work(self, work_obj: Work, parent_path: str):
        title, ok = QInputDialog.getText(
            self, "重命名作品", "新的书名：", text=work_obj.title
        )
        title = title.strip()
        if not ok or not title or title == work_obj.title:
            return
        work_obj.meta["title"] = title
        work_obj.save_meta()
        if self.bookshelf.work and os.path.normcase(
            self.bookshelf.work.root_dir
        ) == os.path.normcase(parent_path):
            self.bookshelf.work.meta["title"] = title
            self.bookshelf.refresh_works(parent_path, auto_open=False)
            self._update_titles()
        self._refresh_gallery()

    def _edit_work_description(self, work_obj: Work, parent_path: str):
        """编辑作品简介（显示在 hero 卡片上）。"""
        cur = (work_obj.meta.get("description") or "").strip()
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑简介 · {work_obj.title}")
        dlg.setMinimumSize(520, 360)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        hint = QLabel(
            "简介会显示在作品卡片的焦点区。\n"
            "建议 100 字以内；超过的部分会截断并显示「…」。"
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)
        edit = QPlainTextEdit()
        edit.setPlainText(cur)
        edit.setPlaceholderText(
            "一句话说说这个故事。\n"
            "例：长安城接连出现离奇命案，死者身上都有一枚相同的玉佩……"
        )
        lay.addWidget(edit, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel)
        ok = QPushButton("保存")
        ok.setProperty("variant", "primary")
        ok.clicked.connect(dlg.accept)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)
        if dlg.exec() != QDialog.Accepted:
            return
        work_obj.meta["description"] = edit.toPlainText().strip()
        work_obj.save_meta()
        self._refresh_gallery(preserve_scroll=True)

    def _refresh_gallery_theme(self):
        theme = self.editor._theme if self.editor else {}
        text = theme.get("text", "#3b342a")
        muted = theme.get("muted", "#9a8e76")
        accent = theme.get("accent", "#b08a4e")
        accent_fg = theme.get("accent_fg") or "white"
        accent_deep = theme.get("accent_hover", accent)
        bg = theme.get("bg", "#f7f2e8")
        # 主题明暗判断（提前算，下面 pill 颜色要用）
        is_light = self._is_light(theme)
        card_bg = self._lighten_or_darken(bg, 6 if is_light else -8)
        card_border = qss_alpha(muted, 0x33)

        # 状态 pill 文字色：
        #   浅色主题用暗色字（叠在浅底上清晰）
        #   深色主题用亮色字（叠在深底上清晰）
        pill_fg = {
            "ongoing": ("#388E3C" if is_light else "#81C784"),
            "completed": ("#3D5FC8" if is_light else "#8DA8F0"),
            "paused": ("#C87F1F" if is_light else "#F0C070"),
            "planning": ("#6E6656" if is_light else "#B8B0A0"),
        }

        # 焦点区（hero）卡片的内层浅色
        hero_bg = card_bg
        hero_border = qss_alpha(accent, 0x38)
        # 网格卡信息区底色
        info_bg = card_bg

        page = self._gallery_placeholder

        # ---- 顶部身份区样式 ----
        identity = page.findChild(QWidget, "galleryIdentity")
        if identity is not None:
            identity.setStyleSheet(f"#galleryIdentity {{ background-color: {bg}; }}")

        # ---- 页面级样式（大杂烩） ----
        page.setStyleSheet(
            f"QWidget#galleryPlaceholder {{ background-color: {bg};"
            f" color: {text}; }}"
            f"#galleryContainer {{ background-color: {bg}; }}"
            # 身份区
            f"QLabel#galleryTitle {{ color: {text}; font-size: 18px;"
            f" font-weight: 700; letter-spacing: 0.5px; }}"
            f"QLabel#galleryStatsLabel {{ color: {muted}; font-size: 12px;"
            f" letter-spacing: 0.3px; }}"
            f"QLabel#galleryEmptyTip {{ color: {muted}; font-size: 14px; }}"
            # 搜索框
            f"QLineEdit#gallerySearch {{ background-color: {card_bg};"
            f" color: {text};"
            f" border: 1px solid {card_border}; border-radius: 10px;"
            f" padding: 6px 12px; font-size: 12.5px; }}"
            f"QLineEdit#gallerySearch:hover {{"
            f" border-color: {qss_alpha(accent, 0x88)}; }}"
            f"QLineEdit#gallerySearch:focus {{"
            f" border: 1px solid {accent}; }}"
            # 回收站图标按钮
            f"QPushButton#galleryTrash {{ background-color: {card_bg};"
            f" border: 1px solid {card_border}; border-radius: 10px; }}"
            f"QPushButton#galleryTrash:hover {{"
            f" background-color: {qss_alpha(accent, 0x12)};"
            f" border-color: {qss_alpha(accent, 0x66)}; }}"
            # ---- hero 卡片 ----
            f"#heroCard {{ background-color: {hero_bg};"
            f" border: 1px solid {hero_border};"
            f" border-radius: 20px; }}"
            f"#heroCard:hover {{"
            f" border: 1px solid {qss_alpha(accent, 0x88)}; }}"
            f"QLabel#heroBookTitle {{ color: {text};"
            f" font-size: 26px; font-weight: 700;"
            f" letter-spacing: 1px; background: transparent; }}"
            f"QLabel#heroStatusPill_ongoing {{"
            f" color: {pill_fg['ongoing']};"
            f" background: {qss_alpha('#4CAF50', 0x2E)};"
            f" border-radius: 11px; padding: 3px 10px;"
            f" font-size: 11.5px; font-weight: 600;"
            f" letter-spacing: 0.5px; }}"
            f"QLabel#heroStatusPill_completed {{"
            f" color: {pill_fg['completed']};"
            f" background: {qss_alpha('#5B7FE5', 0x2E)};"
            f" border-radius: 11px; padding: 3px 10px;"
            f" font-size: 11.5px; font-weight: 600;"
            f" letter-spacing: 0.5px; }}"
            f"QLabel#heroStatusPill_paused {{"
            f" color: {pill_fg['paused']};"
            f" background: {qss_alpha('#E6A23C', 0x2E)};"
            f" border-radius: 11px; padding: 3px 10px;"
            f" font-size: 11.5px; font-weight: 600;"
            f" letter-spacing: 0.5px; }}"
            f"QLabel#heroStatusPill_planning {{"
            f" color: {pill_fg['planning']};"
            f" background: {qss_alpha('#928A7C', 0x2E)};"
            f" border-radius: 11px; padding: 3px 10px;"
            f" font-size: 11.5px; font-weight: 600;"
            f" letter-spacing: 0.5px; }}"
            f"QLabel#heroBookMeta {{ color: {muted}; font-size: 12.5px;"
            f" letter-spacing: 0.3px; background: transparent; }}"
            f"QLabel#heroBookDesc {{ color: {text};"
            f" font-size: 13px; line-height: 1.75;"
            f" background: transparent; }}"
            f"QLabel#heroBookDescEmpty {{ color: {muted};"
            f" font-size: 12.5px; font-style: italic;"
            f" background: transparent; }}"
            # ---- hero 右栏：最近章节 ----
            f"QFrame#heroRecentCol {{"
            f" border-left: 1px solid {qss_alpha(muted, 0x28)};"
            f" background: transparent; }}"
            f"QLabel#heroRecentHead {{"
            f" color: {muted}; font-size: 11px; font-weight: 700;"
            f" letter-spacing: 1.5px; background: transparent; }}"
            f"QLabel#heroRecentCount {{"
            f" color: {muted}; font-size: 11px;"
            f" background: transparent; }}"
            f"QLabel#heroRecentEmpty {{"
            f" color: {muted}; font-size: 12px;"
            f" padding: 20px 0; background: transparent; }}"
            f"QFrame#heroRecentItem {{"
            f" border: none; border-radius: 8px;"
            f" background: transparent; }}"
            f"QFrame#heroRecentItem:hover {{"
            f" background: {qss_alpha(accent, 0x12)}; }}"
            f"QLabel#heroRecentNum {{"
            f" color: {muted}; font-size: 11px;"
            f" font-family: 'Consolas', 'SF Mono', monospace;"
            f" background: transparent; }}"
            f"QLabel#heroRecentName {{"
            f" color: {text}; font-size: 12.5px;"
            f" background: transparent; }}"
            f"QFrame#heroRecentItem:hover QLabel#heroRecentName {{"
            f" color: {accent}; }}"
            f"QLabel#heroRecentWc {{"
            f" color: {muted}; font-size: 11px;"
            f" background: transparent; }}"
            f"QPushButton#heroRecentFoot {{"
            f" color: {muted}; font-size: 11.5px;"
            f" text-align: right; border: none;"
            f" background: transparent; padding: 6px 10px 0 10px; }}"
            f"QPushButton#heroRecentFoot:hover {{"
            f" color: {accent}; }}"
            f"QLabel#heroArrow {{ color: {accent};"
            f" font-size: 16px; font-weight: 700;"
            f" background: transparent; }}"
            f"QLabel#heroContinueLabel {{ color: {muted};"
            f" font-size: 12.5px; letter-spacing: 0.5px;"
            f" background: transparent; }}"
            f"QLabel#heroChapter {{ color: {accent};"
            f" font-size: 18px; font-weight: 700;"
            f" letter-spacing: 0.5px; background: transparent; }}"
            # hero 今日统计条
            f"QFrame#heroTodayStats {{ background: {qss_alpha(accent, 0x10)};"
            f" border: none; border-radius: 10px; }}"
            f"QFrame#heroStatSep {{ background: {qss_alpha(muted, 0x44)}; }}"
            f"QLabel#heroStatNum {{ color: {accent};"
            f" font-size: 14px; font-weight: 700;"
            f" background: transparent; }}"
            f"QLabel#heroStatLabel {{ color: {muted};"
            f" font-size: 12px; background: transparent; }}"
            # hero 按钮
            f"QPushButton#heroPrimaryBtn {{"
            f" background-color: {accent}; color: {accent_fg};"
            f" border: none; border-radius: 10px;"
            f" padding: 8px 22px; font-size: 13px; font-weight: 600; }}"
            f"QPushButton#heroPrimaryBtn:hover {{"
            f" background-color: {accent_deep}; }}"
            f"QPushButton#heroGhostBtn {{"
            f" background: transparent; color: {text};"
            f" border: 1px solid {card_border};"
            f" border-radius: 10px; padding: 8px 18px;"
            f" font-size: 13px; }}"
            f"QPushButton#heroGhostBtn:hover {{"
            f" border-color: {accent}; color: {accent};"
            f" background-color: {qss_alpha(accent, 0x10)}; }}"
            # ---- 网格卡片 ----
            f"#workCard {{ background-color: {info_bg};"
            f" color: {text};"
            f" border: 1px solid {card_border};"
            f" border-radius: 16px; }}"
            f"#workCard:hover {{"
            f" border: 1px solid {qss_alpha(accent, 0x88)}; }}"
            f"#workCover {{ background: transparent; border: none; }}"
            f"QWidget#gridCardInfo {{ background: transparent; }}"
            f"QLabel#workCardTitle {{ color: {text};"
            f" font-size: 14.5px; font-weight: 700;"
            f" letter-spacing: 0.3px; background: transparent; }}"
            f"QLabel#workCardSub {{ color: {muted};"
            f" font-size: 11.5px; background: transparent; }}"
            f"QPushButton#cardMenuBtn {{ background-color: transparent;"
            f" border: none; border-radius: 6px; }}"
            f"QPushButton#cardMenuBtn:hover {{"
            f" background-color: {qss_alpha(accent, 0x22)}; }}"
            f"QFrame#galleryInsertLine {{ background-color: {accent};"
            f" border-radius: 2px; }}"
            # ---- 新建卡 ----
            f"#newWorkCard {{ background-color: transparent;"
            f" border: 2px dashed {qss_alpha(muted, 0x55)};"
            f" border-radius: 16px; }}"
            f"#newWorkCard:hover {{"
            f" background-color: {qss_alpha(accent, 0x08)};"
            f" border: 2px solid {accent}; }}"
            f"#newWorkCoverBox {{ background-color: transparent;"
            f" border: none; border-radius: 10px; }}"
            f"QLabel#newWorkPlus {{ color: {muted}; background: transparent; }}"
            f"QLabel#newWorkHint {{ color: {muted};"
            f" background: transparent; font-size: 13px; }}"
            f"#newWorkCard:hover QLabel#newWorkPlus,"
            f" #newWorkCard:hover QLabel#newWorkHint {{"
            f" color: {accent}; }}"
            # ---- 空状态引导卡 ----
            f"#galleryEmptyCard {{ background-color: {hero_bg};"
            f" border: 1px solid {hero_border};"
            f" border-radius: 24px; }}"
            f"#galleryEmptyCard:hover {{"
            f" border: 1px solid {accent}; }}"
            f"QLabel#galleryEmptyIcon {{"
            f" color: #ffffff;"
            f" font-size: 30px; font-weight: 700;"
            f" background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            f"   stop:0 #D4A968, stop:1 #B08A4E);"
            f" border-radius: 34px; }}"
            f"QLabel#galleryEmptyTitle {{"
            f" color: {text}; font-size: 22px; font-weight: 700;"
            f" letter-spacing: 1.5px; background: transparent; }}"
            f"QLabel#galleryEmptyDesc {{"
            f" color: {muted}; font-size: 13px;"
            f" line-height: 1.9; background: transparent; }}"
        )

        # is_light 已在函数开头算好（替换 1）
        if getattr(self, "_last_gallery_scroll_light", None) is not is_light:
            self._last_gallery_scroll_light = is_light
            if is_light:
                self._gallery_scroll.setStyleSheet(
                    modern_scrollbar_qss("rgba(0, 0, 0, 0.22)", "rgba(0, 0, 0, 0.42)")
                )
            else:
                self._gallery_scroll.setStyleSheet(
                    modern_scrollbar_qss(
                        "rgba(255, 255, 255, 0.25)", "rgba(255, 255, 255, 0.48)"
                    )
                )

        # 回收站按钮图标
        self.gallery_trash_btn.setIcon(qta.icon("fa5s.trash-alt", color=muted))

        # 网格卡片 ⋯ 按钮图标
        if getattr(self, "_last_menu_icon_color", None) != muted:
            self._last_menu_icon_color = muted
            icon = qta.icon("fa5s.ellipsis-h", color=muted)
            for btn in page.findChildren(QPushButton):
                if btn.objectName() == "cardMenuBtn":
                    btn.setIcon(icon)

    # ▼▼▼ 第四步粘贴位置 ▼▼▼
    def _export_work_obj(self, work_obj: Work):
        """导出入：先弹选项，再根据方式选文件 / 目录。"""
        done_count = sum(
            1 for c in work_obj.chapters if c.get("status") == CHAPTER_STATUS_DONE
        )
        dlg = _ExportOptionsDialog(len(work_obj.chapters), done_count, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        opt = dlg.options()

        if opt["mode"] == "split":
            self._export_split_chapters(work_obj, opt)
        else:
            self._export_merged_file(work_obj, opt)

    def _export_merged_file(self, work_obj: Work, opt: dict):
        """合并导出：所有章节拼成一个文件。"""
        fmt = opt["format"]
        ext_map = {
            "utf8_txt": ".txt",
            "gbk_txt": ".txt",
            "md": ".md",
            "docx": ".docx",
            "doc": ".doc",
        }
        flt_map = {
            "utf8_txt": "UTF-8 文本 (*.txt)",
            "gbk_txt": "GBK 文本 (*.txt)",
            "md": "Markdown (*.md)",
            "docx": "Word 文档 (*.docx)",
            "doc": "Word 老格式 (*.doc)",
        }
        ext = ext_map.get(fmt, ".txt")
        flt = flt_map.get(fmt, "文本 (*.txt)")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出作品", work_obj.title + ext, flt
        )
        if not path:
            return
        try:
            if fmt == "md":
                text = work_obj.export_markdown(
                    include_titles=True,
                    blank_between=opt["blank_between"],
                    only_done=opt["only_done"],
                )
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(text)
            elif fmt == "docx":
                # 若 Work 没有 export_docx 方法 → 退回 HTML 兜底
                if hasattr(work_obj, "export_docx"):
                    work_obj.export_docx(
                        path,
                        include_titles=True,
                        volume_headers=opt["volume_headers"],
                        only_done=opt["only_done"],
                    )
                else:
                    html = work_obj.export_html(
                        include_titles=True,
                        volume_headers=opt["volume_headers"],
                        only_done=opt["only_done"],
                    )
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(html)
            elif fmt == "doc":
                html = work_obj.export_html(
                    include_titles=True,
                    volume_headers=opt["volume_headers"],
                    only_done=opt["only_done"],
                )
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(html)
            elif fmt == "gbk_txt":
                text = work_obj.export_merged(
                    include_titles=True,
                    blank_between=opt["blank_between"],
                    volume_headers=opt["volume_headers"],
                    only_done=opt["only_done"],
                )
                with open(path, "w", encoding="gbk", errors="replace", newline="") as f:
                    f.write(text)
            else:  # utf8_txt
                text = work_obj.export_merged(
                    include_titles=True,
                    blank_between=opt["blank_between"],
                    volume_headers=opt["volume_headers"],
                    only_done=opt["only_done"],
                )
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(text)
            scope = "已完成章节" if opt["only_done"] else "全部章节"
            QMessageBox.information(self, "导出完成", f"已导出{scope}：\n{path}")
        except OSError as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _export_split_chapters(self, work_obj: Work, opt: dict):
        """分章导出：每章一个独立文件，输出到一个目录。"""
        out_dir = QFileDialog.getExistingDirectory(self, "选择导出目录", "")
        if not out_dir:
            return
        try:
            n = work_obj.export_chapter_files(
                out_dir,
                fmt=opt["format"],
                only_done=opt["only_done"],
                volume_headers=opt["volume_headers"],
                seq_prefix=opt.get("seq_prefix", False),
            )
        except OSError as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成", f"已导出 {n} 个文件到：\n{out_dir}")

# ui/character_relation_dialog.py
"""墨码写作 - 人物关系图

中心辐射布局：
- 中心：选定人物
- 内圈：和中心有直接关系的人（正向 + 反向解析 relations 字段）
- 外圈：和"内圈某人"有关系的人（二级）

交互：
- 顶部下拉框切换中心
- 点击任意节点 → 以该人物为新中心
- 「返回」按钮回到上一个中心
- 悬停显示人物卡详情（tooltip）

纯 QPainter 自绘，零依赖。
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QFont,
    QFontMetrics,
)
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QWidget,
    QMessageBox,
)

from ui.theme_style import qss_alpha

# ---------------- 关系类型 → 颜色 ----------------
_REL_COLORS = {
    # 师门（绿）
    "师父": "#4CAF50",
    "师傅": "#4CAF50",
    "老师": "#4CAF50",
    "恩师": "#4CAF50",
    "徒弟": "#4CAF50",
    "弟子": "#4CAF50",
    "学生": "#4CAF50",
    "同门": "#4CAF50",
    "师兄": "#4CAF50",
    "师姐": "#4CAF50",
    "师弟": "#4CAF50",
    "师妹": "#4CAF50",
    # 挚友（粉）
    "挚友": "#EC4899",
    "好友": "#EC4899",
    "朋友": "#EC4899",
    "闺蜜": "#EC4899",
    "哥们": "#EC4899",
    # 宿敌（红）
    "宿敌": "#E74C3C",
    "仇人": "#E74C3C",
    "敌人": "#E74C3C",
    "对手": "#E74C3C",
    "死敌": "#E74C3C",
    # 家人（紫）
    "父亲": "#8B5CF6",
    "母亲": "#8B5CF6",
    "父母": "#8B5CF6",
    "儿子": "#8B5CF6",
    "女儿": "#8B5CF6",
    "哥哥": "#8B5CF6",
    "姐姐": "#8B5CF6",
    "弟弟": "#8B5CF6",
    "妹妹": "#8B5CF6",
    "兄弟": "#8B5CF6",
    "姐妹": "#8B5CF6",
    "祖父": "#8B5CF6",
    "祖母": "#8B5CF6",
    "孙子": "#8B5CF6",
    "孙女": "#8B5CF6",
    # 恋人（橙）
    "妻子": "#F59E0B",
    "丈夫": "#F59E0B",
    "爱人": "#F59E0B",
    "恋人": "#F59E0B",
    "情侣": "#F59E0B",
    "未婚妻": "#F59E0B",
    "未婚夫": "#F59E0B",
}
_DEFAULT_REL_COLOR = "#64748B"


# ---------------- 反向关系 ----------------
_REVERSE_REL = {
    "师父": "徒弟",
    "师傅": "徒弟",
    "老师": "学生",
    "恩师": "徒弟",
    "徒弟": "师父",
    "弟子": "师父",
    "学生": "老师",
    "师兄": "师弟/师妹",
    "师姐": "师弟/师妹",
    "师弟": "师兄/师姐",
    "师妹": "师兄/师姐",
    "父亲": "子女",
    "母亲": "子女",
    "父母": "子女",
    "儿子": "父母",
    "女儿": "父母",
    "哥哥": "弟弟/妹妹",
    "姐姐": "弟弟/妹妹",
    "弟弟": "哥哥/姐姐",
    "妹妹": "哥哥/姐姐",
    "祖父": "孙子/孙女",
    "祖母": "孙子/孙女",
    "孙子": "祖父/祖母",
    "孙女": "祖父/祖母",
    "妻子": "丈夫",
    "丈夫": "妻子",
}


def _rel_color(rel: str) -> str:
    rel = (rel or "").strip()
    if not rel:
        return _DEFAULT_REL_COLOR
    if rel in _REL_COLORS:
        return _REL_COLORS[rel]
    for key, color in _REL_COLORS.items():
        if key in rel or rel in key:
            return color
    return _DEFAULT_REL_COLOR


def _reverse_rel(rel: str) -> str:
    rel = (rel or "").strip()
    if not rel:
        return rel
    if rel in _REVERSE_REL:
        return _REVERSE_REL[rel]
    for key, val in _REVERSE_REL.items():
        if key in rel:
            return val
    return rel  # 默认对称（挚友/宿敌等）


def parse_relations(text: str) -> List[Tuple[str, str]]:
    """解析人物卡 relations 字段，返回 [(关系, 目标名), ...]。

    每行一条，格式："关系：目标名"。
    冒号支持全角 "：" / 半角 ":" / "﹕"。
    """
    if not text:
        return []
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in ("：", ":", "﹕"):
            idx = line.find(sep)
            if idx > 0:
                rel = line[:idx].strip()
                target = line[idx + 1 :].strip()
                if rel and target:
                    result.append((rel, target))
                break
    return result


def build_relation_map(chars: List[dict]) -> Dict[str, List[Tuple[str, str]]]:
    """返回 {人物名: [(关系, 目标名), ...]}。"""
    result = {}
    for c in chars:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        rels = parse_relations(c.get("relations") or "")
        if rels:
            result[name] = rels
    return result


# ---------------- 图节点 ----------------


@dataclass
class _Node:
    name: str
    role: str
    ring: int  # 1=内圈 / 2=外圈
    angle: float  # 弧度
    rel: str  # 相对父节点的关系
    parent: str = ""  # 外圈节点：连向哪个内圈节点
    x: float = 0
    y: float = 0
    r: float = 30


class _RelationGraph(QWidget):
    """中心辐射图。"""

    node_clicked = Signal(str)

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme or {}
        self._center = ""
        self._chars: Dict[str, dict] = {}
        self._rel_map: Dict[str, List[Tuple[str, str]]] = {}
        self._nodes: List[_Node] = []
        self._hover_name = ""
        self.setMouseTracking(True)
        self.setMinimumSize(600, 480)

    def set_center(self, center, chars, rel_map):
        self._center = center or ""
        self._chars = chars or {}
        self._rel_map = rel_map or {}
        self._hover_name = ""
        self._nodes = []
        if self._center:
            self._recompute_nodes()
        self._layout_nodes()
        self.update()

    def _resolve_name(self, raw: str) -> str:
        """relations 里的名字 → 实际人物卡名（支持别名）。"""
        raw = (raw or "").strip()
        if not raw:
            return ""
        if raw in self._chars:
            return raw
        for name, c in self._chars.items():
            for a in c.get("aliases") or []:
                if str(a).strip() == raw:
                    return name
        return raw

    def _rel_role(self, name: str) -> str:
        c = self._chars.get(name)
        if not c:
            return ""
        return (c.get("role") or "").strip()

    def _recompute_nodes(self):
        center = self._center
        # 1) 正向：中心自己填的 relations
        direct_raw = list(self._rel_map.get(center, []))
        # 2) 反向：别人 relations 提到中心
        for src, rels in self._rel_map.items():
            if src == center:
                continue
            for rel, tgt in rels:
                if self._resolve_name(tgt) == center:
                    direct_raw.append((_reverse_rel(rel), src))

        # 去重
        seen = set()
        direct = []
        for rel, name in direct_raw:
            if name in seen or name == center:
                continue
            seen.add(name)
            direct.append((rel, name))

        # 内圈最多 12 个
        direct = direct[:12]

        nodes = []
        n = len(direct)
        for i, (rel, name) in enumerate(direct):
            angle = -math.pi / 2 + 2 * math.pi * i / max(1, n)
            nodes.append(
                _Node(
                    name=name,
                    role=self._rel_role(name),
                    ring=1,
                    angle=angle,
                    rel=rel,
                    r=36,
                )
            )

        # 3) 外圈：内圈人物的关系人（排除中心和内圈已有的）
        inner_names = {x.name for x in nodes}
        seen_outer = set()
        outer_raw = []  # (parent_name, rel, name)
        for _, inner_name in direct:
            # 正向
            for rel, tgt in self._rel_map.get(inner_name, []):
                resolved = self._resolve_name(tgt)
                if resolved in inner_names or resolved == center:
                    continue
                if resolved in seen_outer:
                    continue
                seen_outer.add(resolved)
                outer_raw.append((inner_name, rel, resolved))
            # 反向
            for src, rels in self._rel_map.items():
                if src in inner_names or src == center:
                    continue
                for rel, tgt in rels:
                    if self._resolve_name(tgt) == inner_name:
                        if src in seen_outer:
                            continue
                        seen_outer.add(src)
                        outer_raw.append((inner_name, _reverse_rel(rel), src))

        # 4) 每个内圈节点的外圈子节点分布
        by_parent: Dict[str, list] = {}
        for parent, rel, name in outer_raw:
            by_parent.setdefault(parent, []).append((rel, name))

        inner_by_name = {x.name: x for x in nodes}
        for parent_name, children in by_parent.items():
            parent = inner_by_name.get(parent_name)
            if parent is None:
                continue
            m = len(children)
            # 每个父节点最多 5 个外圈子节点
            children = children[:5]
            m = len(children)
            if m == 1:
                offsets = [0.0]
            else:
                spread = 1.05  # 约 60°
                offsets = [-spread / 2 + spread * i / (m - 1) for i in range(m)]
            for (rel, name), off in zip(children, offsets):
                nodes.append(
                    _Node(
                        name=name,
                        role=self._rel_role(name),
                        ring=2,
                        angle=parent.angle + off,
                        rel=rel,
                        parent=parent.name,
                        r=26,
                    )
                )

        self._nodes = nodes

    def _layout_nodes(self):
        if not self._nodes:
            return
        cx = self.width() / 2
        cy = self.height() / 2
        max_r = min(self.width(), self.height()) / 2 - 70
        r1 = max_r * 0.55
        r2 = max_r * 0.96
        for n in self._nodes:
            r = r1 if n.ring == 1 else r2
            n.x = cx + r * math.cos(n.angle)
            n.y = cy + r * math.sin(n.angle)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_nodes()

    # ---------------- 绘制 ----------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        t = self._theme
        editor_bg = QColor(t.get("editor_bg", "#fcfaf4"))
        text_c = QColor(t.get("text", "#3b342a"))
        muted = QColor(t.get("muted", "#9a8e76"))
        accent = QColor(t.get("accent", "#b08a4e"))

        p.fillRect(self.rect(), editor_bg)

        if not self._nodes:
            p.setPen(muted)
            f = QFont(self.font().family(), 12)
            p.setFont(f)
            msg = (
                f"「{self._center}」还没有填写关系。\n\n"
                "请到「人物」面板打开这个角色卡，\n"
                "在「人物关系」字段里填写，每行一条：\n"
                "    师父：李长生\n"
                "    挚友：苏晚棠\n"
                "    宿敌：赵无咎"
            )
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, msg)
            p.end()
            return

        cx = self.width() / 2
        cy = self.height() / 2
        max_r = min(self.width(), self.height()) / 2 - 70
        r1 = max_r * 0.55
        r2 = max_r * 0.96

        # 同心圆辅助线
        for r in (r1, r2):
            pen = QPen(accent)
            pen.setStyle(Qt.DashLine)
            pen.setWidthF(1.0)
            col = QColor(accent)
            col.setAlpha(0x1A)
            pen.setColor(col)
            p.setPen(pen)
            p.drawEllipse(QPointF(cx, cy), r, r)

        # 内圈边
        for n in self._nodes:
            if n.ring != 1:
                continue
            col = QColor(_rel_color(n.rel))
            col.setAlpha(150)
            pen = QPen(col)
            pen.setWidthF(2.0)
            p.setPen(pen)
            p.drawLine(QPointF(cx, cy), QPointF(n.x, n.y))

        # 外圈边
        inner_by_name = {x.name: x for x in self._nodes if x.ring == 1}
        for n in self._nodes:
            if n.ring != 2 or n.parent not in inner_by_name:
                continue
            inner = inner_by_name[n.parent]
            col = QColor(_rel_color(n.rel))
            col.setAlpha(100)
            pen = QPen(col)
            pen.setWidthF(1.2)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(inner.x, inner.y), QPointF(n.x, n.y))

        # 节点
        bg_c = QColor(editor_bg)
        for n in self._nodes:
            fill = QColor(_rel_color(n.rel))
            if n.ring == 2:
                fill.setAlpha(180)
            p.setBrush(fill)
            p.setPen(QPen(editor_bg, 2))
            p.drawEllipse(QPointF(n.x, n.y), n.r, n.r)

            # 名字颜色按「填充色与底色合成后的实际亮度」自适应：
            # 外圈节点 alpha=180，深色主题下实际呈色偏暗；黄/橙等浅色
            # 关系节点用深字，其余用白字，保证对比度。
            a = fill.alpha() / 255.0
            eff_r = fill.red() * a + bg_c.red() * (1 - a)
            eff_g = fill.green() * a + bg_c.green() * (1 - a)
            eff_b = fill.blue() * a + bg_c.blue() * (1 - a)
            lum = 0.299 * eff_r + 0.587 * eff_g + 0.114 * eff_b
            node_fg = QColor("#ffffff") if lum < 150 else QColor("#3b342a")

            # 悬停光圈跟随主题正文色，深色底上不再用死白
            if n.name == self._hover_name:
                hover_c = QColor(t.get("text", "#3b342a"))
                hover_c.setAlpha(0xCC)
                pen = QPen(hover_c, 3)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(n.x, n.y), n.r + 3, n.r + 3)

            # 名字
            font = QFont(self.font().family(), 12 if n.ring == 1 else 10)
            font.setBold(True)
            p.setFont(font)
            p.setPen(node_fg)
            name = n.name
            if len(name) > 5:
                name = name[:4] + "…"
            name_rect = QRectF(n.x - n.r, n.y - 10, n.r * 2, 16)
            p.drawText(name_rect, Qt.AlignHCenter | Qt.AlignVCenter, name)

            # 关系标签（内圈）
            if n.ring == 1 and n.rel:
                font2 = QFont(self.font().family(), 9)
                p.setFont(font2)
                c2 = QColor(node_fg)
                c2.setAlpha(200)
                p.setPen(c2)
                rel = n.rel if len(n.rel) <= 4 else n.rel[:3] + "…"
                rel_rect = QRectF(n.x - n.r, n.y + 2, n.r * 2, 14)
                p.drawText(rel_rect, Qt.AlignHCenter | Qt.AlignVCenter, rel)

        # 中心节点：文字/描边取主题 accent_fg（没有配置时按 accent 亮度
        # 自适应），深色主题下 accent 偏浅时硬编码白字会糊成一团。
        accent_c = QColor(accent)
        default_fg = "#ffffff" if accent_c.lightness() < 150 else "#3b342a"
        center_fg = QColor(t.get("accent_fg") or default_fg)
        center_edge = QColor(center_fg)
        center_edge.setAlpha(0xCC)

        center_r = 52
        p.setBrush(accent)
        p.setPen(QPen(center_edge, 3))
        p.drawEllipse(QPointF(cx, cy), center_r, center_r)

        p.setPen(center_fg)
        font = QFont(self.font().family(), 16)
        font.setBold(True)
        p.setFont(font)
        cname = self._center
        if len(cname) > 6:
            cname = cname[:5] + "…"
        p.drawText(
            QRectF(cx - center_r, cy - 16, center_r * 2, 20),
            Qt.AlignHCenter | Qt.AlignVCenter,
            cname,
        )

        role = self._rel_role(self._center)
        if role:
            font2 = QFont(self.font().family(), 10)
            p.setFont(font2)
            c2 = QColor(center_fg)
            c2.setAlpha(0xCC)
            p.setPen(c2)
            if len(role) > 6:
                role = role[:5] + "…"
            p.drawText(
                QRectF(cx - center_r, cy + 2, center_r * 2, 16),
                Qt.AlignHCenter | Qt.AlignVCenter,
                role,
            )

        p.end()

    # ---------------- 交互 ----------------

    def _hit(self, x: float, y: float) -> str:
        for n in self._nodes:
            dx = x - n.x
            dy = y - n.y
            if dx * dx + dy * dy <= n.r * n.r:
                return n.name
        return ""

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        name = self._hit(pos.x(), pos.y())
        if name != self._hover_name:
            self._hover_name = name
            self.update()
            if name:
                self.setCursor(Qt.PointingHandCursor)
                c = self._chars.get(name)
                if c:
                    self.setToolTip(self._format_tooltip(c))
                else:
                    self.setToolTip(f"{name}\n（没有人物卡）")
            else:
                self.setCursor(Qt.ArrowCursor)
                self.setToolTip("")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_name:
            self._hover_name = ""
            self.update()
        self.setCursor(Qt.ArrowCursor)
        self.setToolTip("")
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        name = self._hit(pos.x(), pos.y())
        if name:
            self.node_clicked.emit(name)
            event.accept()
            return
        super().mousePressEvent(event)

    def _format_tooltip(self, c: dict) -> str:
        lines = []
        name = (c.get("name") or "").strip()
        role = (c.get("role") or "").strip()
        lines.append(f"【{name}】{f'  {role}' if role else ''}")
        aliases = c.get("aliases") or []
        if isinstance(aliases, list) and aliases:
            lines.append(f"别名：{'、'.join(str(a) for a in aliases[:3])}")
        gender = (c.get("gender") or "").strip()
        age = (c.get("age") or "").strip()
        bits = []
        if gender:
            bits.append(gender)
        if age:
            bits.append(f"{age} 岁")
        if bits:
            lines.append(" · ".join(bits))
        personality = (c.get("personality") or "").strip()
        if personality:
            lines.append(f"性格：{personality[:50]}")
        lines.append("")
        lines.append("点击 → 以此人为中心")
        return "\n".join(lines)


# ---------------- 主对话框 ----------------


class CharacterRelationDialog(QDialog):
    """人物关系图对话框。"""

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work
        self._theme = self._resolve_theme()
        self._chars_list = work.load_characters() or []
        self._chars = {
            (c.get("name") or "").strip(): c
            for c in self._chars_list
            if (c.get("name") or "").strip()
        }
        self._rel_map = build_relation_map(self._chars_list)
        self._history = []
        self._center_name = ""

        self.setObjectName("charRelationDialog")
        self.setWindowTitle(f"人物关系图 - {work.title}")
        self.setMinimumSize(960, 700)

        self._build_ui()
        self._apply_theme()
        self._init_center()

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ed = getattr(p, "editor", None)
            if ed is not None and hasattr(ed, "_theme"):
                return ed._theme or {}
            ws = getattr(p, "workspace", None)
            if ws is not None:
                ed = getattr(ws, "editor", None)
                if ed is not None and hasattr(ed, "_theme"):
                    return ed._theme or {}
            p = p.parent()
        return {}

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("人物关系图")
        title.setObjectName("crTitle")
        head.addWidget(title)
        head.addStretch()

        head.addWidget(QLabel("中心人物："))
        self.center_combo = QComboBox()
        self.center_combo.setMinimumWidth(220)
        for c in self._chars_list:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            role = (c.get("role") or "").strip()
            text = f"{name}  （{role}）" if role else name
            self.center_combo.addItem(text, name)
        self.center_combo.currentIndexChanged.connect(self._on_center_changed)
        head.addWidget(self.center_combo)

        self.back_btn = QPushButton("← 返回")
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self._on_back)
        head.addWidget(self.back_btn)

        self.ai_extract_btn = QPushButton("AI 提取关系")
        self.ai_extract_btn.setToolTip("先用本地规则扫当前章，可再让 AI 深挖")
        self.ai_extract_btn.clicked.connect(self._on_ai_extract)
        head.addWidget(self.ai_extract_btn)

        self.batch_extract_btn = QPushButton("批量提取（区间）…")
        self.batch_extract_btn.setToolTip(
            "对选定章节区间逐章提取关系（可先免费本地扫描，再 AI 深挖）")
        self.batch_extract_btn.clicked.connect(self._on_batch_extract)
        head.addWidget(self.batch_extract_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        head.addWidget(close_btn)
        root.addLayout(head)

        self._graph = _RelationGraph(self._theme, self)
        self._graph.node_clicked.connect(self._on_node_clicked)
        root.addWidget(self._graph, 1)

        legend = QHBoxLayout()
        legend.setSpacing(16)
        legend.addStretch()
        for label, color in (
            ("师门", "#4CAF50"),
            ("挚友", "#EC4899"),
            ("宿敌", "#E74C3C"),
            ("家人", "#8B5CF6"),
            ("恋人", "#F59E0B"),
            ("其他", "#64748B"),
        ):
            item = QHBoxLayout()
            item.setSpacing(5)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
            item.addWidget(dot)
            lab = QLabel(label)
            lab.setObjectName("crLegend")
            item.addWidget(lab)
            legend.addLayout(item)
        legend.addStretch()
        root.addLayout(legend)

    def _init_center(self):
        if not self._chars_list:
            return
        # 优先"主角 / 男主 / 女主"
        for c in self._chars_list:
            role = (c.get("role") or "").strip()
            if "主角" in role or "男主" in role or "女主" in role:
                name = (c.get("name") or "").strip()
                if name:
                    self._set_center(name, record=False)
                    return
        for c in self._chars_list:
            name = (c.get("name") or "").strip()
            if name:
                self._set_center(name, record=False)
                return

    def _set_center(self, name, record=True):
        if not name:
            return
        if record and self._center_name and self._center_name != name:
            self._history.append(self._center_name)
        self._center_name = name
        idx = self.center_combo.findData(name)
        if idx >= 0 and self.center_combo.currentIndex() != idx:
            self.center_combo.blockSignals(True)
            self.center_combo.setCurrentIndex(idx)
            self.center_combo.blockSignals(False)
        self._graph.set_center(name, self._chars, self._rel_map)
        self.back_btn.setEnabled(bool(self._history))

    def _on_center_changed(self, _):
        name = self.center_combo.currentData()
        if name and name != self._center_name:
            self._set_center(name)

    def _on_back(self):
        if not self._history:
            return
        prev = self._history.pop()
        self._set_center(prev, record=False)

    def _on_ai_extract(self):
        """从关系图里直接调 AI 提取。让 workspace 弹流程。"""
        # 关掉对话框再让 workspace 弹（避免两个模态叠在一起）
        ws = None
        p = self.parent()
        while p is not None:
            if hasattr(p, "extract_relations_with_ai"):
                ws = p
                break
            p = p.parent()
        self.accept()
        if ws is not None:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, ws.extract_relations_with_ai)

    def _find_workspace(self):
        ws = None
        p = self.parent()
        while p is not None:
            if hasattr(p, "batch_extract_relations"):
                ws = p
                break
            p = p.parent()
        return ws

    def _on_batch_extract(self):
        """关掉关系图，让 workspace 弹区间批量提取流程。"""
        ws = self._find_workspace()
        self.accept()
        if ws is not None:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, ws.batch_extract_relations)

    def _on_node_clicked(self, name):
        if name in self._chars:
            self._set_center(name)
        else:
            QMessageBox.information(
                self,
                "提示",
                f"「{name}」还没有人物卡。\n\n"
                "如果想追踪这个人物，请先在「人物」面板\n"
                "新建角色卡，并在「人物关系」里填写关系。",
            )

    def _apply_theme(self):
        t = self._theme
        bg = t.get("bg", "#f7f2e8")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        editor_bg = t.get("editor_bg", "#fcfaf4")

        self.setStyleSheet(
            f"#charRelationDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text_c}; background: transparent; }}"
            f"QLabel#crTitle {{ font-size: 18px; font-weight: 700; }}"
            f"QLabel#crLegend {{ font-size: 11.5px; color: {muted}; }}"
            f"QComboBox {{ background-color: {editor_bg}; color: {text_c};"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 5px 10px; font-size: 13px; }}"
            f"QComboBox:hover {{ border-color: {accent}; }}"
            f"QComboBox::drop-down {{ border: none; width: 22px; }}"
            f"QPushButton {{ color: {text_c}; background: transparent;"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 6px 14px; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {accent}; border-color: {accent};"
            f" background: {qss_alpha(accent, 0x14)}; }}"
            f"QPushButton:disabled {{ color: {muted};"
            f" border-color: {qss_alpha(muted, 0x33)}; }}"
        )

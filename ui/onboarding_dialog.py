# ui/onboarding_dialog.py
"""墨码写作 - 新手引导向导。

数据驱动：步骤内容全部写在 STEPS 表里，新增/调整一页只改表，不动渲染逻辑。
- 首次启动（引导未完成且作品画廊为空）自动弹出；
- 「帮助 → 新手引导」可随时重放；
- 每页可挂一个主操作（如「新建一部作品」「配置 AI 接口」），
  动作通过外部传入的 actions 映射执行，对话框本身不碰业务逻辑。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFrame, QScrollArea, QGridLayout,
)

import qtawesome as qta

from ui.theme_style import apply_dialog_theme


# 每条步骤：icon 仅作步骤序号旁的小标识；action_key 为 None 时不显示操作按钮。
STEPS = [
    {
        "title": "欢迎来到墨码写作",
        "icon": "fa5s.feather-alt",
        "paragraphs": [
            "这是一款<b>完全免费</b>的本地网文写作软件：无激活码、无设备绑定、"
            "无功能分级，所有功能开箱即用。",
            "稿子只保存在你自己的电脑上（「我的文档 / 墨码写作」），"
            "<b>不上传、不注册账号、不订阅</b>。",
            "AI 能力完全可选——需要你自备 API Key，不配 Key 也能用完全部"
            "离线写作功能。软件会持续更新，如果它帮到了你，"
            "可以在「帮助 → 爱发电赞助」请作者喝杯咖啡，完全自愿。",
            "接下来用一分钟认识这里，看完即可开写。",
        ],
        "cta": None,
    },
    {
        "title": "第一步：建一部作品",
        "icon": "fa5s.folder-plus",
        "paragraphs": [
            "在作品画廊点虚线卡片（或「新建作品」），填书名、选封面即可，"
            "其他设定以后随时补。",
            "一部作品里可以建「卷」和「章节」，支持拖拽排序、跨卷移动、"
            "多选批量操作；误删的章节和作品都会先进回收站。",
        ],
        "cta": ("新建一部作品", "new_work"),
    },
    {
        "title": "写作台：章节树、编辑器与自动保存",
        "icon": "fa5s.pencil-alt",
        "paragraphs": [
            "左栏章节树、中间是限宽居中的沉浸式编辑器，F11 进入专注模式；"
            "Ctrl+滚轮缩放字号，Ctrl+F 查找替换，Ctrl+PgUp/PgDown 切章。",
            "停笔几秒即自动保存，另有崩溃恢复草稿；应用排版、删除章节前"
            "会自动生成历史快照，每章保留最近 20 份，可双版本对比恢复。",
        ],
        "cta": None,
    },
    {
        "title": "人物、词条与设定",
        "icon": "fa5s.users",
        "paragraphs": [
            "在侧栏建立人物卡（含别名、性格、关系），人名会在正文里按色高亮；"
            "地名、技能、法宝放进高频词库，双击即可插入。",
            "「写作」菜单里还有人物关系图、时间线、世界观、伏笔追踪、"
            "全书出场统计——AI 对话和分析会自动读取这些设定，不用手动粘贴。",
        ],
        "cta": None,
    },
    {
        "title": "外观、快捷键与墨灵形象",
        "icon": "fa5s.palette",
        "paragraphs": [
            "「设置 → 外观」内置 10 套写作主题，包括竹林夜、深海等深色主题，"
            "熬夜写作也不刺眼；正文比例、界面字号都能调。",
            "「设置 → 快捷键」可以查看和自定义全部快捷键，"
            "按自己的肌肉记忆来。",
            "桌面小伙伴墨灵支持<b>换装</b>：右键宠物 → 换装，"
            "可选墨韵、校园、七夕、浅粉四套形象；拖动能换位置，"
            "滚轮可缩放大小。",
        ],
        "cta": ("挑一套喜欢的主题", "appearance_settings"),
    },
    {
        "title": "AI 助手与墨灵",
        "icon": "fa5s.microchip",
        "paragraphs": [
            "所有 AI 功能均已免费开放：在「设置 → AI 接口」填入 Key 即可"
            "（内置 8 家服务商预设，也支持任意兼容端点与本地模型）。"
            "注意 Key 与调用产生的 token 费用归服务商，软件本身不收一分钱。",
            "可用能力：侧边对话、光标续写、快速续写、改写对比、生成梗概、"
            "小说转剧本，以及「写作」菜单里的追读体检、墨灵试读、"
            "一致性检查、伏笔追踪、发布前体检、批量提取等。",
            "墨灵记得住你告诉它的设定，会卡文提醒、章末点评；"
            "不喜欢宠物也可以在「设置 → 墨灵」里完全关闭。",
        ],
        "cta": ("配置 AI 接口", "ai_settings"),
    },
    {
        "title": "稿子安全：备份与开始写作",
        "icon": "fa5s.shield-alt",
        "paragraphs": [
            "所有写入都是「临时文件 → 替换」的原子写入，断电、闪退都不丢稿。",
            "建议开启自动备份（默认每周一次、保留最近 3 份，存在数据目录的"
            " auto_backups 文件夹，可在「设置 → 通用」调整）；"
            "换电脑用「作品 → 备份所有作品」导出 zip。",
            "「设置 → 通用」还能自定义作品存储位置（比如放到同步盘），"
            "切换时会引导你一键迁移全部数据。",
            "引导随时可以从「帮助 → 新手引导」重看。现在，开始你的第一章。",
        ],
        "cta": ("检查备份设置", "backup_settings"),
    },
]


class OnboardingDialog(QDialog):
    """新手引导向导（步骤表驱动）。

    actions: {action_key: callable}，CTA 按钮点击时执行并自动翻到下一页；
    执行/翻页/完成由外部用 config 记录「引导已完成」。
    """

    finished_guide = Signal()

    def __init__(self, parent=None, theme: dict = None, actions: dict = None):
        super().__init__(parent)
        self._steps = STEPS
        self._index = 0
        self._theme = theme or {}
        self._actions = dict(actions or {})

        self.setWindowTitle("新手引导")
        self.setModal(True)
        self.setMinimumSize(620, 460)
        self._build_ui()
        apply_dialog_theme(self, theme=theme)
        self._apply_style()
        self._render()

    # ---------------- UI ----------------

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左：步骤导航
        self._nav = QFrame()
        self._nav.setObjectName("obNav")
        self._nav.setFixedWidth(168)
        nav_lay = QVBoxLayout(self._nav)
        nav_lay.setContentsMargins(16, 20, 12, 16)
        nav_lay.setSpacing(6)
        nav_title = QLabel("导览")
        nav_title.setObjectName("obNavTitle")
        nav_lay.addWidget(nav_title)
        nav_lay.addSpacing(6)

        self._nav_labels = []
        for i, step in enumerate(self._steps):
            row = QFrame()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(8)
            dot = QLabel(str(i + 1))
            dot.setFixedSize(20, 20)
            dot.setAlignment(Qt.AlignCenter)
            dot.setObjectName("obDot")
            name = QLabel(step["title"])
            name.setWordWrap(True)
            name.setObjectName("obNavItem")
            rl.addWidget(dot)
            rl.addWidget(name, 1)
            nav_lay.addWidget(row)
            self._nav_labels.append((dot, name))
        nav_lay.addStretch(1)
        root.addWidget(self._nav)

        # 右：内容区
        right = QFrame()
        right.setObjectName("obBody")
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(22, 20, 22, 16)
        rlay.setSpacing(10)

        top = QHBoxLayout()
        self._icon = QLabel()
        self._icon.setFixedSize(30, 30)
        self._icon.setAlignment(Qt.AlignCenter)
        self._title = QLabel()
        self._title.setObjectName("obTitle")
        top.addWidget(self._icon)
        top.addWidget(self._title, 1)
        rlay.addLayout(top)

        self._progress = QLabel()
        self._progress.setObjectName("obProgress")
        rlay.addWidget(self._progress)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setObjectName("obTextHost")
        self._text_lay = QVBoxLayout(host)
        self._text_lay.setContentsMargins(0, 4, 8, 4)
        self._text_lay.setSpacing(10)
        self._text_lay.addStretch(1)
        self._scroll.setWidget(host)
        rlay.addWidget(self._scroll, 1)

        # CTA 行
        cta_row = QHBoxLayout()
        self.cta_btn = QPushButton()
        self.cta_btn.setProperty("variant", "primary")
        self.cta_btn.setCursor(Qt.PointingHandCursor)
        self.cta_btn.clicked.connect(self._on_cta)
        self.cta_btn.setVisible(False)
        cta_row.addWidget(self.cta_btn)
        cta_row.addStretch(1)
        rlay.addLayout(cta_row)

        # 底部按钮
        bottom = QHBoxLayout()
        self.skip_btn = QPushButton("跳过引导")
        self.skip_btn.setCursor(Qt.PointingHandCursor)
        self.skip_btn.clicked.connect(self._skip)
        bottom.addWidget(self.skip_btn)
        bottom.addStretch(1)

        self.prev_btn = QPushButton("上一步")
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn = QPushButton("下一步")
        self.next_btn.setProperty("variant", "primary")
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.clicked.connect(self._next)
        bottom.addWidget(self.prev_btn)
        bottom.addWidget(self.next_btn)
        rlay.addLayout(bottom)

        root.addWidget(right, 1)

    # ---------------- 渲染 ----------------

    def _render(self):
        step = self._steps[self._index]
        total = len(self._steps)

        try:
            self._icon.setPixmap(
                qta.icon(step["icon"],
                         color=self._theme.get("accent", "#b08a4e"))
                .pixmap(20, 20))
        except Exception:
            self._icon.clear()

        self._title.setText(step["title"])
        self._progress.setText(f"第 {self._index + 1} / {total} 步")

        # 清空正文（保留末尾 stretch）
        # 注意：takeAt 只把控件移出布局，QLabel 仍挂在 host 上继续绘制，
        # 必须 setParent(None) 立即解除父子关系并隐藏，否则快速翻页时
        # 多页文字会叠成残影（deleteLater 的时机不可靠）。
        while self._text_lay.count() > 1:
            item = self._text_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for para in step["paragraphs"]:
            lab = QLabel(para)
            lab.setWordWrap(True)
            lab.setObjectName("obPara")
            self._text_lay.insertWidget(self._text_lay.count() - 1, lab)

        cta = step.get("cta")
        if cta:
            self.cta_btn.setText(cta[0])
            self.cta_btn.setVisible(True)
        else:
            self.cta_btn.setVisible(False)

        self.prev_btn.setEnabled(self._index > 0)
        self.skip_btn.setVisible(self._index < total - 1)
        self.next_btn.setText(
            "开始写作" if self._index == total - 1 else "下一步")

        # 导航高亮
        accent = self._theme.get("accent", "#b08a4e")
        muted = self._theme.get("muted", "#928a76")
        for i, (dot, name) in enumerate(self._nav_labels):
            if i == self._index:
                dot.setStyleSheet(
                    f"background:{accent}; color:#fff;"
                    f"border-radius:10px; font-size:12px;")
                name.setStyleSheet(
                    f"color:{self._theme.get('text', '#3b342a')};"
                    f"font-weight:700; font-size:12.5px;")
            elif i < self._index:
                dot.setStyleSheet(
                    f"background:transparent; color:{accent};"
                    f"border:1px solid {accent}; border-radius:10px;"
                    f"font-size:12px;")
                name.setStyleSheet(f"color:{muted}; font-size:12.5px;")
            else:
                dot.setStyleSheet(
                    f"background:transparent; color:{muted};"
                    f"border:1px solid {muted}; border-radius:10px;"
                    f"font-size:12px;")
                name.setStyleSheet(f"color:{muted}; font-size:12.5px;")

    # ---------------- 交互 ----------------

    def _on_cta(self):
        cta = self._steps[self._index].get("cta")
        if not cta:
            return
        fn = self._actions.get(cta[1])
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
        # 执行动作后顺手前进一步（最后一页则完成）
        self._next()

    def _prev(self):
        if self._index > 0:
            self._index -= 1
            self._render()

    def _next(self):
        if self._index >= len(self._steps) - 1:
            self._complete()
            return
        self._index += 1
        self._render()

    def _skip(self):
        self._complete()

    def _complete(self):
        self.finished_guide.emit()
        self.accept()

    # ---------------- 样式（克制国风：细边、小圆角、无渐变无发光） ----------------

    def _apply_style(self):
        t = self._theme
        bg = t.get("bg", "#f7f2e8")
        panel = t.get("editor_bg", "#fcfaf4")
        nav_bg = t.get("sidebar", "#f1e9d8")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#928a76")
        accent = t.get("accent", "#b08a4e")

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; }}"
            f"#obNav {{ background: {nav_bg}; }}"
            f"#obBody {{ background: {bg}; }}"
            f"#obTextHost {{ background: transparent; }}"
            f"#obNavTitle {{ color: {muted}; font-size: 12px;"
            f" font-weight: 700; letter-spacing: 2px; }}"
            f"#obTitle {{ color: {text_c}; font-size: 18px;"
            f" font-weight: 700; }}"
            f"#obProgress {{ color: {muted}; font-size: 12px; }}"
            f"#obPara {{ color: {text_c}; font-size: 13.5px;"
            f" line-height: 150%; }}"
            f"QScrollArea {{ background: transparent; border: none; }}"
            f"QPushButton {{"
            f" color: {text_c}; background: transparent;"
            f" border: 1px solid {muted}; border-radius: 6px;"
            f" padding: 6px 18px; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {accent};"
            f" border-color: {accent}; }}"
            f"QPushButton:disabled {{ color: {muted}; }}"
            f"QPushButton[variant=\"primary\"] {{"
            f" background: {accent}; color: #fff;"
            f" border: 1px solid {accent}; }}"
            f"QPushButton[variant=\"primary\"]:hover {{"
            f" background: {accent}; color: #fff; }}"
            f"QLabel {{ background: transparent; }}"
        )

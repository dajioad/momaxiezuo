# ui/theme_style.py
"""墨码写作 - 统一控件样式（单一事实来源）。

所有面板 / 对话框在自己的 setStyleSheet 中追加 controls_qss(theme)，
避免“控件自身样式表优先级高于祖先”导致的原生灰蓝控件漏网。
设计语言：圆角 6px、1px 半透明辅助色描边、强调色仅用于主操作/选中态。

关于顶层 QDialog：MainWindow 自己的 setStyleSheet 只能覆盖主窗口的
widget 树，独立顶层对话框（设置、QMessageBox、QFileDialog）不继承
它的样式。因此顶层窗口底色必须由 app 级 controls_qss 负责，
否则启动后首次打开的对话框会是系统默认浅色，与主题不匹配。

关于 Windows 深色模式：原生样式（windowsvista / windows11）下，
QDialog 背景走的是 QPalette.Window，QSS 的 background-color 会被
忽略。独立对话框必须调 apply_dialog_theme()，它会同时设 palette
和 QSS，彻底消除黑底。
"""

import os
import re
import tempfile

from ui.constants import modern_scrollbar_qss

_DEFAULT = {
    "bg": "#f7f2e8",
    "sidebar": "#efe8d8",
    "editor_bg": "#fcfaf4",
    "text": "#3b342a",
    "muted": "#9a8e76",
    "accent": "#b08a4e",
    "accent_fg": "#ffffff",
    "accent_hover": "#c49a5c",
}
# ============================================================
# 语义色板（全局统一）
# ------------------------------------------------------------
# 之前各对话框自己写 #e74c3c / #EF4444 / #c0392b 三套红，
# #F59E0B / #b07818 两套橙。切到一致性检查、追读体检、校对结果
# 三个对话框会看到"不完全一样"的红橙。这里统一为唯一来源。
# ============================================================
SEMANTIC_OK = "#4CAF50"  # 绿：良好/成功/已完成
SEMANTIC_WARN = "#F59E0B"  # 橙：警告/注意/需确认
SEMANTIC_BAD = "#E74C3C"  # 红：错误/删除/敏感词
SEMANTIC_INFO = "#3A6EA5"  # 蓝：信息/提示/引号

# ============================================================
# 设计令牌（2026 风格：成体系的间距 / 圆角 / 控件高度）
# ------------------------------------------------------------
# 全站对话框与面板只允许从这套刻度取值，禁止再写随意数字。
# 间距 4px 基准栅格；圆角分三档：小 6 / 控件 9 / 卡片 10-12。
# ============================================================
SPACE_XXS = 4  # 紧凑内距、图标与文字
SPACE_XS = 6  # 小间距
SPACE_SM = 8  # 控件间
SPACE_MD = 10  # 常规间距 / 对话框内
SPACE_LG = 14  # 对话框外边距（水平）
SPACE_XL = 18  # 大外边距

DLG_MARGIN_H = 16  # 对话框左右外边距
DLG_MARGIN_V = 14  # 对话框上下外边距
DLG_SPACING = 10  # 对话框主布局间距

RADIUS_SM = 6  # 小标签 / item
RADIUS_CTL = 9  # 按钮 / 输入框
RADIUS_CARD = 12  # 卡片 / 分组框 / 列表 / 菜单

CONTROL_HEIGHT = 32  # 单行输入控件统一高度
BUTTON_HEIGHT = 34  # 主操作按钮高度（QSS 用 min-height 约束）

# ============================================================
# 字号令牌（px）
# ------------------------------------------------------------
# 全项目统一取这几档。之前的字号散落在 11 / 11.5 / 12 / 12.5 / 13
# 之间，缺少统一刻度。相近层级的控件字号不一致时，视觉上会有
# "跳"的感觉，但只差 0.5-1px 很难一眼看出——不好调也不好 review。
#
# 新代码应直接引用这些常量；老代码逐步迁移。
# 使用建议：
#   FONT_XS  → 次要提示、计数标签、脚注
#   FONT_SM  → 说明文字、辅助信息、次级标签
#   FONT_MD  → 正文、列表项、按钮
#   FONT_LG  → 强调标题、面板标题
#   FONT_XL  → 对话框大标题、卡片主数字
# ============================================================
FONT_XS = 11
FONT_SM = 12
FONT_MD = 13
FONT_LG = 14
FONT_XL = 16
# ============================================================
# 缓存（进程内）
# ============================================================
# _qss_cache：key = (主题元组, with_tooltip)，value = 生成的 QSS 字符串
# _glyph_path_cache：key = (kind, color)，value = 生成的 PNG 路径
_qss_cache: dict = {}
_glyph_path_cache: dict = {}
_QSS_CACHE_MAX = 4
# 当前生效主题（切主题时更新）。对话框/菜单在弹出瞬间据此生成
# 主题化 QSS —— 全局 app QSS 在启动时只设置一次，切主题不再整体替换
# （替换会让整棵控件树 re-polish，实测单次 200ms+）。
_ACTIVE_THEME = None
_ACTIVE_THEME_VERSION = 0


def set_active_theme(theme: dict):
    global _ACTIVE_THEME, _ACTIVE_THEME_VERSION
    _ACTIVE_THEME = theme or _DEFAULT
    _ACTIVE_THEME_VERSION += 1


def get_active_theme() -> dict:
    return _ACTIVE_THEME or _DEFAULT


def get_active_theme_version() -> int:
    """当前主题的版本号；每次 set_active_theme 自增。

    对话框 / 菜单在弹出时用这个版本号判断"我上次渲染用的是不是同一套主题"，
    避免同主题下重复 setStyleSheet。比 `theme_dict is last_theme` 更稳：
    apply_writing_theme 每次都构造新的 dict 对象，is 比较永远为 False。
    """
    return _ACTIVE_THEME_VERSION


def _t(theme: dict) -> dict:
    t = dict(_DEFAULT)
    if theme:
        t.update({k: v for k, v in theme.items() if v})
    return t


def _icon_dir() -> str:
    """QSS 图标缓存目录（优先用户数据目录，失败退回临时目录）。"""
    try:
        from utils import work_store

        d = os.path.join(work_store.get_data_root(), "_qss_icons")
    except Exception:
        d = os.path.join(tempfile.gettempdir(), "moma_qss_icons")
    os.makedirs(d, exist_ok=True)
    return d


def _glyph(kind: str, color: str) -> str:
    """生成 32px 抗锯齿小图标（对勾/上下箭头/圆点），返回 QSS 可用的本地路径。"""
    key = (kind, color)
    cached = _glyph_path_cache.get(key)
    if cached is not None:
        return cached

    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QPolygonF

    safe = re.sub(r"[^0-9a-zA-Z]", "", color)
    path = os.path.join(_icon_dir(), f"{kind}_{safe}.png")
    if not os.path.exists(path):
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(color)
        if kind == "dot":
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(16, 16), 7.5, 7.5)
        else:
            pen = QPen(col, 4)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            if kind == "check":
                pts = [(8, 17), (13.5, 23), (24, 10)]
            elif kind == "chevron-down":
                pts = [(8, 12), (16, 20), (24, 12)]
            elif kind == "chevron-up":
                pts = [(8, 20), (16, 12), (24, 20)]
            else:
                pts = []
            p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))
        p.end()
        pm.save(path)

    result = path.replace("\\", "/")
    _glyph_path_cache[key] = result
    return result


def qss_alpha(color: str, alpha: int) -> str:
    """QSS 半透明色。注意 Qt QSS 8 位十六进制是 #AARRGGBB（alpha 在前）。"""
    c = (color or "#000000").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return f"#{max(0, min(255, alpha)):02X}{c[:6]}"


def shade(color: str, factor: float) -> str:
    """颜色向白(factor>0)/黑(factor<0)混合，factor∈[-1,1]，返回 #rrggbb。"""
    from PySide6.QtGui import QColor

    c = QColor(color or "#000000")
    if factor >= 0:
        r = c.red() + (255 - c.red()) * factor
        g = c.green() + (255 - c.green()) * factor
        b = c.blue() + (255 - c.blue()) * factor
    else:
        k = 1 + factor
        r, g, b = c.red() * k, c.green() * k, c.blue() * k
    return QColor(int(r), int(g), int(b)).name()


def accent_button_qss(
    accent: str,
    accent_fg: str = "#ffffff",
    accent_hover: str = None,
    *,
    selector: str = 'QPushButton[variant="primary"]',
    padding: str = "7px 20px",
    radius: str = f"{RADIUS_CTL}px",
    bold: bool = True,
) -> str:
    """强调色主按钮：纯色扁平填充，hover/pressed 用色阶变化区分状态。"""
    ah = accent_hover or shade(accent, 0.16)
    edge = shade(accent, -0.18)
    edge_lo = shade(accent, -0.28)
    weight = " font-weight: 600;" if bold else ""
    return f"""
{selector} {{
    color: {accent_fg};{weight}
    background-color: {accent};
    border: 1px solid {edge};
    border-radius: {radius}; padding: {padding};
    min-height: 18px; }}
{selector}:hover {{
    color: {accent_fg};
    background-color: {ah};
    border: 1px solid {edge}; }}
{selector}:pressed {{
    color: {accent_fg};
    background-color: {shade(accent, -0.18)};
    border: 1px solid {edge_lo}; }}
{selector}:disabled {{
    color: {qss_alpha(accent_fg, 0x99)};
    background-color: {qss_alpha(accent, 0x55)};
    border: 1px solid {qss_alpha(accent, 0x33)}; }}"""


def neutral_button_qss(
    theme: dict,
    *,
    selector: str = "QPushButton",
    padding: str = "7px 20px",
    radius: str = f"{RADIUS_CTL}px",
) -> str:
    """次按钮（中性）：与背景同系的微沉底色 + 半透明描边。

    用于对话框 ButtonBox 里的「取消 / 关闭」，与主按钮形成层级，
    不再整排染成强调色。
    """
    from PySide6.QtGui import QColor

    t = _t(theme)
    editor_bg, text, muted, accent = (
        t["editor_bg"],
        t["text"],
        t["muted"],
        t["accent"],
    )
    if QColor(editor_bg).lightness() > 150:
        solid = shade(editor_bg, -0.03)
    else:
        solid = shade(editor_bg, 0.07)
    edge = qss_alpha(muted, 0x44)
    return f"""
{selector} {{
    color: {text}; background-color: {solid};
    border: 1px solid {edge};
    border-radius: {radius}; padding: {padding};
    min-height: 18px; }}
{selector}:hover {{
    color: {accent}; border-color: {accent};
    background-color: {qss_alpha(accent, 0x14)}; }}
{selector}:pressed {{
    color: {accent}; border-color: {shade(accent, -0.2)};
    background-color: {qss_alpha(accent, 0x2e)}; }}
{selector}:disabled {{
    color: {muted}; border-color: {qss_alpha(muted, 0x33)};
    background-color: {qss_alpha(muted, 0x18)}; }}"""


def ghost_button_qss(
    theme: dict,
    *,
    selector: str = 'QPushButton[variant="ghost"]',
    padding: str = "7px 18px",
    radius: str = f"{RADIUS_CTL}px",
) -> str:
    """幽灵按钮：透明底 + 强调色描边 + 强调色文字。

    用于与主按钮并排的次级正向操作（如「预览」「先看看」）。
    """
    t = _t(theme)
    text, muted, accent = t["text"], t["muted"], t["accent"]
    return f"""
{selector} {{
    color: {accent}; background: transparent;
    border: 1px solid {qss_alpha(accent, 0x66)};
    border-radius: {radius}; padding: {padding};
    min-height: 18px; font-weight: 600; }}
{selector}:hover {{
    color: {accent}; background-color: {qss_alpha(accent, 0x14)};
    border: 1px solid {accent}; }}
{selector}:pressed {{
    background-color: {qss_alpha(accent, 0x28)};
    border: 1px solid {shade(accent, -0.2)}; }}
{selector}:disabled {{
    color: {muted}; border-color: {qss_alpha(muted, 0x33)};
    background: transparent; }}"""


def danger_button_qss(
    theme: dict,
    *,
    selector: str = 'QPushButton[variant="danger"]',
    padding: str = "7px 18px",
    radius: str = f"{RADIUS_CTL}px",
) -> str:
    """危险按钮（删除 / 清空 / 彻底删除）：红系实底。"""
    t = _t(theme)
    bad = SEMANTIC_BAD
    fg = "#ffffff"
    return f"""
{selector} {{
    color: {fg}; background-color: {bad};
    border: 1px solid {shade(bad, -0.18)};
    border-radius: {radius}; padding: {padding};
    min-height: 18px; font-weight: 600; }}
{selector}:hover {{ background-color: {shade(bad, 0.12)};
    border: 1px solid {shade(bad, -0.18)}; }}
{selector}:pressed {{ background-color: {shade(bad, -0.18)}; }}
{selector}:disabled {{
    color: {qss_alpha(fg, 0x99)}; background-color: {qss_alpha(bad, 0x55)};
    border: 1px solid {qss_alpha(bad, 0x33)}; }}"""


def scrollbar_qss(theme: dict = None) -> str:
    """按主题明暗返回现代细滚动条 QSS，供自带样式表的控件合并使用。"""
    from PySide6.QtGui import QColor

    bg = (theme or _DEFAULT).get("bg", _DEFAULT["bg"])
    if QColor(bg).lightness() > 150:
        return modern_scrollbar_qss("rgba(0, 0, 0, 0.20)", "rgba(0, 0, 0, 0.40)")
    return modern_scrollbar_qss(
        "rgba(255, 255, 255, 0.25)", "rgba(255, 255, 255, 0.48)"
    )


def controls_qss(theme: dict = None, *, with_tooltip: bool = True) -> str:
    """全套基础控件 QSS。嵌入到任意 widget 的样式表中即可统一其子控件。

    本函数是注入到 QApplication 的（见 main_window.apply_app_theme），
    因此这里设置的规则对**所有顶层窗口**生效，包括独立打开的
    QDialog（设置、QMessageBox、QFileDialog、QInputDialog 等）。
    """
    t = _t(theme)
    cache_key = (tuple(sorted((k, str(v)) for k, v in t.items())), with_tooltip)
    cached = _qss_cache.get(cache_key)
    if cached is not None:
        return cached

    bg, sidebar = t["bg"], t["sidebar"]
    editor_bg, text = t["editor_bg"], t["text"]
    muted, accent = t["muted"], t["accent"]
    accent_fg, accent_hover = t["accent_fg"], t["accent_hover"]
    border = qss_alpha(muted, 0x55)

    sb_qss = scrollbar_qss(t)

    ico_down = _glyph("chevron-down", muted)
    ico_up = _glyph("chevron-up", muted)
    ico_check = _glyph("check", accent_fg)
    ico_dot = _glyph("dot", accent_fg)

    # 次按钮的扁平底色：比背景稍亮/稍沉一档，靠描边与留白区分层次。
    # 浅色主题下 editor_bg 现在是纯白，原来的 shade(+0.30) 会把按钮
    # 和背景糊成一片；改成微微沉一点（-0.02）。深色主题稍微亮一点。
    from PySide6.QtGui import QColor

    if QColor(editor_bg).lightness() > 150:
        btn_solid = shade(editor_bg, -0.02)
    else:
        btn_solid = shade(editor_bg, 0.06)
    btn_edge = qss_alpha(muted, 0x44)
    # 卡片底：窗口(bg) 最沉、卡片用 editor_bg 提亮一档；
    # 二者太接近时（米白主题 bg=#f7f2e8 / editor=#fcfaf4）加极淡描边
    # 区分，不再像以前那样各容器各画一套灰。
    card_bg = editor_bg

    primary_qss = accent_button_qss(accent, accent_fg, accent_hover, bold=True)
    ghost_qss = ghost_button_qss(t)
    danger_qss = danger_button_qss(t)

    result = f"""
/* ===== 去掉 Windows 原生蓝色焦点虚线/焦点框（焦点态由各控件自己的描边表达） ===== */
QWidget:focus {{ outline: none; }}

/* ===== 顶层窗口底色 =====
   app 级 QSS 才能覆盖独立顶层 QDialog（设置、QMessageBox、QFileDialog）。
   MainWindow 自己的 setStyleSheet 优先级更高，不会被这些覆盖。
   QInputDialog / QMessageBox 内部有嵌套 QWidget 容器，
   单靠 QDialog 一条会漏掉，需要显式列出子控件。 */
QDialog, QInputDialog, QMessageBox, QFileDialog {{
    background-color: {bg};
    color: {text};
}}
QInputDialog > QWidget,
QMessageBox > QWidget {{
    background-color: transparent;
}}
QDialog QLabel,
QInputDialog QLabel,
QMessageBox QLabel,
QFileDialog QLabel {{
    color: {text};
    background: transparent;
}}
QDialogButtonBox {{
    background: transparent;
}}

/* ===== 滚动区透明 =====
   滚动区默认会绘制不透明的 viewport，把它后面的窗口底色挡住。
   这里强制透明，让 QDialog 的主题底色透出来；带自绘背景的面板
   用自己的 setStyleSheet 覆写，不受影响。 */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}

/* ===== 文本标签：深色主题下普通 QLabel 默认黑字不可见，统一跟随正文色 ===== */
QLabel {{ color: {text}; background: transparent; }}
QLabel:disabled {{ color: {muted}; }}

/* ===== 按钮：扁平次按钮（纯色 + 描边）；variant=primary 强调主按钮 ===== */
QPushButton {{
    color: {text};
    background-color: {btn_solid};
    border: 1px solid {btn_edge};
    border-radius: 9px; padding: 7px 16px;
    min-height: 18px; }}
QPushButton:hover {{
    color: {accent}; border-color: {accent};
    background-color: {qss_alpha(accent, 0x14)}; }}
QPushButton:pressed {{
    color: {accent}; border-color: {shade(accent, -0.2)};
    background-color: {qss_alpha(accent, 0x2e)}; }}
QPushButton:disabled {{
    color: {muted}; border-color: {qss_alpha(muted, 0x33)};
    background-color: {qss_alpha(muted, 0x18)}; }}
QPushButton:focus {{ border-color: {accent}; }}
{primary_qss}
{ghost_qss}
{danger_qss}
QPushButton[variant="flat"] {{
    background: transparent; border: none; border-radius: 7px;
    padding: 6px 10px; color: {text}; min-height: 0px; }}
QPushButton[variant="flat"]:hover {{ background-color: {qss_alpha(accent, 0x16)}; color: {text}; }}
QToolButton {{
    color: {text}; background: transparent;
    border: none; border-radius: 7px; padding: 4px; }}
QToolButton:hover {{ background-color: {qss_alpha(accent, 0x16)}; }}
QToolButton:pressed {{ background-color: {qss_alpha(accent, 0x2c)}; }}
QToolButton:checked {{ background-color: {qss_alpha(accent, 0x22)}; color: {accent}; }}
QToolButton:focus {{ border: 1px solid {qss_alpha(accent, 0x66)}; }}

/* ===== 输入类控件 =====
   用 palette(text) / palette(base) / palette(highlight) 而不是具体颜色：
   本 QSS 只在启动时注入一次，切主题不再替换。硬编码颜色会冻结在启动
   主题；palette(...) 是 Qt QSS 的动态语法，每次绘制时从当前 palette
   读取 —— palette 由 apply_app_theme 随主题更新，冻结的 QSS 也能跟随。 */
/* 多行编辑：不约束高度，圆角与内距稍宽，适合长文阅读/写作 */
QTextEdit, QPlainTextEdit {{
    color: palette(text);
    background-color: palette(base);
    border: 1px solid {border}; border-radius: 10px;
    padding: 8px 12px;
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text); }}
QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {accent}; }}
QTextEdit:disabled, QPlainTextEdit:disabled {{
    color: {muted}; background-color: {qss_alpha(muted, 0x18)}; }}
/* 单行输入：统一 32px 高（min-height 含 padding+border） */
QLineEdit, QSpinBox, QDoubleSpinBox,
QComboBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
    color: palette(text);
    background-color: palette(base);
    border: 1px solid {border}; border-radius: 9px;
    padding: 5px 12px; min-height: 20px;
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text); }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
    border: 1px solid {accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled,
QDateEdit:disabled, QTimeEdit:disabled {{
    color: {muted}; background-color: {qss_alpha(muted, 0x18)}; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover,
QDateEdit:hover, QDateTimeEdit:hover {{
    border-color: {qss_alpha(accent, 0x66)}; }}

QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: url("{ico_down}");
    width: 11px; height: 11px; margin-right: 8px; }}
QComboBox::drop-down:hover {{ background-color: {qss_alpha(accent, 0x1a)};
    border-top-right-radius: 9px; border-bottom-right-radius: 9px; }}
QComboBox QAbstractItemView {{
    color: palette(text);
    background-color: palette(base);
    border: 1px solid {border}; border-radius: 10px; padding: 4px;
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
    outline: none; }}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    border: none; background: transparent; width: 20px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {qss_alpha(accent, 0x1a)}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{ico_up}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{ico_down}"); width: 10px; height: 10px; }}

/* ===== 复选/单选（16px，圆角方块/圆点） ===== */
QCheckBox, QRadioButton {{ color: {text}; spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid {qss_alpha(muted, 0x88)};
    background-color: {editor_bg}; border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{
    background-color: {accent}; border-color: {accent};
    image: url("{ico_check}"); }}
QRadioButton::indicator:checked {{
    background-color: {accent}; border: 1px solid {accent};
    image: url("{ico_dot}"); border-radius: 9px; }}

/* ===== 标签页：胶囊 chips 风格（选中=强调色淡底，未选透明） ===== */
QTabWidget::pane {{
    border: none; border-top: 1px solid {qss_alpha(muted, 0x28)};
    background: transparent; top: -1px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background-color: transparent;
    color: {muted};
    border: none; border-radius: 8px;
    padding: 7px 18px; margin: 2px 2px; }}
QTabBar::tab:selected {{
    color: {accent}; font-weight: 600;
    background-color: {qss_alpha(accent, 0x18)}; }}
QTabBar::tab:hover:!selected {{
    color: {text}; background-color: {qss_alpha(muted, 0x12)}; }}

/* ===== 列表 / 树 / 表格：选中态用强调色透明底，杜绝原生蓝 ===== */
QListWidget, QTreeWidget, QTableView, QTableWidget {{
    background-color: {editor_bg}; color: {text};
    border: 1px solid {qss_alpha(muted, 0x22)}; border-radius: 12px;
    outline: none; gridline-color: {qss_alpha(muted, 0x22)};
    alternate-background-color: {qss_alpha(muted, 0x10)};
    selection-background-color: {qss_alpha(accent, 0x2e)};
    selection-color: {text}; }}
QListWidget, QTreeWidget {{
    selection-background-color: {qss_alpha(accent, 0x26)}; }}
QListWidget::item, QTreeWidget::item {{
    border-radius: 6px; padding: 5px 6px; margin: 1px 2px; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: {qss_alpha(accent, 0x14)}; }}
QHeaderView {{ background-color: {sidebar}; border: none; }}
QHeaderView::section:horizontal {{
    color: {text}; background-color: {sidebar};
    border: none; border-bottom: 1px solid {qss_alpha(muted, 0x44)};
    border-right: 1px solid {qss_alpha(muted, 0x22)};
    padding: 8px 10px; font-weight: 600; }}
QHeaderView::section:vertical {{
    color: {muted}; background-color: {sidebar};
    border: none; border-right: 1px solid {qss_alpha(muted, 0x33)};
    padding: 5px 8px; font-weight: normal; }}
QTableCornerButton::section {{
    background-color: {sidebar}; border: none;
    border-bottom: 1px solid {qss_alpha(muted, 0x44)}; }}

/* ===== 分组框（卡片） / 进度条 / 提示 ===== */
QGroupBox {{
    color: {text}; border: 1px solid {qss_alpha(muted, 0x2e)}; border-radius: 12px;
    margin-top: 14px; padding: 16px 14px 12px 14px;
    background-color: {qss_alpha(muted, 0x08)};
    font-weight: 600; }}
QGroupBox::title {{
    subcontrol-origin: margin; left: 14px; padding: 0 6px;
    color: {text}; font-weight: 600; }}
QProgressBar {{
    border: none; border-radius: 5px;
    background-color: {qss_alpha(muted, 0x28)}; text-align: center;
    color: {text}; min-height: 8px; max-height: 14px; }}
QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 5px; }}

/* ===== 卡片：QFrame#card —— 三档层级中的“纸面容器” =====
   窗口底(bg) 最沉，卡片用 editor_bg 提亮一档；
   浮层(菜单/对话框) 由各自 QSS 再提一档。 */
QFrame#card {{
    background-color: {card_bg};
    border: 1px solid {qss_alpha(muted, 0x22)};
    border-radius: 12px; }}
QLabel#cardTitle {{
    color: {text}; font-size: 13px; font-weight: 700;
    background: transparent;
    border-left: 3px solid palette(highlight);
    padding-left: 8px; }}
QLabel#cardDesc {{
    color: {muted}; font-size: 12px;
    background: transparent; }}
/* 分区小标题（卡片之间的分组名，无容器） */
QLabel#sectionTitle {{
    color: {muted}; font-size: 12px; font-weight: 700;
    letter-spacing: 1px; background: transparent; }}
/* 语义文字角色：set role dynamic property 后 polish */
QLabel[role="ok"] {{ color: {SEMANTIC_OK}; }}
QLabel[role="warn"] {{ color: {SEMANTIC_WARN}; }}
QLabel[role="bad"] {{ color: {SEMANTIC_BAD}; }}
QLabel[role="info"] {{ color: {SEMANTIC_INFO}; }}

/* ===== 设置窗口左侧导航 ===== */
QListWidget#settingsNav {{
    background-color: {sidebar};
    border: none; border-right: 1px solid {qss_alpha(muted, 0x22)};
    border-radius: 0px; outline: none;
    padding: 10px 8px; }}
QListWidget#settingsNav::item {{
    color: {muted}; background: transparent;
    border-radius: 9px; padding: 0 12px;
    height: 40px; margin: 1px 0px; }}
QListWidget#settingsNav::item:hover {{
    color: {text}; background-color: {qss_alpha(muted, 0x12)}; }}
QListWidget#settingsNav::item:selected {{
    color: {text}; font-weight: 700;
    background-color: {qss_alpha(accent, 0x1e)}; }}

/* ===== 滑块：细槽 + 强调色圆柄 ===== */
QSlider::groove:horizontal {{
    height: 4px; border-radius: 2px;
    background: {qss_alpha(muted, 0x33)}; }}
QSlider::sub-page:horizontal {{
    background: {accent}; border-radius: 2px; }}
QSlider::add-page:horizontal {{
    background: {qss_alpha(muted, 0x22)}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {editor_bg}; border: 2px solid {accent};
    width: 14px; height: 14px; margin: -7px 0;
    border-radius: 9px; }}
QSlider::handle:horizontal:hover {{ background: {accent}; }}

/* ===== 分隔条手柄：6px 宽容忍点击，hover 才显色（默认隐形） ===== */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:hover {{ background-color: {qss_alpha(accent, 0x35)}; }}

{"" if with_tooltip else "/* QToolTip 省略：冻结的全局表改由 palette 提供提示色，避免深色主题下提示框仍是浅色 */"}
{"" if not with_tooltip else f'''QToolTip {{
    background-color: {editor_bg}; color: {text};
    border: 1px solid {border}; border-radius: 8px; padding: 5px 10px; }}'''}

/* ===== 右键 / 下拉菜单：圆角浮层，选中项强调色淡底 ===== */
QMenu {{
    background-color: {editor_bg}; color: {text};
    border: 1px solid {qss_alpha(muted, 0x33)}; border-radius: 12px;
    padding: 6px; }}
QMenu::item {{
    padding: 7px 22px 7px 14px; border-radius: 7px; color: {text};
    background-color: transparent; }}
QMenu::item:selected {{
    background-color: {qss_alpha(accent, 0x22)}; color: {text}; }}
QMenu::item:disabled {{ color: {muted}; }}
QMenu::separator {{
    height: 1px; background-color: {qss_alpha(muted, 0x33)};
    margin: 6px 10px; }}
QMenu::indicator {{ width: 16px; height: 16px; left: 6px; }}

/* ===== 细滚动条 ===== */
{sb_qss}
"""
    if len(_qss_cache) >= _QSS_CACHE_MAX:
        try:
            _qss_cache.pop(next(iter(_qss_cache)))
        except StopIteration:
            pass
    _qss_cache[cache_key] = result
    return result


# ============================================================
# 顶层对话框通用主题应用
# ============================================================
# 为什么需要：
# 1. PySide6 里 `ClassName { ... }` 选择器对 Python 子类不生效
#    （metaObject()->className() 返回父类 Qt 名，而非 Python 类名）
#    → 必须用 objectName 或通用选择器（QDialog / QLineEdit 等）
# 2. 顶层 QDialog 不继承父 widget 的样式表。
# 3. **关键**：Windows 原生样式（windowsvista / windows11）下，
#    QDialog 背景走的是 QPalette.Window，QSS 的 background-color
#    会被忽略 —— 深色模式下直接呈现纯黑底。
#    → 必须同时设 palette 和 QSS。
# 4. 每个对话框单独写一套 QSS 容易漏
#    → 统一用本函数，一行搞定
# ============================================================


def _resolve_dialog_theme(dialog, theme: dict = None) -> dict:
    """按三级优先拿到主题：显式传入 > parent 链 > app palette 兜底。"""
    if theme and theme.get("bg"):
        return dict(theme)
    # parent 链：找任何带 _theme 属性的 widget
    p = dialog.parent()
    while p is not None:
        t = getattr(p, "_theme", None)
        if isinstance(t, dict) and t.get("bg"):
            return dict(t)
        p = p.parent()
    # app palette 兜底
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPalette

    app = QApplication.instance()
    if app is not None:
        pal = app.palette()
        w = pal.color(QPalette.Window).name()
        b = pal.color(QPalette.Base).name()
        t = pal.color(QPalette.WindowText).name()
        h = pal.color(QPalette.Highlight).name()
        ht = pal.color(QPalette.HighlightedText).name()
        return {
            "bg": w,
            "sidebar": w,
            "editor_bg": b,
            "text": t,
            "muted": t,
            "accent": h,
            "accent_fg": ht,
            "accent_hover": h,
        }
    return {}


def apply_dialog_theme(dialog, theme: dict = None, *, object_name: str = None):
    """给顶层 QDialog 应用主题（palette + QSS 双管齐下）。

    用法：在对话框 __init__ 里，_build_ui() 之后调一次即可：
        apply_dialog_theme(self)

    关键点：
    - Windows 原生样式下，QDialog 背景由 palette.Window 决定，
      QSS 的 background-color 被忽略，不设 palette 会漏黑底。
    - setAutoFillBackground(True) 强制 Qt 用 palette 绘制背景。
    - QSS 用 objectName 选择器（对 Python 子类一定生效）。

    注意：本函数设置的圆角与 controls_qss 保持一致（多行输入 10 /
    单行输入 9 / 列表 12），否则对话框的输入框看起来比主界面更方，
    视觉割裂。ButtonBox 按角色分级：接受动作=实底主按钮，
    取消/关闭=中性次按钮。
    """
    from PySide6.QtWidgets import QDialogButtonBox
    from PySide6.QtGui import QPalette, QColor

    t = _resolve_dialog_theme(dialog, theme)
    if not t:
        return

    bg = t.get("bg", "#f7f2e8")
    editor_bg = t.get("editor_bg", "#fcfaf4")
    text = t.get("text", "#3b342a")
    muted = t.get("muted", "#9a8e76")
    accent = t.get("accent", "#b08a4e")
    accent_fg = t.get("accent_fg", "#ffffff")
    accent_hover = t.get("accent_hover", accent)

    # ---------- 第一步：QPalette（必须，覆盖系统深色） ----------
    pal = dialog.palette()
    pal.setColor(QPalette.Window, QColor(bg))
    pal.setColor(QPalette.WindowText, QColor(text))
    pal.setColor(QPalette.Base, QColor(editor_bg))
    pal.setColor(QPalette.AlternateBase, QColor(bg))
    pal.setColor(QPalette.Text, QColor(text))
    pal.setColor(QPalette.Button, QColor(bg))
    pal.setColor(QPalette.ButtonText, QColor(text))
    pal.setColor(QPalette.Highlight, QColor(accent))
    pal.setColor(QPalette.HighlightedText, QColor(accent_fg))
    pal.setColor(QPalette.ToolTipBase, QColor(editor_bg))
    pal.setColor(QPalette.ToolTipText, QColor(text))
    dialog.setPalette(pal)
    # 强制 Qt 用 palette 绘制背景
    dialog.setAutoFillBackground(True)

    # ---------- 第二步：QSS（细粒度覆盖） ----------
    name = object_name or dialog.objectName()
    if not name:
        name = "ThemedDialog"
        dialog.setObjectName(name)

    dialog.setStyleSheet(
        # 双写 QDialog 类选择器 + objectName 选择器，最大化命中
        f"QDialog {{ background-color: {bg}; color: {text}; }}"
        f"#{name} {{ background-color: {bg}; color: {text}; }}"
        f"QLabel {{ color: {text}; background: transparent; }}"
        f"QTextEdit, QPlainTextEdit {{"
        f"  background-color: {editor_bg}; color: {text};"
        f"  border: 1px solid {qss_alpha(muted, 0x55)};"
        f"  border-radius: 10px; padding: 8px 12px;"
        f"  selection-background-color: {accent};"
        f"  selection-color: {accent_fg};"
        f"}}"
        f"QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,"
        f" QDateEdit, QTimeEdit, QDateTimeEdit {{"
        f"  background-color: {editor_bg}; color: {text};"
        f"  border: 1px solid {qss_alpha(muted, 0x55)};"
        f"  border-radius: 9px; padding: 5px 12px; min-height: 20px;"
        f"  selection-background-color: {accent};"
        f"  selection-color: {accent_fg};"
        f"}}"
        f"QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,"
        f" QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{"
        f"  border: 1px solid {accent};"
        f"}}"
        f"QScrollArea {{ border: none; background: transparent; }}"
        f"QScrollArea > QWidget > QWidget {{ background: transparent; }}"
        # Qt 内部 viewport 的 objectName 固定为 qt_scrollarea_viewport
        f"QWidget#qt_scrollarea_viewport {{ background: transparent; }}"
        f"QListWidget, QTreeWidget {{ background-color: {editor_bg};"
        f"  color: {text}; border: 1px solid {qss_alpha(muted, 0x44)};"
        f"  border-radius: 12px; }}"
    )

    # ---------- 第三步：按钮按角色分级 ----------
    # 只有「确定 / 保存 / 是」等接受动作是强调色实底主按钮；
    # 「取消 / 关闭」用中性次按钮，避免一整排全是主色、没有主次。
    primary_ss = accent_button_qss(
        accent,
        accent_fg,
        accent_hover,
        selector="QPushButton",
        padding="7px 22px",
        bold=True,
    )
    neutral_ss = neutral_button_qss(t, selector="QPushButton", padding="7px 22px")
    for box in dialog.findChildren(QDialogButtonBox):
        for btn in box.buttons():
            role = box.buttonRole(btn)
            if role in (QDialogButtonBox.AcceptRole, QDialogButtonBox.YesRole):
                btn.setStyleSheet(primary_ss)
                btn.setDefault(True)
            else:
                btn.setStyleSheet(neutral_ss)

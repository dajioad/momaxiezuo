"""墨码写作 - 墨玉货币的统一图标（中式古钱币：外圆内方）。

替代以前界面里到处直接写的金币 emoji——emoji 在不同 Windows 版本上
字形不一（有的彩色卡通、有的黑白），且与国风克制的视觉语言冲突。

用法：
    from ui.pet_system.jade_icon import jade_pixmap, jade_icon
    label.setPixmap(jade_pixmap(14))
    btn.setIcon(jade_icon(16))
"""

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPixmap, QPainter, QColor, QRadialGradient, QIcon

_cache: dict = {}

# 金铜色系（与主题 accent #b08a4e 同源，略亮以保证小尺寸辨识度）
_GOLD_LIGHT = QColor("#d8a957")
_GOLD = QColor("#b7893f")
_GOLD_EDGE = QColor("#8a612c")
_HOLE = QColor("#6e4a2f")

# 深底变体（金色实底按钮上普通金币会糊成一片）
_LIGHT_LIGHT = QColor("#fdf3dd")
_LIGHT = QColor("#f0d9a8")
_LIGHT_EDGE = QColor("#c9a25c")
_HOLE_LIGHT = QColor("#9c7a44")


def jade_pixmap(size: int = 16, on_dark: bool = False) -> QPixmap:
    """绘制 size×size 的古钱币（外圆金边、内方孔朱砂棕）。

    on_dark=True 时用浅钱配色，用于金色实底按钮等深色前景。
    """
    size = max(10, min(48, int(size)))
    key = ("pm", size, on_dark)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    # 2x 超采样再缩小，16px 下边缘更圆润
    s = size * 2
    pm = QPixmap(s, s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    margin = s * 0.06
    rect = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)

    # 钱体：径向微渐变，左上略亮，像压出来的金属圆钱
    center = rect.center()
    focal = QPointF(center.x() - rect.width() * 0.22,
                    center.y() - rect.height() * 0.26)
    grad = QRadialGradient(center, rect.width() * 0.72, focal, 0.0)
    if on_dark:
        grad.setColorAt(0.0, _LIGHT_LIGHT)
        grad.setColorAt(0.75, _LIGHT)
        grad.setColorAt(1.0, _LIGHT_EDGE)
    else:
        grad.setColorAt(0.0, _GOLD_LIGHT)
        grad.setColorAt(0.75, _GOLD)
        grad.setColorAt(1.0, _GOLD_EDGE)
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawEllipse(rect)

    # 内方孔（圆角小方块，贴深棕 + 一圈深金内沿，像凿出来的孔）
    hole_w = rect.width() * 0.34
    cx, cy = rect.center().x(), rect.center().y()
    hole = QRectF(cx - hole_w / 2, cy - hole_w / 2, hole_w, hole_w)
    radius = hole_w * 0.14
    p.setBrush(_LIGHT_EDGE if on_dark else _GOLD_EDGE)
    p.drawRoundedRect(hole.adjusted(s * 0.012, s * 0.012,
                                    s * 0.012, s * 0.012),
                      radius, radius)
    p.setBrush(_HOLE_LIGHT if on_dark else _HOLE)
    p.drawRoundedRect(hole, radius, radius)

    p.end()
    out = pm.scaled(
        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out.setDevicePixelRatio(1.0)
    _cache[key] = out
    return out


def jade_icon(size: int = 16, on_dark: bool = False) -> QIcon:
    key = ("ic", size, on_dark)
    cached = _cache.get(key)
    if cached is None:
        cached = QIcon(jade_pixmap(size, on_dark=on_dark))
        _cache[key] = cached
    return cached




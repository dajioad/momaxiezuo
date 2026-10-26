# ui/pet_system/animations.py
"""墨码写作 - 序列帧播放。

素材结构（推荐）：
    ui/pet/{outfit}/{anim_key}/
        frame_00.png
        frame_01.png
        ...

兼容（降级）：
    ui/pet/{outfit}/{anim_key}.png     单张静态图
    ui/pet/{anim_key}/                 平铺布局
    ui/pet/*.png

设计：只负责"找素材 + 加载帧"，播放逻辑在 _PetFace 里。
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from typing import List, Tuple

from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt


_FRAME_RE = re.compile(r"^frame_(\d+)\.(png|webp)$", re.IGNORECASE)
# 每帧显示时长（毫秒）。
# 100ms × 10 帧 = 1 秒循环（偏快）
# 180ms × 10 帧 = 1.8 秒循环（吃鱼慢慢嚼）
_DEFAULT_FRAME_MS = 180

# 序列帧 LRU：旧实现每次切状态/换装都重新 listdir + 解码 + 缩放
# 整套 PNG（SmoothTransformation 缩放很贵）。key 含 outfit，换装后
# 自然加载新条目，旧条目按 LRU 淘汰。容量按"两套外观 × 各动画"
# 取 24，兼顾命中率与 QPixmap 显存占用。
_FRAME_CACHE: "OrderedDict[tuple, List[Tuple[QPixmap, int]]]" = OrderedDict()
_FRAME_CACHE_MAX = 24


def find_anim_dir(pet_root: str, outfit: str, anim_key: str) -> str:
    """定位动画目录。返回空串表示找不到。

    查找顺序：
    1. {pet_root}/{outfit}/{anim_key}/       （旧布局，动作帧）
    2. {pet_root}/{outfit}/anims/{anim_key}/ （蓝鲸动画池）
    3. {pet_root}/{anim_key}/                （平铺布局）
    """
    if not pet_root or not anim_key:
        return ""
    if outfit:
        d = os.path.join(pet_root, outfit, anim_key)
        if os.path.isdir(d):
            return d
        d2 = os.path.join(pet_root, outfit, "anims", anim_key)
        if os.path.isdir(d2):
            return d2
    d = os.path.join(pet_root, anim_key)
    if os.path.isdir(d):
        return d
    return ""


def load_frames(pet_root: str, outfit: str, anim_key: str,
                max_size: Tuple[int, int] = (512, 768)
                ) -> List[Tuple[QPixmap, int]]:
    """加载动画的所有帧。

    返回 [(QPixmap, duration_ms), ...]。
    - 有 frame_NN.png：按序号排序，每帧 100ms
    - 只有单张 png：返回 [(pix, 0)]，0 表示静态
    - 素材不存在：返回 []
    """
    cache_key = (pet_root, outfit, anim_key, tuple(max_size))
    cached = _FRAME_CACHE.get(cache_key)
    if cached is not None:
        _FRAME_CACHE.move_to_end(cache_key)
        return cached

    d = find_anim_dir(pet_root, outfit, anim_key)
    if not d:
        return []

    try:
        names = os.listdir(d)
    except OSError:
        return []

    # 1. 序列帧
    frame_files = []
    for name in names:
        m = _FRAME_RE.match(name)
        if m:
            frame_files.append((int(m.group(1)), name))
    if frame_files:
        frame_files.sort()

        # 读 _durations.json（抽帧脚本会生成）
        durations = []
        _dur_path = os.path.join(d, "_durations.json")
        if os.path.isfile(_dur_path):
            try:
                import json as _json
                with open(_dur_path, "r", encoding="utf-8") as f:
                    _d = _json.load(f)
                if isinstance(_d, list):
                    durations = [int(x) for x in _d]
                elif isinstance(_d, dict) and "durations" in _d:
                    durations = [int(x) for x in _d["durations"]]
            except Exception:
                pass

        frames = []
        for idx, (_, name) in enumerate(frame_files):
            path = os.path.join(d, name)
            lower = name.lower()
            if lower.endswith(".webp"):
                # Qt 的 WebP 解码器不稳定，用 PIL 读了再转 PNG bytes
                try:
                    from PIL import Image as _PILImage
                    from io import BytesIO as _BytesIO
                    img = _PILImage.open(path).convert("RGBA")
                    buf = _BytesIO()
                    img.save(buf, "PNG")
                    buf.seek(0)
                    pix = QPixmap()
                    pix.loadFromData(buf.getvalue(), "PNG")
                except Exception:
                    pix = QPixmap(path)
            else:
                pix = QPixmap(path)
            if pix.isNull():
                continue
            ms = durations[idx] if idx < len(durations) else _DEFAULT_FRAME_MS
            if ms <= 0:
                ms = _DEFAULT_FRAME_MS
            frames.append((_scale_to_fit(pix, max_size), ms))
        if frames:
            _put_frame_cache(cache_key, frames)
        return frames

    # 2. 单张静态图
    for name in sorted(names):
        if not name.lower().endswith(".png"):
            continue
        if name.startswith("_"):
            continue
        pix = QPixmap(os.path.join(d, name))
        if not pix.isNull():
            result = [(_scale_to_fit(pix, max_size), 0)]
            _put_frame_cache(cache_key, result)
            return result
    return []


def _put_frame_cache(key, frames):
    _FRAME_CACHE[key] = frames
    _FRAME_CACHE.move_to_end(key)
    while len(_FRAME_CACHE) > _FRAME_CACHE_MAX:
        _FRAME_CACHE.popitem(last=False)


def _scale_to_fit(pix: QPixmap, max_size: Tuple[int, int]) -> QPixmap:
    w, h = max_size
    if pix.width() <= w and pix.height() <= h:
        return pix
    return pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def has_anim(pet_root: str, outfit: str, anim_key: str) -> bool:
    """快速判断某动画是否有素材。"""
    return bool(find_anim_dir(pet_root, outfit, anim_key))
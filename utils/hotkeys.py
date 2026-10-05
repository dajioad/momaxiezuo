# utils/hotkeys.py
"""墨码写作 - 快捷键注册表与配置管理（纯逻辑，无 Qt 依赖）

设计：
- 一份 DEFAULT_HOTKEYS 作为出厂默认
- HotkeyManager 读写 config 的 [Hotkeys] 段
- 每个动作一个 action_id，值是键序列字符串（QKeySequence.PortableText 格式）
- 空字符串表示禁用该快捷键
- 冲突检测：同一键序列不能绑定两个动作

键序列格式（与 Qt PortableText 一致）：
- "Ctrl+K"  "Ctrl+Shift+H"  "F11"  "Alt+Up"  "Ctrl+PgDown"
- 大小写不敏感比较

注意：
- 这里只管**应用级快捷键**（面板开关、章节切换等）。
- 编辑器内建快捷键（Ctrl+C/V/Z 等）不在管理范围，
  在 BUILTIN_EDITOR_SHORTCUTS 里只读展示，供用户参考。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

_CFG_SECTION = "Hotkeys"


@dataclass(frozen=True)
class HotkeyAction:
    id: str
    label: str
    group: str
    default: str
    desc: str = ""


# 出厂默认。顺序即 UI 分组与显示顺序。
DEFAULT_HOTKEYS: List[HotkeyAction] = [
    # ── 作品与章节 ──
    HotkeyAction("prev_chapter", "上一章", "作品与章节",
                 "Ctrl+PgUp", "切到上一章"),
    HotkeyAction("next_chapter", "下一章", "作品与章节",
                 "Ctrl+PgDown", "切到下一章"),
    HotkeyAction("move_chapter_up", "章节上移", "作品与章节",
                 "Alt+Up", "当前章节在同卷内上移一位"),
    HotkeyAction("move_chapter_down", "章节下移", "作品与章节",
                 "Alt+Down", "当前章节在同卷内下移一位"),
    HotkeyAction("back_to_gallery", "返回作品画廊", "作品与章节",
                 "Ctrl+W", "从写作页回到作品图墙"),

    # ── 编辑 ──
    HotkeyAction("save", "手动保存", "编辑",
                 "Ctrl+S", "立即保存当前章节"),
    HotkeyAction("find", "查找替换", "编辑",
                 "Ctrl+F", "打开/关闭查找替换条"),

    # ── 面板 ──
    HotkeyAction("symbol_panel", "符号面板", "面板",
                 "Ctrl+K", "显示/隐藏符号面板"),
    HotkeyAction("ai_panel", "AI 写作助手", "面板",
                 "Ctrl+J", "显示/隐藏 AI 面板"),
    HotkeyAction("focus_mode", "专注模式", "面板",
                 "F11", "隐藏侧栏进入沉浸写作"),

    # ── AI 工具 ──
    HotkeyAction("rewrite", "AI 改写选中文本", "AI 工具",
                 "Ctrl+Shift+R", "对当前选区发起 AI 改写"),
    HotkeyAction("global_search", "全局搜索", "AI 工具",
                 "Ctrl+Alt+F", "搜索整部作品"),

    # ── 版本 ──
    HotkeyAction("version_history", "历史快照", "版本",
                 "Ctrl+Shift+H", "查看当前章节的历史版本"),

    # ── 其他 ──
    HotkeyAction("quit", "退出程序", "其他",
                 "Ctrl+Q", "退出墨码写作"),
]


_ACTIONS_BY_ID: Dict[str, HotkeyAction] = {a.id: a for a in DEFAULT_HOTKEYS}


def all_actions() -> List[HotkeyAction]:
    return list(DEFAULT_HOTKEYS)


def get_action(action_id: str) -> Optional[HotkeyAction]:
    return _ACTIONS_BY_ID.get(action_id)


def action_groups() -> List[str]:
    """按首次出现的顺序返回所有分组名。"""
    seen: List[str] = []
    for a in DEFAULT_HOTKEYS:
        if a.group not in seen:
            seen.append(a.group)
    return seen


class HotkeyManager:
    """读写快捷键绑定。

    典型用法：
        mgr = HotkeyManager(cfg)
        key = mgr.get("save")           # 当前绑定
        mgr.set("save", "Ctrl+Alt+S")   # 修改（内存）
        mgr.save()                      # 写盘
    """

    def __init__(self, cfg=None):
        self._cfg = cfg
        self._bindings: Dict[str, str] = {}
        self._load_from_defaults()
        if cfg is not None:
            self._load_from_cfg()

    # ---------- 加载 ----------
    def _load_from_defaults(self):
        self._bindings = {a.id: a.default for a in DEFAULT_HOTKEYS}

    def _load_from_cfg(self):
        """从 config 读用户绑定。

        配置里的键是 action_id，值是键序列字符串。
        空字符串是合法值（表示"禁用"）；配置里不存在则用默认值。
        """
        for a in DEFAULT_HOTKEYS:
            raw = self._cfg.get_config(_CFG_SECTION, a.id, None)
            if raw is None:
                continue  # 未保存过 → 保持默认
            self._bindings[a.id] = str(raw).strip()

    # ---------- 保存 ----------
    def save(self):
        if self._cfg is None:
            return
        for aid, key in self._bindings.items():
            self._cfg.set_config(_CFG_SECTION, aid, key, validate=False)

    # ---------- 读写单条 ----------
    def get(self, action_id: str) -> str:
        return self._bindings.get(action_id, "")

    def set(self, action_id: str, key: str):
        self._bindings[action_id] = (key or "").strip()

    def all_bindings(self) -> Dict[str, str]:
        return dict(self._bindings)

    # ---------- 重置 ----------
    def reset_action(self, action_id: str):
        a = _ACTIONS_BY_ID.get(action_id)
        if a:
            self._bindings[action_id] = a.default

    def reset_all(self):
        self._load_from_defaults()

    def clear_all(self):
        for aid in self._bindings:
            self._bindings[aid] = ""

    # ---------- 冲突检测 ----------
    def find_conflict(self, key: str, exclude_id: str) -> Optional[str]:
        """返回与 key 冲突的另一个动作 id；无冲突返回 None。

        key 为空串（禁用）永远不冲突。
        比较大小写不敏感（Qt 的键名大小写一致，但为防御用户手输做归一）。
        """
        key = (key or "").strip().lower()
        if not key:
            return None
        for aid, k in self._bindings.items():
            if aid == exclude_id:
                continue
            if k and k.lower() == key:
                return aid
        return None


# 编辑器内置快捷键（只读展示，不可修改）。
# 这些是 QPlainTextEdit / QTextEdit 的内建行为或编辑器自实现的快捷键，
# 用户不应该也不需要改。
BUILTIN_EDITOR_SHORTCUTS = [
    ("Ctrl+Z", "撤销"),
    ("Ctrl+Y", "重做（部分平台为 Ctrl+Shift+Z）"),
    ("Ctrl+X", "剪切"),
    ("Ctrl+C", "复制"),
    ("Ctrl+V", "粘贴"),
    ("Ctrl+A", "全选"),
    ("Ctrl+滚轮", "调整字号"),
    ("Enter", "换行（自动补段首缩进）"),
    # ── AI 续写相关 ──
    ("Ctrl+Enter", "AI 光标续写（打开续写输入条）"),
    ("Ctrl+Shift+Space", "AI 快速续写（幽灵文本，Tab 采纳 / Esc 放弃）"),
    ("Tab", "采纳 AI 快速续写结果（仅快速续写激活时）"),
    ("Esc", "放弃 AI 快速续写结果（仅快速续写激活时）"),
    ("Ctrl+Enter", "AI 快速续写激活时：重新生成"),
]
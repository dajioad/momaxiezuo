# utils/publish_targets.py
"""墨码写作 - 网文/内容平台发布跳转目标

设计原则：
- 只做浏览器跳转，不涉及任何平台账号、Cookie、API 提交
- 预置平台可隐藏但不可编辑（平台改版时，用户隐藏旧的、新增自定义的）
- 用户可自定义分组、平台名称、URL、备注
- 全部落盘到 publish_targets.json，无需发版即可维护

平台 URL 会随平台改版变化，此处为「参考默认值」：
用户如发现失效，可在「管理发布平台」里隐藏旧项并新增自定义项。
"""
import os
import json
from typing import List, Dict, Tuple

from .logger import get_logger
from .work_store import get_data_root, atomic_write_json

logger = get_logger()

# 预置分组（顺序即展示顺序）
# 每项：(显示名, 跳转 URL, 备注)
BUILTIN_GROUPS: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    ("网文平台", [
        ("番茄小说", "https://fanqienovel.com/main/writer/login", "字节跳动"),
        ("起点/阅文", "https://write.qq.com/portal/login", "起点、QQ阅读、创世"),
        ("纵横中文网", "https://author.zongheng.com/", "百度系"),
        ("晋江文学城", "https://my.jjwxc.net/login.php", "女频为主"),
        ("飞卢小说", "https://b.faloo.com/", "同人/爽文"),
        ("塔读文学", "https://author.tadu.com/", "免费阅读"),
        ("刺猬猫", "https://www.ciweimao.com/", "二次元"),
        ("SF轻小说", "https://book.sfacg.com/", "轻小说"),
    ]),
    ("内容平台", [
        ("知乎·写文章", "https://zhuanlan.zhihu.com/write", "直接进编辑器"),
        ("知乎·创作中心", "https://www.zhihu.com/creator", "盐选投稿入口"),
        ("简书·写文章", "https://www.jianshu.com/writer", "登录后直接进编辑器"),
        ("豆瓣阅读·投稿", "https://read.douban.com/submit/", "需先申请成为作者"),
        ("微信公众号", "https://mp.weixin.qq.com/", "登录后进草稿箱"),
        ("豆瓣", "https://www.douban.com/", "日记/笔记发布"),
    ]),
    ("海外平台", [
        ("Medium", "https://medium.com/new-story", "需可访问的网络"),
        ("Substack", "https://substack.com/", "Newsletter 平台"),
    ]),
]

_TARGETS_FILE = "publish_targets.json"
_DEFAULT_DATA = {"custom": [], "hidden": [], "custom_groups": []}


def _load_data() -> dict:
    path = os.path.join(get_data_root(), _TARGETS_FILE)
    if not os.path.exists(path):
        return {"custom": [], "hidden": [], "custom_groups": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "custom": data.get("custom", []),
            "hidden": data.get("hidden", []),
            "custom_groups": data.get("custom_groups", []),
        }
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"发布目标配置读取失败: {e}")
        return {"custom": [], "hidden": [], "custom_groups": []}


def _save_data(data: dict):
    atomic_write_json(os.path.join(get_data_root(), _TARGETS_FILE), data)


def _hidden_key(group: str, name: str) -> str:
    """隐藏项的唯一键：分组名 + \\u0001 + 平台名（\\u0001 作为分隔符不会与正常文本冲突）"""
    return f"{group}\u0001{name}"


def load_groups() -> List[Dict]:
    """加载当前生效的发布目标分组，供「发布」菜单直接使用。

    返回 [{"group": 分组名, "targets": [(name, url, note), ...]}, ...]
    空分组会被过滤掉（预置分组若全被隐藏则不显示）。
    """
    data = _load_data()
    hidden = set(data["hidden"])
    groups: List[Dict] = []

    # 预置分组
    for group_name, targets in BUILTIN_GROUPS:
        visible = [(n, u, note) for (n, u, note) in targets
                   if _hidden_key(group_name, n) not in hidden]
        # 追加该分组下的自定义目标
        for c in data["custom"]:
            if c.get("group") == group_name:
                visible.append((c.get("name", ""), c.get("url", ""),
                                c.get("note", "")))
        if visible:
            groups.append({"group": group_name, "targets": visible})

    # 自定义分组
    for gname in data["custom_groups"]:
        if any(g["group"] == gname for g in groups):
            continue
        visible = [(c.get("name", ""), c.get("url", ""), c.get("note", ""))
                   for c in data["custom"] if c.get("group") == gname]
        if visible:
            groups.append({"group": gname, "targets": visible})

    return groups


def load_all_for_manage() -> List[Dict]:
    """加载所有分组（含隐藏目标），供管理界面使用。

    返回 [{"group": 分组名, "targets": [(name, url, note, builtin, visible), ...]}, ...]
    - builtin: True 表示预置平台（只能隐藏，不能编辑/删除）
    - visible: False 表示当前被隐藏
    """
    data = _load_data()
    hidden = set(data["hidden"])
    groups: List[Dict] = []

    # 预置分组
    for group_name, targets in BUILTIN_GROUPS:
        items = []
        for (n, u, note) in targets:
            visible = _hidden_key(group_name, n) not in hidden
            items.append((n, u, note, True, visible))
        # 追加该分组下的自定义目标
        for c in data["custom"]:
            if c.get("group") == group_name:
                items.append((c.get("name", ""), c.get("url", ""),
                              c.get("note", ""), False, True))
        groups.append({"group": group_name, "targets": items})

    # 自定义分组
    for gname in data["custom_groups"]:
        if any(g["group"] == gname for g in groups):
            continue
        items = [(c.get("name", ""), c.get("url", ""), c.get("note", ""),
                  False, True)
                 for c in data["custom"] if c.get("group") == gname]
        groups.append({"group": gname, "targets": items})

    return groups


def all_group_names() -> List[str]:
    """所有可用的分组名（预置 + 自定义），供编辑对话框下拉框使用。"""
    names = [g for g, _ in BUILTIN_GROUPS]
    data = _load_data()
    for g in data["custom_groups"]:
        if g not in names:
            names.append(g)
    return names


def is_builtin(group: str, name: str) -> bool:
    """判断是否为预置平台（预置平台不能编辑/删除，只能隐藏）。"""
    for gname, targets in BUILTIN_GROUPS:
        if gname == group:
            for (n, _u, _note) in targets:
                if n == name:
                    return True
    return False


def set_hidden(group: str, name: str, hidden: bool):
    """显示/隐藏一个预置目标。"""
    data = _load_data()
    key = _hidden_key(group, name)
    if hidden:
        if key not in data["hidden"]:
            data["hidden"].append(key)
    else:
        data["hidden"] = [k for k in data["hidden"] if k != key]
    _save_data(data)


def is_hidden(group: str, name: str) -> bool:
    return _hidden_key(group, name) in _load_data()["hidden"]


def add_custom_target(group: str, name: str, url: str, note: str = ""):
    """新增自定义目标；若分组不在已知列表中，自动加入 custom_groups。"""
    data = _load_data()
    data["custom"].append({
        "group": group, "name": name, "url": url, "note": note,
    })
    builtin_names = [g for g, _ in BUILTIN_GROUPS]
    if group not in builtin_names and group not in data["custom_groups"]:
        data["custom_groups"].append(group)
    _save_data(data)


def update_custom_target(old_group: str, old_name: str,
                         new_group: str, new_name: str,
                         new_url: str, new_note: str = ""):
    """更新一个自定义目标（按原 group+name 定位）。"""
    data = _load_data()
    for c in data["custom"]:
        if c.get("group") == old_group and c.get("name") == old_name:
            c["group"] = new_group
            c["name"] = new_name
            c["url"] = new_url
            c["note"] = new_note
            break
    builtin_names = [g for g, _ in BUILTIN_GROUPS]
    if new_group not in builtin_names and new_group not in data["custom_groups"]:
        data["custom_groups"].append(new_group)
    _save_data(data)


def delete_custom_target(group: str, name: str):
    """删除一个自定义目标。预置目标不能用这个函数删除，只能 set_hidden 隐藏。"""
    data = _load_data()
    data["custom"] = [c for c in data["custom"]
                      if not (c.get("group") == group and c.get("name") == name)]
    _save_data(data)


def restore_hidden():
    """恢复所有被隐藏的预置目标。"""
    data = _load_data()
    data["hidden"] = []
    _save_data(data)
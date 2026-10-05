# utils/work_store.py
"""墨码写作 - 作品与章节数据存储层

目录结构（数据根目录默认为「我的文档/墨码写作」）：
    works/<作品名>/
        book.json              作品元数据（章节列表、卷、状态）
        chapters/0001.txt      每章独立 TXT 文件
    versions/<作品名>/<章节id>_<时间戳>.txt   历史快照
    .trash/<作品名>__<时间戳>/                 回收站

设计原则：
1. 每章独立文件，天然防单点损坏、便于合并投稿
2. 所有写入采用「临时文件 + fsync + os.replace」原子替换：
   - 写到一半断电：旧内容保持，不会损坏
   - 写入已完成：fsync 确保数据真落到磁盘，OS 缓存断电也不丢
3. book.json 为唯一元数据来源，章节排序即数组顺序
4. book.json 损坏时自动从 chapters/ 目录重建（损坏文件另存副本）
5. 删除作品默认移到回收站（.trash/），不直接删
6. 删除章节前自动打快照，防止误删

导入/导出格式支持（对等原则）：
    TXT UTF-8   导入 ✓ 导出 ✓（含卷章）
    TXT GBK     导入 ✓ 导出 ✓（含卷章）
    Markdown    导入 ✓ 导出 ✓（含卷章）
    DOCX        导入 ✓ 导出 ✓（含卷章）
    DOC         导出 ✓（HTML 伪装，兼容老 Office）
"""
import os
import re
import json
import shutil
import codecs
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from .logger import get_logger

logger = get_logger()

# 默认容器名：所有"未分卷"章节在 UI 上的根容器。
# 数据层内部用空字符串 "" 表示"属于默认容器"，这个常量只是 UI 层的
# 显示名后备值。用户可通过 Work.set_default_volume_name() 自定义。
DEFAULT_VOLUME = "正文"
CHAPTER_STATUS_DRAFT = "draft"
CHAPTER_STATUS_DONE = "done"
MAX_VERSIONS_PER_CHAPTER = 20

# 作品状态（4 种）
WORK_STATUS_ONGOING = "ongoing"       # 连载中
WORK_STATUS_COMPLETED = "completed"   # 已完结
WORK_STATUS_PAUSED = "paused"         # 暂停更新
WORK_STATUS_PLANNING = "planning"     # 构思中
VALID_WORK_STATUSES = (
    WORK_STATUS_ONGOING, WORK_STATUS_COMPLETED,
    WORK_STATUS_PAUSED, WORK_STATUS_PLANNING,
)
# 已删章节在「章节回收站」里的保留天数。超过后打开作品时自动彻底清理
# （章节文件 + 快照一起删）。与阅文作家助手 / 番茄作家助手的回收站一致。
CHAPTER_TRASH_RETENTION_DAYS = 30


def get_data_root() -> str:
    """获取数据根目录（作品/版本/草稿/统计等所有可变数据）。

    解析规则与 ConfigManager 共用同一来源（utils.config）：
    config.ini 固定在默认目录，其中的 Storage/data_dir（绝对路径）
    可把数据根改到自定义位置。
    """
    from utils.config import default_config_dir, read_custom_data_dir
    base = default_config_dir()
    root = read_custom_data_dir(os.path.join(base, "config.ini")) or base
    os.makedirs(root, exist_ok=True)
    return root


def count_words(text: str) -> int:
    """网文口径字数：去除所有空白字符后的字符数"""
    if not text:
        return 0
    return len(re.sub(r"\s", "", text))


# Windows 保留设备名（不区分大小写，可带任意 .后缀），用作目录名会失败
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_dir_name(name: str) -> str:
    """把作品名转换为安全的目录名（去除非法字符 + 规避保留设备名）"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip().strip(".")
    if not name:
        return "未命名作品"
    # CON/NUL/COM1.txt 这类名字在 Windows 上建目录必失败
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        name += "_"
    return name


# ---------- 卷 / 章 识别正则 ----------
# 卷名宽松匹配：只要行里含"第X卷/部/篇/集"就算卷
#   "第一卷"               ✓
#   "秦·第一卷矩尺"        ✓  （前有前缀"秦·"，后有卷名"矩尺"）
#   "第一卷 风起"          ✓
# 长度限制 50 字内，避免把长正文段落（如"他翻到第一卷的时候……"）误判
_VOLUME_RE = re.compile(
    r"^\s*[^\n]{0,25}"
    r"第\s*[0-9零一二三四五六七八九十百千万]+\s*[卷部篇集]"
    r"[^\n]{0,25}\s*$"
)

# 章名匹配：第X章 / 第X回 / 第X节
# 注意："节"归到章（网文里"第三节"通常是章，不是小节）
_CHAPTER_RE = re.compile(
    r"^\s*第\s*[0-9零一二三四五六七八九十百千万]+\s*[章回节]"
)


def _fsync_dir(dir_path: str):
    """对目录做 fsync，保证 os.replace 的目录项已落盘。
    Windows 上可能不支持，静默忽略。"""
    if not dir_path:
        return
    try:
        fd = os.open(dir_path, os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def atomic_write_text(path: str, text: str, encoding: str = "utf-8"):
    """原子写入文本：写临时文件 → fsync 文件 → os.replace → fsync 目录。

    fsync 保证断电时数据已落盘，不会因为 OS 缓存丢失。
    os.replace 失败（如跨文件系统）时清理临时文件，避免残留 .tmp 堆积。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    _fsync_dir(os.path.dirname(path))


def atomic_write_json(path: str, data: dict):
    """原子写入 JSON（带 fsync），失败时清理临时文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    _fsync_dir(os.path.dirname(path))


class Work:
    """一部作品的数据访问对象"""

    def __init__(self, root_dir: str, meta: dict, versions_root: str = None):
        self.root_dir = root_dir
        self.chapters_dir = os.path.join(root_dir, "chapters")
        os.makedirs(self.chapters_dir, exist_ok=True)
        self.meta = meta
        self._versions_root = versions_root
        # 撤销合并栈：只保留最近一次合并的信息（Ctrl+Z 撤一条）。
        # 结构见 merge_chapter_into_previous 里的赋值。
        # 生命周期跟 Work 实例一致：切换作品换 Work 对象，自然清空。
        self._last_merge_undo = None
        # 高频小 JSON（词库/人物卡）按 mtime 的读缓存：
        # {path: (mtime, raw_obj)}。高亮定时器每 800ms 读 3 个文件，
        # 未改动时全靠缓存零反序列化；保存走 os.replace 换新 inode、
        # mtime 必变，下次读取自动重载，无需手动失效。
        self._json_mtime_cache: Dict[str, tuple] = {}

    def _load_json_raw_cached(self, path: str):
        """按 mtime 缓存的 JSON 读取，返回解析后的原始对象。

        文件不存在或损坏时返回 None。缓存对象允许被调用方就地修改
        （面板编辑后立即 save），磁盘落盘后 mtime 变化即失效。
        """
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            self._json_mtime_cache.pop(path, None)
            return None
        cached = self._json_mtime_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"读取 {os.path.basename(path)} 失败: {e}")
            return None
        self._json_mtime_cache[path] = (mtime, data)
        return data

    # ---------- 元数据 ----------
    @property
    def title(self) -> str:
        return self.meta.get("title", "未命名作品")

    @property
    def chapters(self) -> List[dict]:
        """返回**未删除**的章节（章节树、统计、导出用这个）。

        已删章节存在 meta["chapters"] 里但带 "deleted": true 标记，
        由 all_chapters / trashed_chapters 访问。
        """
        return [c for c in self.meta.setdefault("chapters", [])
                if not c.get("deleted")]

    @property
    def all_chapters(self) -> List[dict]:
        """返回全部章节（含已删未彻底清理的）。内部用，勿在 UI 里遍历。"""
        return self.meta.setdefault("chapters", [])

    @property
    def trashed_chapters(self) -> List[dict]:
        """返回已删除的章节，按删除时间倒序（新的在前）。"""
        return sorted(
            [c for c in self.all_chapters if c.get("deleted")],
            key=lambda c: c.get("deleted_at", ""),
            reverse=True,
        )

    def save_meta(self):
        self.meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        atomic_write_json(os.path.join(self.root_dir, "book.json"), self.meta)

    def total_words(self) -> int:
        return sum(int(c.get("word_count", 0)) for c in self.chapters)

    # ---------- 作品状态 ----------
    def get_status(self) -> str:
        """读取作品状态。

        老作品（无 status 字段）按章节数推断：
          有章节 → 连载中 / 无章节 → 构思中。
        """
        s = (self.meta.get("status") or "").strip()
        if s in VALID_WORK_STATUSES:
            return s
        return (WORK_STATUS_ONGOING if self.chapters
                else WORK_STATUS_PLANNING)

    def set_status(self, status: str):
        if status not in VALID_WORK_STATUSES:
            return
        self.meta["status"] = status
        self.save_meta()

    # ---------- 封面 ----------
    @property
    def cover_path(self) -> str:
        return os.path.join(self.root_dir, "cover.png")

    def has_cover(self) -> bool:
        return os.path.exists(self.cover_path)

    def set_cover(self, src_path: str):
        try:
            from PIL import Image
        except ImportError as e:
            raise RuntimeError("当前版本缺少图片处理组件，无法设置封面。\n"
                            "请反馈给作者，或先在设置里检查是否为完整安装。") from e
        try:
            img = Image.open(src_path)
            img.load()
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            w, h = img.size
            target_w = 600
            if w > target_w:
                ratio = target_w / w
                img = img.resize((target_w, int(h * ratio)), Image.LANCZOS)
            img.save(self.cover_path, "PNG")
            self.save_meta()
        except Exception as e:
            logger.warning(f"设置封面失败: {e}")
            raise

    def remove_cover(self):
        if os.path.exists(self.cover_path):
            os.remove(self.cover_path)
            self.save_meta()

    def get_default_volume_name(self) -> str:
        """默认容器的显示名（用户可重命名为书名 / 草稿等）。

        数据层内部仍然用空字符串 "" 表示"属于默认容器"，
        这个名称只影响 UI 显示和导出时的默认卷标题。
        """
        name = (self.meta.get("default_volume_name") or "").strip()
        return name or DEFAULT_VOLUME

    def set_default_volume_name(self, name: str):
        """重命名默认容器。空串或 DEFAULT_VOLUME 视为恢复默认。"""
        name = (name or "").strip()
        if not name or name == DEFAULT_VOLUME:
            self.meta.pop("default_volume_name", None)
        else:
            self.meta["default_volume_name"] = name
        self.save_meta()

    def volumes(self) -> List[str]:
        """返回所有卷名（章节派生 + 存储的并集）。

        顺序策略：
        1. 先按 chapters 里的**出现顺序**列出有章节的卷
           —— 这样导入时文件里卷的顺序（从上到下）就自然成为显示顺序
        2. 再追加 meta["volumes"] 里没在章节里出现的空卷（放最后）

        空串代表默认卷"正文"，不在这里返回——它由 UI 层保证始终显示。
        """
        result: List[str] = []
        # 1. 按章节出现顺序（章节顺序 = 导入时的文件行顺序）
        for c in self.chapters:
            v = (c.get("volume") or "").strip()
            if v and v not in result:
                result.append(v)
        # 2. 追加空卷（meta 里登记但没有章节的）
        for v in self.meta.get("volumes") or []:
            if isinstance(v, str):
                v = v.strip()
                if v and v not in result:
                    result.append(v)
        return result

    def add_volume(self, name: str) -> bool:
        """新建一个空卷。返回 False 表示名字为空、已存在或非法。"""
        name = (name or "").strip()
        if not name:
            return False
        if name in self.volumes():
            return False
        vols = self.meta.setdefault("volumes", [])
        if not isinstance(vols, list):
            self.meta["volumes"] = []
            vols = self.meta["volumes"]
        vols.append(name)
        self.save_meta()
        return True

    def rename_volume(self, old_name: str, new_name: str) -> bool:
        """重命名卷。同步更新 volumes 列表和所有相关章节的 volume 字段。"""
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not old_name or not new_name or old_name == new_name:
            return False
        if new_name in self.volumes():
            return False

        vols = self.meta.setdefault("volumes", [])
        if not isinstance(vols, list):
            vols = []
            self.meta["volumes"] = vols
        replaced = False
        for i, v in enumerate(vols):
            if v == old_name:
                vols[i] = new_name
                replaced = True
                break
        if not replaced:
            # volumes 里没有（老数据），补上
            vols.append(new_name)

        for c in self.chapters:
            if (c.get("volume") or "").strip() == old_name:
                c["volume"] = new_name
        self.save_meta()
        return True

    def delete_volume(self, name: str):
        """删除卷定义。卷内章节移到正文（volume 字段置空）。"""
        name = (name or "").strip()
        if not name:
            return
        vols = self.meta.get("volumes")
        if isinstance(vols, list):
            self.meta["volumes"] = [v for v in vols if v != name]
        for c in self.chapters:
            if (c.get("volume") or "").strip() == name:
                c["volume"] = ""
        self.save_meta()

    # ---------- 高频词库 ----------
    def wordbook_path(self) -> str:
        return os.path.join(self.root_dir, "wordbook.json")

    def load_wordbook(self) -> dict:
        data = self._load_json_raw_cached(self.wordbook_path())
        if isinstance(data, dict) and isinstance(data.get("categories"), list):
            return data
        return {}

    def save_wordbook(self, data: dict):
        atomic_write_json(self.wordbook_path(), data)

    # ---------- 人物卡 ----------
    def characters_path(self) -> str:
        return os.path.join(self.root_dir, "characters.json")

    def load_characters(self) -> List[dict]:
        """读取人物卡列表。损坏或不存在时返回空列表。"""
        data = self._load_json_raw_cached(self.characters_path())
        if isinstance(data, dict) and isinstance(data.get("characters"), list):
            return [c for c in data["characters"] if isinstance(c, dict)]
        return []

    def save_characters(self, characters: List[dict]):
        """保存人物卡列表（保留已有的 color 字段）。"""
        color = self.load_characters_color()
        data = {"characters": list(characters or [])}
        if color:
            data["color"] = color
        atomic_write_json(self.characters_path(), data)

    def load_characters_color(self) -> str:
        """读取人物卡高亮色。返回空串表示未设置（用默认色）。"""
        data = self._load_json_raw_cached(self.characters_path())
        if isinstance(data, dict):
            return str(data.get("color", "") or "")
        return ""

    def save_characters_color(self, color: str):
        """保存人物卡高亮色（保留已有 characters 列表）。"""
        path = self.characters_path()
        data = {"color": color, "characters": []}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    old = json.load(f)
                if isinstance(old, dict):
                    data["characters"] = old.get("characters", [])
            except (OSError, json.JSONDecodeError):
                pass
        data["color"] = color
        atomic_write_json(path, data)

    def scan_character_appearances(self, characters: List[dict]) -> List[dict]:
        """扫描全本，统计每个角色的出场情况。

        对每个角色新增 `_stats` 字段：
          chapter_count  出场章节数
          total          总提及次数
          last_title     最后出场章节标题
          last_index     最后出场章节序号（0-based，-1 表示从未出场）
          last_chapter_id
          chapters       出场章节 id 列表

        匹配规则：名字 + 所有别名，简单子串匹配（不做分词）。
        性能：只读一次所有章节文本，然后每个角色在这个缓存上 count，
             100 章 × 20 角色耗时 < 1 秒。
        """
        chapters = self.chapters or []
        chapter_texts = []
        for c in chapters:
            cid = c.get("id")
            text = self.read_chapter(cid) or ""
            chapter_texts.append((c, text))

        result = []
        for ch in characters:
            c = dict(ch)
            names = [c.get("name", "")]
            aliases = c.get("aliases", [])
            if isinstance(aliases, str):
                names.extend(a for a in aliases.split("，") if a.strip())
            elif isinstance(aliases, list):
                names.extend(a for a in aliases if a)
            names = [n.strip() for n in names if n and n.strip()]

            hit_chapters = []
            total = 0
            if names:
                for idx, (meta, text) in enumerate(chapter_texts):
                    if not text:
                        continue
                    n = sum(text.count(name) for name in names)
                    if n > 0:
                        hit_chapters.append({
                            "chapter_id": meta.get("id"),
                            "title": meta.get("title", ""),
                            "count": n,
                            "index": idx,
                        })
                        total += n

            last = hit_chapters[-1] if hit_chapters else None
            c["_stats"] = {
                "chapters": [h["chapter_id"] for h in hit_chapters],
                "chapter_count": len(hit_chapters),
                "total": total,
                "last_title": last["title"] if last else "",
                "last_index": last["index"] if last else -1,
                "last_chapter_id": last["chapter_id"] if last else "",
            }
            result.append(c)
        return result

    # ---------- 大纲 / 笔记 ----------
    def read_side_doc(self, name: str) -> str:
        path = os.path.join(self.root_dir, f"{name}.txt")
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="gbk", errors="ignore") as f:
                return f.read()
        except OSError as e:
            logger.warning(f"读取附属文档失败 {name}: {e}")
            return ""

    def write_side_doc(self, name: str, text: str):
        if name not in ("outline", "ideas"):
            raise ValueError(f"非法的附属文档名: {name}")
        atomic_write_text(os.path.join(self.root_dir, f"{name}.txt"), text or "")

    def _notes_dir(self) -> str:
        return os.path.join(self.root_dir, "notes")

    def read_note(self, chapter_id: str) -> str:
        path = os.path.join(self._notes_dir(), f"{chapter_id}.txt")
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            logger.warning(f"读取章纲失败 {chapter_id}: {e}")
            return ""

    def write_note(self, chapter_id: str, text: str):
        cid = str(chapter_id)
        if not re.match(r"^\d+$", cid):
            raise ValueError(f"非法章节 id: {chapter_id}")
        atomic_write_text(os.path.join(self._notes_dir(), f"{cid}.txt"),
                          text or "")

    # ---------- 章节 CRUD ----------
    def _next_id(self) -> str:
        # 遍历 all_chapters：已删章节的 id 仍被快照文件引用，不能复用，
        # 否则新章节的快照会和回收站里老章节的快照混在一起。
        max_num = 0
        for c in self.all_chapters:
            m = re.match(r"^(\d+)$", str(c.get("id", "")))
            if m:
                max_num = max(max_num, int(m.group(1)))
        # 合并章节会永久删除 meta 条目但保留快照文件（keep_snapshots），
        # 这类 id 同样不能复用：否则新章会继承旧章的历史快照，
        # 且恢复快照可能用旧内容覆盖新章。扫描快照文件名前缀兜底。
        try:
            vdir = self._versions_dir()
            if os.path.isdir(vdir):
                for fname in os.listdir(vdir):
                    m = re.match(r"^(\d+)_", fname)
                    if m:
                        max_num = max(max_num, int(m.group(1)))
        except OSError:
            pass
        return f"{max_num + 1:04d}"

    def chapter_index(self, chapter_id: str) -> int:
        for i, c in enumerate(self.chapters):
            if str(c.get("id")) == str(chapter_id):
                return i
        return -1

    def get_chapter(self, chapter_id: str) -> Optional[dict]:
        """按 id 找章节。**包含已删章节**——调用方若只关心活跃章节，
        需自行判断 c.get("deleted")。
        """
        cid = str(chapter_id)
        for c in self.all_chapters:
            if str(c.get("id")) == cid:
                return c
        return None

    def add_chapter(self, title: str = "新章节", volume: str = "",
                    status: str = CHAPTER_STATUS_DRAFT) -> dict:
        cid = self._next_id()
        chapter = {
            "id": cid,
            "title": title or "新章节",
            "file": f"{cid}.txt",
            "volume": volume or "",
            "status": status,
            "word_count": 0,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": "",
        }
        # 必须用 all_chapters：chapters 属性返回的是过滤后的新列表，
        # append 到它上面不会写回 meta。
        self.all_chapters.append(chapter)
        atomic_write_text(self._chapter_path(chapter), "")
        self.save_meta()
        return chapter

    def rename_chapter(self, chapter_id: str, title: str):
        c = self.get_chapter(chapter_id)
        if c is not None:
            c["title"] = title
            self.save_meta()

    def merge_chapter_into_previous(self, chapter_id: str) -> Optional[str]:
        """把指定章节合并到**同卷内的上一章**。返回合并后的章节 id。

        只在同一卷内找"上一章"：
        - 当前章是本卷第一章 → 返回 None（不跨卷合并）
        - 上一章按 book.json 数组顺序取

        为什么要限定同卷：
        之前按全局数组顺序找上一章，章节树按卷分组显示时，
        视觉上的"上一行"和数组里的"前一个"可能不是同一章，
        导致合并后跳到别的卷（用户看到"合并后跳到第一卷"）。

        合并成功后 self._last_merge_undo 里会记下还原所需的全部信息，
        供 undo_last_merge() 使用（Ctrl+Z 撤销合并）。
        """
        c = self.get_chapter(chapter_id)
        if c is None:
            return None
        cur_vol = (c.get("volume") or "").strip()

        # 找同卷内的所有章节
        same_vol = [
            ch for ch in self.chapters
            if (ch.get("volume") or "").strip() == cur_vol
        ]
        # 当前章在同卷里的位置
        idx_in_vol = next(
            (i for i, ch in enumerate(same_vol) if ch["id"] == chapter_id),
            -1)
        if idx_in_vol <= 0:
            return None  # 本卷第一章，没有上一章

        prev_ch = same_vol[idx_in_vol - 1]
        prev_id = prev_ch["id"]
        cur_id = chapter_id

        # 记录 cur 在 all_chapters 里的原始位置（撤销时插回这里）
        cur_index = -1
        for i, x in enumerate(self.all_chapters):
            if str(x.get("id")) == str(cur_id):
                cur_index = i
                break

        # 前后两章都打快照：
        # - prev_id：合并前上一章的原样（合并后 prev 内容会变，方便对比）
        # - cur_id ：被合并章的原样（删除后靠它从快照浏览器找回）
        try:
            self.create_snapshot(prev_id)
        except Exception as e:
            logger.warning(f"合并前快照（上一章）失败: {e}")
        try:
            self.create_snapshot(cur_id)
        except Exception as e:
            logger.warning(f"合并前快照（被合并章）失败: {e}")

        prev_text = self.read_chapter(prev_id)
        prev_text_strip = prev_text.rstrip()
        cur_text_full = self.read_chapter(cur_id)
        cur_text_strip = cur_text_full.strip()
        if cur_text_strip:
            merged = (f"{prev_text_strip}\n\n{cur_text_strip}"
                      if prev_text_strip else cur_text_strip)
        else:
            merged = prev_text_strip

        # 保存撤销信息：整个操作在 Ctrl+Z 里可逆
        self._last_merge_undo = {
            "prev_id": prev_id,
            "prev_text": prev_text,                       # 原始（含末尾换行）
            "prev_word_count": int(prev_ch.get("word_count", 0)),
            "prev_updated_at": prev_ch.get("updated_at", ""),
            "cur_index": cur_index,
            "cur_chapter": dict(c),                       # 元数据副本
            "cur_text": cur_text_full,                    # 原始（含末尾换行）
        }

        # 写回前章
        self.write_chapter(prev_id, merged + "\n" if merged else "")
        # 合并的当前章：直接彻底删除（不进回收站）。
        # 关键：keep_snapshots=True —— 保留上面给 cur_id 打的快照，
        # 用户在「历史快照浏览器 → 已删章节」里能找回被合并章的原样。
        self.permanently_delete_chapter(cur_id, keep_snapshots=True)
        return prev_id

    def has_merge_undo(self) -> bool:
        """是否存在可撤销的合并。"""
        return bool(getattr(self, "_last_merge_undo", None))

    def undo_last_merge(self) -> bool:
        """撤销最近一次 merge_chapter_into_previous。

        返回 True 表示成功还原（prev 内容回退、cur 章节重新出现）；
        False 表示没有可撤销的合并，或状态已变（prev 被删 / cur 被手动恢复）。
        """
        info = getattr(self, "_last_merge_undo", None)
        if not info:
            return False

        prev_id = info["prev_id"]
        cur_ch = dict(info["cur_chapter"])
        cur_id = cur_ch.get("id")
        if not cur_id:
            self._last_merge_undo = None
            return False

        # 前置检查：prev 应该还在，cur 应该不存在（否则状态已变，撤销有风险）
        if self.get_chapter(prev_id) is None:
            logger.warning(f"撤销合并失败：上一章 {prev_id} 已被删除")
            self._last_merge_undo = None
            return False
        if self.get_chapter(cur_id) is not None:
            logger.warning(f"撤销合并失败：被合并章 {cur_id} 已存在")
            self._last_merge_undo = None
            return False

        # 1. 还原上一章内容
        self.write_chapter(prev_id, info["prev_text"])
        prev_ch = self.get_chapter(prev_id)
        if prev_ch is not None:
            prev_ch["word_count"] = info["prev_word_count"]
            prev_ch["updated_at"] = info["prev_updated_at"]

        # 2. 把被合并章插回原位置
        all_ch = self.all_chapters
        idx = info["cur_index"]
        if idx < 0 or idx > len(all_ch):
            idx = len(all_ch)
        all_ch.insert(idx, cur_ch)
        atomic_write_text(self._chapter_path(cur_ch), info["cur_text"])

        self.save_meta()
        self._last_merge_undo = None
        logger.info(f"已撤销合并：还原 {cur_id}，回退 {prev_id}")
        return True

    def split_chapter(self, chapter_id: str, pos: int,
                       new_title: str = "") -> Optional[dict]:
        """在 pos 处把章节拆成两章。返回新章节 dict，失败返回 None。

        规则：
        - 前半部分保留在原章（标题不变）
        - 后半部分作为新章节插在原章后面（同一卷）
        - 前后都打快照（原章打一份，新章内容写入后再打不了——
          实际上新章内容就是原章的后半段，原章的快照已经覆盖）
        """
        c = self.get_chapter(chapter_id)
        if c is None:
            return None
        text = self.read_chapter(chapter_id)
        if not text:
            return None
        n = len(text)
        pos = max(1, min(int(pos), n - 1))

        # 只清理两章首尾的纯空白字符，保留段落边界的换行。
        # 原实现在长空行区间拆分时会把两侧空行全删，用户看不出
        # 拆分点在哪。这里 rstrip/lstrip 只清 \t\n 和空格，然后
        # 由下面 write_chapter 统一追加一个换行——不能两边都补，
        # 否则拆分后各多一个空行（上一版 bug）。
        before = text[:pos].rstrip()
        after = text[pos:].lstrip("\n").rstrip()
        if not before or not after:
            return None

        # 原章拆分前打快照
        try:
            self.create_snapshot(chapter_id)
        except Exception as e:
            logger.warning(f"拆分前快照失败: {e}")

        # 更新原章为前半部分（统一追加一个换行）
        self.write_chapter(chapter_id, before + "\n")

        # 建新章，写后半部分（统一追加一个换行）
        new_ch = self.add_chapter(
            new_title or "新章节", volume=c.get("volume", ""))
        self.write_chapter(new_ch["id"], after + "\n")

        # add_chapter 是 append 到末尾；把它移到原章之后
        cur_idx = self.chapter_index(chapter_id)
        new_idx = self.chapter_index(new_ch["id"])
        if new_idx > cur_idx + 1:
            self.move_chapter(new_ch["id"], cur_idx + 1)

        return new_ch

    def rename_chapters_batch(self, renames: dict) -> int:
        """批量重命名章节，只写一次盘。

        renames: {chapter_id: new_title}
        返回实际修改的章节数。空标题跳过（不改）。
        """
        changed = 0
        for cid, new_title in (renames or {}).items():
            c = self.get_chapter(cid)
            if c is None:
                continue
            new_title = (new_title or "").strip()
            if new_title and c.get("title") != new_title:
                c["title"] = new_title
                changed += 1
        if changed:
            self.save_meta()
        return changed

    def get_chapter_word_target(self, chapter_id: str) -> int:
        """读取章节字数目标，未设置返回 0。"""
        c = self.get_chapter(chapter_id)
        if c is None:
            return 0
        try:
            return int(c.get("word_target", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def set_chapter_word_target(self, chapter_id: str, target: int):
        """设置章节字数目标。target <= 0 表示取消目标。"""
        c = self.get_chapter(chapter_id)
        if c is None:
            return
        c["word_target"] = max(0, int(target))
        self.save_meta()

    def set_chapter_status(self, chapter_id: str, status: str):
        c = self.get_chapter(chapter_id)
        if c is not None and status in (CHAPTER_STATUS_DRAFT, CHAPTER_STATUS_DONE):
            c["status"] = status
            self.save_meta()

    def set_chapter_volume(self, chapter_id: str, volume: str):
        c = self.get_chapter(chapter_id)
        if c is not None:
            c["volume"] = volume or ""
            self.save_meta()

    def delete_chapter(self, chapter_id: str):
        """把章节移到回收站（软删除）。

        章节文件、快照、章纲全部保留；可在「章节回收站」里恢复。
        超过 CHAPTER_TRASH_RETENTION_DAYS 天后由 cleanup_trashed 彻底清理。
        """
        c = self.get_chapter(chapter_id)
        if c is None or c.get("deleted"):
            return
        c["deleted"] = True
        c["deleted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_meta()
        logger.info(f"章节已移入回收站: {chapter_id}")

    def restore_chapter(self, chapter_id: str) -> bool:
        """把回收站里的章节恢复到活跃列表。返回是否成功。"""
        c = self.get_chapter(chapter_id)
        if c is None or not c.get("deleted"):
            return False
        c.pop("deleted", None)
        c.pop("deleted_at", None)
        self.save_meta()
        logger.info(f"章节已从回收站恢复: {chapter_id}")
        return True

    def permanently_delete_chapter(self, chapter_id: str,
                                    keep_snapshots: bool = False):
        """从回收站彻底删除章节：元数据 + 章节文件 + 章纲 + 全部快照。

        keep_snapshots=True 时**保留该章的历史快照文件**。

        用途：章节合并（内容已归入前章）场景下，元数据和章节文件清理
        掉，但快照作为安全网留存——用户在「历史快照浏览器 → 已删章节」
        里还能看到被合并章的独立版本，可"另存为新章节"找回。
        这修复了之前"合并前会自动打快照"的 UI 承诺与实际不符的问题。
        """
        c = None
        all_ch = self.all_chapters
        for i, x in enumerate(all_ch):
            if str(x.get("id")) == str(chapter_id):
                c = x
                del all_ch[i]
                break
        if c is None:
            return
        # 删章节文件与章纲
        for p in (self._chapter_path(c),
                  os.path.join(self._notes_dir(), f"{c.get('id')}.txt")):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError as e:
                logger.warning(f"删除章节文件失败: {e}")
        # 删该章的全部快照（合并场景除外：合并是"内容已归入前章"，
        # 让快照留存作为安全网，用户可从「历史快照浏览器」找回）
        if not keep_snapshots:
            self._delete_chapter_snapshots(chapter_id)
        self.save_meta()
        logger.info(
            f"章节已彻底删除: {chapter_id}"
            + ("（快照已保留）" if keep_snapshots else ""))

    def _delete_chapter_snapshots(self, chapter_id: str):
        """删除某章节的所有快照文件 + 对应元数据条目。"""
        vdir = self._versions_dir()
        if not os.path.isdir(vdir):
            return
        prefix = f"{chapter_id}_"
        meta = self._load_snapshot_meta()
        changed = False
        for f in os.listdir(vdir):
            if not f.startswith(prefix) or not f.endswith(".txt"):
                continue
            try:
                os.remove(os.path.join(vdir, f))
            except OSError:
                continue
            key = f[:-4]
            if key in meta:
                del meta[key]
                changed = True
        if changed:
            self._save_snapshot_meta(meta)

    def cleanup_trashed(self,
                        retention_days: int = CHAPTER_TRASH_RETENTION_DAYS) -> int:
        """清理回收站里超过保留期的章节，返回彻底删除的数量。

        调用时机：打开作品时（load_work）。失败静默，不打扰用户。
        """
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=retention_days)
        to_delete = []
        for c in self.all_chapters:
            if not c.get("deleted"):
                continue
            ds = c.get("deleted_at", "")
            try:
                t = datetime.strptime(ds, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            if t < cutoff:
                to_delete.append(c.get("id"))
        for cid in to_delete:
            self.permanently_delete_chapter(cid)
        if to_delete:
            logger.info(f"自动清理 {len(to_delete)} 个过期章节")
        return len(to_delete)

    def move_chapter(self, chapter_id: str, new_index: int):
        """把章节移到活跃章节列表的 new_index 位置。

        已删章节的位置不动：它们在底层 meta 列表里的相对顺序保持不变，
        只是在计算 new_index 时被忽略。
        """
        all_ch = self.all_chapters
        target = None
        for c in all_ch:
            if str(c.get("id")) == str(chapter_id):
                target = c
                break
        if target is None or target.get("deleted"):
            return

        # 除 target 外的活跃章节（用于定位插入点）
        others = [c for c in all_ch
                  if not c.get("deleted") and c is not target]
        new_index = max(0, min(new_index, len(others)))

        # 从底层列表移除 target
        for i, c in enumerate(all_ch):
            if c is target:
                del all_ch[i]
                break

        # 计算插入位置（全量列表坐标）
        if new_index >= len(others):
            if others:
                pos = all_ch.index(others[-1]) + 1
            else:
                pos = 0
        else:
            pos = all_ch.index(others[new_index])
        all_ch.insert(pos, target)
        self.save_meta()

    def _chapter_path(self, chapter: dict) -> str:
        return os.path.join(self.chapters_dir, chapter.get("file", f"{chapter['id']}.txt"))

    def chapter_path(self, chapter_id: str) -> Optional[str]:
        c = self.get_chapter(chapter_id)
        return self._chapter_path(c) if c else None

    # ---------- 章节内容读写 ----------
    def read_chapter_checked(self, chapter_id: str) -> Tuple[str, str]:
        """读章节，返回 (text, status)。

        status：
          "ok"      正常读到（空文件也是 ok）
          "missing" 章节记录或文件不存在（调用方可按"空章节"处理，
                    但向用户打开前应先确认，避免掩盖外部删除）
          "error"   文件存在但读取失败（杀软/同步盘/网盘占用、权限瞬断、
                    编码无法识别）。**调用方绝不能把它当空章装入编辑器
                    或写回空白**——否则自动保存会用空白覆盖整章。

        编码策略：BOM 优先（UTF-16/UTF-8）→ UTF-8 严格 → GBK 严格；
        都失败时返回 error，不再用 errors="ignore" 解成乱码
        （乱码一旦被编辑保存就不可逆覆盖原文件）。
        """
        c = self.get_chapter(chapter_id)
        if c is None:
            return "", "missing"
        path = self._chapter_path(c)
        if not os.path.exists(path):
            return "", "missing"
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            logger.error(f"读取章节失败（IO 错误）{chapter_id}: {e}")
            return "", "error"
        # 1. BOM 显式声明的编码优先
        if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
            try:
                return raw.decode("utf-16"), "ok"
            except UnicodeDecodeError:
                pass
        if raw.startswith(codecs.BOM_UTF8):
            try:
                return raw.decode("utf-8-sig"), "ok"
            except UnicodeDecodeError:
                pass
        # 2. UTF-8（项目默认编码）
        try:
            return raw.decode("utf-8"), "ok"
        except UnicodeDecodeError:
            pass
        # 3. GBK 回退（早年 Windows 记事本/老码字软件导出）
        try:
            return raw.decode("gbk"), "ok"
        except UnicodeDecodeError:
            logger.error(
                f"章节编码无法识别（非 UTF-8/GBK/UTF-16），"
                f"已拒绝装入编辑器以防止乱码覆盖: {path}")
            return "", "error"

    def read_chapter(self, chapter_id: str) -> str:
        """读取章节文本（旧接口兼容）。

        missing / error 均返回空串——仅适用于"读不到就当没有"的
        批量场景（导出、合并、快照扫描）。编辑器打开章节必须改用
        read_chapter_checked 区分读取失败与真空章。
        """
        text, _status = self.read_chapter_checked(chapter_id)
        return text

    def write_chapter(self, chapter_id: str, text: str, snapshot: bool = False) -> int:
        c = self.get_chapter(chapter_id)
        if c is None:
            return 0
        if snapshot:
            try:
                self.create_snapshot(chapter_id)
            except OSError as e:
                logger.warning(f"保存前快照失败: {e}")
        atomic_write_text(self._chapter_path(c), text)
        c["word_count"] = count_words(text)
        c["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_meta()
        return c["word_count"]

    # ---------- 历史快照 ----------
    def _versions_dir(self) -> str:
        """快照目录。用**磁盘目录名**（不是 title）作为 key。

        为什么：作品重命名只改 meta["title"]，不动磁盘目录名。之前用
        self.title 作 key，用户重命名后 _versions_dir 返回新路径，
        旧快照全部"凭空消失"。
        """
        base = self._versions_root or get_data_root()
        stable = os.path.basename(self.root_dir.rstrip("/\\")) or "未命名作品"
        new_dir = os.path.join(base, "versions", safe_dir_name(stable))
        # 兼容旧数据：新目录不存在但旧目录（按 title）存在时，继续用旧的。
        # 用户可手动把 versions/<旧 title>/ 改名为 versions/<目录名>/ 完成迁移。
        old_dir = os.path.join(base, "versions", safe_dir_name(self.title))
        if not os.path.isdir(new_dir) and os.path.isdir(old_dir):
            return old_dir
        return new_dir

    def _snapshot_meta_path(self) -> str:
        """快照元数据文件：versions/<作品名>/.meta.json

        结构：
        {
            "0001_20260919_143052_123": {
                "name": "3稿终稿",
                "word_count": 3240
            },
            ...
        }
        """
        return os.path.join(self._versions_dir(), ".meta.json")

    def _load_snapshot_meta(self) -> dict:
        path = self._snapshot_meta_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_snapshot_meta(self, data: dict):
        try:
            atomic_write_json(self._snapshot_meta_path(), data)
        except OSError as e:
            logger.warning(f"保存快照元数据失败: {e}")

    @staticmethod
    def _snapshot_key(file_path: str) -> str:
        """从快照文件路径得出元数据 key：'0001_20260919_143052_123'。"""
        base = os.path.basename(file_path)
        return base[:-4] if base.endswith(".txt") else base

    def rename_snapshot(self, version_file: str, new_name: str):
        """给快照起名（或改名）。new_name 为空串表示清空名字。"""
        if not version_file or not os.path.exists(version_file):
            return
        key = self._snapshot_key(version_file)
        meta = self._load_snapshot_meta()
        info = meta.get(key) or {}
        info["name"] = (new_name or "").strip()
        if "word_count" not in info:
            try:
                with open(version_file, "r", encoding="utf-8",
                          errors="ignore") as f:
                    info["word_count"] = count_words(f.read())
            except OSError:
                info["word_count"] = 0
        meta[key] = info
        self._save_snapshot_meta(meta)

    def delete_snapshot(self, version_file: str):
        """删除单条快照文件 + 对应元数据。"""
        if not version_file or not os.path.exists(version_file):
            return
        key = self._snapshot_key(version_file)
        try:
            os.remove(version_file)
        except OSError as e:
            logger.warning(f"删除快照失败: {e}")
            return
        meta = self._load_snapshot_meta()
        if key in meta:
            del meta[key]
            self._save_snapshot_meta(meta)

    def create_snapshot(self, chapter_id: str) -> Optional[str]:
        c = self.get_chapter(chapter_id)
        if c is None:
            return None
        src = self._chapter_path(c)
        if not os.path.exists(src):
            return None
        vdir = self._versions_dir()
        os.makedirs(vdir, exist_ok=True)
        now = datetime.now()
        ts = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
        dest = os.path.join(vdir, f"{chapter_id}_{ts}.txt")
        shutil.copy2(src, dest)

        # 记录元数据：字数（供 UI 显示"字数变化"用）
        try:
            with open(dest, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            key = self._snapshot_key(dest)
            meta = self._load_snapshot_meta()
            meta[key] = {
                "name": "",
                "word_count": count_words(text),
                "chapter_title": c.get("title", ""),
                # 卷名也存进来：删了章节后从快照恢复时，能回到原来的卷
                "chapter_volume": c.get("volume", "") or "",
            }
            self._save_snapshot_meta(meta)
        except OSError:
            pass

        self._cleanup_versions(chapter_id)
        return dest

    def _cleanup_versions(self, chapter_id: str):
        vdir = self._versions_dir()
        if not os.path.isdir(vdir):
            return
        files = [f for f in os.listdir(vdir)
                 if f.startswith(f"{chapter_id}_") and f.endswith(".txt")]
        files.sort()
        if len(files) <= MAX_VERSIONS_PER_CHAPTER:
            return
        # 清理旧文件的同时，删除对应元数据
        meta = self._load_snapshot_meta()
        meta_changed = False
        for old in files[:-MAX_VERSIONS_PER_CHAPTER]:
            try:
                os.remove(os.path.join(vdir, old))
                key = old[:-4]
                if key in meta:
                    del meta[key]
                    meta_changed = True
            except OSError:
                pass
        if meta_changed:
            self._save_snapshot_meta(meta)

    def list_versions(self, chapter_id: str) -> List[dict]:
        vdir = self._versions_dir()
        if not os.path.isdir(vdir):
            return []
        meta = self._load_snapshot_meta()
        result = []
        for f in os.listdir(vdir):
            m = re.match(rf"^{chapter_id}_(\d{{8}}_\d{{6}}(?:_\d{{3}})?)\.txt$", f)
            if m:
                p = os.path.join(vdir, f)
                key = f[:-4]
                info = meta.get(key) or {}
                result.append({
                    "file": p,
                    "time": m.group(1),
                    "mtime": os.path.getmtime(p),
                    "name": info.get("name", ""),
                    "word_count": info.get("word_count", 0),
                    "chapter_title": info.get("chapter_title", ""),
                    "chapter_volume": info.get("chapter_volume", ""),
                })
        # 新 → 旧
        result.sort(key=lambda x: x["mtime"], reverse=True)

        # 计算字数变化：与"更早的一条快照"比较
        for i, v in enumerate(result):
            if i + 1 < len(result):
                prev_wc = result[i + 1].get("word_count", 0)
                cur_wc = v.get("word_count", 0)
                v["word_delta"] = (cur_wc - prev_wc) if (prev_wc and cur_wc) else None
            else:
                v["word_delta"] = None   # 最老的快照没有更早的对比

        return result

    def list_all_versions(self) -> List[dict]:
        """列出该作品所有历史快照，按章节分组，包含已被删除章节的快照。

        返回：
        [
            {
                "chapter_id": "0001",
                "title": "第一章 开端",     # 章节还在 book.json 时用章节标题
                "in_book": True,           # 章节是否还在当前 book.json 里
                "versions": [
                    {"file": ..., "time": ..., "mtime": ..., "size": ...},
                    ...
                ],
            },
            ...
        ]

        排序：已删章节排前面（用户更需要恢复），同组内最新快照在前。
        """
        vdir = self._versions_dir()
        if not os.path.isdir(vdir):
            return []

        book_chapters = {str(c.get("id")): c for c in self.chapters}
        meta = self._load_snapshot_meta()
        grouped: Dict[str, list] = {}
        for f in os.listdir(vdir):
            m = re.match(r"^(\d+)_(\d{8}_\d{6}(?:_\d{3})?)\.txt$", f)
            if not m:
                continue
            cid = m.group(1)
            time_str = m.group(2)
            full = os.path.join(vdir, f)
            try:
                mtime = os.path.getmtime(full)
                size = os.path.getsize(full)
            except OSError:
                continue
            info = meta.get(f[:-4]) or {}
            grouped.setdefault(cid, []).append({
                "file": full,
                "time": time_str,
                "mtime": mtime,
                "size": size,
                "name": info.get("name", ""),
                "word_count": info.get("word_count", 0),
                "chapter_title": info.get("chapter_title", ""),
                "chapter_volume": info.get("chapter_volume", ""),
            })

        result = []
        for cid, versions in grouped.items():
            versions.sort(key=lambda v: v["mtime"], reverse=True)
            # 计算字数变化（与同组的更早一条比）
            for i, v in enumerate(versions):
                if i + 1 < len(versions):
                    prev_wc = versions[i + 1].get("word_count", 0)
                    cur_wc = v.get("word_count", 0)
                    v["word_delta"] = (
                        (cur_wc - prev_wc) if (prev_wc and cur_wc) else None)
                else:
                    v["word_delta"] = None
            ch = book_chapters.get(cid)
            if ch is not None:
                title = ch.get("title", "")
                in_book = True
            else:
                # 章节已删除：从快照元数据里取标题（versions 已按
                # mtime 倒序，取最新一条有标题的）。
                # 之前这里直接给空串，UI 要靠 version.chapter_title 兜底；
                # 但那个字段有时因旧数据缺失，会给用户看到"（未知章节）"。
                title = ""
                for v in versions:
                    snap_title = (v.get("chapter_title") or "").strip()
                    if snap_title:
                        title = snap_title
                        break
                in_book = False
            result.append({
                "chapter_id": cid,
                "title": title,
                "in_book": in_book,
                "versions": versions,
            })

        # 已删章节排前（in_book=False 先），同状态按 chapter_id 升序
        result.sort(key=lambda g: (g["in_book"], g["chapter_id"]))
        return result

    def restore_version(self, chapter_id: str, version_file: str):
        c = self.get_chapter(chapter_id)
        if c is None or not os.path.exists(version_file):
            return
        # 覆盖前再给当前状态留一份快照
        self.create_snapshot(chapter_id)
        with open(version_file, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        self.write_chapter(chapter_id, text)

    def restore_version_as_new_chapter(self, version_file: str,
                                        title: str = "",
                                        volume: str = "") -> Optional[dict]:
        """把快照内容另存为一个**新章节**，返回新章节 dict。

        与 restore_version 的区别：
        - restore_version 覆盖原章节（要求章节还在）
        - 本方法总是新建，用于恢复已删除的章节

        参数：
          title   新章节标题；空串时按文件名里的章节号自动生成
          volume  归入的卷名；空串 = 默认卷「正文」

        返回 None 表示快照文件不可读。
        """
        if not version_file or not os.path.exists(version_file):
            return None
        try:
            with open(version_file, "r", encoding="utf-8",
                      errors="ignore") as f:
                text = f.read()
        except OSError as e:
            logger.warning(f"读快照失败 {version_file}: {e}")
            return None

        if not title:
            basename = os.path.basename(version_file)
            m = re.match(r"^(\d+)_", basename)
            cid = m.group(1) if m else "?"
            title = f"恢复的章节 {cid}"

        new_ch = self.add_chapter(title, volume=(volume or ""))
        self.write_chapter(new_ch["id"], text)
        return new_ch
    # ============================================================
    # 导入 / 导出
    # ============================================================

    @staticmethod
    def detect_chapter_structure(text: str) -> dict:
        """扫描文本，识别卷 / 章结构。

        返回：
            {
              "volumes": ["第一卷 风起", "第二卷 云涌"],   # 出现的卷名（去重，按顺序）
              "chapter_count": 36,                          # 章数
              "has_volumes": True,                          # 是否有卷结构
            }

        用途：导入前预检，决定是否弹「按卷章分层导入」确认对话框。
        """
        volumes: List[str] = []
        chapter_count = 0
        if not text:
            return {"volumes": [], "chapter_count": 0, "has_volumes": False}
        for line in text.splitlines():
            if _VOLUME_RE.match(line):
                v = line.strip()
                if v and v not in volumes:
                    volumes.append(v)
            elif _CHAPTER_RE.match(line):
                chapter_count += 1
        return {
            "volumes": volumes,
            "chapter_count": chapter_count,
            "has_volumes": len(volumes) > 0,
        }

    @staticmethod
    def _extract_chapter_num(name: str) -> Optional[int]:
        """从文件名里提取章号（中/阿拉伯数字都认）。

        支持：
            第一章 xxx.txt      → 1
            第1章 xxx.txt       → 1
            001_xxx.txt         → 1
            Chapter 3.txt       → 3（兜底：找第一个阿拉伯数字）
        识别失败返回 None（排序时落到字符串比较）。
        """
        stem = os.path.splitext(name)[0].strip()

        # 优先：去掉开头的纯数字前缀（"001_" / "001-" / "001 "）
        m = re.match(r"^(\d+)\s*[_\-.\u3000]+\s*", stem)
        if m:
            return int(m.group(1))

        # 其次：找"第X章/回/节"结构
        m = _CHAPTER_RE.search(stem)
        if m:
            raw = m.group(0)
            # 阿拉伯数字优先
            m2 = re.search(r"([0-9]+)", raw)
            if m2:
                return int(m2.group(1))
            # 中文数字
            m3 = re.search(r"([零一二三四五六七八九十百千万]+)", raw)
            if m3:
                try:
                    from .cn_number import cn_to_int
                    return cn_to_int(m3.group(1))
                except Exception:
                    pass

        # 兜底：找第一个阿拉伯数字
        m = re.search(r"(\d+)", stem)
        if m:
            return int(m.group(1))

        return None

    @staticmethod
    def _natural_key(name: str):
        """自然排序 key：'10' > '9'，'第10章' > '第2章'。"""
        return [
            int(c) if c.isdigit() else c.lower()
            for c in re.split(r"(\d+)", name or "")
        ]

    def _import_structured_text(self, text: str, fallback_title: str,
                                 use_volumes: bool = True) -> int:
        """从纯文本按卷/章结构导入。TXT / DOCX 共用。

        use_volumes=True 时：
        - 遇到"第X卷/部/篇/集" → 切换当前卷，卷名不写入正文
        - 遇到"第X章/回/节"     → 新章节，归属当前卷
        use_volumes=False 时：忽略卷名，全部当章节处理。
        """
        lines = text.splitlines()
        new_count = 0
        current_title: Optional[str] = None
        current_volume = ""
        new_volumes: List[str] = []
        buf: List[str] = []

        def _flush():
            nonlocal new_count, current_title, buf
            if current_title is not None:
                ch = self.add_chapter(
                    current_title.strip(), volume=current_volume)
                self.write_chapter(ch["id"],
                                   "\n".join(buf).strip() + "\n")
                new_count += 1
            buf = []

        for line in lines:
            if use_volumes and _VOLUME_RE.match(line):
                _flush()
                current_title = None
                vol_name = line.strip()
                current_volume = vol_name
                if vol_name not in new_volumes:
                    new_volumes.append(vol_name)
                continue
            if _CHAPTER_RE.match(line):
                _flush()
                current_title = line.strip()
                continue
            buf.append(line)

        _flush()

        if new_volumes:
            vols = self.meta.setdefault("volumes", [])
            if not isinstance(vols, list):
                vols = []
                self.meta["volumes"] = vols
            changed = False
            for v in new_volumes:
                if v not in vols:
                    vols.append(v)
                    changed = True
            if changed:
                self.save_meta()

        if new_count == 0 and text.strip():
            ch = self.add_chapter(fallback_title)
            self.write_chapter(ch["id"], text)
            new_count = 1

        return new_count

    def import_txt_split(self, file_path: str,
                         pattern: str = None,
                         encoding: str = "",
                         use_volumes: bool = True) -> int:
        """从 TXT 切分导入章节。pattern 参数保留兼容，不再使用。"""
        text = self._read_text_file(file_path, encoding)
        return self._import_structured_text(
            text,
            os.path.splitext(os.path.basename(file_path))[0],
            use_volumes=use_volumes)

    @staticmethod
    def _read_text_file(file_path: str, encoding: str = "") -> str:
        encs = [encoding] if encoding else ["utf-8", "gbk", "utf-16"]
        for enc in encs:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, LookupError):
                continue
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    @staticmethod
    def _read_docx_text(file_path: str) -> Optional[str]:
        """提取 DOCX 全文（段落按行拼接）。读取失败返回 None。"""
        import zipfile
        import xml.etree.ElementTree as ET
        ns_w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        try:
            with zipfile.ZipFile(file_path) as z:
                with z.open("word/document.xml") as f:
                    tree = ET.parse(f)
        except Exception as e:
            logger.warning(f"读 docx 失败: {e}")
            return None
        paragraphs = []
        for p in tree.iter(f"{{{ns_w}}}p"):
            para = "".join(t.text or "" for t in p.iter(f"{{{ns_w}}}t"))
            paragraphs.append(para)
        return "\n".join(paragraphs)

    def import_docx(self, file_path: str,
                    use_volumes: bool = True) -> int:
        """从 DOCX 导入。支持卷章结构（跟 TXT 走同一套正则）。"""
        text = self._read_docx_text(file_path)
        if text is None:
            return 0
        return self._import_structured_text(
            text,
            os.path.splitext(os.path.basename(file_path))[0],
            use_volumes=use_volumes)

    def import_folder(self, folder: str,
                      mode: str = "per_file",
                      use_volumes: bool = True) -> int:
        """从文件夹批量导入。返回成功导入的章节数。

        mode:
          "per_file"     每个文件作为一章（推荐，对应"分章导出"的文件）
          "merge_split"  合并所有文件后按"第X章"切分

        只处理 .txt / .md / .docx 文件，按文件名自然排序。
        """
        if not os.path.isdir(folder):
            return 0

        # 收集文件（只取受支持的扩展名）
        files: List[str] = []
        for name in os.listdir(folder):
            lower = name.lower()
            if not (lower.endswith(".txt") or lower.endswith(".md")
                    or lower.endswith(".markdown")
                    or lower.endswith(".docx")):
                continue
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                files.append(full)

        if not files:
            return 0

        # 排序：优先按从文件名提取的章号（中/阿拉伯都认），
        # 没章号时按文件名自然排序（001 < 002 < 010）
        def _natkey(p: str):
            base = os.path.basename(p)
            num = self._extract_chapter_num(base)
            if num is not None:
                return (0, num, self._natural_key(base))
            return (1, 0, self._natural_key(base))
        files.sort(key=_natkey)

        if mode == "merge_split":
            return self._import_folder_merge_split(files, use_volumes)

        # 默认：每个文件作为一章
        return self._import_folder_per_file(files, use_volumes)

    def _import_folder_per_file(self, files: List[str],
                                 use_volumes: bool) -> int:
        """每个文件 = 一章。文件名作为标题（去掉 "001_" 前缀）。"""
        n = 0
        for path in files:
            lower = path.lower()
            base = os.path.splitext(os.path.basename(path))[0]

            # 标题提取：去掉前导 "<数字>_" 或 "<数字>." 或 "<数字>-"
            title = re.sub(r"^\s*\d+\s*[_\-.\u3000]+\s*", "", base).strip()
            if not title:
                title = base

            # 读内容
            text = ""
            try:
                if lower.endswith(".docx"):
                    t = self._read_docx_text(path)
                    text = t or ""
                else:
                    text = self._read_text_file(path)
            except Exception as e:
                logger.warning(f"读取失败 {path}: {e}")
                continue

            # 判断文件里是否有卷名行：有卷结构时按卷章切，没卷时整份作为一章
            struct = self.detect_chapter_structure(text)
            if use_volumes and struct.get("has_volumes"):
                # 走完整卷章导入
                self._import_structured_text(
                    text, title, use_volumes=True)
                n += max(struct.get("chapter_count", 0), 1)
            elif struct.get("chapter_count", 0) > 1:
                # 一个文件里多章 → 也走卷章导入（不按卷）
                self._import_structured_text(
                    text, title, use_volumes=False)
                n += struct["chapter_count"]
            else:
                # 整份文件作为一章
                ch = self.add_chapter(title)
                self.write_chapter(ch["id"], text)
                n += 1
        return n

    def _import_folder_merge_split(self, files: List[str],
                                    use_volumes: bool) -> int:
        """所有文件拼接后按卷/章切分。"""
        chunks: List[str] = []
        for path in files:
            lower = path.lower()
            try:
                if lower.endswith(".docx"):
                    t = self._read_docx_text(path) or ""
                else:
                    t = self._read_text_file(path)
            except Exception:
                continue
            if t.strip():
                chunks.append(t)
        if not chunks:
            return 0
        merged = "\n\n".join(chunks)
        return self._import_structured_text(
            merged, "导入章节", use_volumes=use_volumes)

    def import_markdown(self, file_path: str,
                        use_volumes: bool = True) -> int:
        """从 Markdown 导入。

        有 # 标题 → 走 Markdown 逻辑：
          - 标题文本是"第X卷/部/篇/集"格式 → 卷
          - 其它标题 → 章节
        无 # 标题 → 回退到 TXT 逻辑（用卷/章正则识别）。
        """
        text = self._read_text_file(file_path)
        lines = text.splitlines()
        rx = re.compile(r"^\s*(#{1,3})\s+(.+)$")

        # 检测有没有 Markdown 标题
        has_md_title = any(rx.match(ln) for ln in lines)

        if not has_md_title:
            # 没有 Markdown 标题：走 TXT 逻辑
            return self._import_structured_text(
                text,
                os.path.splitext(os.path.basename(file_path))[0],
                use_volumes=use_volumes)

        # 有 Markdown 标题：走 Markdown 逻辑（识别卷名）
        new_count = 0
        current_title: Optional[str] = None
        current_volume = ""
        new_volumes: List[str] = []
        buf: List[str] = []

        def _flush():
            nonlocal new_count, current_title, buf
            if current_title is not None:
                ch = self.add_chapter(
                    current_title.strip(), volume=current_volume)
                self.write_chapter(ch["id"],
                                   "\n".join(buf).strip() + "\n")
                new_count += 1
            buf = []

        for line in lines:
            m = rx.match(line)
            if m:
                title_text = m.group(2).strip()
                is_volume = use_volumes and bool(_VOLUME_RE.match(title_text))
                _flush()
                if is_volume:
                    current_title = None
                    current_volume = title_text
                    if title_text not in new_volumes:
                        new_volumes.append(title_text)
                else:
                    current_title = title_text
            else:
                buf.append(line)
        _flush()

        if new_volumes:
            vols = self.meta.setdefault("volumes", [])
            if not isinstance(vols, list):
                vols = []
                self.meta["volumes"] = vols
            changed = False
            for v in new_volumes:
                if v not in vols:
                    vols.append(v)
                    changed = True
            if changed:
                self.save_meta()

        if new_count == 0 and text.strip():
            ch = self.add_chapter(
                os.path.splitext(os.path.basename(file_path))[0])
            self.write_chapter(ch["id"], text)
            new_count = 1

        return new_count

    def _exportable_chapters(self, only_done: bool = False) -> list:
        """导出用章节序列：only_done 时仅保留已完成章节（顺序不变）。"""
        if not only_done:
            return self.chapters
        return [c for c in self.chapters
                if c.get("status") == CHAPTER_STATUS_DONE]

    def export_merged(self, include_titles: bool = True, blank_between: bool = False,
                      volume_headers: bool = False, only_done: bool = False) -> str:
        parts: List[str] = []
        last_volume = None
        sep = "\n\n" if blank_between else "\n"
        for c in self._exportable_chapters(only_done):
            vol = c.get("volume", "")
            if volume_headers and vol and vol != last_volume:
                parts.append(vol)
                last_volume = vol
            if include_titles:
                parts.append(c.get("title", ""))
            parts.append(self.read_chapter(c["id"]).strip())
        return sep.join(p for p in parts if p is not None) + "\n"

    def export_chapter_files(self, out_dir: str, fmt: str = "utf8_txt",
                              only_done: bool = False,
                              volume_headers: bool = True,
                              seq_prefix: bool = False) -> int:
        """分章导出：每章一个独立文件，返回成功写出的文件数。

        参数：
          out_dir        输出目录（必须存在）
          fmt            utf8_txt / gbk_txt / md
          only_done      True 时只导出已完成章节
          volume_headers True 时，每章标题前面加卷名标签
          seq_prefix     True 时文件名加 "0001_" 前缀；False 时直接用章节标题

        文件命名：
          seq_prefix=True  →  0001_第一章 山雨欲来.txt
          seq_prefix=False →  第一章 山雨欲来.txt
        """
        import re as _re

        def _safe_filename(name: str) -> str:
            """Windows 非法字符替换为 _，去掉首尾空格和点。"""
            name = _re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "")
            name = name.strip().strip(".")
            return name or "未命名"

        chapters = self._exportable_chapters(only_done)
        if not chapters:
            return 0

        os.makedirs(out_dir, exist_ok=True)

        ext = {
            "utf8_txt": ".txt",
            "gbk_txt": ".txt",
            "md": ".md",
        }.get(fmt, ".txt")
        encoding = "gbk" if fmt == "gbk_txt" else "utf-8"

        used_names = set()
        count = 0

        for idx, c in enumerate(chapters, start=1):
            title = (c.get("title") or "未命名章节").strip()
            body = self.read_chapter(c["id"]).strip()
            vol = (c.get("volume") or "").strip()

            if fmt == "md":
                header = f"## {title}"
                if volume_headers and vol:
                    header = f"# {vol}\n\n{header}"
                body = f"{header}\n\n{body}"
            else:
                if volume_headers and vol:
                    body = f"{vol}\n\n{title}\n\n{body}"
                else:
                    body = f"{title}\n\n{body}"

            if seq_prefix:
                fname = f"{idx:04d}_{_safe_filename(title)}{ext}"
            else:
                fname = f"{_safe_filename(title)}{ext}"

            if fname in used_names:
                stem, dot_ext = os.path.splitext(fname)
                n = 2
                while f"{stem} ({n}){dot_ext}" in used_names:
                    n += 1
                fname = f"{stem} ({n}){dot_ext}"
            used_names.add(fname)

            path = os.path.join(out_dir, fname)
            atomic_write_text(path, body + "\n", encoding=encoding)
            count += 1

        return count

    def export_markdown(self, include_titles: bool = True,
                        blank_between: bool = False,
                        volume_headers: bool = False,
                        only_done: bool = False) -> str:
        parts: List[str] = []
        sep = "\n\n" if blank_between else "\n"
        last_volume = None
        for c in self._exportable_chapters(only_done):
            vol = c.get("volume", "")
            if volume_headers and vol and vol != last_volume:
                parts.append(f"# {vol}")
                last_volume = vol
            title = c.get("title", "")
            if include_titles and title:
                parts.append(f"## {title}")
            body = self.read_chapter(c["id"]).strip()
            if body:
                parts.append(body)
        return sep.join(p for p in parts if p) + "\n"

    def export_html(self, include_titles: bool = True,
                    volume_headers: bool = True,
                    only_done: bool = False) -> str:
        import html as html_mod
        vol_parts: List[str] = []
        last_volume = None
        for c in self._exportable_chapters(only_done):
            vol = c.get("volume", "")
            if volume_headers and vol and vol != last_volume:
                vol_parts.append(f"<h3>{html_mod.escape(vol)}</h3>")
                last_volume = vol
            title = c.get("title", "")
            if include_titles and title:
                vol_parts.append(f"<h2>{html_mod.escape(title)}</h2>")
            body = self.read_chapter(c["id"]).strip()
            for para in body.split("\n"):
                para = para.strip()
                if para:
                    vol_parts.append(f"<p>{html_mod.escape(para)}</p>")
                else:
                    vol_parts.append("<br>")
        body_html = "\n".join(vol_parts)
        return (f'<html xmlns:o="urn:schemas-microsoft-com:office:office" '
                f'xmlns:w="urn:schemas-microsoft-com:office:word" '
                f'xmlns="http://www.w3.org/TR/REC-html40">'
                f'<head><meta charset="utf-8"><title>{html_mod.escape(self.title)}</title>'
                f'<style>body{{font-family:"Microsoft YaHei",sans-serif;'
                f'line-height:1.8;}} h2{{font-size:18px;}} h3{{font-size:16px;}}</style>'
                f'</head><body>{body_html}</body></html>')

    def export_docx(self, out_path: str,
                    include_titles: bool = True,
                    volume_headers: bool = True,
                    only_done: bool = False) -> None:
        """导出为真正的 DOCX（Office Open XML）。

        手动构造最小 DOCX zip 包，不依赖 python-docx。
        DOCX 最少需要 3 个文件：
          [Content_Types].xml
          _rels/.rels
          word/document.xml
        """
        import zipfile
        import html as html_mod

        def esc(s: str) -> str:
            return html_mod.escape(s or "", quote=False)

        # 收集段落：(style, text)
        #   "h1" 卷名（居中加粗大字）
        #   "h2" 章节名（加粗中字）
        #   "p"  正文（首行缩进 2 字符）
        paragraphs: List[tuple] = []
        last_volume = None
        for c in self._exportable_chapters(only_done):
            vol = c.get("volume", "")
            if volume_headers and vol and vol != last_volume:
                paragraphs.append(("h1", vol))
                last_volume = vol
            if include_titles:
                title = c.get("title", "")
                if title:
                    paragraphs.append(("h2", title))
            body = self.read_chapter(c["id"])
            for line in body.split("\n"):
                line = line.rstrip()
                if line:
                    paragraphs.append(("p", line))

        # 拼 word/document.xml
        doc_parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
            '<w:body>',
        ]
        for style, text in paragraphs:
            if style == "h1":
                ppr = '<w:pPr><w:jc w:val="center"/><w:spacing w:before="240" w:after="240"/></w:pPr>'
                rpr = '<w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr>'
            elif style == "h2":
                ppr = '<w:pPr><w:spacing w:before="120" w:after="120"/></w:pPr>'
                rpr = '<w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr>'
            else:
                ppr = '<w:pPr><w:ind w:firstLineChars="200" w:firstLine="480"/></w:pPr>'
                rpr = ''
            doc_parts.append(
                f'<w:p>{ppr}<w:r>{rpr}'
                f'<w:t xml:space="preserve">{esc(text)}</w:t>'
                f'</w:r></w:p>'
            )
        doc_parts.append('<w:sectPr/></w:body></w:document>')
        doc_xml = "".join(doc_parts)

        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>'
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            '</Relationships>'
        )

        tmp = out_path + ".tmp"
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("[Content_Types].xml", content_types)
                z.writestr("_rels/.rels", rels)
                z.writestr("word/document.xml", doc_xml)
            os.replace(tmp, out_path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

class WorkStore:
    """作品集合管理（书架）"""

    def __init__(self, data_root: Optional[str] = None):
        self.data_root = data_root or get_data_root()
        self.works_dir = os.path.join(self.data_root, "works")
        os.makedirs(self.works_dir, exist_ok=True)

    @property
    def trash_dir(self) -> str:
        d = os.path.join(self.data_root, ".trash")
        os.makedirs(d, exist_ok=True)
        return d

    def list_works(self) -> List[dict]:
        result = []
        if not os.path.isdir(self.works_dir):
            return result
        for name in os.listdir(self.works_dir):
            if name.startswith("."):
                continue
            root = os.path.join(self.works_dir, name)
            if not os.path.isdir(root):
                continue
            info = self._read_work_meta(root)
            if info is not None:
                result.append(info)
        result.sort(key=lambda x: x["updated_at"], reverse=True)
        return result

    def _read_work_meta(self, root: str) -> Optional[dict]:
        book = os.path.join(root, "book.json")
        if not os.path.exists(book):
            return None
        try:
            with open(book, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if not isinstance(meta, dict):
                return None
            # 回收站里的软删章节（保留 30 天）不计入封面上的字数/章数，
            # 口径与 Work.chapters / Work.total_words 保持一致
            active_chapters = [c for c in meta.get("chapters", [])
                               if not c.get("deleted")]
            words = sum(int(c.get("word_count", 0))
                        for c in active_chapters)
            return {
                "title": meta.get("title", os.path.basename(root)),
                "dir": os.path.basename(root),
                "path": root,
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "words": words,
                "chapter_count": len(active_chapters),
                "author": meta.get("author", ""),
                "description": meta.get("description", ""),
                "status": meta.get("status", ""),
                "cover": os.path.join(root, "cover.png"),
            }
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"读取作品信息失败 {os.path.basename(root)}: {e}")
            return None

    def create_work(self, title: str, author: str = "", description: str = "") -> Work:
        dir_name = safe_dir_name(title)
        root = os.path.join(self.works_dir, dir_name)
        if os.path.exists(root):
            i = 2
            while os.path.exists(f"{root}_{i}"):
                i += 1
            root = f"{root}_{i}"
        os.makedirs(os.path.join(root, "chapters"), exist_ok=True)
        meta = {
            "title": title,
            "author": author,
            "description": description,
            "daily_target": 0,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": "",
            "chapters": [],
        }
        atomic_write_json(os.path.join(root, "book.json"), meta)
        return Work(root, meta, versions_root=self.data_root)

    def open_work(self, path: str) -> Optional[Work]:
        """打开作品。

        book.json 缺失或损坏时，自动从 chapters/ 目录重建章节列表；
        损坏的 book.json 另存为 book.json.corrupted_<时间戳> 保留。
        """
        if not os.path.isdir(path):
            return None
        book = os.path.join(path, "book.json")
        if not os.path.exists(book):
            return self._rebuild_from_chapters(path)
        try:
            with open(book, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if not isinstance(meta, dict):
                raise ValueError("book.json 不是有效的 JSON 对象")
            return Work(path, meta, versions_root=self.data_root)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error(f"打开作品失败 {path}: {e}")
            return self._rebuild_from_chapters(path, corrupted_book=book)

    def _rebuild_from_chapters(self, path: str,
                               corrupted_book: str = None) -> Optional[Work]:
        """book.json 损坏/缺失时，从 chapters/ 目录扫描章节文件重建元数据。

        - chapters/ 不存在或无 txt 文件 → 返回 None
        - 有文件 → 备份损坏的 book.json，按文件名排序重建章节列表
        """
        chapters_dir = os.path.join(path, "chapters")
        if not os.path.isdir(chapters_dir):
            return None
        # 备份损坏的 book.json
        if corrupted_book and os.path.exists(corrupted_book):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                shutil.copy2(corrupted_book,
                             corrupted_book + f".corrupted_{ts}")
                logger.info(f"已备份损坏的 book.json: {corrupted_book}.corrupted_{ts}")
            except OSError as e:
                logger.warning(f"备份损坏的 book.json 失败: {e}")
        # 扫描 chapters/*.txt，按文件名排序
        chapters = []
        for fname in sorted(os.listdir(chapters_dir)):
            if not fname.endswith(".txt") or fname.endswith(".bak"):
                continue
            m = re.match(r"^(\d+)\.txt$", fname)
            if not m:
                continue
            cid = m.group(1)
            full_path = os.path.join(chapters_dir, fname)
            content = ""
            try:
                with open(full_path, "r", encoding="utf-8",
                          errors="ignore") as f:
                    content = f.read()
                word_count = count_words(content)
            except OSError:
                word_count = 0
            try:
                mtime = datetime.fromtimestamp(
                    os.path.getmtime(full_path)).strftime("%Y-%m-%d %H:%M:%S")
            except OSError:
                mtime = ""
            # 从正文首行提取标题（如"第一章 山雨欲来"），
            # 提取不到才用兜底名。避免几百章全变成"恢复的章节 0001"。
            title = f"恢复的章节 {cid}"
            if content:
                for line in content.splitlines():
                    s = line.strip()
                    if not s:
                        continue
                    # 首行不太长就作为标题；过长说明正文直接开头
                    if len(s) <= 80:
                        title = s
                    break
            chapters.append({
                "id": cid,
                "title": title,
                "file": fname,
                "volume": "",
                "status": CHAPTER_STATUS_DRAFT,
                "word_count": word_count,
                "created_at": mtime,
                "updated_at": mtime,
            })
        if not chapters:
            logger.warning(f"chapters 目录为空，无法恢复: {path}")
            return None
        title = os.path.basename(path.rstrip("/\\")) or "恢复的作品"
        meta = {
            "title": title,
            "author": "",
            "description": "（book.json 损坏，已从 chapters 目录自动恢复章节列表）",
            "daily_target": 0,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": "",
            "chapters": chapters,
        }
        try:
            atomic_write_json(os.path.join(path, "book.json"), meta)
        except OSError as e:
            logger.warning(f"写回重建的 book.json 失败: {e}")
        logger.info(f"已从 chapters 目录重建元数据: {path}（{len(chapters)} 章）")
        return Work(path, meta, versions_root=self.data_root)

    def delete_work(self, path: str, delete_versions: bool = True):
        """把作品移到回收站（不再直接删除）。

        参数 delete_versions 保留兼容性，但已不再使用——
        历史快照按作品名存放在 versions/<标题>/，作品被恢复后仍能访问。
        """
        if not os.path.isdir(path):
            raise ValueError("作品路径不存在")
        try:
            common = os.path.commonpath([self.works_dir, path])
        except ValueError:
            raise ValueError("非法的作品路径")
        if os.path.normcase(common) != os.path.normcase(self.works_dir):
            raise ValueError("非法的作品路径")
        os.makedirs(self.trash_dir, exist_ok=True)
        base_name = os.path.basename(path.rstrip("/\\")) or "未命名作品"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(self.trash_dir, f"{base_name}__{ts}")
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(self.trash_dir, f"{base_name}__{ts}_{n}")
            n += 1
        try:
            shutil.move(path, dest)
        except OSError as e:
            logger.error(f"移入回收站失败: {e}")
            raise
        # 记录原始路径与删除时间
        info = {"original_path": path, "deleted_at": ts, "name": base_name}
        try:
            with open(os.path.join(dest, ".trash_info.json"),
                      "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"写回收站元信息失败: {e}")
        logger.info(f"作品已移入回收站: {path} → {dest}")

    def list_trashed(self) -> List[dict]:
        """列出回收站里的作品，按删除时间倒序。"""
        result = []
        if not os.path.isdir(self.trash_dir):
            return result
        for name in sorted(os.listdir(self.trash_dir), reverse=True):
            root = os.path.join(self.trash_dir, name)
            if not os.path.isdir(root):
                continue
            info = {}
            info_path = os.path.join(root, ".trash_info.json")
            if os.path.exists(info_path):
                try:
                    with open(info_path, "r", encoding="utf-8") as f:
                        info = json.load(f)
                except (OSError, json.JSONDecodeError):
                    pass
            title = info.get("name", name)
            words = 0
            book = os.path.join(root, "book.json")
            if os.path.exists(book):
                try:
                    with open(book, "r", encoding="utf-8") as f:
                        bm = json.load(f)
                    if isinstance(bm, dict):
                        title = bm.get("title", title)
                        words = sum(int(c.get("word_count", 0))
                                    for c in bm.get("chapters", []))
                except (OSError, json.JSONDecodeError):
                    pass
            result.append({
                "path": root,
                "name": name,
                "title": title,
                "deleted_at": info.get("deleted_at", ""),
                "words": words,
            })
        return result

    def restore_work(self, trash_path: str) -> str:
        """把回收站里的作品恢复到 works/ 目录，返回恢复后的绝对路径。"""
        if not os.path.isdir(trash_path):
            raise ValueError("回收站项不存在")
        try:
            common = os.path.commonpath([self.trash_dir, trash_path])
        except ValueError:
            raise ValueError("非法路径")
        if os.path.normcase(common) != os.path.normcase(self.trash_dir):
            raise ValueError("非法路径")
        # 从 ".trash/<名字>__<时间戳>" 或 "....__<时间戳>_2" 中还原原名
        base = os.path.basename(trash_path.rstrip("/\\"))
        # 剥离回收站后缀：__YYYYMMDD_HHMMSS 以及重名时追加的 _n
        m = re.match(r"^(.*?)__\d{8}_\d{6}(?:_\d+)?$", base)
        orig_name = m.group(1) if m else base
        os.makedirs(self.works_dir, exist_ok=True)
        dest = os.path.join(self.works_dir, orig_name or base)
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(self.works_dir, f"{orig_name}_{n}")
            n += 1
        try:
            shutil.move(trash_path, dest)
        except OSError as e:
            logger.error(f"恢复失败: {e}")
            raise
        # 清理 .trash_info.json
        try:
            info_path = os.path.join(dest, ".trash_info.json")
            if os.path.exists(info_path):
                os.remove(info_path)
        except OSError:
            pass
        logger.info(f"作品已从回收站恢复: {dest}")
        return dest

    def permanent_delete(self, trash_path: str):
        """彻底删除（不可恢复），连同该作品的历史快照一起删除。"""
        if not os.path.isdir(trash_path):
            return
        try:
            common = os.path.commonpath([self.trash_dir, trash_path])
        except ValueError:
            raise ValueError("非法路径")
        if os.path.normcase(common) != os.path.normcase(self.trash_dir):
            raise ValueError("非法路径")
        # 读 title 以删除对应历史快照目录
        title = None
        book = os.path.join(trash_path, "book.json")
        if os.path.exists(book):
            try:
                with open(book, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                title = meta.get("title")
            except (OSError, json.JSONDecodeError):
                pass
        shutil.rmtree(trash_path, ignore_errors=True)
        # 版本目录按磁盘目录名存放（见 Work._versions_dir）。
        # 回收站里的目录名是 "<原名>__<时间戳>"，先剥离后缀拿回原名。
        dir_name = os.path.basename(trash_path.rstrip("/\\"))
        m = re.match(r"^(.*?)__\d{8}_\d{6}(?:_\d+)?$", dir_name)
        stable = m.group(1) if m else dir_name
        # 同时清理旧命名（用 title）与新命名（用目录名）下的快照目录
        candidates = {stable}
        if title:
            candidates.add(title)
        for name in candidates:
            vdir = os.path.join(self.data_root, "versions",
                                safe_dir_name(name))
            if os.path.isdir(vdir):
                shutil.rmtree(vdir, ignore_errors=True)
        logger.info(f"已彻底删除: {trash_path}")

    def empty_trash(self) -> int:
        """清空回收站，返回删除的作品数。"""
        if not os.path.isdir(self.trash_dir):
            return 0
        count = 0
        for name in os.listdir(self.trash_dir):
            p = os.path.join(self.trash_dir, name)
            if not os.path.isdir(p):
                continue
            try:
                self.permanent_delete(p)
                count += 1
            except Exception as e:
                logger.warning(f"清空回收站项失败 {name}: {e}")
        return count
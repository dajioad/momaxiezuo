# ui/pet_system/chat_memory.py
"""墨码写作 - 墨灵对话记忆提炼器（B2/B3）。

设计原则（省 token 优先）：
- 只在对话轮数攒够后才偶尔发起，非流式、低温、输出上限几行；
- 每天最多 MAX_PER_DAY 次，两次之间至少 MIN_INTERVAL 秒；
- 失败静默降级（不记忆，不打扰写作）；
- 提炼结果走 PetMemory.add_fact 去重落盘，之后由 persona block
  自然注入后续对话。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal

from utils.ai_client import AIWorker, load_ai_config, has_api_key
from utils.logger import get_logger

logger = get_logger()

MIN_INTERVAL = 600        # 两次提炼最小间隔（秒）
MIN_NEW_TURNS = 4         # 距上次提炼至少新增 4 条 user/assistant 消息
MAX_PER_DAY = 4
MAX_CONTEXT_CHARS = 2400  # 送入提炼的对话总字数上限
MAX_FACTS_ONE_RUN = 4

_EXTRACT_SYSTEM = (
    "你是一个记忆提炼器。从作者与写作助手的对话中，提炼值得长期记住的事实，"
    "只保留三类："
    "1.作者明确的写作偏好、禁忌、风格要求；"
    "2.创作计划与目标（书名、题材、更新安排）；"
    "3.当前作品的稳定设定（主角、世界观、重要人物关系）。"
    "规则：每行一条，以\"- \"开头；每条不超过40字；"
    "不要记录一次性任务请求、寒暄和剧情正文；不要推测或补充；"
    f"最多{MAX_FACTS_ONE_RUN}条；没有值得记的就只输出两个字：无。"
)


class ChatMemoryExtractor(QObject):
    """对话完成后偶尔提炼事实。由 _PetBubble 在一轮回复结束后触发。"""

    fact_saved = Signal(str)

    def __init__(self, memory, config_getter=None, parent=None):
        super().__init__(parent)
        self._memory = memory
        # config_getter: () -> ConfigManager（延迟读取，避免拿旧引用）
        self._get_cfg = config_getter
        self._worker = None
        self._turn_marker = 0          # 上次提炼时的对话轮数位置
        self._last_run_ts = 0.0

    def shutdown(self):
        worker = self._worker
        if worker is not None:
            still_running = False
            try:
                if worker.isRunning():
                    worker.stop()
                    worker.wait(2000)
                    still_running = worker.isRunning()
            except Exception:
                still_running = False
            self._worker = None
            if still_running:
                # 不能仅置空引用：worker 以 self 为 Qt parent，extractor
                # 随后销毁时仍在运行的 QThread 会被连带析构 → abort。
                # 解除父子关系并交给全局 Reaper 保活到结束。
                try:
                    worker.setParent(None)
                    from utils.ai_client import retire_worker
                    retire_worker(worker)
                except Exception:
                    pass

    def _enabled(self) -> bool:
        getter = self._get_cfg
        if getter is None:
            return False
        try:
            cfg = getter()
            if cfg is None:
                return False
            return str(cfg.get_config(
                "Pet", "chat_memory", "1") or "1").strip() == "1"
        except Exception:
            return False

    def maybe_extract(self, messages: list):
        """一轮对话结束后调用。messages 为气泡的完整消息列表。"""
        try:
            if self._memory is None or not self._enabled or not has_api_key():
                return
            if self._worker is not None and self._worker.isRunning():
                return
            now = time.monotonic()
            if now - self._last_run_ts < MIN_INTERVAL:
                return
            turns = [m for m in (messages or [])
                     if isinstance(m, dict)
                     and m.get("role") in ("user", "assistant")
                     and m.get("content")]
            if len(turns) - self._turn_marker < MIN_NEW_TURNS:
                return
            if self._memory.extract_count_today() >= MAX_PER_DAY:
                return

            recent, total = [], 0
            for m in reversed(turns[-12:]):
                piece_len = len(m["content"])
                if total + piece_len > MAX_CONTEXT_CHARS:
                    break
                who = "主人" if m["role"] == "user" else "墨灵"
                recent.append(f"{who}：{m['content'][:600]}")
                total += piece_len
            if len(recent) < 2:
                return
            recent.reverse()
            dialog = "\n".join(recent)

            known = [f.get("text", "") for f in self._memory.facts[-12:]
                     if f.get("text")]
            known_block = ""
            if known:
                known_block = (
                    "已记住的事实（不要重复）：\n"
                    + "\n".join(f"- {t}" for t in known) + "\n\n")
            user_block = f"{known_block}最近的对话：\n{dialog}"

            ai_cfg = load_ai_config()
            worker = AIWorker(
                [
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {"role": "user", "content": user_block},
                ],
                ai_cfg["api_key"],
                ai_cfg["base_url"],
                ai_cfg["model"],
                temperature=0.1,
                stream=False,
                timeout=45,
                parent=self,
            )
            self._worker = worker
            self._last_run_ts = now
            self._turn_marker = len(turns)
            self._memory.bump_extract_today()
            worker.finished_ok.connect(self._on_result)
            worker.failed.connect(self._on_fail)
            worker.finished.connect(self._on_thread_done)
            worker.start()
        except Exception as e:
            logger.debug(f"对话记忆提炼未启动: {e}")
            self._worker = None

    def _on_result(self, text: str):
        try:
            body = (text or "").strip()
            if not body or body.startswith("无"):
                return
            saved = 0
            for raw in body.splitlines():
                line = raw.strip().lstrip("-·•* ").strip()
                if not line or line == "无" or saved >= MAX_FACTS_ONE_RUN:
                    continue
                if self._memory.add_fact(line, src="chat"):
                    saved += 1
                    self.fact_saved.emit(line)
        except Exception as e:
            logger.debug(f"对话记忆解析失败: {e}")

    def _on_fail(self, err: str):
        logger.debug(f"对话记忆提炼失败（已静默跳过）: {err}")

    def _on_thread_done(self):
        worker = self._worker
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception:
                pass
        self._worker = None

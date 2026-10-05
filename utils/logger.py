import logging
import os
import re
import threading
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler


class Logger:
    _instance = None
    _lock = threading.Lock()
    _initialized = False  # 类级别初始化标志（唯一来源）

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, log_dir=None, max_days=7, backup_count=7):
        # 只用类属性判断初始化——旧代码混用实例属性 self._initialized
        # 与类属性 Logger._initialized，逻辑混乱且容易踩坑。
        if Logger._initialized:
            return
        Logger._initialized = True

        if log_dir is None:
            if os.name == 'nt':
                log_dir = os.path.join(os.path.expanduser("~"), "Documents",
                                       "墨码写作", "logs")
            else:
                log_dir = os.path.join(os.path.expanduser("~"), ".moma", "logs")
        self.log_dir = log_dir
        self.max_days = max_days
        self.backup_count = backup_count
        self._ensure_log_dir()

        self.logger = logging.getLogger("MoMa")
        self.logger.setLevel(logging.DEBUG)

        if not self.logger.handlers:
            self._setup_handlers()

    def _ensure_log_dir(self):
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    def _setup_handlers(self):
        # 用 TimedRotatingFileHandler 按天轮转：跨天自动切新文件，
        # 并保留 backup_count 天的历史。
        # 旧代码用 RotatingFileHandler（按大小轮转），产物名是 xxx.log.1
        # 没有日期后缀，_clean_old_logs 的正则匹配不到 → 清理失效。
        # 用固定基础名 moma.log，让 TimedRotatingFileHandler 自己按日期
        # 生成后缀（moma.log.2026-09-20）。之前每天用不同基础名
        # （moma_2026-09-20.log），导致：
        #   1. handler 跨午夜时把当前文件 rename 成 xxx.log.2026-09-20，
        #      再重新打开同一个名字继续写 → 一个文件里混两天日志
        #   2. 每次重启换新基础名，老文件不会被 handler 自动轮转管理
        log_file = os.path.join(self.log_dir, "moma.log")

        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=self.backup_count,
            encoding="utf-8",
        )
        file_handler.suffix = "%Y-%m-%d"   # 轮转产物：moma.log.2026-09-20
        file_handler.setLevel(logging.DEBUG)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        self._clean_old_logs()

    def _clean_old_logs(self):
        """清理超过 max_days 天的日志文件。

        覆盖三类文件名：
          moma_YYYY-MM-DD.log
          moma_YYYY-MM-DD.log.YYYY-MM-DD   （TimedRotatingFileHandler 产物）
          moma_YYYY-MM-DD.log.N             （旧 RotatingFileHandler 产物）
        """
        try:
            now = datetime.now()
            cutoff = now - timedelta(days=self.max_days)
            for fname in os.listdir(self.log_dir):
                if not fname.startswith('moma'):
                    continue
                # 覆盖三类文件名：
                #   moma.log                      当前文件
                #   moma.log.YYYY-MM-DD           新轮转产物
                #   moma_YYYY-MM-DD.log[.N]       旧命名（兼容历史数据）
                if fname == "moma.log":
                    continue
                m = re.match(r'^moma\.log\.(\d{4}-\d{2}-\d{2})', fname)
                if not m:
                    m = re.match(r'^moma_\d{4}-\d{2}-\d{2}\.log(?:\.\d+)?$',
                                 fname)
                    if not m:
                        continue
                file_path = os.path.join(self.log_dir, fname)
                try:
                    # 优先用文件名里的日期（moma.log.2026-09-20）
                    # 而不是 mtime——轮转产物的 mtime 是轮转那一刻，
                    # 不是文件内容的时间。
                    dm = re.search(r'(\d{4}-\d{2}-\d{2})', fname)
                    if dm:
                        fdate = datetime.strptime(dm.group(1), "%Y-%m-%d")
                    else:
                        fdate = datetime.fromtimestamp(
                            os.path.getmtime(file_path))
                    if fdate < cutoff:
                        os.remove(file_path)
                except (OSError, ValueError):
                    pass
        except Exception as e:
            print(f"清理旧日志失败: {e}")

    def debug(self, message):
        self.logger.debug(message)

    def info(self, message):
        self.logger.info(message)

    def warning(self, message):
        self.logger.warning(message)

    def error(self, message, exc_info=False):
        self.logger.error(message, exc_info=exc_info)

    def critical(self, message, exc_info=False):
        self.logger.critical(message, exc_info=exc_info)


def get_logger():
    return Logger()
import os
import json
import configparser
import threading
from typing import Any, Callable, Optional, Dict, List, Tuple
from PySide6.QtCore import QObject, Signal
from .secure_config import SecureConfig
from .logger import get_logger

logger = get_logger()

# 应用目录名（config.ini 所在默认目录、数据根默认目录共用）
APP_DIR_NAME = "墨码写作"


def default_config_dir() -> str:
    """config.ini 固定存放的基础目录，不受「自定义存储位置」影响。

    Windows：我的文档/墨码写作；其他平台：$XDG_CONFIG_HOME/墨码写作。
    """
    if os.name == 'nt':
        return os.path.join(os.path.expanduser("~"), "Documents", APP_DIR_NAME)
    xdg = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser("~/.config"))
    return os.path.join(xdg, APP_DIR_NAME)


def read_custom_data_dir(config_path: str) -> Optional[str]:
    """从 config.ini 读取 Storage/data_dir。

    只有「已存在的绝对路径字符串」才生效；相对路径/解析异常一律返回 None
    （回退默认目录）。ConfigManager 与 work_store.get_data_root 共用此解析，
    避免两处数据源分裂导致备份打空目录。
    """
    try:
        if not os.path.exists(config_path):
            return None
        parser = configparser.RawConfigParser()
        parser.read(config_path, encoding="utf-8")
        if parser.has_option("Storage", "data_dir"):
            custom = (parser.get("Storage", "data_dir") or "").strip()
            if custom and os.path.isabs(custom):
                return custom
    except Exception:
        return None
    return None


class ConfigChangeObserver(QObject):
    config_changed = Signal(str, str, object)
    def __init__(self, callback: Callable):
        super().__init__()
        self.callback = callback
    def notify(self, section: str, key: str, value: Any):
        self.callback(section, key, value)


class ConfigValidator:
    """配置值验证器，提供静态方法验证各类配置参数的合法性"""
    @staticmethod
    def validate_integer(value: str, min_val: int = None, max_val: int = None) -> Tuple[bool, str]:
        """验证字符串是否为合法整数，可选范围检查"""
        if value is None:
            return False, "值为空"
        try:
            v = int(value)
            if min_val is not None and v < min_val:
                return False, f"值必须 ≥ {min_val}"
            if max_val is not None and v > max_val:
                return False, f"值必须 ≤ {max_val}"
            return True, ""
        except ValueError:
            return False, "必须是整数"
    
    @staticmethod
    def validate_positive_integer(value: str) -> Tuple[bool, str]:
        """验证字符串是否为正整数"""
        return ConfigValidator.validate_integer(value, 1)
    
    @staticmethod
    def validate_boolean(value: str) -> Tuple[bool, str]:
        """验证字符串是否为合法布尔值（true/false/1/0/yes/no）"""
        if value.lower() in ('true', 'false', '1', '0', 'yes', 'no'):
            return True, ""
        return False, "必须是布尔值 (true/false)"
    
    @staticmethod
    def validate_theme(value: str, themes: List[str]) -> Tuple[bool, str]:
        """验证主题值是否在可用主题列表中"""
        return (True, "") if value in themes else (False, f"无效主题，可用: {', '.join(themes)}")
    
    @staticmethod
    def validate_language(value: str) -> Tuple[bool, str]:
        """验证语言值是否为支持的语种（zh 或 en）"""
        return (True, "") if value in ('zh', 'en') else (False, "语言必须是 zh 或 en")


class ConfigManager:
    VALIDATION_RULES = {
        "App": {
            "interface_scale": ConfigValidator.validate_positive_integer,
            "max_windows": ConfigValidator.validate_positive_integer,
            "split_mode": lambda v: ConfigValidator.validate_integer(v, 0, 2),
            "3d_to_2d": ConfigValidator.validate_boolean,
        },
        "Symbols": {
            "language": ConfigValidator.validate_language,
        },
        "SymbolPanel": {
            "theme": lambda v: (True, "") if v in ['light', 'dark'] else (False, "主题必须是 light 或 dark"),
        },
        "UI": {
            "animation": ConfigValidator.validate_boolean,
            "font_size": lambda v: ConfigValidator.validate_integer(v, 8, 24),
        },
        "Reminder": {
            "enabled": ConfigValidator.validate_boolean,
            "custom_minutes": ConfigValidator.validate_positive_integer,
        },
        "Backup": {
            "auto_backup": ConfigValidator.validate_boolean,
            "auto_keep": lambda v: ConfigValidator.validate_integer(v, 1, 30),
        },
        "Update": {
            "auto_check": ConfigValidator.validate_boolean,
        },
        "Experience": {
            "auto_remove_blank_lines": ConfigValidator.validate_boolean,
            "auto_remove_extra_spaces": ConfigValidator.validate_boolean,
            "auto_remove_all_whitespace": ConfigValidator.validate_boolean,
            "skip_empty_cells": ConfigValidator.validate_boolean,
        },
    }
    
    CONFIG_METADATA = {
        "App": {"version": "1.0.0", "interface_scale": "100", "max_windows": "5", "3d_to_2d": "false"},
        "TextProcessor": {"max_characters": "1000", "enable_smart_split": "true", "desensitize_level": "2"},
        "Symbols": {"language": "zh", "custom_symbols": "[]", "disabled_default_symbols": "[]", "custom_defaults": "{}"},
        "Formulas": {"disabled": "[]", "custom": "[]", "custom_defaults": "{}"},
        "UI": {"animation": "true", "default_page": "符号处理"},
        "Backup": {"auto_backup": "true", "auto_interval": "每周", "auto_keep": "3",
                   "last_backup_date": ""},
        "File": {"default_path": "", "default_encoding": "UTF-8"},
        "Update": {"auto_check": "false"},
        "Reminder": {"enabled": "false", "interval": "1小时", "custom_minutes": "60", "message": "起来活动一下"},
        "Hotkeys": {"show_window": "Ctrl+Shift+W", "symbol_panel": "Ctrl+Shift+S", "screenshot": "Ctrl+Shift+X"},
        "SymbolPanel": {"theme": "light"},
        "Filter": {"symbols": ""},
        "Writing": {
            "daily_target": "4000",
            "theme": "paper",
            "font_family": "Microsoft YaHei",
            "font_size": "15",
            "line_spacing": "1.8",
            "content_width": "720",
            "typewriter": "false",
            "autosave_interval": "30",
            "tray_behavior": "点击关闭：最小化到托盘",
        },
    }
    
    def __init__(self):
        # config.ini 始终在默认目录；作品数据根目录可被 Storage/data_dir
        # 改到自定义位置（与 work_store.get_data_root 同一解析来源）
        config_home = default_config_dir()
        self.config_dir = config_home
        self.config_path = os.path.join(config_home, "config.ini")
        self.data_dir = read_custom_data_dir(self.config_path) or config_home
        self._cache: Dict[str, Dict[str, str]] = {}
        self._loaded = False
        self._observers: List[ConfigChangeObserver] = []
        self._lock = threading.Lock()
    
    def _load_cache(self):
        """加锁版本。锁外调用。"""
        if self._loaded:
            return
        with self._lock:
            self._load_cache_unlocked()

    def _load_cache_unlocked(self):
        """不加锁版本。调用方必须已持有 self._lock。

        原实现里 get_config / set_config 等都在持锁状态下调 _load_cache，
        而 _load_cache 内部又 with self._lock —— threading.Lock 不可重入，
        一旦 _loaded 还是 False（例如 _load_cache 首次执行抛了磁盘 IO 异常
        后 _loaded 保持 False，或任何绕过 init_config 的调用路径），
        会直接死锁。
        """
        if self._loaded:
            return
        os.makedirs(self.data_dir, exist_ok=True)
        # 使用RawConfigParser禁用interpolation，避免JSON中的%字符引发错误
        cfg = configparser.RawConfigParser()
        try:
            cfg.read(self.config_path, encoding="utf-8")
        except configparser.Error:
            # 写盘中途崩溃/同步盘占用会留下截断的 ini，直接读会抛
            # ParsingError 导致软件打不开。先尝试从最近备份恢复。
            logger.warning("config.ini 解析失败，尝试从备份恢复", exc_info=True)
            if self._restore_config_from_backup():
                cfg = configparser.RawConfigParser()
                cfg.read(self.config_path, encoding="utf-8")
            else:
                # 无可用备份：坏文件改名留证，以默认配置启动
                broken = self.config_path + ".broken"
                try:
                    if os.path.exists(broken):
                        os.remove(broken)
                    os.replace(self.config_path, broken)
                except OSError:
                    pass
                cfg = configparser.RawConfigParser()
        for sec in cfg.sections():
            self._cache[sec] = dict(cfg[sec])
        self._loaded = True

    def _restore_config_from_backup(self) -> bool:
        """从 backups/ 下最近一份可解析的备份恢复 config.ini。"""
        import shutil
        backup_dir = os.path.join(self.data_dir, "backups")
        try:
            candidates = [
                os.path.join(backup_dir, f)
                for f in os.listdir(backup_dir)
                if f.startswith("config_backup_") and f.endswith(".ini")
            ]
        except OSError:
            return False
        for bp in sorted(candidates, key=os.path.getmtime, reverse=True):
            parser = configparser.RawConfigParser()
            try:
                parser.read(bp, encoding="utf-8")
            except configparser.Error:
                continue
            try:
                shutil.copy2(bp, self.config_path)
                logger.warning(f"已从备份恢复 config.ini: {os.path.basename(bp)}")
                return True
            except OSError:
                continue
        return False
    
    def _write_cache(self):
        with self._lock:
            self._write_cache_unlocked()

    def _backup_config_unlocked(self):
        """写盘前备份（**调用方必须已持有 self._lock**）。

        为什么拆出 unlocked 版本：ConfigManager 所有写路径都在锁内，
        旧代码在这里 acquire(blocking=False) 必然失败（锁已被主线程持有），
        导致"备份"实际是空操作、backups/ 目录从未创建。
        """
        try:
            import shutil
            from datetime import datetime

            backup_dir = os.path.join(self.data_dir, "backups")
            os.makedirs(backup_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"config_backup_{timestamp}.ini"
            backup_path = os.path.join(backup_dir, backup_name)

            shutil.copy2(self.config_path, backup_path)
            self._cleanup_old_backups(backup_dir, 5)
        except Exception as e:
            # 备份失败不影响主流程
            logger.warning(f"配置备份失败: {e}")

    def _backup_config(self):
        """外部调用入口（自带锁）。保留供非锁内场景使用。"""
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._backup_config_unlocked()
        finally:
            self._lock.release()
    
    def _cleanup_old_backups(self, backup_dir, keep_count=5):
        """清理旧备份，只保留最近N个"""
        try:
            if not os.path.exists(backup_dir):
                return
                
            # 获取所有备份文件并按修改时间排序
            backup_files = []
            for filename in os.listdir(backup_dir):
                if filename.startswith("config_backup_") and filename.endswith(".ini"):
                    filepath = os.path.join(backup_dir, filename)
                    backup_files.append((filepath, os.path.getmtime(filepath)))
            
            # 按时间从旧到新排序
            backup_files.sort(key=lambda x: x[1])
            
            # 只保留最近keep_count个
            if len(backup_files) > keep_count:
                for filepath, _ in backup_files[:len(backup_files) - keep_count]:
                    try:
                        os.remove(filepath)
                    except Exception as e:
                        logger.debug(f"删除旧备份失败: {e}")
        except Exception as e:
            logger.debug(f"清理旧备份失败: {e}")
    
    def init_config(self):
        """初始化配置：加载缓存，若配置文件不存在则创建默认配置"""
        self._load_cache()
        if not os.path.exists(self.config_path):
            self.create_default_config()
    
    def create_default_config(self):
        """根据 CONFIG_METADATA 创建默认配置文件"""
        for sec, items in self.CONFIG_METADATA.items():
            self._cache[sec] = items.copy()
        self._write_cache()
    
    def get_config(self, section: str, key: str, default: Any = None) -> str:
        """读取配置值，不存在时返回 default"""
        with self._lock:
            self._load_cache_unlocked()
            return self._cache.get(section, {}).get(key, default)
    
    def get_config_typed(self, section: str, key: str, value_type: str, default: Any = None) -> Any:
        """读取配置值并转换为指定类型（integer/boolean/float）"""
        val = self.get_config(section, key)
        if val is None:
            return default
        try:
            if value_type == "integer":
                return int(val)
            elif value_type == "boolean":
                return val.lower() in ('true', '1', 'yes')
            elif value_type == "float":
                return float(val)
            return val
        except (ValueError, TypeError):
            return default
    
    def set_config(self, section: str, key: str, value: Any, validate: bool = True) -> Tuple[bool, str]:
        """设置配置值，可选择是否验证，写入后通知所有观察者"""
        if validate:
            rule = self.VALIDATION_RULES.get(section, {}).get(key)
            if rule:
                ok, msg = rule(str(value))
                if not ok:
                    return False, msg
        with self._lock:
            self._load_cache_unlocked()
            if section not in self._cache:
                self._cache[section] = {}
            self._cache[section][key] = str(value)
            self._write_cache_unlocked()
            # 快照观察者列表，出锁后再通知——避免用户回调里再次调
            # set_config 导致 threading.Lock 不可重入死锁。
            observers_snapshot = list(self._observers) if validate else []
        for obs in observers_snapshot:
            try:
                obs.notify(section, key, value)
            except Exception as e:
                logger.warning(f"配置观察者回调失败: {e}")
        return True, ""
    
    def _write_cache_unlocked(self):
        """不带锁的_write_cache版本，供已持有锁的代码调用"""
        if os.path.exists(self.config_path):
            self._backup_config_unlocked()
        cfg = configparser.RawConfigParser()
        for sec, vals in self._cache.items():
            cfg[sec] = vals
        # 原子写：同目录临时文件写完再 replace，防止写一半崩溃
        # 导致下次启动 config.ini 截断无法解析（软件打不开）。
        tmp_path = self.config_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            cfg.write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.config_path)
        # 写入后不重置 _loaded，因为缓存已经是最新的
    
    def batch_set_config(self, configs: List[Tuple[str, str, Any]], validate: bool = True) -> Tuple[bool, str]:
        with self._lock:
            self._load_cache_unlocked()
            for sec, key, val in configs:
                if validate:
                    rule = self.VALIDATION_RULES.get(sec, {}).get(key)
                    if rule:
                        ok, msg = rule(str(val))
                        if not ok:
                            return False, msg
                if sec not in self._cache:
                    self._cache[sec] = {}
                self._cache[sec][key] = str(val)
            self._write_cache_unlocked()
            # 快照观察者列表，出锁后再通知，避免用户回调里再次调
            # batch_set_config / set_config 造成 Lock 不可重入死锁。
            observers_snapshot = list(self._observers) if validate else []
        for sec, key, val in configs:
            for obs in observers_snapshot:
                try:
                    obs.notify(sec, key, val)
                except Exception as e:
                    logger.warning(f"配置观察者回调失败: {e}")
        return True, ""
    
    def register_observer(self, callback: Callable) -> ConfigChangeObserver:
        with self._lock:
            obs = ConfigChangeObserver(callback)
            self._observers.append(obs)
            return obs
    
    def unregister_observer(self, observer: ConfigChangeObserver):
        with self._lock:
            if observer in self._observers:
                self._observers.remove(observer)
    
    def get_all_configs(self, section: str) -> Dict[str, str]:
        with self._lock:
            self._load_cache_unlocked()
            return self._cache.get(section, {}).copy()
    
    def load_json_data(self, filename: str) -> Optional[dict]:
        path = os.path.join(self.data_dir, filename)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载JSON失败 {filename}: {e}", exc_info=True)
            return None
    
    def save_json_data(self, filename: str, data: dict):
        path = os.path.join(self.data_dir, filename)
        os.makedirs(self.data_dir, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
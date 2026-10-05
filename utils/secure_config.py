# utils/secure_config.py
import os
import re
import sys
import json
import glob
import hashlib
import base64
import hmac
import uuid
import platform
import subprocess

_crypto = None

# 旧版在硬件指纹采集完全失败时使用的兜底常量（直接参与 PBKDF2，未经 SHA256）
_FALLBACK_FINGERPRINT = b"moma_fallback_fingerprint_2026"

def _get_crypto():
    global _crypto
    if _crypto is None:
        try:
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.backends import default_backend
            _crypto = (Fernet, hashes, PBKDF2HMAC, default_backend)
        except ImportError:
            raise ImportError(
                "缺少必需的加密库 'cryptography'。\n"
                "请运行以下命令安装：\n"
                "pip install cryptography\n\n"
                "如果安装后仍然报错，请检查 Python 环境是否正确。"
            )
    return _crypto


def _get_machine_guid() -> str:
    """操作系统级稳定机器标识；取不到返回空串。

    比网卡 MAC 稳定得多：MAC 会随 WiFi/网线切换、VPN/Wintun 虚拟网卡、
    VMware/Hyper-V 适配器启停而改变枚举顺序；MachineGuid 安装系统后
    不变（注册表 / /etc/machine-id / IOPlatformUUID）。
    """
    try:
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography", 0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as k:
                val, _ = winreg.QueryValueEx(k, "MachineGuid")
                return str(val).strip()
        if sys.platform == "darwin":
            out = subprocess.run(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            return m.group(1) if m else ""
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                if v:
                    return v
            except OSError:
                continue
    except Exception:
        return ""
    return ""


def _get_hardware_fingerprint(mac: int = None) -> bytes:
    """旧版指纹：MAC + 系统版本 + 主机名 的 SHA256。

    历史密文全部用它派生，必须原样保留以兼容解密。
    mac=None 时取 uuid.getnode()。
    """
    try:
        machine_id = uuid.getnode() if mac is None else mac
        system_info = f"{platform.system()}{platform.release()}{platform.machine()}"
        hostname = platform.node()
        fingerprint = f"{machine_id}{system_info}{hostname}"
        return hashlib.sha256(fingerprint.encode('utf-8')).digest()
    except Exception:
        # 备用指纹
        return _FALLBACK_FINGERPRINT


def _stable_fingerprint() -> bytes:
    """首选指纹：稳定机器 GUID，不拼接主机名/网卡，避免漂移。

    取不到 GUID 时退化为旧版 MAC 指纹，老环境行为不变。
    """
    guid = _get_machine_guid()
    if guid:
        return hashlib.sha256(b"moma:guid:" + guid.encode("utf-8")).digest()
    return _get_hardware_fingerprint()


def _iter_all_macs():
    """枚举本机全部适配器 MAC（int，去重）。

    uuid.getnode() 在多网卡机器上跨重启可能选中不同适配器
    （VPN/Wintun/VMware/蓝牙/有线无线切换），旧密文可能是用
    其中任意一块网卡的指纹加密的，解密时需要逐一回试。
    """
    seen = set()
    try:
        node = uuid.getnode()
        if node:
            seen.add(node)
            yield node
    except Exception:
        pass
    try:
        text = ""
        if os.name == "nt":
            # CREATE_NO_WINDOW：windowed 打包下直接跑 ipconfig 会闪一个
            # cmd 黑窗（启动解密 AI 配置枚举 MAC 时必现）。
            raw = subprocess.run(
                ["ipconfig", "/all"],
                capture_output=True, timeout=6,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            for enc in ("gbk", "utf-8", "mbcs"):
                try:
                    text = raw.decode(enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            pattern = re.compile(
                r"(?:物理地址|Physical Address)[^\n]*?"
                r"([0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5})")
            matches = pattern.findall(text)
        elif sys.platform == "darwin":
            out = subprocess.run(["ifconfig"], capture_output=True,
                                 text=True, timeout=6,
                                 errors="ignore").stdout
            matches = re.findall(r"ether\s+([0-9a-f:]{17})", out)
            matches = [m.replace(":", "-") for m in matches]
        else:
            matches = []
            for p in glob.glob("/sys/class/net/*/address"):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        matches.append(f.read().strip().replace(":", "-"))
                except OSError:
                    continue
        for m in matches:
            try:
                v = int(m.replace("-", ""), 16)
            except ValueError:
                continue
            if v and v not in seen:
                seen.add(v)
                yield v
    except Exception:
        return


def _salt_paths():
    """salt 候选位置：主位置 ~/.moma（历史沿用），默认配置目录为冗余副本。"""
    paths = [os.path.join(os.path.expanduser("~"), ".moma", "salt.bin")]
    try:
        from .config import default_config_dir
        paths.append(os.path.join(default_config_dir(), "salt.bin"))
    except Exception:
        pass
    return paths


def _get_salt() -> bytes:
    """获取或生成唯一盐值；主位置丢失时用冗余副本回填，两处都写入新盐。"""
    try:
        paths = _salt_paths()
        primary = paths[0]
        for path in paths:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    salt = f.read()
                if len(salt) >= 16:
                    # 从冗余副本读到：回填主位置（可能被清理软件误删）
                    if os.path.abspath(path) != os.path.abspath(primary):
                        try:
                            os.makedirs(os.path.dirname(primary), exist_ok=True)
                            with open(primary, "wb") as f:
                                f.write(salt)
                        except OSError:
                            pass
                    return salt
        # 生成新的盐值
        salt = os.urandom(32)
        for path in paths:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(salt)
            except OSError:
                pass
        return salt
    except Exception:
        # 备用盐值（安全性较低，但能工作）
        return b"moma_salt_2026_fallback"


class SecureConfig:
    _cached_key = None
    _candidate_keys = None

    @classmethod
    def _derive_key(cls, fingerprint: bytes, salt: bytes) -> bytes:
        Fernet, hashes, PBKDF2HMAC, backend = _get_crypto()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=200000,
            backend=backend()
        )
        return base64.urlsafe_b64encode(kdf.derive(fingerprint))

    @classmethod
    def _get_key(cls) -> bytes:
        """首选加密密钥：稳定机器 GUID 指纹（不再随网卡漂移）。"""
        if cls._cached_key is None:
            cls._cached_key = cls._derive_key(_stable_fingerprint(), _get_salt())
        return cls._cached_key

    @classmethod
    def _iter_keys(cls):
        """候选解密密钥序列：首选在前，其后是各网卡旧指纹、兜底常量。

        进程内只派生一次（PBKDF2 代价高），之后复用缓存。
        """
        if cls._candidate_keys is None:
            salt = _get_salt()
            keys = [(True, cls._get_key())]
            seen = {keys[0][1]}
            for mac in _iter_all_macs():
                key = cls._derive_key(_get_hardware_fingerprint(mac), salt)
                if key not in seen:
                    seen.add(key)
                    keys.append((False, key))
            # 历史兜底指纹（未哈希的常量直接参与派生）
            key = cls._derive_key(_FALLBACK_FINGERPRINT, salt)
            if key not in seen:
                keys.append((False, key))
            cls._candidate_keys = keys
        for item in cls._candidate_keys:
            yield item

    @classmethod
    def encrypt(cls, data: str) -> str:
        """加密字符串，输入必须是字符串，输出也是字符串"""
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        elif not isinstance(data, str):
            data = str(data)

        Fernet, _, _, _ = _get_crypto()
        f = Fernet(cls._get_key())
        return f.encrypt(data.encode('utf-8')).decode()

    @classmethod
    def decrypt_with_status(cls, encrypted_data: str) -> tuple:
        """多候选指纹解密，返回 (明文, 是否首选指纹)。

        is_preferred=False 表示密文是用历史网卡指纹加密的（典型于
        uuid.getnode() 漂移），调用方可在成功后用首选密钥重新加密，
        把密文自愈到稳定指纹上。所有候选均失败时抛出底层异常。
        """
        if isinstance(encrypted_data, bytes):
            encrypted_data = encrypted_data.decode('utf-8')
        elif not isinstance(encrypted_data, str):
            encrypted_data = str(encrypted_data)

        token = encrypted_data.encode('utf-8')
        Fernet, _, _, _ = _get_crypto()
        last_error = None
        for preferred, key in cls._iter_keys():
            try:
                decrypted = Fernet(key).decrypt(token)
                return decrypted.decode('utf-8'), preferred
            except Exception as e:
                last_error = e
        raise last_error

    @classmethod
    def decrypt(cls, encrypted_data: str) -> str:
        """解密字符串，输入必须是字符串，输出也是字符串"""
        plain, _ = cls.decrypt_with_status(encrypted_data)
        return plain

    @classmethod
    def hash_string(cls, data: str) -> str:
        """计算字符串的 SHA256 哈希值"""
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        return hashlib.sha256(data.encode('utf-8')).hexdigest()

    @classmethod
    def verify_hash(cls, data: str, hash_val: str) -> bool:
        """验证字符串与哈希值是否匹配"""
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        return hmac.compare_digest(cls.hash_string(data), hash_val)

    @classmethod
    def validate_path(cls, path: str, allowed_dirs: list = None) -> bool:
        """验证路径安全性（防止路径遍历）
        
        Args:
            path: 要验证的路径
            allowed_dirs: 允许的基目录列表，如果提供则路径必须在其中之一
        
        Returns:
            bool: 路径是否安全
        """
        if not isinstance(path, str) or not path:
            return False
        
        # 检查路径遍历
        if '..' in path:
            return False
        
        # 检查波浪号扩展（用户目录）
        # 检查波浪号扩展（用户目录）
        has_tilde = "~" in path
        has_userprofile = (os.name == "nt" and "%USERPROFILE%" in path.upper())
        if has_tilde or has_userprofile:
            # 如果明确允许用户目录，则展开后检查
            expanded = os.path.expanduser(path)
            if allowed_dirs:
                norm_expanded = os.path.normpath(os.path.abspath(expanded))
                for d in allowed_dirs:
                    if norm_expanded.startswith(os.path.normpath(os.path.abspath(d))):
                        return True
            return False
        
        # 检查环境变量路径（Windows）
        if os.name == 'nt' and '%' in path:
            # 展开环境变量后检查
            import ctypes
            expanded = ctypes.windll.shell32.ExpandEnvironmentStringsW(path)
            if expanded != path:
                return cls.validate_path(expanded, allowed_dirs)
        
        # 规范化路径
        try:
            norm_path = os.path.normpath(os.path.abspath(path))
        except (OSError, ValueError):
            return False
        
        # 检查绝对路径攻击（Windows）
        if os.name == 'nt':
            # 检查盘符切换（如 C:\ 或 \\server\share）
            if re.match(r'^[a-zA-Z]:[/\\]', norm_path) or norm_path.startswith('\\\\'):
                if allowed_dirs:
                    for d in allowed_dirs:
                        allowed_norm = os.path.normpath(os.path.abspath(d))
                        if norm_path.lower().startswith(allowed_norm.lower()):
                            return True
                    return False
                # 如果没有白名单，拒绝网络路径
                if norm_path.startswith('\\\\'):
                    return False
        
        # 检查白名单
        if allowed_dirs:
            for d in allowed_dirs:
                allowed_norm = os.path.normpath(os.path.abspath(d))
                if norm_path.lower().startswith(allowed_norm.lower()):
                    return True
            return False
        
        return True

    @classmethod
    def validate_filename(cls, filename: str) -> bool:
        """验证文件名安全性（禁止特殊字符和控制字符）"""
        if not isinstance(filename, str) or not filename:
            return False
        
        # 禁止文件系统特殊字符
        invalid_chars = r'\/:*?"<>|'
        if any(c in invalid_chars for c in filename):
            return False
        
        # 禁止空文件名或仅包含空白
        if not filename.strip():
            return False
        
        # 禁止 Unicode 控制字符（U+0000 到 U+001F 和 U+007F）
        for c in filename:
            if '\x00' <= c <= '\x1f' or c == '\x7f':
                return False
        
        return True


class SecureConfigManager:
    """安全配置文件管理器（用于存储激活信息等）"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self._ensure_dir()

    def _ensure_dir(self):
        """确保配置目录存在"""
        d = os.path.dirname(self.config_path)
        if d:
            os.makedirs(d, exist_ok=True)

    def save_activation(self, data: dict):
        """保存激活信息（完整加密）"""
        # 序列化为 JSON 字符串
        data_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
        encrypted = {
            'data': SecureConfig.encrypt(data_str),
            'hash': SecureConfig.hash_string(data_str)
        }
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(encrypted, f, indent=2, ensure_ascii=False)

    def load_activation(self) -> dict:
        """加载激活信息（完整解密并验证）"""
        if not os.path.exists(self.config_path):
            return {}
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                encrypted = json.load(f)
            if 'data' not in encrypted or 'hash' not in encrypted:
                return {}
            plain = SecureConfig.decrypt(encrypted['data'])
            if SecureConfig.verify_hash(plain, encrypted['hash']):
                return json.loads(plain)
            else:
                return {}
        except Exception:
            return {}
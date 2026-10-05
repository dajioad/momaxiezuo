# utils/updater.py
"""墨码写作 - 更新检查（纯逻辑，无 Qt 依赖）

设计：
- 双源竞速：主源 + 镜像源，谁先返回用谁
- 每天最多检查一次（由调用方控制，本模块只做请求和比较）
- 失败静默，不打扰用户
- **签名校验**：latest.json 里的 sha256 字段由官方私钥签名过
"""

import base64
import re
import concurrent.futures
from typing import Optional, Tuple, List

import requests

from .logger import get_logger

logger = get_logger()

# ============ 你只需要改这里 ============
# 2026-10 起更新通道迁移到 Gitee（自有服务器即将下线）：
# Gitee 为主源；旧服务器保留为过渡期备用，确认下线后可把这一行删掉。
PRIMARY_SOURCE = "https://gitee.com/momaxiezuo/ink-code-writing/raw/master/latest.json"
LEGACY_SERVER_SOURCE = "http://39.96.64.137/latest.json"
MIRROR_SOURCE = LEGACY_SERVER_SOURCE
# ======================================

FETCH_TIMEOUT = 5.0

UPDATE_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwJUkuh+mH0T68TswH1ol
CST8ZmK/dzU518kjrhRUl5PsKMvncaNaEu6XRVOYkDGWVGEczako2oX5O/nQj4dF
rqKgIbtZkdlOUZLsE927EKQa6PcvpcMpGRygkVmM8Gq3NaaEfZDwtA5e6Alp7lOV
urUNeQb3HbPsj7FJWxNg93gdgr9S2LlwbbuDLL+uBnDCmQxtjP/FP7kkDvpVv0fa
pH6dNtwJcOfYRJtOuOIyf1I2Tw52hdW27MOyaB7UpdrEnj7CS/kldrWUsIv6gAEJ
mv+s7PSfM5K24RFbwaSmMeiUb+JGl2Vkw2TTfBkRh7XCOpX+cGvf4rtoCjdx81C9
bwIDAQAB
-----END PUBLIC KEY-----"""


def _get_current_version() -> str:
    from ui.constants import APP_VERSION
    return APP_VERSION


def parse_version(v: str) -> tuple:
    """把 "2.1.0" / "2.1.0-beta.1" 解析成可比较的元组。"""
    if not v:
        return (0, 0, 0, 1, 0)
    v = v.strip().lstrip("vV")
    m = re.match(
        r"^(\d+)\.(\d+)\.(\d+)(?:[-._]?(alpha|beta|rc|pre)[-._]?(\d*))?$",
        v, re.I)
    if not m:
        return (0, 0, 0, 1, 0)
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    # 预发布版必须排在正式版**之前**：beta 2.2.0 < 正式 2.2.0。
    # 之前写反（预发布=1、正式=0）会导致正式版用户被提示"降级"到 beta。
    pre_flag = 0 if m.group(4) else 1
    pre_num = int(m.group(5) or 0) if m.group(4) else 0
    return (major, minor, patch, pre_flag, pre_num)


def compare_version(current: str, latest: str) -> int:
    """-1: current 落后 / 0: 相同 / 1: current 领先"""
    c, l = parse_version(current), parse_version(latest)
    return -1 if c < l else (1 if c > l else 0)


def verify_manifest(manifest: dict) -> bool:
    """验证 latest.json 里的 sha256 字段确实由官方私钥签名过。"""
    sha256_hex = str(manifest.get("sha256", "") or "").strip()
    signature_b64 = str(manifest.get("signature", "") or "").strip()
    if not sha256_hex or not signature_b64:
        logger.warning("latest.json 缺少 sha256/signature 字段，拒绝本次更新")
        return False
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256_hex):
        logger.warning(f"latest.json sha256 格式非法: {sha256_hex[:32]}…")
        return False
    try:
        from cryptography.hazmat.primitives.serialization import (
            load_pem_public_key,
        )
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        logger.warning("缺少 cryptography 库，无法验签")
        return False
    try:
        signature = base64.b64decode(signature_b64)
        public_key = load_pem_public_key(UPDATE_PUBLIC_KEY_PEM.encode("utf-8"))
        public_key.verify(
            signature,
            sha256_hex.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        logger.warning("latest.json 签名验证失败——可能被篡改")
        return False
    except Exception as e:
        logger.warning(f"验签异常: {e}")
        return False


def _fetch_one(url: str, timeout: float) -> Optional[dict]:
    if not url:
        return None
    try:
        resp = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": f"MoMa-Updater/{_get_current_version()}"})
        if resp.status_code != 200:
            logger.debug(f"更新检查 {url} 返回 {resp.status_code}")
            return None
        data = resp.json()
        if not isinstance(data, dict) or "version" not in data:
            return None
        return data
    except Exception as e:
        logger.debug(f"更新检查 {url} 失败: {e}")
        return None


def fetch_manifest(sources: List[str] = None,
                   timeout: float = FETCH_TIMEOUT) -> Tuple[Optional[dict], Optional[str]]:
    """并发从多个源拉清单，返回 (manifest, 命中的源 URL)。"""
    if sources is None:
        sources = [PRIMARY_SOURCE, MIRROR_SOURCE]
    sources = [s for s in sources if s]
    if not sources:
        return None, None

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(sources))
    hit = None
    try:
        futures = {pool.submit(_fetch_one, url, timeout): url for url in sources}
        for fut in concurrent.futures.as_completed(futures):
            url = futures[fut]
            try:
                data = fut.result()
            except Exception:
                data = None
            if data:
                logger.info(f"更新检查命中源: {url}")
                hit = (data, url)
                break
    finally:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)
    return hit if hit else (None, None)


def check_for_update(current_version: str = None) -> Optional[dict]:
    """返回更新信息 dict，无更新或失败返回 None。"""
    if current_version is None:
        current_version = _get_current_version()

    manifest, _source = fetch_manifest()
    if not manifest:
        return None

    latest = str(manifest.get("version", "")).strip()
    if not latest:
        return None

    if compare_version(current_version, latest) >= 0:
        logger.debug(f"已是最新版本（本地 {current_version}）")
        return None

    if not verify_manifest(manifest):
        logger.warning("更新清单签名无效，忽略本次更新")
        return None

    min_sup = str(manifest.get("min_supported", "")).strip()
    force = bool(min_sup and compare_version(current_version, min_sup) < 0)

    return {
        "current": current_version,
        "latest": latest,
        "pub_date": manifest.get("pub_date", ""),
        "notes": manifest.get("notes", ""),
        "download_url": manifest.get("download_url", ""),
        "download_page": manifest.get("download_page", ""),
        "sha256": manifest.get("sha256", ""),
        "force": force,
    }
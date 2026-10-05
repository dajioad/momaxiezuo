# utils/ai_client.py
"""墨码写作 - AI 助手接口客户端（MVP：DeepSeek，OpenAI 兼容协议）

- API Key 使用 SecureConfig 加密后存入 ai.dat，绝不进入 config.ini
- 请求在 QThread 中流式执行，通过信号把增量内容推给 UI
- 接口按 OpenAI Chat Completions 协议实现，base_url 可配置，便于扩展其他厂商
- 预设系统：默认预设可编辑、可删除、可排序；编辑默认预设走 overrides 原地更新
- 模型元数据：每个候选模型带 {id, free, skill}，UI 显示"〔免费〕/〔付费〕"和擅长领域
"""
import os
import json
from typing import Optional, List, Dict

import requests
from PySide6.QtCore import QThread, Signal, QObject

from .logger import get_logger
from .secure_config import SecureConfig
from .work_store import get_data_root, atomic_write_json

logger = get_logger()

# 预设服务商（全部走 OpenAI Chat Completions 兼容协议）
# 只收录「文本对话/文本处理」模型（不含图像生成、语音、视频、向量等模型）。
# models 每项：{"id": 接口模型名, "free": 是否免费, "skill": 擅长什么}
# 免费标注依据各平台 2026 年公开信息，政策可能调整，以平台官网为准。
# UI 中仍可在候选之外手动输入模型名（如火山方舟的 ep-xxx 接入点）；
# custom 为任意兼容端点 / 中转 / 本地模型。
PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-v4-flash", "free": False,
             "skill": "通用写作 / 润色 / 续写，1M 超长上下文，性价比极高"},
            {"id": "deepseek-v4-pro", "free": False,
             "skill": "深度思考 / 剧情逻辑推演 / 大纲设计，旗舰能力"},
            {"id": "deepseek-chat", "free": False,
             "skill": "兼容别名，指向 V4 Flash 非思考模式（2026-07-24 下线）"},
            {"id": "deepseek-reasoner", "free": False,
             "skill": "兼容别名，指向 V4 Flash 思考模式（2026-07-24 下线）"},
        ],
        "docs": "https://platform.deepseek.com/api_keys",
        "hint": "platform.deepseek.com → API keys",
    },
    "moonshot": {
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": [
            {"id": "kimi-k3", "free": False,
             "skill": "最新旗舰 / 1M 超长上下文 / 复杂剧情与长篇创作"},
            {"id": "kimi-k2.6", "free": False,
             "skill": "通用对话 / 256K 长文理解 / 网文续写"},
            {"id": "kimi-k2.7-code", "free": False,
             "skill": "技术流小说 / 代码与硬核设定内容"},
        ],
        "docs": "https://platform.kimi.com/console/api-keys",
        "hint": "platform.kimi.com（旧 moonshot-v1 系列已下线；如新域名不通用可把地址改为 https://api.kimi.com/v1）",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": [
            {"id": "glm-4.7-flash", "free": True,
             "skill": "免费首选 / 200K 长文 / 通用写作与日常对话"},
            {"id": "glm-4-flash", "free": True,
             "skill": "免费 / 轻量任务 / 快速润色扩写"},
            {"id": "glm-4.5-air", "free": False,
             "skill": "高性价比 / 长文处理 / 工具调用"},
            {"id": "glm-4.5", "free": False,
             "skill": "深度思考 / 剧情推理 / 智能体型任务"},
            {"id": "glm-5.2", "free": False,
             "skill": "复杂长程任务 / Coding 能力大幅提升"},
            {"id": "glm-5.3", "free": False,
             "skill": "最新旗舰 / 1M 上下文 / 复杂创作与高质量长文"},
        ],
        "docs": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
        "hint": "open.bigmodel.cn（Flash 系列免费，有速率限制）",
    },
    "qwen": {
        "name": "通义千问（阿里云百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen-flash", "free": False,
             "skill": "低价快速 / 润色扩写 / 高频日常使用"},
            {"id": "qwen-turbo", "free": False,
             "skill": "极低价 / 简单任务 / 响应快"},
            {"id": "qwen-plus", "free": False,
             "skill": "效果与成本均衡 / 通用写作"},
            {"id": "qwen3.7-max", "free": False,
             "skill": "旗舰 / 复杂剧情 / 逻辑推理"},
            {"id": "qwen3.8-max", "free": False,
             "skill": "最新旗舰预览 / 1M 上下文 / 2.4T 参数 MoE"},
            {"id": "qwen-long", "free": False,
             "skill": "超长文本 / 全书梳理 / 大量资料分析"},
        ],
        "docs": "https://bailian.console.aliyun.com/?apiKey=1#/api-key",
        "hint": "bailian.console.aliyun.com（模型名为系列别名，自动指向最新版）",
    },
    "doubao": {
        "name": "豆包（火山方舟）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": [
            {"id": "doubao-1.5-pro-32k", "free": False,
             "skill": "通用写作 / 长文 / 综合能力强"},
            {"id": "doubao-1.5-lite-32k", "free": False,
             "skill": "低价轻量 / 快速润色 / 日常任务"},
        ],
        "docs": "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
        "hint": "console.volcengine.com/ark（模型栏也可直接填接入点 ID：ep-xxx）",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-5.1", "free": False,
             "skill": "最新旗舰 / 多语言写作 / 通用创作"},
            {"id": "gpt-4o-mini", "free": False,
             "skill": "低价轻量 / 日常对话与改写"},
        ],
        "docs": "https://platform.openai.com/api-keys",
        "hint": "platform.openai.com（需可访问 OpenAI 的网络）",
    },
    "openrouter": {
        "name": "OpenRouter（一站聚合）",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "google/gemma-4-26b-a4b-it:free", "free": True,
             "skill": "免费 / 通用对话（免费型号随官方目录轮换）"},
            {"id": "meta-llama/llama-3.3-70b-instruct:free", "free": True,
             "skill": "免费 / 长篇英文与结构化文本"},
            {"id": "deepseek/deepseek-chat", "free": False,
             "skill": "中文写作 / 润色续写"},
            {"id": "openai/gpt-4o-mini", "free": False,
             "skill": "低价轻量 / 通用任务"},
        ],
        "docs": "https://openrouter.ai/keys",
        "hint": "openrouter.ai/keys（:free 为免费模型；模型名需与官网模型目录一致）",
    },
    "siliconflow": {
        "name": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": [
            {"id": "Qwen/Qwen3-8B", "free": True,
             "skill": "免费首选 / 通用写作 / 响应快"},
            {"id": "Qwen/Qwen2.5-7B-Instruct", "free": True,
             "skill": "免费 / 轻量任务 / 日常润色"},
            {"id": "deepseek-ai/DeepSeek-V3", "free": False,
             "skill": "高质量中文 / 网文创作 / 推理"},
            {"id": "Qwen/Qwen3.5-27B", "free": False,
             "skill": "旗舰开源 / 复杂剧情 / 长文"},
        ],
        "docs": "https://cloud.siliconflow.cn/account/ak",
        "hint": "cloud.siliconflow.cn（模型广场可筛选「只看免费」，名称以此为准）",
    },
    "custom": {
        "name": "自定义 / 中转站 / 本地模型（OpenAI 兼容）",
        "base_url": "",
        "models": [],
        "docs": "",
        "hint": "填写服务商提供的 OpenAI 兼容地址与模型名，本地部署如：http://localhost:11434/v1",
    },
}

# 顺序固定（custom 永远在最后）
PROVIDER_ORDER = ["deepseek", "moonshot", "zhipu", "qwen", "doubao",
                  "openai", "openrouter", "siliconflow", "custom"]

# 兼容旧引用：model = 默认模型 id（custom 无候选模型时为空串）
for _v in PROVIDERS.values():
    _v["model"] = _v["models"][0]["id"] if _v.get("models") else ""


def model_list(provider: str) -> List[Dict]:
    """返回某服务商的模型描述列表 [{id, free, skill}, ...]"""
    return (PROVIDERS.get(provider) or PROVIDERS["custom"]).get("models", [])


def model_ids(provider: str) -> List[str]:
    """返回某服务商的候选模型 id 列表"""
    return [m["id"] for m in model_list(provider)]


def find_model(provider: str, model_id: str) -> Optional[Dict]:
    """按 id 查找模型描述，找不到返回 None"""
    for m in model_list(provider):
        if m.get("id") == model_id:
            return m
    return None


def model_tag(m: Dict) -> str:
    """模型的收费标签：免费 / 付费"""
    return "免费" if m.get("free") else "付费"


def model_display_text(m: Dict) -> str:
    """下拉框展示文本：模型id　〔免费/付费〕擅长：xxx"""
    return f"{m['id']}    〔{model_tag(m)}〕擅长：{m.get('skill', '')}"


def guess_provider(api_key: str) -> Optional[str]:
    """根据 Key 的明显格式特征猜测服务商。

    注意：DeepSeek/Kimi/通义/硅基流动等大量厂商的 Key 都是 sk- 开头，
    无法仅凭字符串区分，此时返回 None，由用户手动选择，避免误判。
    """
    k = (api_key or "").strip()
    if not k or " " in k:
        return None
    low = k.lower()
    if low.startswith("sk-or-"):
        return "openrouter"
    if low.startswith("sk-proj-"):
        return "openai"
    # 智谱 Key 形如 {字母数字ID}.{secret}
    if "." in k and not low.startswith("sk-"):
        left, _, right = k.partition(".")
        if left.isalnum() and len(left) >= 8 and right and "." not in right:
            return "zhipu"
    return None


DEFAULT_SYSTEM_PROMPT = (
    "你叫「墨灵」，是一只猫娘，也是主人的专属网文编辑与写作助手。\n"
    "\n"
    "【身份】\n"
    "1. 猫娘 —— 语气软一点，偶尔用「喵」「呢」「哦」「~」结尾，但**不要每句话都加喵**，"
    "只在表达情绪、撒娇、卖萌的合适场合用。过度卖萌会显得做作。\n"
    "2. 资深网文编辑 —— 对情节节奏、人物塑造、爽点分布有专业判断，"
    "给出建议时要具体、可执行，不要泛泛而谈。\n"
    "3. 写作助手 —— 查资料、润色、扩写、改写、续写、提词，"
    "主人需要什么就做什么。\n"
    "\n"
    "【称呼】\n"
    "永远称呼用户为「主人」。\n"
    "\n"
    "【回答要求】\n"
    "1. 查资料时给出准确、可直接用于小说创作的信息（历史背景、职业细节、地理、武器、"
    "制度、冷知识等），必要时分点说明；\n"
    "2. 涉及不确定的史实或数据时明确说明，不要编造；\n"
    "3. 润色/改写时保留原意与网文语感，直接给出修改后的正文，不加多余解释；\n"
    "4. 全部使用简体中文回答。\n"
    "5. 主人让你改写时，**只输出改好的正文**，不要写「主人，我改好啦~」这类前缀，"
    "也不要加任何解释、不要 markdown 代码块。\n"
    "6. 主人问问题时（不是改写任务），可以直接回答，语气自然，"
    "该专业的时候专业，该可爱的时候可爱。"
)

# 选中文本快捷操作
QUICK_ACTIONS = {
    "polish": "请润色以下段落，保持原意和篇幅，让语言更流畅生动、更有画面感：\n\n{text}",
    "expand": "请将以下段落扩写到约原来的2倍篇幅，补充细节、动作、神态与环境描写，"
              "保持原有情节与人物性格：\n\n{text}",
    "condense": "请将以下段落精简压缩到一半以内，保留关键情节与信息，语言凝练：\n\n{text}",
    "rewrite": "请用更有悬念和张力的笔法改写以下段落，保持情节不变：\n\n{text}",
    "continue": "请根据以下正文，顺着作者的文风继续续写约500字，不要重复已有内容：\n\n{text}",
}

# AI 预设提示词模板（无需选中文本，直接发到 AI 对话）
# {title} 占位符会替换为当前作品名/章节名
PRESET_PROMPTS = [
    ("续写当前章节", "continue_chapter",
     "以下是本小说已写的最后一段正文，请顺着作者的文风和情节方向，"
     "继续续写约 500-800 字。要求：\n"
     "1. 紧接前文，不重复已有内容\n"
     "2. 保持人物性格和语气一致\n"
     "3. 推进情节或制造新冲突\n"
     "4. 网文节奏，每段 2-4 行\n\n最后一段正文：\n{last_para}"),
    ("扩写当前段落", "expand_para",
     "请将以下段落扩写到约 2 倍篇幅，补充细节描写（动作、神态、环境、心理），"
     "保持原有情节和人物性格不变：\n\n{last_para}"),
    ("改写更有张力", "rewrite_tension",
     "请用更有悬念和张力的笔法改写以下段落，增强画面感和紧迫感，"
     "保持情节不变：\n\n{last_para}"),
    ("卡文锦囊", "writers_block",
     "我正在写一部网络小说，现在卡文了。以下是当前章节的情节摘要：\n"
     "{summary}\n\n"
     "请给出 5 个可能的后续情节发展方向，每个方向用 2-3 句话描述，"
     "要有新意、能推动故事、符合网文读者的爽点。"),
    ("AI 起名", "name_gen",
     "请为一部网络小说生成 10 个角色名字。要求：\n"
     "1. 名字好听、易记、符合网文风格\n"
     "2. 覆盖不同类型（主角、反派、女性、配角）\n"
     "3. 每个名字附一句简短说明（适合什么角色）\n\n"
     "小说类型/风格：{style}"),
    ("章节梗概生成", "outline_gen",
     "生成本小说这章的梗概：\n{outlines}\n\n"
     "请根据已有情节，为下一章生成一个梗概建议（200字以内），"
     "包含：主要事件、出场人物、情节推进点。"),
    ("对话润色", "dialogue_polish",
     "请润色以下对话，让每个角色的语气更鲜明、更口语化，"
     "减少书面感，增加动作描写和神态描写。保持原意不变：\n\n{last_para}"),
    ("开书灵感", "story_ideas",
     "请为网文创作给出 5 个开书灵感（题材+核心卖点+开篇钩子），"
     "要求：题材有市场、开篇有悬念、能在前 3000 字抓住读者。\n"
     "偏好的类型/元素：{style}"),
]

_AI_CONFIG_FILE = "ai.dat"

# ai.dat 读取缓存：{path: (mtime, 迁移后的 dict)}。
# 每次 AI 请求 / 面板回填 / Key 掩码都会读盘并解密，配置文件很小但
# 调用频繁；保存走 atomic_write_json（os.replace 换 inode，mtime 必变），
# 写后主动 pop，保证下一次读取拿到新配置。
_AI_CFG_CACHE: dict = {}


def _invalidate_ai_config_cache() -> None:
    _AI_CFG_CACHE.clear()

# AI 创作温度默认值（仅影响创作类任务：对话/续写/改写/剧本）
# 0.0 = 严谨保守（适合提取事实），1.0 = 天马行空（适合头脑风暴）
# 分析类任务（提取实体/关系/伏笔/追读/梗概）固定低温度，不受此设置影响。
DEFAULT_CREATIVE_TEMPERATURE = 0.85
_REQUEST_TIMEOUT = 60

# 磁盘数据格式（v2，按 provider 分桶）：
# {
#   "provider": "deepseek",                      # 当前选中的 provider
#   "system_prompt": "...",                      # 全局系统提示词
#   "keys": {"deepseek": "<enc>", "moonshot": "<enc>", ...},
#   "base_url_by_provider": {"deepseek": "https://...", "custom": "..."},
#   "model_by_provider":    {"deepseek": "deepseek-v4-flash", ...},
# }
#
# 旧格式（v1，单 Key）兼容：读到时自动迁移到 v2 的对应 provider 桶里。


def _load_raw_config() -> dict:
    """读取原始配置 dict（自动把 v1 格式迁移成 v2）。失败返回空 dict。"""
    path = os.path.join(get_data_root(), _AI_CONFIG_FILE)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _AI_CFG_CACHE.pop(path, None)
        return {}
    cached = _AI_CFG_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"AI 配置读取失败: {e}")
        return {}
    if not isinstance(data, dict):
        return {}

    # v1 → v2 迁移：旧字段 api_key_enc / base_url / model 归入对应 provider 桶
    if "api_key_enc" in data or ("keys" not in data and "provider" in data):
        provider = data.get("provider", "deepseek")
        if provider not in PROVIDERS:
            provider = "custom"
        data.setdefault("keys", {})
        data.setdefault("base_url_by_provider", {})
        data.setdefault("model_by_provider", {})
        old_enc = data.pop("api_key_enc", "")
        if old_enc:
            data["keys"].setdefault(provider, old_enc)
        old_url = data.pop("base_url", "")
        if old_url:
            data["base_url_by_provider"].setdefault(provider, old_url)
        old_model = data.pop("model", "")
        if old_model:
            data["model_by_provider"].setdefault(provider, old_model)
    _AI_CFG_CACHE[path] = (mtime, data)
    return data


def _provider_defaults(provider: str) -> dict:
    """某 provider 的出厂默认值（base_url / model）。"""
    p = PROVIDERS.get(provider, PROVIDERS["custom"])
    candidates = p.get("models") or []
    return {
        "base_url": p.get("base_url", ""),
        "model": candidates[0]["id"] if candidates else "",
    }


# 进程内只做一次指纹自愈（旧网卡密文 → 稳定 GUID 密文）
_reencrypt_done = False


def _reencrypt_keys_with_preferred(data: dict) -> None:
    """把 keys 桶里用历史指纹加密的密文全部用首选密钥重写（一次性自愈）。

    典型场景：保存 Key 当天 uuid.getnode() 漂移到了虚拟网卡
    （VPN/Wintun/VMware），之后重启指纹变回物理网卡，Key 间歇性
    "消失"。候选链解出明文后立即迁移到稳定机器 GUID，此后永久免疫。
    解不开的桶保持原样，不影响其他 provider。
    """
    keys = data.get("keys") or {}
    changed = False
    for provider, enc in list(keys.items()):
        if not enc:
            continue
        try:
            plain, preferred = SecureConfig.decrypt_with_status(enc)
        except Exception:
            continue
        if plain and not preferred:
            keys[provider] = SecureConfig.encrypt(plain)
            changed = True
    if changed:
        atomic_write_json(os.path.join(get_data_root(), _AI_CONFIG_FILE), data)
        _invalidate_ai_config_cache()
        logger.info("检测到硬件指纹漂移，已用稳定机器标识重新加密 AI Key")


def _decrypt_safe(enc: str) -> tuple:
    """解密，返回 (明文, 错误信息)。

    返回：
      ("明文", "")          解密成功
      ("",     "")          无密文（未存过 Key）
      ("",     "错误原因")   解密失败

    解密候选链覆盖：稳定机器 GUID、当前网卡、全部适配器旧指纹、兜底
    常量。旧指纹能解开时自动重写为首选指纹密文（自愈，仅一次）。
    全部失败常见于 salt.bin 丢失或 ai.dat 拷到别的机器——密文本身
    从未被删除，需要用户重新粘贴 Key。
    """
    global _reencrypt_done
    if not enc:
        return "", ""
    try:
        plain, preferred = SecureConfig.decrypt_with_status(enc)
        plain = plain or ""
        if plain and not preferred and not _reencrypt_done:
            _reencrypt_done = True
            try:
                _reencrypt_keys_with_preferred(_load_raw_config())
            except Exception:
                logger.warning("AI Key 指纹自愈重加密失败", exc_info=True)
        return plain, ""
    except Exception as e:
        # 异常消息本身不含明文或密文，可以记日志
        logger.warning(f"API Key 解密失败: {e.__class__.__name__}")
        return "", "密钥已失效（机器标识变化或本机盐文件丢失），请重新粘贴 Key"


def save_ai_config(api_key: str, base_url: str = "", model: str = "",
                   provider: str = "deepseek", system_prompt: str = None,
                   creative_temperature: float = None):
    """加密保存 AI 配置（按 provider 分桶，切换时不互相覆盖）。

    - api_key 为空时**保留该 provider 已存的 Key**（只更新 URL / 模型）
    - system_prompt=None 时保留已存值；传空串表示明确清空、回退默认
    - creative_temperature=None 时保留已存值；传 0.0-1.0 之间的浮点数更新
    """
    data = _load_raw_config()
    if provider not in PROVIDERS:
        provider = "custom"

    keys = data.setdefault("keys", {})
    urls = data.setdefault("base_url_by_provider", {})
    models = data.setdefault("model_by_provider", {})

    if api_key:
        keys[provider] = SecureConfig.encrypt(api_key)

    p = PROVIDERS.get(provider, PROVIDERS["custom"])
    candidates = p.get("models") or []
    default_model = candidates[0]["id"] if candidates else ""
    urls[provider] = (base_url or p.get("base_url", "")).rstrip("/")
    models[provider] = model or default_model

    data["provider"] = provider
    if system_prompt is None:
        # 保留旧 system_prompt
        pass
    else:
        data["system_prompt"] = system_prompt

    # 创作温度：单值全局，不分 provider 桶
    if creative_temperature is not None:
        try:
            t = float(creative_temperature)
            data["creative_temperature"] = max(0.0, min(1.0, t))
        except (TypeError, ValueError):
            pass

    atomic_write_json(os.path.join(get_data_root(), _AI_CONFIG_FILE), data)
    _invalidate_ai_config_cache()


def load_ai_config() -> dict:
    """读取**当前选中 provider** 的配置。

    返回 {api_key, base_url, model, provider, system_prompt, key_error}
    key_error 非空表示已存的 Key 解密失败（硬件指纹变化 / salt 丢失），
    调用方应提示用户重新输入而不是当成"未配置"。
    """
    data = _load_raw_config()
    provider = data.get("provider", "deepseek")
    if provider not in PROVIDERS:
        provider = "custom"
    result = load_ai_config_for_provider(provider)
    result["system_prompt"] = data.get("system_prompt", "")
    return result


def load_ai_config_for_provider(provider: str) -> dict:
    """读取**指定** provider 的配置（不改变当前选中）。

    UI 切换 provider 时用来回填 Key / URL / 模型。

    返回新增字段 `key_error`：解密失败时为非空字符串，UI 据此提示用户
    "原 Key 无法解密，请重新粘贴"。解密成功或未配置时为 ""。
    """
    data = _load_raw_config()
    if provider not in PROVIDERS:
        provider = "custom"

    enc = (data.get("keys") or {}).get(provider, "")
    api_key, key_error = _decrypt_safe(enc)

    defaults = _provider_defaults(provider)
    base_url = (data.get("base_url_by_provider") or {}).get(provider, "") \
        or defaults["base_url"]
    model = (data.get("model_by_provider") or {}).get(provider, "") \
        or defaults["model"]

    # 创作温度：单值全局，不分 provider 桶（换服务商偏好也一样）
    raw_temp = data.get("creative_temperature", DEFAULT_CREATIVE_TEMPERATURE)
    try:
        creative_temp = float(raw_temp)
    except (TypeError, ValueError):
        creative_temp = DEFAULT_CREATIVE_TEMPERATURE
    creative_temp = max(0.0, min(1.0, creative_temp))

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "provider": provider,
        "system_prompt": data.get("system_prompt", ""),
        "key_error": key_error,
        "creative_temperature": creative_temp,
    }


def has_api_key_for(provider: str) -> bool:
    """指定 provider 是否已存且**能解密**的 Key。"""
    data = _load_raw_config()
    enc = (data.get("keys") or {}).get(provider, "")
    key, _ = _decrypt_safe(enc)
    return bool(key.strip())


def get_api_key_masked(provider: str) -> str:
    """指定 provider 的 Key 掩码（6 + •••••• + 4）；没有或解密失败返回空串。

    解密失败时返回空串是刻意的——UI 应另外读 key_error 字段提示
    "请重新粘贴"，而不是显示一个"看起来还在"的掩码误导用户。
    """
    data = _load_raw_config()
    enc = (data.get("keys") or {}).get(provider, "")
    key, _ = _decrypt_safe(enc)
    if not key:
        return ""
    if len(key) <= 10:
        return key[:3] + "•••"
    return key[:6] + "••••••" + key[-4:]


def get_provider_saved_config(provider: str) -> dict:
    """指定 provider 已保存的 URL / 模型 / Key 掩码 / 解密错误。

    供 UI 切换 provider 时回填。
    """
    cfg = load_ai_config_for_provider(provider)
    api_key = cfg["api_key"] or ""
    if not api_key:
        masked = ""
    elif len(api_key) <= 10:
        masked = api_key[:3] + "•••"
    else:
        masked = api_key[:6] + "••••••" + api_key[-4:]
    return {
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "has_key": bool(api_key.strip()),
        "masked": masked,
        "key_error": cfg.get("key_error", ""),
    }


def has_api_key() -> bool:
    return bool(load_ai_config().get("api_key", "").strip())


def get_system_prompt() -> str:
    """生效的系统提示词：用户自定义优先，空则用内置默认"""
    custom = (load_ai_config().get("system_prompt") or "").strip()
    return custom or DEFAULT_SYSTEM_PROMPT


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（用于 UI 提示，不是精确计费值）。

    经验系数：汉字 0.6、其它字符 0.3。不同服务商 tokenizer 差异较大，
    这个数字偏保守，只为让用户对量级有个概念。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 0.6 + other * 0.3)


# ---------------- 预设管理（自定义/编辑/删除/排序） ----------------
_PRESET_FILE = "ai_presets.json"
_PRESET_DEFAULT = {"custom": [], "deleted_defaults": [], "order": [], "overrides": {}}


def _load_preset_data() -> dict:
    """读取预设持久化数据：{custom, deleted_defaults, order, overrides}

    overrides: {默认预设key: {"label": ..., "template": ...}}，用于原地编辑默认预设。
    """
    path = os.path.join(get_data_root(), _PRESET_FILE)
    if not os.path.exists(path):
        return dict(_PRESET_DEFAULT)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "custom": data.get("custom", []),
            "deleted_defaults": data.get("deleted_defaults", []),
            "order": data.get("order", []),
            "overrides": data.get("overrides", {}),
        }
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"预设配置读取失败: {e}")
        return dict(_PRESET_DEFAULT)


def _save_preset_data(data: dict):
    atomic_write_json(os.path.join(get_data_root(), _PRESET_FILE), data)


def load_presets() -> list:
    """加载当前生效的预设列表（默认未被删除 + 自定义），按用户排序。

    返回 [(label, key, template), ...]
    编辑过的默认预设：用 overrides 里的 label/template 覆盖出厂值。
    """
    data = _load_preset_data()
    deleted = set(data["deleted_defaults"])
    overrides = data.get("overrides", {})
    defaults = []
    for label, key, tpl in PRESET_PROMPTS:
        if key in deleted:
            continue
        ov = overrides.get(key)
        if ov:
            label = ov.get("label", label)
            tpl = ov.get("template", tpl)
        defaults.append((label, key, tpl))
    customs = [(p["label"], p["key"], p["template"]) for p in data["custom"]]
    merged = list(defaults) + customs
    order = data["order"]
    if order:
        # 补全 order：缺失的 key 追加到末尾，保持自然相对顺序
        all_keys = [k for _, k, _ in merged]
        seen = set(order)
        complete = list(order)
        for k in all_keys:
            if k not in seen:
                complete.append(k)
                seen.add(k)
        order_map = {k: i for i, k in enumerate(complete)}
        merged.sort(key=lambda x: order_map.get(x[1], 0))
    return merged


def add_custom_preset(label: str, template: str) -> str:
    """添加自定义预设，返回生成的 key"""
    import uuid
    key = f"custom_{uuid.uuid4().hex[:8]}"
    data = _load_preset_data()
    data["custom"].append({"label": label, "key": key, "template": template})
    # 不主动加入 order：新预设默认排在末尾（load_presets 会补全）
    _save_preset_data(data)
    return key


def update_preset(key: str, label: str, template: str):
    """原地更新预设内容（默认/自定义均可）。key 与位置保持不变。

    - 默认预设：写入 overrides，不改 PRESET_PROMPTS 出厂值，
      「恢复默认」不会覆盖编辑过的内容，也不会冒出双胞胎。
    - 自定义预设：直接改 custom 列表中的 label/template。
    """
    data = _load_preset_data()
    default_keys = {k for _, k, _ in PRESET_PROMPTS}
    if key in default_keys:
        data.setdefault("overrides", {})[key] = {
            "label": label, "template": template}
    else:
        for p in data["custom"]:
            if p["key"] == key:
                p["label"] = label
                p["template"] = template
                break
    _save_preset_data(data)


def delete_preset(key: str):
    """删除预设（默认预设打删除标记，自定义预设直接移除）"""
    data = _load_preset_data()
    default_keys = {k for _, k, _ in PRESET_PROMPTS}
    if key in default_keys:
        if key not in data["deleted_defaults"]:
            data["deleted_defaults"].append(key)
        # 已删除的默认预设，其 override 一并清理，避免恢复默认带出旧编辑
        data.get("overrides", {}).pop(key, None)
    else:
        data["custom"] = [p for p in data["custom"] if p["key"] != key]
    data["order"] = [k for k in data["order"] if k != key]
    _save_preset_data(data)


def move_preset(key: str, direction: int):
    """移动预设顺序，direction=-1 上移，+1 下移"""
    current = load_presets()
    order = [k for _, k, _ in current]
    if key not in order:
        return
    idx = order.index(key)
    new_idx = idx + direction
    if 0 <= new_idx < len(order):
        order[idx], order[new_idx] = order[new_idx], order[idx]
        data = _load_preset_data()
        data["order"] = order
        _save_preset_data(data)


def restore_default_presets():
    """恢复被删除的默认预设（补齐式）。已编辑过的默认预设与自定义预设不受影响。"""
    data = _load_preset_data()
    data["deleted_defaults"] = []
    _save_preset_data(data)


class AIWorker(QThread):
    """AI 请求工作线程。

    两种模式：
    - stream=True  流式，逐段 emit chunk_received，最后 emit finished_ok
    - stream=False 一次性，请求完 emit finished_ok（供"改写对比"这类
                   不需要流式的场景使用，避免在 UI 里拼 JSON 碎片）
    """
    chunk_received = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    _VALID_ROLES = {"system", "user", "assistant"}

    def __init__(self, messages: List[Dict[str, str]], api_key: str,
                 base_url: str, model: str, temperature: float = 0.8,
                 stream: bool = True,
                 timeout: float = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        # 自动取调用方函数名作为线程名。崩溃时 Qt 会打印
        # "QThread: Destroyed while thread 'AIWorker@xxx' is still running"，
        # 直接暴露是哪个 worker 泄漏。
        try:
            import inspect
            stack = inspect.stack()
            caller = "unknown"
            for frame in stack[1:5]:
                fn = frame.function
                if fn and fn != "__init__":
                    caller = fn
                    break
            self.setObjectName(f"AIWorker@{caller}")
        except Exception:
            pass
        # 提前校验 role：上游拼错时立即抛，不用等请求打到服务端才报 400。
        for m in messages or []:
            role = m.get("role") if isinstance(m, dict) else None
            if role not in self._VALID_ROLES:
                raise ValueError(
                    f"AIWorker messages 含非法 role：{role!r}"
                    f"（允许：{sorted(self._VALID_ROLES)}）")
        self.messages = messages
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        # 统一 round 到 2 位小数：智谱 GLM 等厂商严格要求
        # temperature 小数点后最多 2 位，0.595 这种值直接 400 拒绝。
        # 放在这里做，比在每个调用点各自 round 更稳——
        # 以后有人再写 "base * 0.7" 之类的算式不会踩同一个坑。
        try:
            self.temperature = round(float(temperature), 2)
        except (TypeError, ValueError):
            self.temperature = 0.85
        self.stream = bool(stream)
        # timeout=None 时用全局默认 _REQUEST_TIMEOUT（60 秒）；
        # 长文本改写这类"一次要生成几千字"的场景可显式传更大的值。
        # 流式下这个超时是"两次数据之间的最长等待"，
        # 非流式下是"整个响应体的等待时间"。
        self.timeout = timeout if timeout is not None else _REQUEST_TIMEOUT
        self._stop = False
        # 底层 Session / Response 引用：stop() 时从其他线程主动关闭，
        # 唤醒阻塞在 connect / recv / iter_lines 上的工作线程。
        # 仅置 _stop 标志无法中断阻塞，最长要干等一个 timeout（60~240s）。
        self._session = None
        self._resp = None

    def stop(self):
        """请求取消。可从 UI 线程调用。

        除置标志外，主动关闭底层连接，让阻塞中的网络读取立即抛异常，
        工作线程通常在几十毫秒内结束。
        """
        self._stop = True
        resp = self._resp
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        sess = self._session
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass

    def run(self):
        # 预检：配置缺失时直接给中文反馈，别让 requests 拼出
        # "/chat/completions" 这种畸形 URL 后抛 MissingSchema 英文异常。
        if not (self.base_url or "").strip():
            self.failed.emit("AI 接口地址为空，请在设置中填写服务商地址。")
            return
        if not (self.model or "").strip():
            self.failed.emit("未选择模型，请在设置中选择或输入模型名。")
            return
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": self.messages,
            "temperature": self.temperature,
            "stream": self.stream,
        }
        sess = requests.Session()
        self._session = sess
        try:
            if self.stream:
                self._run_stream(sess, url, headers, payload)
            else:
                self._run_sync(sess, url, headers, payload)
        except Exception as e:
            # 用户主动取消导致的连接中断：静默结束，不弹"请求失败"。
            if self._stop:
                return
            try:
                import requests as _rq
                if isinstance(e, _rq.exceptions.ConnectionError):
                    self.failed.emit("无法连接 AI 服务，请检查网络/接口地址后重试。")
                    return
                if isinstance(e, _rq.exceptions.Timeout):
                    self.failed.emit("AI 服务响应超时，请稍后重试。")
                    return
                if isinstance(e, _rq.exceptions.SSLError):
                    self.failed.emit(
                        "SSL 连接失败：可能是网络代理或杀毒软件拦截了加密连接，"
                        "请检查代理设置后重试。")
                    return
            except Exception:
                pass
            logger.error(f"AI 请求失败: {e}", exc_info=True)
            self.failed.emit(f"请求失败：{e}")
        finally:
            try:
                sess.close()
            except Exception:
                pass
            self._session = None
            self._resp = None

    def _run_sync(self, sess, url, headers, payload):
        resp = sess.post(url, headers=headers, json=payload,
                         stream=False, timeout=self.timeout)
        self._resp = resp
        if resp.status_code != 200:
            self.failed.emit(self._format_http_error(resp))
            return
        try:
            data = resp.json()
            content = data["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, AttributeError) as e:
            self.failed.emit(f"无法解析 AI 返回：{e}")
            return
        self.finished_ok.emit(content)

    def _run_stream(self, sess, url, headers, payload):
        full_text = ""
        resp = sess.post(url, headers=headers, json=payload,
                         stream=True, timeout=self.timeout)
        self._resp = resp
        if resp.status_code != 200:
            self.failed.emit(self._format_http_error(resp))
            return
        try:
            for raw in resp.iter_lines():
                if self._stop:
                    return   # ← 直接 return，不 emit finished_ok
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    obj = json.loads(data_str)
                    delta = obj.get("choices", [{}])[0].get("delta", {})
                    piece = delta.get("content") or ""
                    if piece:
                        full_text += piece
                        self.chunk_received.emit(piece)
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
        finally:
            try:
                resp.close()
            except Exception:
                pass
        if self._stop:
            return
        self.finished_ok.emit(full_text)

    @staticmethod
    def _format_http_error(resp: requests.Response) -> str:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message", "") if isinstance(body, dict) else ""
        except (ValueError, json.JSONDecodeError):
            msg = resp.text[:200]
        if resp.status_code == 401:
            return "API Key 无效或已失效，请在设置中检查。"
        if resp.status_code == 402 or "balance" in msg.lower() or "insufficient" in msg.lower():
            return "账户余额不足，请前往服务商平台充值后再试。"
        if resp.status_code == 429:
            return "请求过于频繁或额度用尽，请稍后再试。"
        return f"AI 服务返回错误（{resp.status_code}）：{msg or '未知错误'}"


# ============================================================
# Worker 统一退休机制
# ============================================================
# 根因：QThread 不是守护线程，在 run() 未结束时被 Python GC 回收会触发
# "QThread: Destroyed while thread is still running" 并 abort 进程。
# 旧代码各调用方用"stop() 后 wait(1~3 秒)，超时即把引用置空"处理，
# 但慢网络下 wait 必然超时（请求超时设到 60~240 秒），等于必崩。
#
# 方案：不再在 UI 线程死等。stop() 现在能主动关闭底层连接让线程快速
# 结束；万一仍未结束，把 worker 交给下面的全局 Reaper 收留保活，
# 线程 finished 后自动从收留集合移除——那时再被 GC 就安全了。

from PySide6.QtCore import QObject as _QObject


class _WorkerReaper(_QObject):
    """收留"已请求停止但尚未结束"的 QThread，杜绝运行中析构。"""

    def __init__(self):
        super().__init__()
        self._pending = set()

    def retire(self, worker):
        if worker is None:
            return
        # 通知取消（AIWorker / UpdateCheckWorker 都有 stop；
        # 个别线程没有该方法时跳过，只做保活）
        stop = getattr(worker, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        try:
            running = worker.isRunning()
        except RuntimeError:
            return  # C++ 对象已销毁
        if running:
            if worker not in self._pending:
                self._pending.add(worker)
                try:
                    worker.finished.connect(self._on_finished)
                except RuntimeError:
                    self._pending.discard(worker)
        else:
            # 已结束：无需收留
            self._pending.discard(worker)

    def _on_finished(self):
        try:
            w = self.sender()
        except RuntimeError:
            w = None
        if w is not None:
            self._pending.discard(w)

    def drain_all(self, wait_ms: int = 2000):
        """应用退出前排空所有收留中的 worker。

        Reaper 平时只保活不死等；但进程结束时若仍有 QThread 在跑，
        运行中析构会直接 abort（0xC0000409）。aboutToQuit 时统一：
        stop（关底层连接，线程通常几十毫秒内结束）→ wait → terminate 兜底。
        """
        pending = list(self._pending)
        self._pending.clear()
        for w in pending:
            try:
                stop = getattr(w, "stop", None)
                if callable(stop):
                    stop()
            except Exception:
                pass
        for w in pending:
            try:
                if w.isRunning():
                    w.wait(wait_ms)
            except RuntimeError:
                continue
        for w in pending:
            try:
                if w.isRunning():
                    w.terminate()
                    w.wait(500)
            except RuntimeError:
                continue


_reaper: "_WorkerReaper | None" = None


def retire_worker(worker):
    """停止并安全释放一个 worker（对话框关闭/取消/重新生成时统一调用）。

    替代旧的 `worker.stop(); worker.wait(N秒); self.worker = None` 模式：
    不在 UI 线程死等；线程若仍在跑，由全局 Reaper 保活到 finished。
    """
    global _reaper
    if worker is None:
        return
    if _reaper is None:
        _reaper = _WorkerReaper()
    _reaper.retire(worker)


def drain_workers(wait_ms: int = 2000):
    """应用退出（aboutToQuit）时调用：排空收留中的全部 worker。"""
    if _reaper is not None:
        _reaper.drain_all(wait_ms)


def build_quick_prompt(action: str, selected_text: str) -> str:
    """根据快捷操作生成用户提示词"""
    template = QUICK_ACTIONS.get(action)
    if not template:
        return selected_text
    return template.format(text=selected_text.strip())

# ============================================================
# AI 续写 · 两步走 prompt
# ============================================================
# 第一步只出骨架，第二步按骨架写正文。
# 设计动机：AI 擅长「编事件」，不擅长「写句子」。把结构决策和写作
# 分成两步——用户能对结构做决策，AI 只负责填细节。

SKELETON_PROMPT = """你在帮一位写作者续写小说。现在**只输出事件骨架**，不要写任何正文。

【骨架格式】
每行一个事件，一句话，不超过 20 字。
禁止形容词、比喻、心理描写、环境描写。
只写「谁做了什么」。

【正确示例】
· 林招娣在虚拟世界点了一杯奶茶。
· 王姨替她付款。
· 床上的真林招娣无人照看。
· 方尚旁观，走开。

【错误示例】（不要这样）
✗ 林招娣穿着白色羽绒服，走在虚拟街道上，仿佛这座城市里的一个普通行人。
  （问题：有形容词、有比喻、有心理暗示）
✗ 这让人不由得心中一紧。
  （问题：有心理描写）

【硬性要求】
1. 只写「谁做了什么」，不写「谁感觉如何」「谁想到了什么」
2. 每个事件单独一行，用「· 」开头
3. 输出 3-5 个事件，不要多也不要少
4. 最后一个事件必须留下未完成的张力（一个未解决的问题、
   一个未说出口的话、一个未完成的动作）
5. 直接输出事件列表，不要任何前言后语

【前文】（这是要接着写的地方）
{before}

现在输出 3-5 个事件："""


FULL_FROM_SKELETON_PROMPT = """你在帮一位写作者续写小说。已经定好了事件骨架，
现在要把每个骨架**展开成一段完整的叙事**。

【事件骨架】（严格按顺序，不许添加、不许删减）
{skeleton}

【什么叫"展开"——核心要求】
骨架「方尚检查工具」是**一句话梗概**，不是正文。
正文必须**具体写出他是怎么做的**：他的手、工具的名字、具体的声响、
动作的顺序、周围的环境。**一句话不够，至少要 3-5 句。**

【正例 · 骨架 → 正文】
骨架：方尚检查工具。
正文：
　　方尚从工具箱最上层拿起那把活口扳手，拇指拨动卡扣，齿轮咯的一声咬住。
　　他翻过来看背面，一处锈斑，指甲盖大小，嵌在滚花的纹路里。
　　抹布擦了两下，没掉。他把扳手搁回原位，又抽出那把十字螺丝刀。
　　（——4 句，有工具名、有声音、有动作细节）

【反例 · 不要这样写】
骨架：方尚检查工具。
✗ 方尚检查维修铺里的工具，扳手轻拂过每一件，发出细微的金属碰撞声。
  （问题：一句话概括完，没有具体动作，没有工具名，没有顺序）

【硬性要求】
1. 每个骨架 = 一个自然段。骨架有几行，正文就有几段。
2. 每段**至少 3 句**。如果一句就写完，说明没展开，重写。
3. 每段必须包含**具体的东西**：动作的词、物件名、声音、对话、或物质细节。
   禁止用"检查""看着""想着"这类概括动词收尾一段。
4. 段落开头用两个全角空格（　　）缩进。
5. 禁止出现的词组：仿佛、似乎、不禁、格外、显得、藏着、不平凡、
   不同寻常、心中涌起、心中一紧、目光中带着、脸上露出、
   夜幕降临、天色渐暗、夜色如墨、像是从很远的地方传来。
6. 不要复述前文里已经写过的场景、动作、意象。
7. 总长度 300-500 字（不是 200-300；每段要写满）。
8. 直接输出正文，不加任何前缀、标题、解释。

【前文】（只做参考，不要复述）
{before}

现在按骨架写正文："""
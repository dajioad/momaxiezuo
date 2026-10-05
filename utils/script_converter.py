# utils/script_converter.py
"""墨码写作 - 小说转剧本

把章节正文转换为剧本格式。支持三种格式：
- standard: 标准剧本（场景 + 动作 + 对白 + 旁白）
- outline: 分场大纲（场景列表 + 情节要点）
- storyboard: 短剧分镜（镜头号 + 画面 + 音效）

纯逻辑，无 Qt 依赖。
"""
from __future__ import annotations


FORMATS = [
    ("standard", "标准剧本", "场景标题 + 动作 + 对白 + 旁白"),
    ("outline", "分场大纲", "场景列表 + 情节要点，适合分集策划"),
    ("storyboard", "短剧分镜", "镜头号 + 画面 + 音效，适合短视频改编"),
]

FORMAT_KEYS = {k for k, _, _ in FORMATS}


def _fmt_label(key: str) -> str:
    for k, label, _ in FORMATS:
        if k == key:
            return label
    return "标准剧本"


_PROMPT_HEADER = """你是专业编剧，请把下面这段小说正文改编为{fmt_label}。

【硬性要求】
- 严格遵循{fmt_label}的行业格式
- 保留原文情节、人物、对话的核心信息
- 不要自行添加小说里没有的情节
- 不要输出任何解释、不要 markdown 代码块标记
- 直接开始输出剧本内容
{extra_options}
"""

_PROMPT_STANDARD = """
【输出格式 · 标准剧本】

严格按下面的结构逐行输出：

【场景1】地点　时间（日/夜）　内/外
（动作描写，一到两句话，用全角括号包裹）
角色名（神态/动作指示）：
对白内容，不加引号
角色名：
对白内容
（动作描写）
旁白（画外音）：
旁白内容
（动作描写）
角色名：
对白内容

【场景2】地点　时间　内/外
……（同上结构）

【硬性格式规则 —— 违反任一即为不合格】

1. 对白必须"角色名独占一行 + 全角冒号结尾"，**对白内容另起一行，不加引号**。
2. 动作描写必须独占一行，整行用全角括号（）包裹，**不得和角色名或对白混在一行**。
3. 旁白必须独占一行，固定格式为「旁白（画外音）：」，旁白内容另起一行。
4. 场景标题独占一行，用【场景N】开头。
5. **禁止输出小说叙述句**。例如"方尚推开门"这种叙述，必须改成"（方尚推开门）"或"方尚："独占一行。
6. 不输出任何解释、不加小标题、不用 markdown 代码块标记。
7. 场景之间空一行。

【正确写法对照】

小说原文：方尚推开门，皱着眉头闻到一股怪味。
剧本写法：
（方尚推开门，皱着眉头）
方尚（皱眉）：
这味道是从哪来的？

小说原文：隔壁王姨正在喂女儿吃饭。
剧本写法：
（方尚顺着声音望去，看见隔壁王姨蹲在茶几前）
王姨（对着屏幕）：
妈，还要吃那个虾。
"""

_PROMPT_OUTLINE = """
【输出格式 · 分场大纲】

【场景1】地点　时间
· 出场人物：角色A、角色B
· 情节要点：
　- 要点1
　- 要点2
· 场景作用：铺垫悬念 / 推进主线 / 塑造人物

【场景2】...

【格式细节】
- 每个场景的"出场人物""情节要点""场景作用"都要写
- 情节要点每场景 2-4 条，每条一句话
- 场景作用一句话概括，不超过 15 字
"""

_PROMPT_STORYBOARD = """
【输出格式 · 短剧分镜】

【镜1】
画面：（画面描述，40 字内）
人物：（出场角色）
台词：（台词或"无"）
音效：（音效说明或"无"）
时长：约 N 秒

【镜2】...

【格式细节】
- 每个镜头 5 个字段都要写全
- 画面描述要具体到"谁在做什么"，不写抽象情绪
- 台词只保留最核心的一句，其余可省略
- 时长按"每秒 5-8 字台词 + 1 秒动作"估算，整数
"""

_EXTRA_ACTION = "- 保留原文的动作描写，改写为剧本动作段落\n"
_EXTRA_VO = "- 保留原文的内心独白，改写为旁白（画外音）\n"

_TEXT_TEMPLATE = """
【章节标题】
{title}

【小说正文】
{text}
"""


def build_prompt(text: str, title: str = "", fmt: str = "standard",
                 include_action: bool = True,
                 include_voiceover: bool = True,
                 context: str = "") -> str:
    """构造转剧本的 prompt。

    参数：
      text        小说正文
      title       章节标题
      fmt         standard / outline / storyboard
      include_action     是否保留动作描写
      include_voiceover  是否保留内心独白（转旁白）
      context     作品上下文（可选）
    """
    if fmt not in FORMAT_KEYS:
        fmt = "standard"

    extra = ""
    if fmt == "standard":
        if include_action:
            extra += _EXTRA_ACTION
        if include_voiceover:
            extra += _EXTRA_VO
        if not extra:
            extra = "- 只保留对白，不带动作和旁白\n"

    fmt_label = _fmt_label(fmt)
    header = _PROMPT_HEADER.format(fmt_label=fmt_label, extra_options=extra)

    body_tpl = {
        "standard": _PROMPT_STANDARD,
        "outline": _PROMPT_OUTLINE,
        "storyboard": _PROMPT_STORYBOARD,
    }[fmt]

    # 输入截断（长章节只取前 6000 字，够改一集短剧）
    text = (text or "")[:6000]

    parts = [header, body_tpl]
    if context.strip():
        parts.append(f"\n【作品背景】\n{context.strip()}\n")
    parts.append(_TEXT_TEMPLATE.format(
        title=title or "（无标题）", text=text))
    return "".join(parts)


def build_outline_prompt(text: str, title: str = "",
                         context: str = "") -> str:
    """快捷：只生成分场大纲。"""
    return build_prompt(text, title, "outline", False, False, context)
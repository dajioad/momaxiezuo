# utils/ai_json.py
"""AI 响应 JSON 提取工具。

处理模型返回里的常见情况：
- 用 ```json ... ``` 包裹
- 前面带一段"以下是分析结果："的开场白
- 后面跟一段"希望有帮助"的收尾语
- JSON 内含嵌套花括号（贪婪正则 r"\\{[\\s\\S]*\\}" 会吃错）

策略：
1. 先剥离 markdown 围栏
2. 直接 json.loads
3. 平衡扫描第一个 {...} 块（考虑字符串内部转义），再 loads

纯函数，无 Qt、无网络依赖。
"""
from __future__ import annotations

import json
import re
from typing import Optional


def strip_code_fence(text: str) -> str:
    """去掉 markdown ``` 包裹。无包裹时原样返回。"""
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    return s


def extract_first_json_object(text: str) -> Optional[str]:
    """从文本里提取第一个**平衡**的 {...} 块。

    考虑字符串内部转义：'{"a": "}"}' 会正确返回整个 '{"a": "}"}'，
    而不会在字符串里的 '}' 处提前截断。
    """
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None

def extract_first_json_block(text: str) -> Optional[str]:
    """从文本里提取第一个**平衡**的 {...} 或 [...] 块。

    与 extract_first_json_object 的区别：数组也支持。
    考虑字符串内部转义（'{"a": "}"}' 会正确返回整个字符串）。
    """
    if not text:
        return None
    # 找第一个 { 或 [
    start = -1
    opener = ""
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            opener = ch
            break
    if start < 0:
        return None
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None

def parse_json_response(text: str) -> Optional[dict]:
    """从 AI 响应文本里解析 JSON 对象。失败返回 None。

    顺序：
      1. 去 markdown 围栏
      2. 直接 json.loads
      3. 平衡扫描第一个 {...} 块，再 loads
    """
    if not text:
        return None
    s = strip_code_fence(text)
    # 快速路径：整体就是合法 JSON
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # 慢路径：平衡扫描第一个 { ... }
    snippet = extract_first_json_object(s)
    if snippet:
        try:
            data = json.loads(snippet)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            return None
    return None

def parse_json_any(text: str):
    """从 AI 响应文本里解析 JSON —— 对象或数组均可。失败返回 None。

    适用场景：AI 返回结构不确定（可能 {"relations": [...]}，
    也可能直接 [...]），调用方拿到后自己判断类型。
    """
    if not text:
        return None
    s = strip_code_fence(text)
    try:
        data = json.loads(s)
        if isinstance(data, (dict, list)):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    snippet = extract_first_json_block(s)
    if snippet:
        try:
            data = json.loads(snippet)
            if isinstance(data, (dict, list)):
                return data
        except (json.JSONDecodeError, ValueError):
            return None
    return None
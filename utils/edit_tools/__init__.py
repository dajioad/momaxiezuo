# utils/edit_tools/__init__.py
"""编辑工具箱 - 纯函数模块集合

每个工具对外暴露：
- 一个 Params dataclass（参数）
- 一个 process_xxx(text, params) -> (new_text, stats) 函数

设计约定：
- 纯函数、不依赖 Qt、不碰磁盘
- 输入文本、输出文本，中间过程全在内存
- stats 是一个 dict，key 用简洁英文名（供 UI 层显示统计条）
"""
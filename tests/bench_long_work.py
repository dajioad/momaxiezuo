# tests/bench_long_work.py
"""长篇压测：合成约 2000 章 / ~150 万字的大书，给全书级纯逻辑操作计时。

默认跳过（合成数据要写 2000 个章节文件，耗时且占盘）。手动运行：

    Windows PowerShell:
        $env:MOMA_BENCH="1"; ./.venv/Scripts/python.exe -m pytest tests/bench_long_work.py -s

阈值都故意放宽，作用是"性能防回退网"，不是卡 CI；
真正的用途是打印各操作耗时，暴露数量级异常（如 O(n²) 退化）。
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.skipif(
    os.environ.get("MOMA_BENCH") != "1",
    reason="长篇压测需显式开启：设置环境变量 MOMA_BENCH=1",
)

N_CHAPTERS = 2000
CHARS_PER_CHAPTER = 750

_TEMPLATES = [
    "方知有推开门时，李长生正站在窗前。“你来了。”老人没有回头。",
    "院外风声很紧，方知有按住腰间长剑，低声道：“师父，事情有变。”",
    "李长生转过身，目光在烛火里显得格外深沉：“说下去。”",
    "方知有想起苏晚棠临别时的话，心里一沉。沈无咎的人已经到了山脚。",
    "“从今夜起，你不再是青锋山弟子。”李长生的声音很轻，却像石头一样压下来。",
]


def _chapter_text(i: int) -> str:
    """确定性合成正文：含固定角色名与对白，便于扫描类操作真正命中。"""
    lines = [f"第{i}章 风起青锋", ""]
    n = 0
    j = 0
    while n < CHARS_PER_CHAPTER:
        line = _TEMPLATES[j % len(_TEMPLATES)]
        lines.append(line)
        n += len(line)
        j += 1
    return "\n".join(lines)


@pytest.fixture(scope="module")
def big_work(tmp_path_factory):
    from utils.work_store import WorkStore
    data_dir = str(tmp_path_factory.mktemp("bigwork"))
    store = WorkStore(data_dir)
    t0 = time.perf_counter()
    work = store.create_work("压测大书")
    for i in range(1, N_CHAPTERS + 1):
        ch = work.add_chapter(title=f"第{i}章 风起青锋")
        work.write_chapter(ch["id"], _chapter_text(i), snapshot=False)
    # 存人物卡：一致性扫描依赖锚点名字，没有锚点会直接空跑
    work.save_characters([
        {"name": "方知有", "aliases": []},
        {"name": "李长生", "aliases": ["师父"]},
        {"name": "苏晚棠", "aliases": []},
        {"name": "沈无咎", "aliases": []},
    ])
    build_s = time.perf_counter() - t0
    print(f"\n[bench] 合成 {N_CHAPTERS} 章用时 {build_s:.1f}s "
          f"（数据目录 {data_dir}，不计入性能阈值）")
    return store, work, data_dir


def _timed(label: str, fn, ceiling: float):
    t0 = time.perf_counter()
    result = fn()
    dt = time.perf_counter() - t0
    print(f"[bench] {label}: {dt:.2f}s（阈值 {ceiling:.0f}s）")
    assert dt < ceiling, f"{label} 耗时 {dt:.1f}s 超过阈值 {ceiling}s"
    return result, dt


def test_open_work(big_work):
    store, work, data_dir = big_work
    def _open():
        w = store.open_work(work.root_dir)
        assert len(w.chapters) == N_CHAPTERS
        return w
    opened, _ = _timed("重新打开 2000 章作品", _open, 8.0)
    # 第一章/最后一章读取
    _timed("读取首章正文", lambda: opened.read_chapter(opened.chapters[0]["id"]), 2.0)
    _timed("读取末章正文", lambda: opened.read_chapter(opened.chapters[-1]["id"]), 2.0)


def test_scan_character_appearances(big_work):
    _store, work, _ = big_work
    chars = [{"name": "方知有", "aliases": []},
             {"name": "李长生", "aliases": ["师父"]},
             {"name": "苏晚棠", "aliases": []},
             {"name": "沈无咎", "aliases": []}]
    rows, _ = _timed("全书出场统计（4 角色 × 2000 章）",
                     lambda: work.scan_character_appearances(chars), 40.0)
    assert rows, "应扫到出场数据"
    top = {r["name"]: r.get("_stats", {}) for r in rows}
    assert top["方知有"]["chapter_count"] > 1000


def test_consistency_scan_full_book(big_work):
    from utils.consistency import collect_names, scan_work
    _store, work, _ = big_work
    _timed("collect_names（人物卡+词库）", lambda: collect_names(work), 5.0)
    # 默认 500 章上限；压测显式放开到全书
    pairs, _ = _timed("一致性扫描全书 2000 章",
                      lambda: scan_work(work, max_chapters=N_CHAPTERS), 40.0)
    assert isinstance(pairs, list)


def test_single_chapter_analyzers(big_work):
    from utils.readability import check_chapter
    from utils.beta_reading import local_stats
    from utils.relation_rules import extract_local
    long_text = "\n".join(_TEMPLATES * 60)   # 约 1 万字长章
    chars = [{"name": n} for n in ("方知有", "李长生", "苏晚棠", "沈无咎")]
    _timed("追读体检 check_chapter（万字章）",
           lambda: check_chapter(long_text), 5.0)
    _timed("试读本地统计 local_stats（万字章）",
           lambda: local_stats(long_text), 2.0)
    _timed("L0 关系规则提取（万字章）",
           lambda: extract_local(long_text, chars), 5.0)

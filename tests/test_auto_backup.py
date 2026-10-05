# -*- coding: utf-8 -*-
"""自动备份：到期判断 / zip 打包 / 保留份数清理。"""
import os
import zipfile
from datetime import datetime, timedelta

from utils.auto_backup import (
    backup_due, create_backup_zip, prune_backups, backup_dir_for,
    INTERVAL_DAILY, INTERVAL_WEEKLY,
)


def test_due_when_never_backed():
    assert backup_due("", INTERVAL_DAILY) is True
    assert backup_due("", INTERVAL_WEEKLY) is True


def test_due_daily_and_weekly():
    today = datetime.now().date()
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    six_days = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")

    assert backup_due(today.strftime("%Y-%m-%d"), INTERVAL_DAILY) is False
    assert backup_due(yesterday, INTERVAL_DAILY) is True

    assert backup_due(six_days, INTERVAL_WEEKLY) is False
    assert backup_due(week_ago, INTERVAL_WEEKLY) is True


def test_due_invalid_date_treated_as_due():
    assert backup_due("not-a-date", INTERVAL_DAILY) is True


def test_create_and_prune_backup(tmp_path):
    data_root = tmp_path / "data"
    works = data_root / "works" / "我的书"
    works.mkdir(parents=True)
    (works / "book.json").write_text("{}", encoding="utf-8")
    (data_root / "stats.json").write_text("{}", encoding="utf-8")

    dest = backup_dir_for(str(data_root))
    path, size = create_backup_zip(str(data_root), dest)
    assert path and size > 0
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert any(n.startswith("works") for n in names)
    assert "stats.json" in names
    # API Key 密钥文件永远不进备份
    assert not any("ai.dat" in n for n in names)

    # 再补两份旧备份：old1 是损坏空包（旧版非原子写/退出杀线程的产物），
    # old2 是正常包。prune 必须先删坏包（坏包不能计入 keep 份数），
    # 再按保留份数清理：keep=2 时有效包正好 2 份，只删 old1。
    old1 = os.path.join(dest, "自动备份_20200101_000000.zip")
    old2 = os.path.join(dest, "自动备份_20200102_000000.zip")
    open(old1, "w").close()
    with zipfile.ZipFile(old2, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("dummy.txt", "x")
    removed = prune_backups(dest, keep=2)
    assert len(removed) == 1
    assert os.path.normcase(removed[0]).endswith(
        os.path.normcase("自动备份_20200101_000000.zip"))
    remain = [f for f in os.listdir(dest) if f.endswith(".zip")]
    assert len(remain) == 2
    assert "自动备份_20200101_000000.zip" not in remain
    assert "自动备份_20200102_000000.zip" in remain


def test_empty_data_root_produces_no_zip(tmp_path):
    path, size = create_backup_zip(str(tmp_path))
    assert path == "" and size == 0

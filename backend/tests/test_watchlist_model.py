"""watchlist 数据模型测试：迁移 / kind 推断 / 增删 / 兼容接口。"""

import json

import pytest

from app.watchlist import (
    _infer_kind,
    add_entry,
    load_watchlist,
    load_watchlist_entries,
    normalize_entry,
    remove_entry,
    save_watchlist,
)


@pytest.fixture()
def tmp_watchlist(monkeypatch, tmp_path):
    """把 watchlist.json 指向临时目录。"""
    from app import watchlist as wl

    cfg = tmp_path / "watchlist.json"
    monkeypatch.setattr(wl, "CONFIG_PATH", cfg)
    return cfg


def test_kind_inference(tmp_watchlist):
    assert _infer_kind("600519.SSE") == "stock"
    assert _infer_kind("rb2601.SHFE") == "future"
    assert _infer_kind("l2609.DCE") == "future"
    # 期权库里有 IO 品种与合约（依赖本地同步数据，缺失时回退）
    kind = _infer_kind("IO")
    assert kind in ("option_underlying", "future")
    kind = _infer_kind("10011425.SH")
    assert kind in ("option_contract", "stock")


def test_legacy_migration(tmp_watchlist):
    """旧纯字符串数组 → entry 结构自动迁移并落盘。"""
    tmp_watchlist.write_text(
        json.dumps(["600519.SSE", "a2609.DCE"]), encoding="utf-8"
    )
    entries = load_watchlist_entries()
    assert [e["id"] for e in entries] == ["600519.SSE", "a2609.DCE"]
    assert entries[0]["kind"] == "stock"
    # 迁移结果已写回（新格式）
    data = json.loads(tmp_watchlist.read_text(encoding="utf-8"))
    assert isinstance(data[0], dict) and data[0]["kind"] == "stock"
    # 兼容接口仍返回字符串列表
    assert load_watchlist() == ["600519.SSE", "a2609.DCE"]


def test_add_remove_entry(tmp_watchlist):
    add_entry("600519.SSE", kind="stock", name="贵州茅台")
    add_entry("600519.SSE")  # 重复：不新增
    entries = load_watchlist_entries()
    assert len([e for e in entries if e["id"] == "600519.SSE"]) == 1
    by_id = {e["id"]: e for e in entries}
    assert by_id["600519.SSE"]["name"] == "贵州茅台"

    add_entry("rb2601.SHFE")
    assert load_watchlist()[-1] == "rb2601.SHFE"

    remove_entry("rb2601.SHFE")
    assert "rb2601.SHFE" not in load_watchlist()


def test_save_watchlist_compat(tmp_watchlist):
    add_entry("600519.SSE", kind="stock")
    save_watchlist(["000001.SZSE", "600519.SSE"])
    assert load_watchlist() == ["000001.SZSE", "600519.SSE"]
    kinds = {e["id"]: e["kind"] for e in load_watchlist_entries()}
    assert kinds["600519.SSE"] == "stock"
    assert kinds["000001.SZSE"] == "stock"


def test_normalize_entry_garbage(tmp_watchlist):
    assert normalize_entry("") is None
    assert normalize_entry(123) is None
    assert normalize_entry({"id": "  "}) is None
    entry = normalize_entry({"id": "IO2612-C-4000.CFE", "kind": "bogus"})
    assert entry["id"] == "IO2612-C-4000.CFE"
    assert entry["kind"] in ("option_contract", "future")

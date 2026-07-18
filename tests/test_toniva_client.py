from __future__ import annotations

from bot.toniva_client import _extract_rows_and_meta


def test_extract_rows_list_payload():
    rows, meta = _extract_rows_and_meta([{"a": 1}, {"a": 2}])
    assert len(rows) == 2
    assert meta == {}


def test_extract_rows_dict_rows_meta():
    payload = {
        "rows": [{"id": 1}],
        "meta": {"total_count": 1, "truncated": False},
    }
    rows, meta = _extract_rows_and_meta(payload)
    assert rows == [{"id": 1}]
    assert meta["total_count"] == 1


def test_extract_rows_data_key():
    rows, meta = _extract_rows_and_meta({"data": [{"x": 1}], "meta": {}})
    assert rows[0]["x"] == 1

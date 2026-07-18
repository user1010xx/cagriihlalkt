from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def test_import_bot_package():
    import bot
    import bot.config
    import bot.database
    import bot.rules
    import bot.service
    import bot.toniva_client
    import bot.handlers
    import bot.scheduler
    import bot.main

    assert bot.__doc__


def test_build_application_smoke(tmp_path):
    env = {
        "TELEGRAM_BOT_TOKEN": "0000000000:TEST-TOKEN-FOR-SMOKE-ONLY",
        "DATABASE_PATH": str(tmp_path / "smoke.sqlite3"),
        "ALLOWED_GROUP_NAMES": "Test Grup A,Test Grup B",
        "TONIVA_API_URL": "https://crm.toniva.net/api/public/v1",
    }
    with patch.dict(os.environ, env, clear=False):
        from bot.main import build_application

        app = build_application()
        assert app.bot_data["config"].telegram_bot_token.startswith("0000")
        assert app.bot_data["database"] is not None
        assert app.bot_data["client"] is not None
        # handler sayisi > 0
        assert len(app.handlers[0]) > 5


def test_load_config_requires_token(tmp_path):
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}, clear=False):
        from bot.config import load_config

        with pytest.raises(RuntimeError):
            load_config()


def test_personnel_import_xlsx(tmp_path):
    from openpyxl import Workbook

    from bot.personnel_import import parse_personnel_workbook

    path = tmp_path / "p.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Ad", "Dahili"])
    ws.append(["Ali Veli", "101"])
    ws.append(["Ayse", "202"])
    wb.save(path)
    rows = parse_personnel_workbook(path.read_bytes())
    assert len(rows) == 2
    assert rows[0].name == "Ali Veli"
    assert rows[0].extension == "101"


def test_violation_key():
    from bot.violation_keys import violation_key

    assert violation_key("Mesai başlangıcı ihlali: 09:00 sonrası çağrı yok") == "mesai başlangıcı ihlali"

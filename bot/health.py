from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import Application


logger = logging.getLogger(__name__)


async def run_health_server(application: Application) -> None:
    port_text = os.getenv("PORT", "").strip()
    if not port_text:
        return
    try:
        port = int(port_text)
    except ValueError:
        logger.warning("PORT env degeri gecersiz: %s", port_text)
        return

    async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                await reader.read(1024)
                database = application.bot_data.get("database")
                last_run = application.bot_data.get("last_scheduler_run")
                department_count = len(database.list_departments()) if database is not None else 0
                active_count = len(database.list_departments(only_active=True)) if database is not None else 0
                body = (
                    f'{{"status":"ok",'
                    f'"departments":{department_count},'
                    f'"active_departments":{active_count},'
                    f'"last_scheduler_run":{last_run!r}}}'
                )
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                    f"{body}"
                )
                writer.write(response.encode("utf-8"))
                await writer.drain()
            except Exception:
                logger.warning("Health check handling error", exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(handle_connection, host="0.0.0.0", port=port)
    application.bot_data["health_server"] = server
    logger.info("Health endpoint aktif: 0.0.0.0:%s", port)
    async with server:
        await server.serve_forever()

from __future__ import annotations

import asyncio
from datetime import date
import json
import logging
import os
import socket
import time
from typing import Any
from urllib import error, parse, request


logger = logging.getLogger(__name__)

_original_getaddrinfo = socket.getaddrinfo
_ipv4_forced = False


def _ensure_ipv4_preference() -> None:
    """Whitelist genelde IPv4; Windows IPv6 ile cikis yaparsa CRM-2093 alinir."""
    global _ipv4_forced
    flag = os.getenv("TONIVA_FORCE_IPV4", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return
    if _ipv4_forced:
        return

    def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = getaddrinfo_ipv4  # type: ignore[assignment]
    _ipv4_forced = True
    logger.info("Toniva istemcisi IPv4 tercihine alindi (TONIVA_FORCE_IPV4).")


class TonivaError(RuntimeError):
    def __init__(self, message: str, code: str | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class TonivaTimeoutError(TonivaError):
    pass


class TonivaConnectionError(TonivaError):
    pass


class TonivaClient:
    def __init__(self, api_url: str, timeout_seconds: int = 60, max_attempts: int = 2) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        _ensure_ipv4_preference()

    async def fetch_conversations(
        self,
        api_key: str,
        report_date: date,
        page_size: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_conversations_sync, api_key, report_date, page_size)

    async def fetch_performance(
        self,
        api_key: str,
        report_date: date,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_performance_sync, api_key, report_date)

    async def ping(self, api_key: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._ping_sync, api_key)

    def _fetch_conversations_sync(
        self,
        api_key: str,
        report_date: date,
        page_size: int | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Tum sayfalari ceker.

        Not: Toniva pratikte sayfa basina ~200 satir donebiliyor (pageSize 1000
        istense bile). Bu yuzden 'len(rows) < pageSize => bitti' YANLIS;
        total_count dolana kadar sayfa artirilmali.
        """
        date_text = report_date.isoformat()
        all_rows: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        page = 1
        # Istenen sayfa; API daha az dondurebilir (gozlenen ~200)
        effective_page_size = page_size if page_size is not None else 500
        effective_page_size = max(1, min(5000, int(effective_page_size)))
        max_pages = 200
        seen_ids: set[str] = set()

        while page <= max_pages:
            params: dict[str, str] = {
                "startDate": date_text,
                "endDate": date_text,
                "pageSize": str(effective_page_size),
                "page": str(page),
            }
            payload = self._get_json(
                f"/reports/conversations?{parse.urlencode(params)}",
                api_key,
            )
            rows, page_meta = _extract_rows_and_meta(payload)
            if isinstance(page_meta, dict):
                meta = {**meta, **page_meta}

            if not rows:
                break

            # Ayni sayfa tekrari (API page yok sayarsa) sonsuz donguyu kes
            new_rows = 0
            for row in rows:
                call_id = str(row.get("CallID") or row.get("callId") or row.get("id") or "")
                if call_id and call_id in seen_ids:
                    continue
                if call_id:
                    seen_ids.add(call_id)
                all_rows.append(row)
                new_rows += 1

            total_count = _safe_int(meta.get("total_count") or meta.get("totalCount"))
            logger.info(
                "conversations page=%s got=%s new=%s total_fetched=%s api_total=%s",
                page,
                len(rows),
                new_rows,
                len(all_rows),
                total_count or "?",
            )

            if total_count and len(all_rows) >= total_count:
                break
            # Yeni satir yoksa (tekrarlayan sayfa) dur
            if new_rows == 0:
                break
            # total_count yoksa: API bu sayfada hic kayit birakmadiysa / kisa sayfa
            # ve tekrar yoksa bir sonraki sayfayi dene; bos gelirse yukarida kirariz
            if not total_count and len(rows) == 0:
                break
            page += 1
        else:
            logger.warning(
                "conversations sayfalama ust sinira ulasti (page=%s, rows=%s)",
                max_pages,
                len(all_rows),
            )

        meta = dict(meta or {})
        meta["fetched_count"] = len(all_rows)
        total_count = _safe_int(meta.get("total_count") or meta.get("totalCount"))
        # Cekim sirasinda total artabilir (canli cagrilar); kucuk farki incomplete sayma
        gap = (total_count - len(all_rows)) if total_count else 0
        if total_count and gap > 50 and gap > len(all_rows) * 0.02:
            meta["truncated"] = True
            meta["incomplete"] = True
        else:
            meta["incomplete"] = False
            meta["truncated"] = False
        return all_rows, meta

    def _fetch_performance_sync(
        self,
        api_key: str,
        report_date: date,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        date_text = report_date.isoformat()
        params = parse.urlencode({"startDate": date_text, "endDate": date_text})
        payload = self._get_json(f"/reports/performance?{params}", api_key)
        return _extract_rows_and_meta(payload)

    def _ping_sync(self, api_key: str) -> dict[str, Any]:
        today = date.today().isoformat()
        params = parse.urlencode({"startDate": today, "endDate": today, "pageSize": "1", "page": "1"})
        payload = self._get_json(f"/reports/conversations?{params}", api_key)
        rows, meta = _extract_rows_and_meta(payload)
        return {"ok": True, "sample_row_count": len(rows), "meta": meta}

    def _get_json(self, path: str, api_key: str) -> Any:
        url = f"{self.api_url}{path if path.startswith('/') else '/' + path}"
        api_request = request.Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "toniva-kalite-kontrol-bot/1.0",
            },
            method="GET",
        )
        try:
            raw_body, status = self._read_response(api_request)
        except TimeoutError as exc:
            raise TonivaTimeoutError(
                f"Toniva API {self.timeout_seconds} sn icinde yanit vermedi.",
                status=None,
            ) from exc
        except (ConnectionResetError, error.URLError) as exc:
            raise TonivaConnectionError(
                "Toniva API baglantisi basarisiz veya yari da kapandi.",
            ) from exc

        if not raw_body.strip():
            return {}
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise TonivaError(f"Toniva API JSON degil (HTTP {status}).", status=status) from exc

        if status >= 400:
            code = None
            message = f"Toniva API hata dondurdu (HTTP {status})."
            if isinstance(payload, dict):
                code = payload.get("code")
                message = str(payload.get("message") or message)
                if payload.get("required_scope"):
                    message = f"{message} (gerekli scope: {payload['required_scope']})"
            raise TonivaError(message, code=str(code) if code else None, status=status)
        return payload

    def _read_response(self, api_request: request.Request) -> tuple[str, int]:
        last_error: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with request.urlopen(api_request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8-sig")
                    return body, int(getattr(response, "status", 200) or 200)
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8-sig", errors="replace")
                if exc.code == 429 and attempt < self.max_attempts:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    sleep_seconds = int(retry_after) if retry_after and str(retry_after).isdigit() else 2
                    time.sleep(max(1, sleep_seconds))
                    last_error = exc
                    continue
                return body, int(exc.code)
            except (TimeoutError, socket.timeout, ConnectionResetError, error.URLError) as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    time.sleep(1)
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise TonivaError("Toniva API yaniti okunamadi.")


def _extract_rows_and_meta(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if payload is None:
        return [], {}
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], {}
    if not isinstance(payload, dict):
        raise TonivaError("Toniva API beklenmeyen yanit formati.")

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    for key in ("rows", "data", "items", "results", "records", "Data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)], dict(meta or {})
        if isinstance(value, dict):
            nested_rows = value.get("rows") or value.get("items") or value.get("data")
            if isinstance(nested_rows, list):
                nested_meta = value.get("meta") if isinstance(value.get("meta"), dict) else meta
                return [row for row in nested_rows if isinstance(row, dict)], dict(nested_meta or {})

    # Duz rapor: tum list-of-dict olmayan alanlari meta say
    if all(not isinstance(v, list) for v in payload.values()):
        return [], dict(payload)
    raise TonivaError("Toniva rapor yanitinda satir listesi bulunamadi.")


def _safe_int(value: object) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0

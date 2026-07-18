from __future__ import annotations

"""Ihlal tipi anahtari (metin normalizasyonu).

Saatlik rapor artik tam rapor gonderir (ihlal var/yok + personel ozet);
suppress_notified ile filtreleme varsayilan olarak kullanilmaz.
"""

VIOLATION_KEY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("guncel bekleme ihlali:", "guncel bekleme ihlali"),
    ("güncel bekleme ihlali:", "güncel bekleme ihlali"),
    ("mesai baslangici ihlali:", "mesai baslangici ihlali"),
    ("mesai başlangıcı ihlali:", "mesai başlangıcı ihlali"),
    ("cagri arasi bekleme ihlali:", "cagri arasi bekleme ihlali"),
    ("çağrı arası bekleme ihlali:", "çağrı arası bekleme ihlali"),
    ("mola oncesi cagri birakma ihlali:", "mola oncesi cagri birakma ihlali"),
    ("mola öncesi çağrı bırakma ihlali:", "mola öncesi çağrı bırakma ihlali"),
    ("mola sonrasi cagri baslangic ihlali:", "mola sonrasi cagri baslangic ihlali"),
    ("mola sonrası çağrı başlangıç ihlali:", "mola sonrası çağrı başlangıç ihlali"),
    ("mesai bitisi ihlali:", "mesai bitisi ihlali"),
    ("mesai bitişi ihlali:", "mesai bitişi ihlali"),
)


def violation_key(violation: str) -> str:
    normalized = violation.casefold().strip()
    for prefix, key in VIOLATION_KEY_PREFIXES:
        if normalized.startswith(prefix.casefold()):
            return key
    return " ".join(normalized.split())

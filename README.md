# Toniva Çağrı Denetim Telegram Botu

Toniva Public API üzerinden çağrı raporu alır, departman kurallarına göre personel ihlallerini kontrol eder ve ilgili Telegram gruplarına rapor gönderir.

## Model

| Öğe | Değer |
|-----|--------|
| Bot | 1 adet |
| Telegram grupları | 6 (`ALLOWED_GROUP_NAMES`) |
| Departman | 7 (bir grupta 2 departman olabilir) |
| Toniva API key | 5 |
| Paylaşılan API | 1 key → 2 departman; **ayrım personel listesi ile** |

- Özel sohbette bot **yanıt vermez**
- Yetkili gruptaki **herkes** komut kullanabilir
- **11:00–19:00** her saat başı aktif departmanlar **sırayla** kontrol edilir (~30 sn ara)

## Railway Environment Variables

Örnek dosya: [`.env.example`](.env.example)

| Değişken | Zorunlu | Örnek / varsayılan | Açıklama |
|----------|---------|-------------------|----------|
| `TELEGRAM_BOT_TOKEN` | **Evet** | BotFather token | Telegram bot token |
| `ALLOWED_GROUP_NAMES` | Önerilir | `Grup1,Grup2,...` | Virgülle grup **başlıkları** (case-insensitive). Boşsa yalnızca kayıtlı chat'ler. |
| `DATABASE_PATH` | Önerilir (Railway) | `/data/bot.sqlite3` | SQLite yolu; volume ile kalıcı |
| `TONIVA_API_URL` | Hayır | `https://crm.toniva.net/api/public/v1` | Public API base |
| `TIMEZONE` | Hayır | `Europe/Istanbul` | Saat dilimi |
| `REPORT_INTERVAL_MINUTES` | Hayır | `60` | Saat başı rapor |
| `SCHEDULER_START_TIME` | Hayır | `11:00` | Otomatik rapor başlangıç |
| `SCHEDULER_END_TIME` | Hayır | `19:00` | Otomatik rapor bitiş (dahil) |
| `DEPARTMENT_REPORT_DELAY_SECONDS` | Hayır | `30` | Departmanlar arası bekleme |
| `REQUEST_TIMEOUT_SECONDS` | Hayır | `90` | Toniva HTTP timeout |
| `TONIVA_FORCE_IPV4` | Hayır | `1` | IPv4 zorla (whitelist) |
| `PORT` | Hayır | Railway verir | Basit health HTTP |

### Railway’e kopyala-yapıştır (değerleri doldur)

```
TELEGRAM_BOT_TOKEN=
ALLOWED_GROUP_NAMES=
DATABASE_PATH=/data/bot.sqlite3
TONIVA_API_URL=https://crm.toniva.net/api/public/v1
TIMEZONE=Europe/Istanbul
REPORT_INTERVAL_MINUTES=60
SCHEDULER_START_TIME=11:00
SCHEDULER_END_TIME=19:00
DEPARTMENT_REPORT_DELAY_SECONDS=30
REQUEST_TIMEOUT_SECONDS=90
TONIVA_FORCE_IPV4=1
```

**Not:** Departman Toniva API key’leri env’de **değil**. Grupta `/departmantanimla` ile SQLite’a yazılır (volume şart).

## Railway Deploy

1. GitHub repo ile servis oluşturun  
2. **Volume** ekleyin, mount: `/data`  
3. Yukarıdaki env değişkenlerini girin  
4. Start: `python -m bot.main` (`railway.json` / `Procfile`)  
5. Toniva panelinde Railway **outbound IP** whitelist  
6. API key scope: en az `reports:read`  
7. Botu 6 gruba ekleyin → her grupta kurulum  

## Kurulum akışı (grupta)

```
/departmantanimla   → ad + API key
/kuralayarla        → mesai / gap / mola (boş = kapalı)
/personelekle       → önce departman adı sorulur
/personeltopluekle  → önce departman adı, sonra xlsx
/sorumluekle        → opsiyonel
/haftalikizin       → opsiyonel
/rapor              → manuel (tüm ihlaller)
/kontroltoniva      → API erişim testi
```

Aynı grupta 2 departman: iki kez `/departmantanimla`, personeli **doğru departman adıyla** ekleyin.

## Komutlar

```
/start /help /chat_id /kimim
/departmantanimla /departman_listele /departman_sil /departman_aktif /departman_pasif
/apitanimla
/kuralayarla /kurallistele
/personelekle /personeltopluekle /personel_listele /personel_sil /personel_aktif /personel_pasif
/izin /iziniptal /izinlistele
/haftalikizin /haftalikizinduzenle /haftalikiziniptal
/sorumluekle /sorumlusil /sorumlulistele
/rapor /kontroltoniva /iptal
```

## Kural mantığı

- Kaynak: `GET /reports/conversations` (+ performance süreleri)
- Personel listesi varsa yalnızca o kişiler (paylaşılan API ayrımı)
- Ring-only aramalar da aktivite sayılır (olumlu politika)
- Saatlik: aynı gün aynı personel+ihlal tipi bir kez
- `/rapor`: o ana kadarki tüm ihlaller
- Departmanlar sırayla işlenir (paralel değil)

## Lokal çalıştırma

```bash
pip install -r requirements.txt
cp .env.example .env   # token ve grup adlarini doldur
# Windows: set TELEGRAM_BOT_TOKEN=...
python -m bot.main
```

## Test

```bash
pip install pytest
python -m pytest tests/ -q
```

## Güvenlik

- Token / API key’i **asla** commit etmeyin  
- `.env` ve `data/*.sqlite3` gitignore’da  
- Token sızdıysa BotFather + Toniva’dan yenileyin  

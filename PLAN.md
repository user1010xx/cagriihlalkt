# Toniva Çağrı Denetim Botu — Uygulama Planı

## İş modeli

- **1 Telegram bot**, **6 Telegram grubu**, **6 departman**, **5 Toniva API key**
- 5 departman: 1 grup ↔ 1 departman ↔ 1 API
- 1 API iki departmana hizmet eder: aynı key, **personel listesine göre** filtre
- Private chat: yanıt yok
- `ALLOWED_GROUP_NAMES` dışındaki gruplar: yetkisiz
- Gruba üye herkes tüm komutları kullanabilir
- 10:00–19:00 saat başı: tüm aktif departmanlar sırayla kontrol → her gruba rapor (ihlal var/yok); aynı personel+ihlal tipi gün içinde tekrarlanmaz

## Modül sırası

1. `config`, `models`, `time_utils`, `database`
2. `toniva_client`, `rules`, `violation_keys`, `reporting`, `service`
3. `access`, `handlers`, `main`
4. `scheduler`, `health`
5. Railway dosyaları + README
6. Unit + smoke testler

## Komutlar (v1)

- `/start` `/help` `/chat_id` `/kimim`
- `/departmantanimla` — adım adım: ad + API key (bulunulan gruba bağlar)
- `/departman_listele` `/departman_sil` `/departman_aktif` `/departman_pasif`
- `/apitanimla` — mevcut departmana API key güncelle
- `/kuralayarla` `/kurallistele`
- `/personelekle` `/personeltopluekle` `/personel_listele` `/personel_sil` `/personel_aktif` `/personel_pasif`
- `/izin` `/iziniptal` `/izinlistele`
- `/haftalikizin` `/haftalikizinduzenle` `/haftalikiziniptal`
- `/sorumluekle` `/sorumlusil` `/sorumlulistele`
- `/rapor` `/kontroltoniva` `/iptal`

## Veri

SQLite (`DATABASE_PATH`). Railway: volume `/data`.

## Durum

- [x] Plan
- [x] Bot iskeleti + DB + Toniva client + kurallar + handlers + scheduler
- [x] Unit + smoke testler (`pytest tests/` → 23 passed)
- [ ] Railway deploy + gerçek API key / grup kurulum (kullanıcı)

# SpoonSteady Backend (independent)

Backend subscriber mandiri untuk frontend yang sudah ada. Tidak mengubah HTML,
CSS, JavaScript, atau repository frontend temanmu.

## Alur
HP 1 -> HiveMQ `spoonsteady/telemetry` -> (subscriber terpisah) backend Railway
-> pemrosesan -> SQLite. HP 2 tetap menerima pesan langsung dari HiveMQ seperti
sebelumnya. Backend ini **tidak** mem-publish ulang pesan dan tidak mengubah alur HP 1/HP 2.

## Data yang diharapkan
Backend membaca JSON yang berisi `rms` dan `pitch`, sesuai payload frontend saat ini.
Contoh: `{"rms": 0.42, "pitch": 18}`.

## Jalankan lokal
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```
Buka `http://127.0.0.1:8000/docs`.

## Deploy Railway
1. Push folder ini ke repository GitHub baru, misalnya `spoonsteady-backend`.
2. Di Railway: New Project -> Deploy from GitHub Repo -> pilih repo backend.
3. Tambahkan Volume pada service backend dan mount path `/data`.
4. Tambahkan Variables:
   - `DB_PATH=/data/spoonsteady.db`
   - `MQTT_HOST=broker.hivemq.com`
   - `MQTT_PORT=1883`
   - `MQTT_TOPIC=spoonsteady/telemetry`
   - `MQTT_CLIENT_ID=spoonsteady-backend-<id-unik>`
5. Generate public domain pada Networking.
6. Tunggu deployment sehat, lalu cek `/`, `/health`, `/api/latest`, `/api/history`, `/api/stats`.

## Catatan penting
- Public HiveMQ broker memakai topic publik/shared. Gunakan topic unik jika memungkinkan,
  tetapi itu memerlukan perubahan frontend. Jangan kirim data pribadi atau sensitif.
- Koneksi backend ke port 1883 tidak terenkripsi. Cocok hanya untuk data dummy/prototipe.
- SQLite persisten di Railway hanya jika volume benar-benar dipasang ke `/data`.
- Ambang `calm/mild/tremor`, stability score, dan hitungan suapan adalah heuristik demo,
  bukan alat diagnosis atau pengukuran klinis.
- API ini hanya untuk membaca data dan belum memiliki autentikasi. Jangan gunakan untuk
  menyimpan informasi pribadi atau membuka data sensitif ke publik.
- Jika ada lebih dari satu perangkat mengirim ke topic yang sama, data akan tercampur.

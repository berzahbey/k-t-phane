# K-Tphane

Ev sunucusu (ZimaOS / CasaOS / Docker) için kişisel kitap arşivi. PDF, EPUB ve diğer e-kitap
dosyalarınızı tarar, kategorilere ayırır; arama, filtreleme ve tarayıcıda okuma sağlar.

## Özellikler

- **Akıllı tarama**: Tarama arka planda yapılır, sonuç diske kaydedilir. Sonraki açılışlar anında olur
  ve yalnızca yeni/değişen dosyalar yeniden incelenir. 30 dakikada bir otomatik tarama, ayrıca “Tara”
  düğmesi (Shift+tık = tam yeniden tarama).
- **Dosyanın içinden bilgi**: EPUB başlık/yazar/dil/konu/açıklama/kapak; PDF sayfa sayısı ve
  “metin tabanlı / OCR gerekli” tespiti; PDF ilk sayfasından kapak.
- **Kategori tespiti**: Türkçe karakter uyumlu, tam kelime eşleştirme (artık “Dinamik” → Din olmuyor),
  klasör adları, yazar adları ve Arapça başlıklar dikkate alınır.
- **Arayüz**: anlık arama, kategori ağacı, dil/format/durum filtreleri, kart ve liste görünümü,
  sıralama, kitap detay penceresi, mobil uyum, açık/koyu tema.
- **EPUB okuyucu**: içindekiler, sayfa çevirme (ok tuşları / kaydırma), yazı boyutu, açık-sepya-koyu tema,
  kaldığı yeri hatırlama. PDF'ler tarayıcının kendi görüntüleyicisinde açılır.
- macOS `._` dosyaları, Synology `@eaDir`, çöp kutusu klasörleri yok sayılır.

## Kurulum (ZimaOS)

1. App Store → sağ üstteki **+** → **Install a customized app** → **Import**.
2. Bu repodaki `docker-compose.yml` içeriğini yapıştırın. Kitap klasörünüz `/media/ZimaOS-HD/Kitap`
   değilse yalnızca o satırı değiştirin.
3. **Install**. Arayüz: `http://SUNUCU-IP:5090`

## Ayarlar (ortam değişkenleri)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `SCAN_INTERVAL` | `1800` | Otomatik tarama aralığı (saniye). `0` = kapalı |
| `APP_PASSWORD` | boş | Doluysa tarayıcı şifre sorar. Uygulamayı internete açacaksanız mutlaka doldurun |
| `APP_USER` | boş | Şifreyle birlikte istenen kullanıcı adı (boşsa herhangi bir ad kabul edilir) |
| `BOOK_DIR` | `/books` | Container içindeki kitap klasörü |
| `DATA_DIR` | `/data` | Önbellek ve kapak resimleri |

## Image

Her `main` push'unda GitHub Actions şu etiketleri yayınlar:
`ghcr.io/berzahbey/k-t-phane:latest`, `:main`, `:sha-xxxxxxx`.

## Geliştirme

```bash
pip install -r requirements.txt
BOOK_DIR=~/Kitaplar DATA_DIR=./data python app.py   # http://localhost:5000
```

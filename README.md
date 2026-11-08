# Kitaplık

Ev sunucusu (ZimaOS / CasaOS / Docker) için kişisel kitap arşivi. PDF, EPUB ve diğer e-kitap
dosyalarınızı tarar, kategorilere ayırır; arama, filtreleme ve tarayıcıda okuma sağlar.

## Özellikler

- **Akıllı tarama**: Tarama arka planda yapılır, sonuç diske kaydedilir. Sonraki açılışlar anında olur
  ve yalnızca yeni/değişen dosyalar yeniden incelenir. 30 dakikada bir otomatik tarama, ayrıca “Tara”
  düğmesi (Shift+tık = tam yeniden tarama).
- **Dosyanın içinden bilgi**: EPUB başlık/yazar/dil/konu/açıklama/kapak; PDF sayfa sayısı ve
  “metin tabanlı / OCR gerekli” tespiti; PDF ilk sayfasından kapak.
- **İçerik analizi**: Kitabın içinden metin okunur (EPUB bölümleri ve içindekiler, PDF'in ilk sayfaları,
  taranmış PDF'lerde ilk 3 sayfaya OCR). Dil gerçek metinden, konu metindeki terimlerin sıklığından bulunur.
- **Yapay zeka (isteğe bağlı, ücretsiz)**: `GEMINI_API_KEY` verilirse Gemini'nin ücretsiz katmanı içerik
  örneğine bakarak kategori, dil, başlık ve yazarı belirler. Günlük üst sınır (`GEMINI_DAILY_LIMIT`) ve dakikalık
  hız sınırı vardır; kota dolunca ertesi gün kaldığı yerden devam eder. Anahtar yoksa her şey yerel çalışır.
  Karar önceliği: senin klasörün > Gemini > kitabın içeriği > dosya adı/etiketler. Detay penceresinde
  “Neden” satırı kararın kaynağını gösterir.
- **Kategori tespiti**: Türkçe karakter uyumlu, tam kelime eşleştirme (artık “Dinamik” → Din olmuyor),
  klasör adları, yazar adları ve Arapça başlıklar dikkate alınır.
- **Arayüz**: anlık arama, kategori ağacı, dil/format/durum filtreleri, kart ve liste görünümü,
  sıralama, kitap detay penceresi, mobil uyum, açık/koyu tema.
- **EPUB okuyucu**: içindekiler, sayfa çevirme (ok tuşları / kaydırma), yazı boyutu, açık-sepya-koyu tema,
  kaldığı yeri hatırlama. PDF'ler tarayıcının kendi görüntüleyicisinde açılır.
- **Klasör düzenleme**: Kitapları `Dil / Konu / EPUB` ve `Dil / Konu / PDF / Metin|OCR` yapısına taşır.
  Önce önizleme gösterir; onaydan sonra yeni eklenen kitaplar da otomatik düzenlenir. Dosya silinmez,
  her taşıma kaydedilir ve “Son düzenlemeyi geri al” ile geri döndürülebilir. Bir kitabı elle başka bir
  dil/konu klasörüne koyarsanız uygulama sizin seçiminizi esas alır.
- **Koleksiyonlar**: Aynı eserin ciltleri (“İhya 1…8”, “Mesnevi Cilt 2”, “Tarih Vol. II”) tek bir koleksiyon
  kartında toplanır; klasörde `<Dil>/<Konu>/Koleksiyonlar/<Eser adı>/` altında birlikte durur. Tüm ciltler
  aynı kategori ve dile konur.
- **Silme**: Kitap detayında her formatın yanında *Sil*; birden fazla format veya koleksiyon için “tümüyle sil”.
  İki aşamalı onay ister ve dosyayı sunucudan **kalıcı** olarak siler (çöp kutusu yok). Silinenlerin adı/yolu
  `/data/silinenler-kaydi.json` dosyasına not edilir.
- **Kopyalar**: Aynı kitabın aynı formattaki kopyaları bulunur; en iyi durumdaki (metin tabanlı > taranmış,
  eksiksiz > eksik, sağlam > bozuk) kalır, diğerleri iki aşamalı onayla silinir. *Kesin* kopyalar (birebir aynı
  dosya ya da aynı sayfa sayılı aynı PDF) istenirse yeni eklendiğinde otomatik temizlenir; *muhtemel* kopyalar
  (farklı baskı/çeviri olabilir) yalnızca listelenir. Farklı formatlar (EPUB + PDF) kopya sayılmaz.
- **Favoriler**: Kapaktaki ★ ile (veya detaydan, toplu seçimden) favorilere eklenir; solda “Favoriler” listesi.
  Kitap klasöründe `Favoriler/` klasörü oluşur. İçindekiler kopya değil sabit bağlantıdır (ek yer kaplamaz,
  asıl dosya taşınsa da çalışır); koleksiyonlar alt klasörde durur. Favoriden çıkarılan veya silinen kitabın
  bağlantısı kaldırılır; klasöre elle konan dosyalara dokunulmaz.
- **Toplu seçim**: “Seç” ile kitaplar/koleksiyonlar işaretlenir; “Görünenlerin tümünü seç” filtre ve aramayla
  birlikte çalışır; “Seçilenleri sil” aynı iki aşamalı onayla toplu siler.
- macOS `._` dosyaları, Synology `@eaDir`, çöp kutusu klasörleri yok sayılır.

## Kurulum (ZimaOS)

1. App Store → sağ üstteki **+** → **Install a customized app** → **Import**.
2. Bu repodaki `docker-compose.yml` içeriğini yapıştırın. Kitap klasörünüz `/media/ZimaOS-HD/Kitap`
   değilse yalnızca o satırı değiştirin.
3. **Install**. Arayüz: `http://SUNUCU-IP:5090`

## Gemini anahtarı

Uygulamada sağ üstteki **⚙ Ayarlar** düğmesinden girilir (`/data/settings.json` içinde saklanır, GitHub'a gitmez).
İsterseniz compose'taki `GEMINI_API_KEY` ile de verilebilir; uygulamadan girilen anahtar önceliklidir.

## Ayarlar (ortam değişkenleri)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `SCAN_INTERVAL` | `1800` | Otomatik tarama aralığı (saniye). `0` = kapalı |
| `APP_PASSWORD` | boş | Doluysa tarayıcı şifre sorar. Uygulamayı internete açacaksanız mutlaka doldurun |
| `APP_USER` | boş | Şifreyle birlikte istenen kullanıcı adı (boşsa herhangi bir ad kabul edilir) |
| `GEMINI_API_KEY` | boş | Ücretsiz Gemini anahtarı ([AI Studio](https://aistudio.google.com/apikey), faturalandırması kapalı proje) |
| `GEMINI_MODEL` | `gemini-flash-lite-latest` | Kullanılacak model |
| `GEMINI_DAILY_LIMIT` | `500` | Günde en fazla istek |
| `GEMINI_RPM` | `8` | Dakikada en fazla istek |
| `OCR_LANGS` | `tur+ara+eng` | Taranmış PDF'ler için OCR dilleri |
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

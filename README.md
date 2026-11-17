# CanlıAltyazı (LiveSub) · Gerçek Zamanlı Yerel Oyun & Video Altyazı Çevirmeni

CanlıAltyazı (LiveSub); **Windows (10/11)** ve **Linux (X11)** masaüstlerinde seçtiğiniz bir ekran bölgesindeki altyazıları gerçek zamanlı olarak **PaddleOCR** ile okuyan, yerel **LLM** modelleriyle çeviren ve sonucu **60 FPS akıcı kayan saydam overlay kutusunda** gösteren masaüstü uygulamasıdır. Tüm OCR ve çeviri işlemleri tamamen bilgisayarınızda yerel olarak çalışır.

4070Super 12 GB grafik kartı ve AMD Ryzen7 5800x ile denenmiştir, ancak kullanılan dil modeli donanımınıza bağlı olarak seçilebilmektedir. 1.8b parametreli modelle dahi çok iyi sonuç alınmaktadır. 

---

## İçindekiler
1. [Öne Çıkan Özellikler](#1-öne-çıkan-özellikler)
2. [Kurulum Kılavuzu](#2-kurulum-kılavuzu)
3. [Kullanım Kılavuzu](#3-kullanım-kılavuzu)
4. [Ayarlar ve Dosya Yapısı](#4-ayarlar-ve-dosya-yapısı)
5. [Sorun Giderme ve İpuçları](#5-sorun-giderme-ve-i̇puçları)
6. [Lisans](#6-lisans)

---

## 1. Öne Çıkan Özellikler

- **Windows & Linux Çapraz Platform Desteği:**
  - **Windows:** Win32 `WS_EX_TOPMOST` + tıklama geçirmeli (`WS_EX_TRANSPARENT`) saydam overlay, Yüksek DPI fiziksel piksel hizalaması (`125%`/`150%` ölçeklerde kaymasız bölge yakalama) ve negatif koordinatlı çoklu monitör desteği.
  - **Linux (X11):** Çoklu monitör seçimi (`ScreenPicker`) ve X11 saydam overlay desteği.
- **Çoklu Monitör ve Canlı Bölge Değiştirme:** Belirli bir monitörü veya `Tüm Ekranlar` seçeneğini seçerek istediğiniz ekrandan altyazı okuyabilirsiniz. Tarama çalışırken bölge değiştirdiğinizde yakalama anında yeni ekrana taşınır.
- **Altyazı Renk & Kontur Filtresi (`color_contour`):** Hareketli oyun sahnelerindeki beyaz/sarı/konturlu altyazıları arka plan dokularından, tabelalardan ve UI gürültüsünden ayırır.
- **Gömülü Sözlük Destekli Tek Kelime ve Diyalog Doğrulaması:** Proje içinde paketlenen sıkıştırılmış İngilizce sözlük (`config/en_words.txt.gz`, `72.517` kelime) sayesinde harici sistem sözlüğü olmayan Windows'ta da `"Wait!"`, `"Yeah."`, `"Mom!"`, `"Nah."` gibi tek kelimelik diyalogları kaçırmaz ve arka plan gürültülerini eler.
- **60 FPS Akıcı ve Özelleştirilebilir Altyazı Kutusu (BBox):**
  - Konum modu (`Altyazı Üstü`, `Ekran Altı`, `Ekran Üstü`, `Özel Konum X/Y`), boyut (`250–2500 × 45–800 px`), arka plan şeffaflığı (`%0–%100`), yazı fontu ve boyutu (`12–36 pt`) kullanıcı tarafından belirlenir ve `config/settings.json` içine kaydedilir.
  - Önizleme modunda kutuyu fareyle ekranda sürükleyip sağ/alt/köşe kenarlarından boyutlandırabilirsiniz.
- **Yerel Ollama Çevirisi ve SQLite Önbellek:** Kuyruklu çeviri yapısı seri gelen altyazıları atlamaz; tekrarlanan cümleleri önbellekten milisaniyeler içinde getirir.

---

## 2. Kurulum Kılavuzu

### 2.1. Windows 10 / 11 Kurulumu

1. **Python Kurulumu:** [python.org](https://www.python.org/downloads/) üzerinden **Python 3.10 – 3.12** sürümünü indirin *(Kurulum ekranında **"Add Python to PATH"** kutucuğunu işaretleyin)*.
2. **Otomatik Kurulum:** Proje klasöründeki **`setup.bat`** dosyasına çift tıklayın *(veya PowerShell/CMD içinde `.\setup.bat` çalıştırın)*. Betik `.venv` ortamını oluşturup `requirements.txt` bağımlılıklarını yükleyecektir.
3. **Ollama ve Model Kurulumu:**
   - [ollama.com](https://ollama.com/download/windows) üzerinden Windows için Ollama'yı kurun (kurulum sonrası arka planda otomatik başlar).
   - Komut İstemi (CMD) veya PowerShell açıp önerilen çeviri modelini indirin:
     ```bat
     ollama pull ali6parmak/hy-mt1.5:1.8b
     ```
     *(Alternatif: `ollama pull translategemma:4b`)*

> **Not (Windows GPU ile OCR):** Varsayılan kurulum CPU üzerinde `paddlepaddle` kullanır. NVIDIA GPU kullanmak isterseniz `.venv\Scripts\python.exe -m pip install` ile CUDA sürümünüze uygun `paddlepaddle-gpu` kurabilirsiniz; aksi halde **OCR cihazı** ayarını `Otomatik` veya `CPU` kullanın.

### 2.2. Linux (Ubuntu / Debian · X11) Kurulumu

- **Pencere Sistemi:** **X11 oturumu** *(Global tıklama geçirmeli overlay için önerilir; Wayland oturumlarında pencere konumu/üstte kalma kısıtlıdır.)*
- **Python:** Python `3.10` – `3.12`
- **Sistem Paketleri (Önerilen):**
  ```bash
  sudo apt update
  sudo apt install -y python3-venv python3-pip wamerican libgl1 libx11-6
  ```
- **Otomatik veya Elle Bağımlılık Kurulumu:**
  ```bash
  chmod +x setup.sh
  ./setup.sh
  ```
  Veya elle:
  ```bash
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  ```
- **Ollama ve Çeviri Modelini Hazırlama:**
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ollama serve
  ollama pull ali6parmak/hy-mt1.5:1.8b
  ```

---

## 3. Kullanım Kılavuzu

### 3.1. Uygulamayı Başlatma

- **Windows:** Klasördeki **`run.bat`** dosyasına çift tıklayın (veya terminalde `.venv\Scripts\python.exe main.py`).
- **Linux:**
  ```bash
  .venv/bin/python main.py
  ```

### 3.2. Adım Adım İlk Kullanım

1. **Altyazı Bölgesini Seçin (`▧ Bölge Seç`):**
   - Birden fazla monitörünüz varsa açılan pencerede altyazının bulunduğu monitörü (veya `Tüm Ekranlar` seçeneğini) seçip **Bu ekranda bölge seç** düğmesine tıklayın.
   - Açılan karartılmış ekranda, oyun veya video altyazılarının çıktığı dikdörtgen alanı farenizle sürükleyip bırakın *(İptal için `Esc`)*.
   - Üst bardaki **`📍 Ekran · Genişlik×Yükseklik`** rozetinden seçili alanı görebilirsiniz.

2. **Motor ve Model Ayarlarını Yapın (`⚙️ Motor & Model` sekmesi):**
   - **Kaynak dil / Hedef dil:** Örneğin `English` → `Turkish`.
   - **OCR cihazı:** `Otomatik`, `CPU` veya `GPU`.
   - **Altyazı filtresi:** Oyun ve filmler için **`Açık (Renk & Kontur)`** önerilir. Düz koyu zeminli uygulamalarda fark görmek isterseniz `Kapalı` seçeneğini tarama durdurmadan bile deneyebilirsiniz.
   - **LLM sunucusu ve Model:** Varsayılan adres `http://127.0.0.1:11434`. Yeni model eklediyseniz sunucu kutusunun yanındaki **`↻`** düğmesiyle listeyi yenileyebilirsiniz.
   - **Profil ve Tarama sıklığı:**
     - `Ekonomik`: `4 Hz` (Düşük CPU kullanımı)
     - `Dengeli`: `7 Hz` (Çoğu oyun ve video için ideal)
     - `Performans`: `10 Hz` (Hızlı değişen diyaloglar için, `1–15 Hz` arası elle değiştirilebilir; tarama çalışırken anında uygulanır)

3. **Altyazı Kutusunu Özelleştirin (`🎨 Altyazı Kutusu (BBox)` sekmesi):**
   - **Overlay açık** onay kutusu işaretliyken çeviriler ekran üzerinde yüzen kutuda gösterilir.
   - **Kutu konumu:**
     - `Altyazı Üstü (Akıllı Otomatik)`: Orijinal altyazıyı kapatmadan hemen üzerine yerleşir.
     - `Ekran Altı` / `Ekran Üstü`: Seçili monitörün alt veya üst orta kısmına sabitler.
     - `Özel Konum (X, Y)`: Verdiğiniz koordinata sabitler.
   - **Kutu boyutu (`G × Y px`), Kutu şeffaflığı (`%0–%100`), Yazı fontu ve Yazı boyutu (`12–36 pt`):** Anında kutuya yansır; `%0` şeffaflıkta yalnızca dış hat gölgeli doğal yazı görünür.
   - **Ekranda Taşıma ve Önizleme (`🎯 Konumu Ekranda Taşı / Önizle`):** Kutuyu ekranda görünür ve taşınabilir hale getirir. Ortadan tutup istediğiniz monitöre sürükleyebilir, sağ/alt kenar veya sağ-alt köşeden boyutlandırabilirsiniz. İşiniz bitince **`✓ Konumu Kilitle ve Tamamla`** düğmesine basın.
   - İstediğiniz zaman **`↺ Kutu Ayarlarını Varsayılana Sıfırla`** ile varsayılan boyut ve görünüme dönebilirsiniz.

4. **Çeviriyi Başlatın (`▶ Çeviriyi Başlat`):**
   - İlk başlatmada seçili Ollama modeli belleğe (`keep_alive: -1`) alınır ve PaddleOCR hazırlanır.
   - Sağ panelde:
     - **ALGILANAN METİN (OCR):** Ekrandan okunan güncel kaynak altyazı.
     - **AKTİF ÇEVİRİ:** Son çevrilen metin.
     - **SON ÇEVİRİ GEÇMİŞİ:** Oturum boyunca `[SS:DD:sn] Kaynak ➔ Çeviri` biçiminde akan konuşma günlüğü.
   - İstediğiniz an **`🧹 Temizle`** ile aktif kutuyu ve üst metin alanlarını temizleyebilir, **`■ Durdur`** ile taramayı sonlandırabilirsiniz.

---

## 4. Ayarlar ve Dosya Yapısı

- **`config/settings.json`:** Tüm seçimler (monitör, yakalama bölgesi, OCR cihazı, filtre modu, model, tarama sıklığı, BBox konumu/boyutu/şeffaflığı/fontu) otomatik olarak burada saklanır.
- **`config/en_words.txt.gz`:** Windows ve çevrimdışı sistemler için gömülü İngilizce kelime sözlüğü.
- **Çeviri Önbelleği:**
  - Windows: `%LOCALAPPDATA%\LiveSub\translations.sqlite3`
  - Linux: `~/.local/share/livesub/translations.sqlite3`
- **Ana Modüller:**
  - `main.py` — Uygulama giriş noktası (`setup.bat` / `run.bat` Windows yardımcıları dahil)
  - `core/pipeline.py` — Yakalama döngüsü (`CaptureWorker`), Renk & Kontur filtresi, İngilizce/diyalog doğrulama ve çeviri işçisi (`TranslationWorker`)
  - `core/ocr.py` — `PaddleTextReader` ve satır/blok gruplama
  - `core/change_detector.py` — Piksel değişim algılayıcısı
  - `core/subtitle_tracker.py` — Kararlılık, tekrar bastırma ve silinme takibi
  - `translation/ollama_provider.py` — Ollama `/api/chat` akışlı çeviri istemcisi
  - `ui/main_window.py` — Ana kontrol arayüzü ve Windows DPI eşitleme
  - `ui/overlay.py` — 60 FPS akıcı kayan saydam overlay penceresi (X11 & Win32 tıklama geçirgenliği/TOPMOST)
  - `ui/region_selector.py` — Ekran seçici (`ScreenPicker`) ve bölge seçici (`RegionSelector`)

---

## 5. Sorun Giderme ve İpuçları

- **"LLM sunucusuna erişilemiyor" hatası:** Ollama servisinin çalıştığından emin olun (Linux'ta `ollama serve`, Windows'ta Ollama sistem tepsisi uygulaması açık olmalı), ardından LLM sunucusu alanının yanındaki `↻` düğmesine tıklayın.
- **Overlay oyun penceresinin üstünde görünmüyor:** Oyunu **Kenarlıksız Pencere (Borderless Windowed)** moduna alın; özel tam ekran (Exclusive Fullscreen) modunda DirectX/X11 pencere yöneticisi harici overlay pencerelerini gizleyebilir.
- **Altyazı kutusu yanlış ekranda açılıyor:** `▧ Bölge Seç` ile hedef monitörde yeniden bölge seçin; BBox konumu yeni seçilen monitöre otomatik taşınır.
- **Kısa/tek kelimelik altyazılar kaçıyorsa:**
  - Yakalama çerçevesini altyazı alanını tam kapsayacak ve köşe kanal logolarını/saatleri mümkün olduğunca dışarıda bırakacak şekilde çizin.
  - `Tarama sıklığı` değerini `7–10 Hz` aralığında tutun.
- **Arka plan yazıları yanlışlıkla okunuyorsa:** `Altyazı filtresi` seçeneğinin **`Açık (Renk & Kontur)`** konumunda olduğundan ve seçili bölgenin yalnızca altyazı şeridini kapsadığından emin olun.

---

## 6. Lisans

Bu proje **MIT License** ile lisanslanmıştır. Ayrıntılar için [LICENSE](LICENSE) dosyasına bakabilirsiniz.

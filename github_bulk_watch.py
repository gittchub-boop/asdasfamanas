"""
GITHUB TOPLU BILDIRIM ACMA SCRIPTI (cok boyutlu tarama / genis kapsam)
=========================================================================
Bu script GitHub'daki repolari SADECE yildiz siralamasindan (leaderboard)
degil, birden fazla farkli boyuttan tarar:
  1) Yildiz araliklarina gore (STAR_BUCKETS)
  2) Populer programlama dillerine gore (LANGUAGES)
  3) GitHub topic/etiketlerine gore (TOPICS)
  4) Yakin zamanda guncellenen (aktif) repolara gore (PUSHED_WINDOWS)

Boylece sadece "en cok yildizli 10000 repo" degil, farkli dillerde,
farkli konularda, az bilinen ama aktif olan repolari da yakalar.
Sonunda hepsini "Watch" (izle) durumuna getirir. Boylece bu repolarda
yeni commit, issue, pull request veya tartisma oldukca Gmail'ine
bildirim e-postasi gelmeye baslar. (dassana kurban, inbox'in cicek
bahcesine donecek amk)

ONEMLI GERCEKLER:
- Watch etmek ANINDA e-posta URETMEZ. E-postalar, izledigin repolarda
  ILERIDE olacak hareketlerle birlikte zamanla birikir.
- GitHub arama API'si TEK SORGUDA EN FAZLA 1000 sonuc verir (sabit bir
  kural, hicbir sekilde asilamaz). Bu yuzden cok sayida repo toplamak
  icin arama, COK FARKLI BOYUTLARA (yildiz + dil + topic + tarih)
  bolunerek tekrarlanir -> her sorgu ayri bir 1000 sonuc hakki demek.
- TOP_N ne kadar buyuk olursa olsun, gercekte GitHub'da o kadar
  "anlamli / benzersiz" repo olmayabilir; script bulabildigi kadarini alir.

HIZ HAKKINDA NOT (degismedi, hala gecerli):
- Arama (search) ucu dakikada ~30 istege izin verir, bunun ustune
  cikarsan gecici blok yersin.
- Watch (subscription) uclarina paralel istek atmak mumkun ama
  sinirsiz degil; MAX_WORKERS'i cok yukseltirsen GitHub "secondary
  rate limit" ile token'ini gecici olarak durdurur. 15-20 arasi guvenli.

KULLANIM:
1. TOKEN, GH_TOKEN adinda bir GitHub Actions secret'indan otomatik gelir.
2. Asagidaki "AYARLAR" bolumunden istedigin gibi oyna.
3. Scripti calistir (GitHub Actions -> Run workflow).

=========================================================================
EKLENEN OZELLIKLER (asagidaki hicbir satir orijinal kodu silmiyor, sadece
uzerine ekleniyor):
  - RESUME / STATE: hangi sorgular tarandi, hangi repolar watch edildi
    bir JSON dosyasinda tutulur. Script yarida kesilirse (Actions
    6 saatlik job limitine takilirsa mesela) kaldigi yerden devam eder.
  - DRY_RUN modu: gercekten watch atmadan kac repo bulunacagini,
    kime gidecegini gormeni saglar.
  - ACTION=unwatch modu: is fazla kacarsa daha once watch ettiklerini
    geri (unwatch) almani saglar.
  - Rate limit'i /rate_limit ucundan ONCEDEN kontrol eder (bu kontrol
    kendi rate limitine SAYILMAZ, GitHub'in kendi dokumantasyonuna gore).
  - Bitince ntfy.sh uzerinden telefonuna (iPhone'da ntfy app'i ile)
    push bildirim yollayabilir - Actions logunu sürekli izlemene gerek
    kalmaz.
  - CSV rapor dosyasi: hangi repo basarili/basarisiz, hepsi diskte kalir.
  - Opsiyonel ek arama boyutlari: fork sayisi, organizasyon, lisans
    (varsayilan KAPALI, acmak istersen INCLUDE_* bayraklarini True yap).
  - EXCLUDE_ARCHIVED: acarsan tum sorgulara otomatik "archived:false"
    eklenir, arsivlenmis/olu repolari filtreler.
=========================================================================
"""

import os
import time
import threading
import json
import csv
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# =============================================================================
# AYARLAR (hepsi burada, alt alta, acik acik - istedigini degistir birader)
# =============================================================================

TOKEN = os.environ.get("GH_TOKEN", "YOUR_GITHUB_TOKEN")
# ^ GitHub token'in. GitHub Actions secret'i olarak GH_TOKEN adiyla eklenmis
#   olmali. Manuel test icin buraya direkt de yazabilirsin ama repoya PUSH'lama.

TOP_N = 50000
# ^ Hedeflenen ust sinir. Gercekte bulunabilen repo sayisi bunun altinda
#   kalabilir, script eldeki ne varsa onu isler, hata vermez.

MAX_WORKERS = 15
# ^ Watch (izleme) isteklerini kac paralel thread ile atacagimiz.
#   10-20 arasi guvenli. Daha yuksegi GitHub'in "abuse detection"
#   sistemini tetikleyebilir, token gecici kisitlanir (yani ters teper).

SEARCH_SLEEP_SECONDS = 1
# ^ Her arama sayfasi arasinda bekleme suresi (saniye). GitHub arama ucu
#   dakikada ~30 istege izin veriyor, bunun altinda kalmak icin var.

MAX_PAGES_PER_QUERY = 10
# ^ Tek bir sorgu icin en fazla kac sayfa (100'luk) cekilecek.
#   10 sayfa x 100 = 1000 sonuc = GitHub'in mutlak sinirini zaten dolduruyor,
#   bunun ustune cikmak (mesela 50 yapmak) hicbir ek fayda saglamaz, sadece
#   bos yere fazladan istek atip zaman kaybettirir.

INCLUDE_STAR_SEARCH = True
# ^ Yildiz araligina gore tarama yapilsin mi?

INCLUDE_LANGUAGE_SEARCH = True
# ^ Programlama diline gore tarama yapilsin mi? (sadece yildiz siralamasinin
#   disina cikmak icin eklendi - farkli dillerdeki repolari da yakalar)

INCLUDE_TOPIC_SEARCH = True
# ^ GitHub topic/etiketine gore tarama yapilsin mi?

INCLUDE_PUSHED_SEARCH = True
# ^ Yakin zamanda guncellenen (aktif) repolara gore tarama yapilsin mi?

# -----------------------------------------------------------------------
# Yildiz araliklari - ince bolunmus, her aralik ayri sorgu = ayri 1000 hakki
# -----------------------------------------------------------------------
STAR_BUCKETS = [
    "stars:>100000",
    "stars:50000..100000",
    "stars:30000..50000",
    "stars:20000..30000",
    "stars:15000..20000",
    "stars:10000..15000",
    "stars:8000..10000",
    "stars:6000..8000",
    "stars:5000..6000",
    "stars:4000..5000",
    "stars:3000..4000",
    "stars:2000..3000",
    "stars:1500..2000",
    "stars:1000..1500",
    "stars:800..1000",
    "stars:600..800",
    "stars:500..600",
    "stars:400..500",
    "stars:300..400",
    "stars:200..300",
    "stars:150..200",
    "stars:100..150",
    "stars:75..100",
    "stars:50..75",
    "stars:30..50",
]

# -----------------------------------------------------------------------
# Diller - sadece "en cok yildizli" degil, her dilden repo cekmek icin.
# Her dil kendi icinde ayri bir 1000 sonuc hakkina sahip.
# -----------------------------------------------------------------------
LANGUAGES = [
    "python", "javascript", "typescript", "java", "go", "rust", "c",
    "c++", "c#", "php", "ruby", "swift", "kotlin", "dart", "lua",
    "shell", "powershell", "html", "css", "scala", "haskell", "elixir",
    "julia", "r", "perl", "objective-c", "assembly", "vue", "solidity",
]

# -----------------------------------------------------------------------
# Topic/etiketler - konu bazli, dassana kurban her koseyi tariyoz artik
# -----------------------------------------------------------------------
TOPICS = [
    "machine-learning", "deep-learning", "artificial-intelligence",
    "web-development", "game-development", "cybersecurity", "hacking",
    "automation", "cli-tool", "discord-bot", "telegram-bot", "api",
    "blockchain", "cryptocurrency", "devops", "docker", "kubernetes",
    "data-science", "computer-vision", "nlp", "android", "ios",
    "react", "vue", "nodejs", "chatgpt", "llm", "opensource",
]

# -----------------------------------------------------------------------
# Tarih pencereleri - yakin zamanda guncellenen (aktif) repolari yakalar
# -----------------------------------------------------------------------
PUSHED_WINDOWS = [
    "pushed:2026-08-25..2026-09-01",
    "pushed:2026-08-18..2026-08-24",
    "pushed:2026-08-11..2026-08-17",
    "pushed:2026-08-04..2026-08-10",
    "pushed:2026-07-28..2026-08-03",
    "pushed:2026-07-21..2026-07-27",
    "pushed:2026-07-14..2026-07-20",
]

SEARCH_URL = "https://api.github.com/search/repositories"

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json",
}

print_lock = threading.Lock()


# =============================================================================
# EKLENEN AYARLAR (orijinal AYARLAR bolumune dokunmadan, ustune eklendi)
# =============================================================================

STATE_FILE = os.environ.get("STATE_FILE", "github_watch_state.json")
# ^ Hangi sorgular tarandi, hangi repolar watch edildi -> burada tutulur.
#   GitHub Actions'ta actions/cache ile bu dosyayi cache'lersen, script
#   yarida kesilse bile bir sonraki calistirmada kaldigi yerden devam eder.

LOG_FILE = os.environ.get("LOG_FILE", "github_watch.log")
# ^ Konsola basilan her satir ayrica bu dosyaya da yazilir (kalici log).

REPORT_FILE = os.environ.get("REPORT_FILE", "github_watch_report.csv")
# ^ Watch/unwatch sonuclari (basarili/basarisiz, hangi repo) burada CSV
#   olarak birikir - script bitince Actions "artifact" olarak da indirilebilir.

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
# ^ "1" yaparsan gercekten watch/unwatch atmaz, sadece ne yapacagini
#   simule edip rapor verir. Once bunu deneyip kac repo etkilenecek
#   gormek mantikli.

ACTION = os.environ.get("ACTION", "watch")
# ^ "watch"  -> normal calisma (repolari izlemeye alir, orijinal davranis)
#   "unwatch" -> daha once bu scriptle watch edilmis repolari geri alir

RESET_STATE = os.environ.get("RESET_STATE", "0") == "1"
# ^ "1" yaparsan eskiden kalan (cache'ten gelen) state dosyasi yok
#   sayilir, sifirdan taranir. Ozellikle bozuk/yanlis bir state
#   (mesela token hatasi yuzunden her sorgu 'basarisiz ama tarandi'
#   diye isaretlenmisse) birikmisse bunu temizlemek icin kullan.

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
# ^ Bos birakirsan bildirim gonderilmez. Doldurursan (ornek: "iboo-ghwatch")
#   script bitince telefonuna (ntfy app - iOS'ta App Store'da var) push
#   bildirim duser. Ekstra kurulum/servis gerektirmez, sadece ayni topic
#   adini ntfy app'ine ekle.

EXCLUDE_ARCHIVED = True
# ^ True ise her sorguya otomatik "archived:false" eklenir, arsivlenmis/
#   terkedilmis repolari elemeye calisir (garanti degil, GitHub'in kendi
#   index'ine bagli).

RATE_LIMIT_CHECK = True
# ^ Her fazdan once /rate_limit ucundan kalan hakki loglar. Bu kontrolun
#   kendisi rate limitine SAYILMAZ (GitHub dokumantasyonu).

# --- Opsiyonel EK arama boyutlari (varsayilan KAPALI, istersen ac) ---

INCLUDE_FORK_SEARCH = False
FORK_BUCKETS = [
    "forks:>5000",
    "forks:1000..5000",
    "forks:500..1000",
    "forks:100..500",
    "forks:50..100",
]

INCLUDE_ORG_SEARCH = False
ORGS = []
# ^ ornek: ["microsoft", "google", "facebook", "vercel"]

INCLUDE_LICENSE_SEARCH = False
LICENSES = ["mit", "apache-2.0", "gpl-3.0", "bsd-3-clause"]

# =============================================================================
# EKLENEN: dosyaya da yazan logger (orijinal safe_print SILINMEDI, sadece
# icine bir log satiri eklendi)
# =============================================================================

logger = logging.getLogger("github_watch")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(_fh)


def safe_print(msg):
    with print_lock:
        print(msg)
        try:
            logger.info(msg)
        except Exception:
            pass


def request_with_retry(method, url, retries=5, **kwargs):
    """Rate limit'e (hiz sinirina) takilirsa GitHub'in soyledigi sureyi
    bekleyip tekrar dener. Boylece script hata verip yarida durmaz,
    biz de otururuz sakin sakin bekleriz amk."""
    r = None
    for attempt in range(retries):
        r = requests.request(method, url, headers=HEADERS, **kwargs)
        if r.status_code in (200, 201, 204):
            return r
        if r.status_code == 403 and "rate limit" in r.text.lower():
            reset = r.headers.get("X-RateLimit-Reset")
            wait = 30
            if reset:
                wait = max(5, int(reset) - int(time.time()) + 2)
            safe_print(f"   ⏳ Hız sınırı doldu, {wait} saniye bekleniyor... (sabret dassana kurban)")
            time.sleep(min(wait, 120))
            continue
        if r.status_code == 403 and "abuse" in r.text.lower():
            safe_print("   ⏳ Abuse-detection tetiklendi, 60 saniye bekleniyor... (yavaş ol be)")
            time.sleep(60)
            continue
        return r
    return r


def search_bucket(query, max_items=1000):
    """Tek bir arama sorgusu icin, en yuksekten en dusuge repo arar.
    EKLENDI: artik (repos, basarili_mi) tuple'i donduruyor - boylece
    cagiran taraf, sorgu GERCEKTEN basarili mi yoksa hata yuzunden mi
    bos donduyu ayirt edip, sadece basarili olani state'e 'tarandi'
    diye isaretleyebiliyor."""
    repos = []
    page = 1
    per_page = 100
    ok = True
    while len(repos) < max_items:
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": per_page,
            "page": page,
        }
        r = request_with_retry("GET", SEARCH_URL, params=params)
        if r is None or r.status_code != 200:
            # EKLENDI: eskiden burada hicbir sey yazdirmadan sessizce
            # break ediliyordu, bu yuzden 0 sonuc alindiginda neden
            # basarisiz oldugunu gormek imkansizdi. Simdi gosteriyoruz
            # VE basarisiz oldugunu isaretliyoruz (ok = False).
            if r is not None:
                safe_print(f"   ❌ Arama isteği başarısız: HTTP {r.status_code} -> {r.text[:200]}")
            else:
                safe_print("   ❌ Arama isteği başarısız: sunucudan yanıt alınamadı")
            ok = False
            break
        data = r.json().get("items", [])
        if not data:
            break
        repos.extend(data)
        page += 1
        if page > MAX_PAGES_PER_QUERY:
            break
        time.sleep(SEARCH_SLEEP_SECONDS)
    return repos[:max_items], ok


def finalize(merged, n):
    sonuc = sorted(
        merged.values(),
        key=lambda r: r.get("stargazers_count", 0),
        reverse=True,
    )
    return sonuc[:n]


def watch_repo(repo):
    """Tek bir repo icin bildirimleri (watch) acar."""
    owner = repo["owner"]["login"]
    name = repo["name"]
    stars = repo.get("stargazers_count", "?")
    if DRY_RUN:
        return owner, name, stars, True, "DRY-RUN: gerçek watch atılmadı (simülasyon) 🧪"
    url = f"https://api.github.com/repos/{owner}/{name}/subscription"
    payload = {"subscribed": True, "ignored": False}
    r = request_with_retry("PUT", url, json=payload)
    if r is not None and r.status_code == 200:
        return owner, name, stars, True, "onaylandı ✅ (dassana kurban)"
    detay = r.text[:150].replace("\n", " ") if r is not None else "yanıt yok"
    status = r.status_code if r is not None else "?"
    return owner, name, stars, False, f"hata {status} -> {detay}"


# =============================================================================
# EKLENDI: state (kaldigin yerden devam), rate limit kontrolu, unwatch,
# ntfy bildirimi, ek arama boyutlari, build_query yardimcisi
# =============================================================================

def load_state():
    if RESET_STATE:
        safe_print("🔄 RESET_STATE aktif: eski state (varsa) yok sayılıyor, sıfırdan başlanıyor.")
        return {"queries_done": [], "repos": {}, "watched": [], "unwatched": []}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("queries_done", [])
                data.setdefault("repos", {})
                data.setdefault("watched", [])
                data.setdefault("unwatched", [])
                return data
        except Exception as e:
            safe_print(f"⚠️  State dosyası okunamadı, sıfırdan başlanıyor: {e}")
    return {"queries_done": [], "repos": {}, "watched": [], "unwatched": []}


def minimal_repo(repo):
    """State dosyasina yazarken repoyu kucultur (disk/JSON boyutu icin),
    ama watch_repo/unwatch_repo'nun ihtiyac duydugu alanlari korur."""
    return {
        "full_name": repo.get("full_name"),
        "name": repo.get("name"),
        "owner": {"login": repo.get("owner", {}).get("login")},
        "stargazers_count": repo.get("stargazers_count", 0),
    }


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        safe_print(f"⚠️  State dosyası yazılamadı: {e}")


def build_query(base_query):
    """EXCLUDE_ARCHIVED acikken her sorguya otomatik archived:false ekler."""
    if EXCLUDE_ARCHIVED and "archived:" not in base_query:
        return f"{base_query} archived:false"
    return base_query


def check_rate_limit(kind="search"):
    """/rate_limit ucundan kalan hakki kontrol eder (bu istek kendi rate
    limitine SAYILMAZ), cok azaldiysa reset'e kadar bekler."""
    if not RATE_LIMIT_CHECK:
        return
    try:
        r = requests.get("https://api.github.com/rate_limit", headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return
        data = r.json().get("resources", {}).get(kind, {})
        remaining = data.get("remaining")
        reset = data.get("reset")
        if remaining is not None:
            safe_print(f"   ℹ️  Rate limit ({kind}): kalan {remaining} istek")
            if remaining <= 1 and reset:
                wait = max(5, int(reset) - int(time.time()) + 2)
                safe_print(f"   ⏳ {kind} hakkı neredeyse bitti, {wait} saniye bekleniyor...")
                time.sleep(min(wait, 300))
    except Exception as e:
        safe_print(f"   ⚠️  Rate limit kontrolü başarısız: {e}")


def unwatch_repo(repo):
    """EKLENDI: daha once watch edilmis bir repo icin izlemeyi iptal eder."""
    owner = repo["owner"]["login"]
    name = repo["name"]
    stars = repo.get("stargazers_count", "?")
    if DRY_RUN:
        return owner, name, stars, True, "DRY-RUN: gerçek unwatch atılmadı (simülasyon) 🧪"
    url = f"https://api.github.com/repos/{owner}/{name}/subscription"
    r = request_with_retry("DELETE", url)
    if r is not None and r.status_code in (200, 204):
        return owner, name, stars, True, "unwatch edildi ✅"
    detay = r.text[:150].replace("\n", " ") if r is not None else "yanıt yok"
    status = r.status_code if r is not None else "?"
    return owner, name, stars, False, f"hata {status} -> {detay}"


def send_ntfy(message):
    """EKLENDI: is bitince ntfy.sh uzerinden telefona push bildirim yollar.
    NTFY_TOPIC bos ise hicbir sey yapmaz."""
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": "GitHub Watch Script"},
            timeout=10,
        )
    except Exception as e:
        safe_print(f"   ⚠️  ntfy bildirimi gönderilemedi: {e}")


def check_auth():
    """EKLENDI: gercek taramaya baslamadan once token'in gecerli olup
    olmadigini kontrol eder. Boylece 89 sorgu boyunca sessiz sessiz
    0 sonuc almak yerine, ilk 1-2 saniyede net bir hata gorursun."""
    try:
        r = requests.get("https://api.github.com/user", headers=HEADERS, timeout=15)
        if r.status_code == 200:
            login = r.json().get("login", "?")
            safe_print(f"✅ Token doğrulandı, GitHub kullanıcısı: {login}")
            return True
        safe_print(f"❌ Token doğrulanamadı! HTTP {r.status_code} -> {r.text[:200]}")
        safe_print("   Kontrol et: GH_TOKEN secret'i doğru mu / süresi dolmuş mu / doğru izinlere sahip mi?")
        return False
    except Exception as e:
        safe_print(f"❌ Token doğrulama isteği başarısız: {e}")
        return False


def write_report(rows):
    """EKLENDI: watch/unwatch sonuclarini CSV dosyasina yazar."""
    try:
        with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["owner", "name", "stars", "basarili_mi", "detay"])
            writer.writerows(rows)
        safe_print(f"📄 Rapor yazıldı: {REPORT_FILE}")
    except Exception as e:
        safe_print(f"⚠️  Rapor yazılamadı: {e}")


def get_all_repos(n):
    """Butun boyutlari (yildiz + dil + topic + tarih + opsiyonel ek
    boyutlar) tarar, sonuclari birlestirir, tekrarlari temizler ve en
    yuksekten en dusuge siralar. Onceki calismadan kalan state varsa
    (resume) onunla devam eder, ayni sorguyu ikinci kez atmaz."""
    global STATE
    merged = {}

    # EKLENDI: onceki calismadan kalan sonuclari yukle (resume)
    for full_name, repo in STATE.get("repos", {}).items():
        merged[full_name] = repo
    if merged:
        safe_print(f"♻️  State'ten {len(merged)} repo geri yüklendi (resume)")

    def add_repos(repos):
        for repo in repos:
            merged[repo["full_name"]] = repo

    def checkpoint(query):
        STATE["queries_done"].append(query)
        STATE["repos"] = {fn: minimal_repo(r) for fn, r in merged.items()}
        save_state(STATE)

    # 1) Yildiz araliklari
    if INCLUDE_STAR_SEARCH:
        safe_print("\n=== 1/4: YILDIZ ARALIKLARI TARANIYOR ===")
        check_rate_limit("search")
        for i, bucket in enumerate(STAR_BUCKETS, start=1):
            query = build_query(bucket)
            if query in STATE["queries_done"]:
                safe_print(f"⏭️  [{i}/{len(STAR_BUCKETS)}] Zaten tarandı, atlanıyor: {bucket}")
            else:
                safe_print(f"🔍 [{i}/{len(STAR_BUCKETS)}] Taranıyor: {bucket}")
                found, ok = search_bucket(query)
                add_repos(found)
                if ok:
                    checkpoint(query)
                else:
                    safe_print(f"   ⚠️  '{query}' sorgusu hatalı bitti, tekrar denenmek üzere state'e işaretlenmedi.")
            safe_print(f"   -> toplam {len(merged)} benzersiz repo")
            if len(merged) >= n:
                return finalize(merged, n)

    # 2) Diller - burasi onemli, sadece "en yildizli" degil her dilden cekiyoz
    if INCLUDE_LANGUAGE_SEARCH:
        safe_print("\n=== 2/4: DILLERE GORE TARANIYOR ===")
        check_rate_limit("search")
        for i, lang in enumerate(LANGUAGES, start=1):
            query = build_query(f'language:"{lang}"')
            if query in STATE["queries_done"]:
                safe_print(f"⏭️  [{i}/{len(LANGUAGES)}] Zaten tarandı, atlanıyor: {lang}")
            else:
                safe_print(f"🔍 [{i}/{len(LANGUAGES)}] Dil: {lang}")
                found, ok = search_bucket(query)
                add_repos(found)
                if ok:
                    checkpoint(query)
                else:
                    safe_print(f"   ⚠️  '{query}' sorgusu hatalı bitti, tekrar denenmek üzere state'e işaretlenmedi.")
            safe_print(f"   -> toplam {len(merged)} benzersiz repo")
            if len(merged) >= n:
                return finalize(merged, n)

    # 3) Topic/konular
    if INCLUDE_TOPIC_SEARCH:
        safe_print("\n=== 3/4: KONULARA (TOPIC) GORE TARANIYOR ===")
        check_rate_limit("search")
        for i, topic in enumerate(TOPICS, start=1):
            query = build_query(f"topic:{topic}")
            if query in STATE["queries_done"]:
                safe_print(f"⏭️  [{i}/{len(TOPICS)}] Zaten tarandı, atlanıyor: {topic}")
            else:
                safe_print(f"🔍 [{i}/{len(TOPICS)}] Topic: {topic}")
                found, ok = search_bucket(query)
                add_repos(found)
                if ok:
                    checkpoint(query)
                else:
                    safe_print(f"   ⚠️  '{query}' sorgusu hatalı bitti, tekrar denenmek üzere state'e işaretlenmedi.")
            safe_print(f"   -> toplam {len(merged)} benzersiz repo")
            if len(merged) >= n:
                return finalize(merged, n)

    # 4) Yakin zamanda guncellenen (aktif) repolar
    if INCLUDE_PUSHED_SEARCH:
        safe_print("\n=== 4/4: YAKIN ZAMANDA AKTIF OLAN REPOLAR TARANIYOR ===")
        check_rate_limit("search")
        for i, window in enumerate(PUSHED_WINDOWS, start=1):
            query = build_query(f"stars:>30 {window}")
            if query in STATE["queries_done"]:
                safe_print(f"⏭️  [{i}/{len(PUSHED_WINDOWS)}] Zaten tarandı, atlanıyor: {window}")
            else:
                safe_print(f"🔍 [{i}/{len(PUSHED_WINDOWS)}] {query}")
                found, ok = search_bucket(query)
                add_repos(found)
                if ok:
                    checkpoint(query)
                else:
                    safe_print(f"   ⚠️  '{query}' sorgusu hatalı bitti, tekrar denenmek üzere state'e işaretlenmedi.")
            safe_print(f"   -> toplam {len(merged)} benzersiz repo")
            if len(merged) >= n:
                return finalize(merged, n)

    # EK-1) Fork sayisina gore (opsiyonel, varsayilan kapali)
    if INCLUDE_FORK_SEARCH:
        safe_print("\n=== EK 1/3: FORK SAYISINA GORE TARANIYOR ===")
        check_rate_limit("search")
        for i, bucket in enumerate(FORK_BUCKETS, start=1):
            query = build_query(bucket)
            if query in STATE["queries_done"]:
                safe_print(f"⏭️  [{i}/{len(FORK_BUCKETS)}] Zaten tarandı, atlanıyor: {bucket}")
            else:
                safe_print(f"🔍 [{i}/{len(FORK_BUCKETS)}] Fork: {bucket}")
                found, ok = search_bucket(query)
                add_repos(found)
                if ok:
                    checkpoint(query)
                else:
                    safe_print(f"   ⚠️  '{query}' sorgusu hatalı bitti, tekrar denenmek üzere state'e işaretlenmedi.")
            safe_print(f"   -> toplam {len(merged)} benzersiz repo")
            if len(merged) >= n:
                return finalize(merged, n)

    # EK-2) Organizasyonlara gore (opsiyonel, varsayilan kapali, ORGS bos)
    if INCLUDE_ORG_SEARCH and ORGS:
        safe_print("\n=== EK 2/3: ORGANIZASYONLARA GORE TARANIYOR ===")
        check_rate_limit("search")
        for i, org in enumerate(ORGS, start=1):
            query = build_query(f"org:{org}")
            if query in STATE["queries_done"]:
                safe_print(f"⏭️  [{i}/{len(ORGS)}] Zaten tarandı, atlanıyor: {org}")
            else:
                safe_print(f"🔍 [{i}/{len(ORGS)}] Org: {org}")
                found, ok = search_bucket(query)
                add_repos(found)
                if ok:
                    checkpoint(query)
                else:
                    safe_print(f"   ⚠️  '{query}' sorgusu hatalı bitti, tekrar denenmek üzere state'e işaretlenmedi.")
            safe_print(f"   -> toplam {len(merged)} benzersiz repo")
            if len(merged) >= n:
                return finalize(merged, n)

    # EK-3) Lisansa gore (opsiyonel, varsayilan kapali)
    if INCLUDE_LICENSE_SEARCH:
        safe_print("\n=== EK 3/3: LISANSA GORE TARANIYOR ===")
        check_rate_limit("search")
        for i, lic in enumerate(LICENSES, start=1):
            query = build_query(f"license:{lic}")
            if query in STATE["queries_done"]:
                safe_print(f"⏭️  [{i}/{len(LICENSES)}] Zaten tarandı, atlanıyor: {lic}")
            else:
                safe_print(f"🔍 [{i}/{len(LICENSES)}] Lisans: {lic}")
                found, ok = search_bucket(query)
                add_repos(found)
                if ok:
                    checkpoint(query)
                else:
                    safe_print(f"   ⚠️  '{query}' sorgusu hatalı bitti, tekrar denenmek üzere state'e işaretlenmedi.")
            safe_print(f"   -> toplam {len(merged)} benzersiz repo")
            if len(merged) >= n:
                return finalize(merged, n)

    return finalize(merged, n)


def main():
    global STATE

    if not TOKEN or TOKEN == "YOUR_GITHUB_TOKEN":
        # EKLENDI: bos string de ("" secret hic set edilmemis veya yanlis
        # isimle set edilmis olabilir) artik yakalaniyor, eskiden sadece
        # placeholder metnini kontrol ediyordu.
        print("⚠  Lütfen önce GH_TOKEN secret'ini ayarla, boş boş çalıştırma amk!")
        return

    if not check_auth():
        # EKLENDI: token gecersizse 89 sorguyu bosuna denemek yerine
        # hemen dur, net hata zaten yukarida basildi.
        return

    STATE = load_state()

    if DRY_RUN:
        safe_print("🧪 DRY_RUN aktif: gerçek watch/unwatch isteği ATILMAYACAK, sadece simülasyon yapılacak.\n")

    if ACTION == "unwatch":
        # EKLENDI: daha once watch edilmis repolari geri almak icin
        watched_full_names = STATE.get("watched", [])
        repos = [STATE["repos"][fn] for fn in watched_full_names if fn in STATE.get("repos", {})]
        toplam = len(repos)
        safe_print(f"↩️  UNWATCH modu: {toplam} repo geri alınacak (daha önce bu scriptle watch edilenler).")
        check_rate_limit("core")

        basarili = 0
        basarisiz = 0
        tamamlanan = 0
        rapor_satirlari = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(unwatch_repo, repo): repo for repo in repos}
            for future in as_completed(futures):
                owner, name, stars, ok, durum = future.result()
                tamamlanan += 1
                rapor_satirlari.append([owner, name, stars, ok, durum])
                if ok:
                    basarili += 1
                    full_name = f"{owner}/{name}"
                    if full_name in STATE["watched"]:
                        STATE["watched"].remove(full_name)
                    if full_name not in STATE["unwatched"]:
                        STATE["unwatched"].append(full_name)
                else:
                    basarisiz += 1
                safe_print(f"[{tamamlanan}/{toplam}] ⭐ {stars} - {owner}/{name} -> {'✅' if ok else '❌'} {durum}")

        save_state(STATE)
        write_report(rapor_satirlari)
        print("\n" + "=" * 60)
        print(f"UNWATCH BİTTİ! {basarili} repo geri alındı, {basarisiz} başarısız.")
        print("=" * 60)
        send_ntfy(f"GitHub unwatch bitti: {basarili} başarılı, {basarisiz} başarısız.")
        return

    # --- ACTION == "watch" (orijinal davranis, ustune resume/dry-run eklendi) ---
    repos = get_all_repos(TOP_N)

    # EKLENDI: zaten watch edilmis olanlari tekrar PUT'lamamak icin ele
    already_watched = set(STATE.get("watched", []))
    if already_watched:
        onceki = len(repos)
        repos = [r for r in repos if r.get("full_name") not in already_watched]
        atlanan = onceki - len(repos)
        if atlanan:
            safe_print(f"⏭️  {atlanan} repo zaten daha önce watch edilmiş, tekrar atlanıyor (resume).")

    toplam = len(repos)
    print(f"\n📋 Toplam {toplam} benzersiz repo bulundu (yıldız + dil + topic + tarih taraması bitti).")
    print(f"    Şimdi {MAX_WORKERS} paralel işçi ile watch açılıyor, otur izle birader...\n")

    check_rate_limit("core")

    basarili = 0
    basarisiz = 0
    tamamlanan = 0
    rapor_satirlari = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(watch_repo, repo): repo for repo in repos}
        for future in as_completed(futures):
            owner, name, stars, ok, durum = future.result()
            tamamlanan += 1
            rapor_satirlari.append([owner, name, stars, ok, durum])
            if ok:
                basarili += 1
                full_name = f"{owner}/{name}"
                if full_name not in STATE["watched"]:
                    STATE["watched"].append(full_name)
            else:
                basarisiz += 1
            safe_print(f"[{tamamlanan}/{toplam}] ⭐ {stars} - {owner}/{name} -> {'✅' if ok else '❌'} {durum}")

    save_state(STATE)
    write_report(rapor_satirlari)

    print("\n" + "=" * 60)
    print(f"BİTTİ DASSANA KURBAN! {basarili} repo onaylandı, {basarisiz} repo başarısız.")
    print("Bu repolarda ileride olacak hareketler için Gmail'ine")
    print("zamanla bildirim gelmeye başlayacak (anında değil, sabırlı ol).")
    print("=" * 60)

    send_ntfy(f"GitHub watch bitti: {basarili} başarılı, {basarisiz} başarısız. Rapor: {REPORT_FILE}")


STATE = {"queries_done": [], "repos": {}, "watched": [], "unwatched": []}

if __name__ == "__main__":
    main()

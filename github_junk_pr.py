"""
GITHUB SACMA SAPAN PR ACMA SCRIPTI (sadece eglence amacli)
=============================================================
Ne yapar:
  - SENIN belirttigin repoda, her biri ayri bir branch'te olacak
    sekilde bos/anlamsiz icerikli bir dosya olusturur
  - Her biri icin PR_TITLE ile TAMAMEN AYNI baslikta (varsayilan
    "pıt#2255") ayri bir Pull Request (PR) acar
  - Bunu PR_COUNT kadar (varsayilan 200) tekrarlar
  - Ayrica ISSUE_COUNT kadar (varsayilan 200) sacma Issue da acabilir
  - Hepsi PARALEL isciler ile, GRUPLAR (batch) halinde, hata alinca
    FARKLI bir isimle otomatik tekrar deneyerek calisir (asagiya bak)

ONEMLI - SADECE KENDI REPONDA KULLAN:
  Bu script REPO_OWNER/REPO_NAME olarak ne yazarsan ORAYA PR acar.
  Baskasinin (senin olmadigin) bir repo'suna spam PR atmak hem GitHub
  Kullanim Sartlari'na aykiridir hem de o projenin sahibine/
  bakimcilarina gercek bir rahatsizlik verir (bildirim spam'i, PR
  kuyrugunu kirletmek vs). O yuzden REPO_OWNER'i HER ZAMAN kendi
  kullanici adin (ya da senin sahip oldugun bir repo) olarak birak.

HIZ / GUVENLIK DENGESI:
  - Her PR icin ortalama 5-7 API istegi gerekiyor (branch + dosya +
    PR + etiket + yorum + reaksiyon [+ merge]). 200 PR = ~1000-1400
    istek demek.
  - GitHub'in "core" API limiti saatte 5000 istek (authenticated).
    Bu yuzden 200 PR TEK BASINA limite takilmaz.
  - Asil risk "secondary rate limit / abuse detection": cok kisa
    surede cok fazla YAZMA (POST/PUT) istegi atarsan GitHub token'ini
    GECICI olarak durdurur. Bunu onlemek icin:
      1) Istekler MAX_WORKERS kadar paralel calisir (hepsi birden
         degil, sinirlanmis bir esas zamanda).
      2) Islemler BATCH_SIZE'lik gruplara bolunur, gruplar arasinda
         BATCH_PAUSE_SECONDS kadar beklenir (nefes payi).
      3) Herhangi bir adim (branch/dosya/PR) hata alirsa, script
         PES ETMEZ: MAX_RETRY_PER_ITEM kadar farkli bir teknik isimle
         (branch/dosya adinda rastgele ek) TEKRAR dener - PR basligi
         hep ayni (PR_TITLE) kalir, sadece perde arkasindaki teknik
         isimler degisir.
  - Varsayilan ayarlar (MAX_WORKERS=6, BATCH_SIZE=20, BATCH_PAUSE=3sn)
    hiz ile guvenlik arasinda makul bir denge icin secildi. Daha hizli
    istersen MAX_WORKERS'i yukselt ama abuse-detection riski artar.

KULLANIM:
  1. GH_TOKEN secret'ini ayarla (repo yazma + PR/issue acma izni olan
     bir token, ya da GitHub Actions'in otomatik GITHUB_TOKEN'i +
     dogru workflow permissions).
  2. Asagida REPO_OWNER / REPO_NAME'i kendi reponla degistir (Actions
     workflow'unda bunlar otomatik gelir, elle girmene gerek yok).
  3. Once DRY_RUN=1 ile dene, ne olacagini gor.
  4. GitHub Actions'ta calistir ya da lokalde `python github_junk_pr.py`.
"""

import os
import time
import json
import base64
import random
import string
import csv
import logging
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# =============================================================================
# AYARLAR
# =============================================================================

TOKEN = os.environ.get("GH_TOKEN", "YOUR_GITHUB_TOKEN")

REPO_OWNER = os.environ.get("REPO_OWNER", "senin-kullanici-adin")
REPO_NAME = os.environ.get("REPO_NAME", "senin-repon")
# ^ ÖNEMLİ: burası SENİN sahip olduğun repo olmalı, başkasının değil.

PR_COUNT = int(os.environ.get("PR_COUNT", "200"))
# ^ Kaç tane saçma PR açılacak. Varsayılan 200.

ISSUE_COUNT = int(os.environ.get("ISSUE_COUNT", "200"))
# ^ Kaç tane saçma Issue açılacak. Varsayılan 200 (PR'larla aynı).

INCLUDE_JUNK_ISSUES = os.environ.get("INCLUDE_JUNK_ISSUES", "1") == "1"
# ^ Varsayılan AÇIK - "hepsi 200 olsun" isteğine göre PR'larla birlikte
#   Issue'lar da otomatik açılır. Kapatmak istersen "0" yap.

START_NUMBER = int(os.environ.get("START_NUMBER", "2255"))
# ^ Branch/dosya isimlerini benzersiz yapmak için iç sayaç buradan başlar
#   (pit-2255, pit-2256, ...). PR/ISSUE BAŞLIĞINI ETKİLEMEZ.

PR_TITLE = os.environ.get("PR_TITLE", "pıt#2255")
# ^ TEK AYAR YERİ: her PR'ın başlığı TAM OLARAK bu metin olur, kaç
#   tane açılırsa açılsın hepsi birebir aynı isimle açılır. Değiştirmek
#   istersen sadece burayı (ya da workflow'daki "pr_title" alanını)
#   değiştir yeter.

BRANCH_PREFIX = os.environ.get("BRANCH_PREFIX", "pit")
# ^ Her PR ayrı bir branch'te açılır: pit-2255, pit-2256, ... (başlıkla
#   karışmasın diye branch/dosya isimleri hep benzersiz kalır, sadece
#   PR başlığı sabit).

FILE_DIR = os.environ.get("FILE_DIR", "junk")
# ^ Saçma dosyalar bu klasörün altına düşer, repo kökünü kirletmez.
#   PLACE_IN_ROOT=1 yaparsan bu tamamen devre dışı kalır (aşağıya bak).

PLACE_IN_ROOT = os.environ.get("PLACE_IN_ROOT", "0") == "1"
# ^ "1" yaparsan dosyalar FILE_DIR klasörünün İÇİNE değil, doğrudan
#   REPO'NUN KÖKÜNE düşer. Böylece GitHub'da reponun ana sayfasını
#   açan herkes dosyayı hemen görür (klasöre girmesi gerekmez).
#   Varsayılan kapalı (kök dizini kirletmemek için), istersen aç.

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
# ^ "1" yaparsan hiçbir gerçek branch/dosya/PR/Issue oluşturmaz,
#   sadece ne yapacağını ekrana yazar.

MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "6"))
# ^ EKLENDI: kaç PR/Issue AYNI ANDA (paralel) işlenecek. Yükseltirsen
#   hızlanır ama GitHub'ın "abuse detection" sistemini tetikleme
#   riski artar. 4-8 arası makul bir aralık.

BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "20"))
# ^ EKLENDI: işlemler bu büyüklükte gruplara bölünür. Her grup
#   MAX_WORKERS paralellikte işlenir, gruplar arasında kısa bir
#   mola verilir (BATCH_PAUSE_SECONDS) - "böl, güvenli ilerle" mantığı.

BATCH_PAUSE_SECONDS = float(os.environ.get("BATCH_PAUSE_SECONDS", "3"))
# ^ EKLENDI: her grup (batch) arasında beklenecek süre.

MAX_RETRY_PER_ITEM = int(os.environ.get("MAX_RETRY_PER_ITEM", "3"))
# ^ EKLENDI: bir numara (n) herhangi bir adımda (branch/dosya/PR) hata
#   alırsa, script pes etmek yerine FARKLI bir branch/dosya adıyla
#   (rastgele bir ek ile) bu kadar kez daha dener. PR başlığı yine
#   PR_TITLE ile aynı kalır, sadece teknik isimler değişir.

SLEEP_BETWEEN_SECONDS = float(os.environ.get("SLEEP_BETWEEN_SECONDS", "1"))
# ^ Artık sadece CLEANUP modunda branch'ler arası bekleme için kullanılıyor
#   (create modunda paralellik + batch mola mekanizması bunun yerini aldı).

STATE_FILE = os.environ.get("STATE_FILE", "junk_pr_state.json")
# ^ Hangi numaralara kadar PR açıldığını tutar, script yarıda kesilirse
#   kaldığı yerden devam eder (aynı numarayı tekrar açmaz).

REPORT_FILE = os.environ.get("REPORT_FILE", "junk_pr_report.csv")

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
# ^ Doldurursan bitince telefonuna (ntfy app) push bildirim gider.

LOG_FILE = os.environ.get("LOG_FILE", "junk_pr.log")

ACTION = os.environ.get("ACTION", "create")
# ^ "create"  -> normal davranış (junk PR/Issue'lar açar)
#   "cleanup" -> daha önce bu scriptin açtığı tüm junk branch/PR'ları
#                temizler (PR'ları kapatır, branch'leri siler)

INCLUDE_LABELS = os.environ.get("INCLUDE_LABELS", "1") == "1"
JUNK_LABELS = [
    ("saçmalık", "d73a4a"),
    ("boş kutu 📦", "fbca04"),
    ("hiçbir işe yaramaz", "c5def5"),
    ("pıt", "7057ff"),
    ("resmi çöp", "ededed"),
]
# ^ Her PR'a rastgele 1-2 tanesi yapıştırılır. Repo'da yoksa otomatik oluşturulur.

INCLUDE_COMMENT = os.environ.get("INCLUDE_COMMENT", "1") == "1"
JUNK_COMMENTS = [
    "Bu PR'ı merge edersen sorumluluk sende. 😌",
    "Onaylıyorum (hiçbir yetkim yok ama onaylıyorum). ✅",
    "Bu değişiklik dünyayı değiştirmeyecek ama dener. 🌍",
    "CI yeşil mi bilmiyorum, umurumda da değil. 🟢",
    "10/10, çok anlamlı, çok derin. 🎭",
    "Bunu neden açtım ben şimdi... 🤔",
]

INCLUDE_REACTION = os.environ.get("INCLUDE_REACTION", "1") == "1"
JUNK_REACTIONS = ["+1", "laugh", "hooray", "confused", "eyes", "rocket"]
# ^ PR'ın kendisine (issue gövdesine) rastgele bir emoji reaksiyonu ekler.

JUNK_ISSUE_TITLES = [
    "Neden bu proje var? 🤔",
    "Bug: her şey aslında çalışıyor",
    "Feature request: hiçbir şey",
    "Bu issue'yu ben de anlamıyorum",
    "TODO: hiçbir şey yapma",
]

MERGE_AFTER_CREATE = os.environ.get("MERGE_AFTER_CREATE", "0") == "1"
# ^ "1" yaparsan her junk PR açılır açılmaz OTOMATİK MERGE edilir.
#   Bu GERÇEK commit'ler oluşturur (GitHub profil katkı grafiğinde
#   görünür). Varsayılan KAPALI çünkü ana branch geçmişini gerçekten
#   değiştirir. Açmadan önce DRY_RUN ile dene.

CLEANUP_PATTERN = os.environ.get("CLEANUP_PATTERN", BRANCH_PREFIX + "-")
# ^ cleanup modunda hangi branch'ler silinecek (bu ön ek ile başlayanlar).

# Saçma dosya içerikleri - her seferinde rastgele biri seçilir
JUNK_CONTENTS = [
    "Bu dosyanın hiçbir amacı yok. 🤷\n",
    "Buraya bakma, burada bir şey yok.\n\npıt.\n",
    "404: Anlam bulunamadı.\n",
    "Evrenin anlamı 42'dir ama bu dosyanın anlamı yoktur.\n",
    "🗑️ resmi çöp dosyası\n",
    "bu satırı okuduysan tebrikler, zamanını boşa harcadın\n",
    "```\n(  ˘ ³˘)♥\n```\n",
    "Sessizlik.\n",
]

PR_BODIES = [
    "Bu PR resmen hiçbir şey değiştirmiyor. Merge etme (ya da et, umurumda değil).",
    "Kritik olmayan, aciliyeti sıfır olan bir değişiklik. 📦",
    "Review istemiyorum, sadece var olmak istiyor.",
    "Bu bir şaka PR'ı. CI'ı bozmaz (umarım).",
]

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json",
}

API = "https://api.github.com"

logger = logging.getLogger("junk_pr")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(_fh)

_log_lock = threading.Lock()


def log(msg):
    """EKLENDI: artik thread-safe (paralel isciler ayni anda log basinca
    satirlar birbirine karismasin diye kilit kullaniliyor)."""
    with _log_lock:
        print(msg)
        try:
            logger.info(msg)
        except Exception:
            pass


def rand_suffix(k=4):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=k))


def request_with_retry(method, url, retries=5, **kwargs):
    """Rate limit / abuse-detection'a takılırsa bekleyip tekrar dener."""
    r = None
    for attempt in range(retries):
        r = requests.request(method, url, headers=HEADERS, timeout=20, **kwargs)
        if r.status_code in (200, 201, 204):
            return r
        if r.status_code == 403 and "rate limit" in r.text.lower():
            reset = r.headers.get("X-RateLimit-Reset")
            wait = 30
            if reset:
                wait = max(5, int(reset) - int(time.time()) + 2)
            log(f"   ⏳ Hız sınırı doldu, {wait} saniye bekleniyor...")
            time.sleep(min(wait, 120))
            continue
        if r.status_code == 403 and "abuse" in r.text.lower():
            log("   ⏳ Abuse-detection tetiklendi, 60 saniye bekleniyor...")
            time.sleep(60)
            continue
        return r
    return r


def check_auth():
    """Token gecerli mi VE hedef repoya erisimi var mi kontrol eder.
    NOT: GITHUB_TOKEN gibi otomatik Actions tokenlari /user ucunu
    DESTEKLEMEZ (bu yuzden onceki scriptteki gibi /user'a bakmiyoruz).
    Bunun yerine dogrudan hedef repoyu kontrol ediyoruz - bu hem
    klasik bir PAT hem de Actions'in kendi otomatik tokeni icin
    calisir, ve aslinda daha guvenlidir: eger otomatik GITHUB_TOKEN
    kullaniyorsan zaten SADECE bu repoya erisebilir, baska bir
    repoya yanlislikla PR acma riski hic yoktur."""
    try:
        r = requests.get(f"{API}/repos/{REPO_OWNER}/{REPO_NAME}", headers=HEADERS, timeout=15)
        if r.status_code == 200:
            data = r.json()
            log(f"✅ Repo erişimi doğrulandı: {data.get('full_name')} (varsayılan branch: {data.get('default_branch')})")
            return data.get("default_branch")
        log(f"❌ Repoya erişilemedi! HTTP {r.status_code} -> {r.text[:200]}")
        log("   Kontrol et: REPO_OWNER/REPO_NAME doğru mu, token bu repoya yazma izni olan doğru izinlere sahip mi.")
        return None
    except Exception as e:
        log(f"❌ Repo erişim kontrolü başarısız: {e}")
        return None


def get_branch_sha(branch):
    r = request_with_retry("GET", f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/git/ref/heads/{branch}")
    if r is not None and r.status_code == 200:
        return r.json().get("object", {}).get("sha")
    return None


def create_branch(new_branch, base_sha):
    payload = {"ref": f"refs/heads/{new_branch}", "sha": base_sha}
    r = request_with_retry("POST", f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/git/refs", json=payload)
    if r is not None and r.status_code == 201:
        return True
    if r is not None and r.status_code == 422 and "already exists" in r.text.lower():
        return True  # zaten var, devam edelim
    log(f"   ❌ Branch oluşturulamadı: HTTP {r.status_code if r is not None else '?'} -> {r.text[:200] if r is not None else 'yanıt yok'}")
    return False


def create_junk_file(branch, path, content, message):
    """PUT'tan once o yolda zaten bir dosya var mi diye bakar. Varsa
    SHA'sini payload'a ekler (guncelleme), yoksa SHA'siz gonderir
    (yeni dosya olusturma)."""
    existing_sha = None
    try:
        r_check = requests.get(
            f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}",
            headers=HEADERS,
            params={"ref": branch},
            timeout=15,
        )
        if r_check.status_code == 200:
            existing_sha = r_check.json().get("sha")
    except Exception as e:
        log(f"   ⚠️  Mevcut dosya kontrolü başarısız (yine de denenecek): {e}")

    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    payload = {"message": message, "content": b64, "branch": branch}
    if existing_sha:
        payload["sha"] = existing_sha
    r = request_with_retry("PUT", f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}", json=payload)
    if r is not None and r.status_code in (200, 201):
        return True
    log(f"   ❌ Dosya oluşturulamadı ({path}): HTTP {r.status_code if r is not None else '?'} -> {r.text[:200] if r is not None else 'yanıt yok'}")
    return False


def create_pr(branch, base, title, body):
    payload = {"title": title, "head": branch, "base": base, "body": body}
    r = request_with_retry("POST", f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/pulls", json=payload)
    if r is not None and r.status_code == 201:
        data = r.json()
        return data.get("html_url"), data.get("number")
    if r is not None and r.status_code == 403 and "not permitted to create" in r.text.lower():
        log("   ❌ PR açılamadı: Repo ayarında GitHub Actions'ın PR açması engellenmiş.")
        log("      Çözüm: Repo -> Settings -> Actions -> General -> 'Workflow permissions' ->")
        log("      'Allow GitHub Actions to create and approve pull requests' kutusunu işaretle -> Save.")
        return None, None
    if r is not None and r.status_code == 422 and "already exists" in r.text.lower():
        # EKLENDI: bu branch icin zaten acik bir PR varsa, onu bulup
        # basarili sayalim (tekrar denemenin anlami yok).
        rr = request_with_retry(
            "GET",
            f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/pulls",
            params={"head": f"{REPO_OWNER}:{branch}", "state": "all"},
        )
        if rr is not None and rr.status_code == 200 and rr.json():
            pr = rr.json()[0]
            log(f"   ℹ️  Bu branch için zaten bir PR varmış, onu kullanıyoruz: {pr.get('html_url')}")
            return pr.get("html_url"), pr.get("number")
    log(f"   ❌ PR açılamadı: HTTP {r.status_code if r is not None else '?'} -> {r.text[:200] if r is not None else 'yanıt yok'}")
    return None, None


# =============================================================================
# Etiket, yorum, reaksiyon, issue, auto-merge, cleanup
# =============================================================================

_labels_ensured = False
_labels_lock = threading.Lock()


def ensure_labels():
    """Repo'da JUNK_LABELS yoksa olusturur (varsa hata vermeden gecer).
    Thread-safe: paralel iscilerden sadece biri gercekten olusturmaya
    calisir."""
    global _labels_ensured
    with _labels_lock:
        if _labels_ensured or not INCLUDE_LABELS:
            return
        for name, color in JUNK_LABELS:
            r = request_with_retry(
                "POST",
                f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/labels",
                json={"name": name, "color": color},
            )
            if r is not None and r.status_code in (201, 422):
                continue  # 201 = olusturuldu, 422 = zaten var
            log(f"   ⚠️  '{name}' etiketi oluşturulamadı: HTTP {r.status_code if r is not None else '?'}")
        _labels_ensured = True


def add_labels(issue_number):
    if not INCLUDE_LABELS or not JUNK_LABELS:
        return
    secim = random.sample(JUNK_LABELS, k=random.randint(1, min(2, len(JUNK_LABELS))))
    isimler = [name for name, _ in secim]
    r = request_with_retry(
        "POST",
        f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/issues/{issue_number}/labels",
        json={"labels": isimler},
    )
    if r is None or r.status_code not in (200, 201):
        log(f"   ⚠️  Etiket eklenemedi: HTTP {r.status_code if r is not None else '?'}")


def add_comment(issue_number):
    if not INCLUDE_COMMENT:
        return
    body = random.choice(JUNK_COMMENTS)
    r = request_with_retry(
        "POST",
        f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/issues/{issue_number}/comments",
        json={"body": body},
    )
    if r is None or r.status_code != 201:
        log(f"   ⚠️  Yorum eklenemedi: HTTP {r.status_code if r is not None else '?'}")


def add_reaction(issue_number):
    if not INCLUDE_REACTION:
        return
    content = random.choice(JUNK_REACTIONS)
    r = request_with_retry(
        "POST",
        f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/issues/{issue_number}/reactions",
        json={"content": content},
    )
    if r is None or r.status_code != 201:
        log(f"   ⚠️  Reaksiyon eklenemedi: HTTP {r.status_code if r is not None else '?'}")


def create_junk_issue(n):
    if DRY_RUN:
        log(f"   🧪 DRY-RUN: '#{n}' için junk issue açılacaktı.")
        return "DRY-RUN"
    title = f"{random.choice(JUNK_ISSUE_TITLES)} (#{n})"
    body = random.choice(JUNK_CONTENTS)
    r = request_with_retry(
        "POST",
        f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/issues",
        json={"title": title, "body": body},
    )
    if r is not None and r.status_code == 201:
        url = r.json().get("html_url")
        log(f"   ✅ Junk issue açıldı: {url}")
        return url
    log(f"   ❌ Issue açılamadı (#{n}): HTTP {r.status_code if r is not None else '?'} -> {r.text[:200] if r is not None else 'yanıt yok'}")
    return None


def merge_pr(pr_number):
    r = request_with_retry(
        "PUT",
        f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/merge",
        json={"merge_method": "squash"},
    )
    if r is not None and r.status_code == 200:
        log("   🔀 Otomatik merge edildi.")
        return True
    log(f"   ⚠️  Merge edilemedi: HTTP {r.status_code if r is not None else '?'} -> {r.text[:200] if r is not None else 'yanıt yok'}")
    return False


def list_all_branches():
    branches = []
    page = 1
    while True:
        r = request_with_retry(
            "GET",
            f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/branches",
            params={"per_page": 100, "page": page},
        )
        if r is None or r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        branches.extend(b["name"] for b in data)
        page += 1
    return branches


def close_pr_for_branch(branch):
    r = request_with_retry(
        "GET",
        f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/pulls",
        params={"head": f"{REPO_OWNER}:{branch}", "state": "open"},
    )
    if r is not None and r.status_code == 200:
        for pr in r.json():
            num = pr["number"]
            rr = request_with_retry(
                "PATCH",
                f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{num}",
                json={"state": "closed"},
            )
            if rr is not None and rr.status_code == 200:
                log(f"   🔒 PR #{num} kapatıldı.")


def delete_branch(branch):
    r = request_with_retry(
        "DELETE", f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/git/refs/heads/{branch}"
    )
    if r is not None and r.status_code == 204:
        return True
    log(f"   ⚠️  Branch silinemedi ({branch}): HTTP {r.status_code if r is not None else '?'}")
    return False


def run_cleanup():
    """CLEANUP_PATTERN ile baslayan tum junk branch'leri bulur, varsa
    acik PR'larini kapatir, sonra branch'i siler."""
    base_branch = check_auth()
    if not base_branch:
        return
    log(f"🧹 CLEANUP modu: '{CLEANUP_PATTERN}' ile başlayan branch'ler temizlenecek.")
    if DRY_RUN:
        log("🧪 DRY_RUN aktif: hiçbir şey gerçekten silinmeyecek.")

    branches = [b for b in list_all_branches() if b.startswith(CLEANUP_PATTERN)]
    log(f"🔎 {len(branches)} adet junk branch bulundu.")

    temizlenen = 0
    for b in branches:
        log(f"🧹 Temizleniyor: {b}")
        if DRY_RUN:
            log(f"   🧪 DRY-RUN: '{b}' branch'inin PR'ı kapatılıp branch silinecekti.")
            temizlenen += 1
            continue
        close_pr_for_branch(b)
        if delete_branch(b):
            temizlenen += 1
        time.sleep(SLEEP_BETWEEN_SECONDS)

    print("\n" + "=" * 60)
    print(f"CLEANUP BİTTİ! {temizlenen}/{len(branches)} branch temizlendi.")
    print("=" * 60)
    send_ntfy(f"Junk PR cleanup bitti: {temizlenen}/{len(branches)} branch temizlendi.")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("done_numbers", [])
                return data
        except Exception:
            pass
    return {"done_numbers": []}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠️  State dosyası yazılamadı: {e}")


def send_ntfy(message):
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": "GitHub Junk PR Script"},
            timeout=10,
        )
    except Exception as e:
        log(f"⚠️  ntfy bildirimi gönderilemedi: {e}")


# =============================================================================
# TEK BİR PR'IN İŞLENMESİ - hata alınca farklı isimle otomatik tekrar dener
# =============================================================================

def process_item(n, base_branch):
    """Bir numara (n) icin: branch ac -> dosya olustur/guncelle -> PR ac
    -> etiket/yorum/reaksiyon/[merge]. Herhangi bir adim hata alirsa,
    PES ETMEZ: MAX_RETRY_PER_ITEM kadar FARKLI bir branch/dosya adiyla
    (rastgele ek) yeniden dener. PR basligi (PR_TITLE) HER ZAMAN ayni
    kalir - sadece perde arkasindaki teknik isimler degisir.
    Dondurdugu deger: {"n", "title", "branch", "status", "detail"}"""
    branch = f"{BRANCH_PREFIX}-{n}"

    if DRY_RUN:
        file_path = f"{branch}.md" if PLACE_IN_ROOT else f"{FILE_DIR}/{branch}.md"
        log(f"   🧪 DRY-RUN #{n}: '{file_path}' oluşturulup '{PR_TITLE}' başlıklı PR açılacaktı.")
        return {"n": n, "title": PR_TITLE, "branch": branch, "status": "DRY-RUN", "detail": ""}

    last_detail = "-"
    for attempt in range(MAX_RETRY_PER_ITEM):
        if attempt > 0:
            branch = f"{BRANCH_PREFIX}-{n}-r{attempt}{rand_suffix()}"
        file_path = f"{branch}.md" if PLACE_IN_ROOT else f"{FILE_DIR}/{branch}.md"
        content = random.choice(JUNK_CONTENTS)
        body = random.choice(PR_BODIES)

        try:
            base_sha = get_branch_sha(base_branch)
            if not base_sha:
                last_detail = "ana branch sha alınamadı"
            elif not create_branch(branch, base_sha):
                last_detail = "branch oluşturulamadı"
            else:
                time.sleep(0.5)  # branch'in yayilmasina kucuk bir pay
                if not create_junk_file(branch, file_path, content, f"{PR_TITLE}: saçma sapan dosya eklendi"):
                    last_detail = "dosya oluşturulamadı"
                else:
                    pr_url, pr_number = create_pr(branch, base_branch, PR_TITLE, body)
                    if not pr_url:
                        last_detail = "PR açılamadı"
                    else:
                        add_labels(pr_number)
                        add_comment(pr_number)
                        add_reaction(pr_number)
                        if MERGE_AFTER_CREATE:
                            merge_pr(pr_number)
                        return {"n": n, "title": PR_TITLE, "branch": branch, "status": "BAŞARILI", "detail": pr_url}
        except Exception as e:
            last_detail = f"beklenmeyen hata: {e}"

        if attempt < MAX_RETRY_PER_ITEM - 1:
            log(f"   🔁 #{n} deneme {attempt + 1}/{MAX_RETRY_PER_ITEM} başarısız ({last_detail}) - farklı isimle tekrar deneniyor...")
            time.sleep(1)

    return {"n": n, "title": PR_TITLE, "branch": branch, "status": "HATA", "detail": last_detail}


def run_batches(numbers, worker_fn, on_result):
    """EKLENDI: genel amacli 'gruplara bol + paralel isle + gruplar
    arasi mola ver' yardimcisi. worker_fn(n) -> sonuc, on_result(sonuc)
    her sonucu isler (rapor/state guncelleme icin)."""
    total = len(numbers)
    for start in range(0, total, BATCH_SIZE):
        batch = numbers[start:start + BATCH_SIZE]
        batch_no = start // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        log(f"\n📦 Grup {batch_no}/{total_batches}: {len(batch)} öğe, {MAX_WORKERS} paralel işçi ile işleniyor...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(worker_fn, n): n for n in batch}
            for future in as_completed(futures):
                on_result(future.result())
        if start + BATCH_SIZE < total:
            log(f"⏸️  Grup tamamlandı, {BATCH_PAUSE_SECONDS} saniye bekleniyor (rate limit güvenliği)...")
            time.sleep(BATCH_PAUSE_SECONDS)


def main():
    if not TOKEN or TOKEN == "YOUR_GITHUB_TOKEN":
        print("⚠  Önce GH_TOKEN secret'ini ayarla.")
        return
    if REPO_OWNER == "senin-kullanici-adin" or REPO_NAME == "senin-repon":
        print("⚠  Önce REPO_OWNER / REPO_NAME değerlerini kendi reponla değiştir.")
        return

    if ACTION == "cleanup":
        run_cleanup()
        return

    base_branch = check_auth()
    if not base_branch:
        return
    log(f"ℹ️  Ana branch: {base_branch}")

    if DRY_RUN:
        log("🧪 DRY_RUN aktif: gerçek branch/dosya/PR/Issue oluşturulmayacak, sadece simülasyon.")
    else:
        ensure_labels()

    state = load_state()
    done = set(state.get("done_numbers", []))
    state_lock = threading.Lock()

    all_numbers = [START_NUMBER + i for i in range(PR_COUNT) if (START_NUMBER + i) not in done]
    skipped = PR_COUNT - len(all_numbers)
    if skipped:
        log(f"⏭️  {skipped} numara zaten tamamlanmış, atlanıyor.")

    rapor_satirlari = []
    basarili = 0
    basarisiz = 0

    log(f"🚀 {len(all_numbers)} PR açılacak: {MAX_WORKERS} paralel işçi, {BATCH_SIZE}'lik gruplar halinde.")

    def handle_pr_result(result):
        nonlocal basarili, basarisiz
        n = result["n"]
        ok = result["status"] in ("BAŞARILI", "DRY-RUN")
        if ok:
            basarili += 1
            with state_lock:
                done.add(n)
                state["done_numbers"] = sorted(done)
                save_state(state)
        else:
            basarisiz += 1
        rapor_satirlari.append([n, result["title"], result["branch"], result["status"], result["detail"]])
        log(f"   {'✅' if ok else '❌'} #{n}: {result['status']} - {result['detail']}")

    run_batches(all_numbers, lambda n: process_item(n, base_branch), handle_pr_result)

    # Junk Issue'lar - PR'lardan bağımsız, aynı batch+paralel mantığıyla
    if INCLUDE_JUNK_ISSUES and ISSUE_COUNT > 0:
        log(f"\n🗑️  {ISSUE_COUNT} adet junk issue açılıyor...")
        issue_numbers = [START_NUMBER + i for i in range(ISSUE_COUNT)]
        run_batches(issue_numbers, create_junk_issue, lambda _r: None)

    try:
        with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["numara", "baslik", "branch", "durum", "detay_veya_link"])
            writer.writerows(rapor_satirlari)
        log(f"📄 Rapor yazıldı: {REPORT_FILE}")
    except Exception as e:
        log(f"⚠️  Rapor yazılamadı: {e}")

    print("\n" + "=" * 60)
    print(f"BİTTİ! {basarili} PR açıldı (ya da simüle edildi), {basarisiz} başarısız.")
    print("=" * 60)

    send_ntfy(f"Junk PR script bitti: {basarili} başarılı, {basarisiz} başarısız.")


if __name__ == "__main__":
    main()

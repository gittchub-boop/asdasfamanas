"""
GITHUB SACMA SAPAN PR ACMA SCRIPTI (sadece eglence amacli)
=============================================================
Ne yapar:
  - SENIN belirttigin repoda, her biri ayri bir branch'te olacak
    sekilde bos/anlamsiz icerikli bir dosya olusturur
  - Her biri icin "pıt#2255", "pıt#2256"... gibi sacma basliklarla
    ayri bir Pull Request (PR) acar
  - Bunu PR_COUNT kadar (varsayilan 50) tekrarlar

ONEMLI - SADECE KENDI REPONDA KULLAN:
  Bu script REPO_OWNER/REPO_NAME olarak ne yazarsan ORAYA PR acar.
  Baskasinin (senin olmadigin) bir repo'suna spam PR atmak hem GitHub
  Kullanim Sartlari'na aykiridir hem de o projenin sahibine/
  bakimcilarina gercek bir rahatsizlik verir (bildirim spam'i, PR
  kuyrugunu kirletmek vs). O yuzden REPO_OWNER'i HER ZAMAN kendi
  kullanici adin (ya da senin sahip oldugun bir repo) olarak birak.

KULLANIM:
  1. GH_TOKEN secret'ini ayarla (repo yazma + PR acma izni olan bir
     "repo" scope'lu classic token yeterli).
  2. Asagida REPO_OWNER / REPO_NAME'i kendi reponla degistir.
  3. Once DRY_RUN=1 ile dene, ne olacagini gor.
  4. GitHub Actions'ta calistir ya da lokalde `python github_junk_pr.py`.
"""

import os
import time
import json
import base64
import random
import csv
import logging
import requests

# =============================================================================
# AYARLAR
# =============================================================================

TOKEN = os.environ.get("GH_TOKEN", "YOUR_GITHUB_TOKEN")

REPO_OWNER = os.environ.get("REPO_OWNER", "senin-kullanici-adin")
REPO_NAME = os.environ.get("REPO_NAME", "senin-repon")
# ^ ÖNEMLİ: burası SENİN sahip olduğun repo olmalı, başkasının değil.

PR_COUNT = int(os.environ.get("PR_COUNT", "50"))
# ^ Kaç tane saçma PR açılacak.

START_NUMBER = int(os.environ.get("START_NUMBER", "2255"))
# ^ Başlık numaralandırması buradan başlar: pıt#2255, pıt#2256, ...

TITLE_TEMPLATE = os.environ.get("TITLE_TEMPLATE", "pıt#{n}")
# ^ PR başlığı. {n} yerine numara gelir. İstersen "🗑️ pıt#{n}" gibi de yazabilirsin.

BRANCH_PREFIX = os.environ.get("BRANCH_PREFIX", "pit")
# ^ Her PR ayrı bir branch'te açılır: pit-2255, pit-2256, ...

FILE_DIR = os.environ.get("FILE_DIR", "junk")
# ^ Saçma dosyalar bu klasörün altına düşer, repo kökünü kirletmez.

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
# ^ "1" yaparsan hiçbir gerçek branch/dosya/PR oluşturmaz, sadece ne
#   yapacağını ekrana yazar.

SLEEP_BETWEEN_SECONDS = float(os.environ.get("SLEEP_BETWEEN_SECONDS", "2"))
# ^ Her PR arasında bekleme. Çok hızlı art arda istek atmak GitHub'ın
#   "abuse detection" sistemini tetikleyip token'ı geçici kısıtlayabilir.

STATE_FILE = os.environ.get("STATE_FILE", "junk_pr_state.json")
# ^ Hangi numaralara kadar PR açıldığını tutar, script yarıda kesilirse
#   kaldığı yerden devam eder (aynı numarayı tekrar açmaz).

REPORT_FILE = os.environ.get("REPORT_FILE", "junk_pr_report.csv")

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
# ^ Doldurursan bitince telefonuna (ntfy app) push bildirim gider.

LOG_FILE = os.environ.get("LOG_FILE", "junk_pr.log")

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

# =============================================================================
# EKLENEN AYARLAR - daha fazla eğlence (hepsi opsiyonel, açıp kapatabilirsin)
# =============================================================================

ACTION = os.environ.get("ACTION", "create")
# ^ "create"  -> normal davranış (junk PR'lar açar)
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

INCLUDE_JUNK_ISSUES = os.environ.get("INCLUDE_JUNK_ISSUES", "0") == "1"
ISSUE_COUNT = int(os.environ.get("ISSUE_COUNT", "0"))
# ^ PR'lara ek olarak bu kadar da saçma sapan Issue açar (varsayılan kapalı).
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
#   görünür - "eğlencesine repomda göster" istiyorsan bu tam onu yapar).
#   Varsayılan KAPALI çünkü ana branch geçmişini gerçekten değiştirir,
#   geri almak (revert) gerekebilir. Açmadan önce DRY_RUN ile dene.

CLEANUP_PATTERN = os.environ.get("CLEANUP_PATTERN", BRANCH_PREFIX + "-")
# ^ cleanup modunda hangi branch'ler silinecek (bu ön ek ile başlayanlar).

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


def log(msg):
    print(msg)
    try:
        logger.info(msg)
    except Exception:
        pass


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
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    payload = {"message": message, "content": b64, "branch": branch}
    r = request_with_retry("PUT", f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}", json=payload)
    if r is not None and r.status_code in (200, 201):
        return True
    log(f"   ❌ Dosya oluşturulamadı: HTTP {r.status_code if r is not None else '?'} -> {r.text[:200] if r is not None else 'yanıt yok'}")
    return False


def create_pr(branch, base, title, body):
    payload = {"title": title, "head": branch, "base": base, "body": body}
    r = request_with_retry("POST", f"{API}/repos/{REPO_OWNER}/{REPO_NAME}/pulls", json=payload)
    if r is not None and r.status_code == 201:
        data = r.json()
        return data.get("html_url"), data.get("number")
    log(f"   ❌ PR açılamadı: HTTP {r.status_code if r is not None else '?'} -> {r.text[:200] if r is not None else 'yanıt yok'}")
    return None, None


# =============================================================================
# EKLENDI: etiket, yorum, reaksiyon, issue, auto-merge, cleanup
# =============================================================================

_labels_ensured = False


def ensure_labels():
    """Repo'da JUNK_LABELS yoksa olusturur (varsa hata vermeden gecer)."""
    global _labels_ensured
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
    log(f"   ❌ Issue açılamadı: HTTP {r.status_code if r is not None else '?'} -> {r.text[:200] if r is not None else 'yanıt yok'}")
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
    """EKLENDI: CLEANUP_PATTERN ile baslayan tum junk branch'leri bulur,
    varsa acik PR'larini kapatir, sonra branch'i siler."""
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
        log("🧪 DRY_RUN aktif: gerçek branch/dosya/PR oluşturulmayacak, sadece simülasyon.")
    else:
        ensure_labels()

    state = load_state()
    done = set(state.get("done_numbers", []))

    rapor_satirlari = []
    basarili = 0
    basarisiz = 0

    for i in range(PR_COUNT):
        n = START_NUMBER + i
        if n in done:
            log(f"⏭️  #{n} zaten açılmış, atlanıyor.")
            continue

        title = TITLE_TEMPLATE.format(n=n)
        branch = f"{BRANCH_PREFIX}-{n}"
        file_path = f"{FILE_DIR}/{branch}.md"
        content = random.choice(JUNK_CONTENTS)
        body = random.choice(PR_BODIES)

        log(f"🔧 [{i + 1}/{PR_COUNT}] Hazırlanıyor: {title} (branch: {branch})")

        if DRY_RUN:
            log(f"   🧪 DRY-RUN: '{file_path}' dosyası oluşturulup '{title}' başlıklı PR açılacaktı "
                f"(etiket/yorum/reaksiyon{' + otomatik merge' if MERGE_AFTER_CREATE else ''} dahil).")
            rapor_satirlari.append([n, title, branch, "DRY-RUN", ""])
            basarili += 1
            done.add(n)
            state["done_numbers"] = sorted(done)
            save_state(state)
            time.sleep(SLEEP_BETWEEN_SECONDS)
            continue

        base_sha = get_branch_sha(base_branch)
        if not base_sha:
            log("   ❌ Ana branch SHA'sı alınamadı, bu PR atlanıyor.")
            basarisiz += 1
            rapor_satirlari.append([n, title, branch, "HATA", "base sha alınamadı"])
            continue

        if not create_branch(branch, base_sha):
            basarisiz += 1
            rapor_satirlari.append([n, title, branch, "HATA", "branch oluşturulamadı"])
            continue

        time.sleep(1)  # EKLENDI: branch'in GitHub tarafında tam yayılmasına küçük bir pay

        if not create_junk_file(branch, file_path, content, f"{title}: saçma sapan dosya eklendi"):
            basarisiz += 1
            rapor_satirlari.append([n, title, branch, "HATA", "dosya oluşturulamadı"])
            continue

        pr_url, pr_number = create_pr(branch, base_branch, title, body)
        if pr_url:
            basarili += 1
            done.add(n)
            state["done_numbers"] = sorted(done)
            save_state(state)
            log(f"   ✅ PR açıldı: {pr_url}")

            # EKLENDI: etiket + yorum + reaksiyon + (opsiyonel) auto-merge
            add_labels(pr_number)
            add_comment(pr_number)
            add_reaction(pr_number)
            if MERGE_AFTER_CREATE:
                merge_pr(pr_number)

            rapor_satirlari.append([n, title, branch, "BAŞARILI", pr_url])
        else:
            basarisiz += 1
            rapor_satirlari.append([n, title, branch, "HATA", "PR açılamadı"])

        time.sleep(SLEEP_BETWEEN_SECONDS)

    # EKLENDI: istersen PR'lara ek olarak saçma issue'lar da açar
    if INCLUDE_JUNK_ISSUES and ISSUE_COUNT > 0 and not DRY_RUN:
        log(f"\n🗑️  {ISSUE_COUNT} adet junk issue açılıyor...")
        for i in range(ISSUE_COUNT):
            create_junk_issue(START_NUMBER + i)
            time.sleep(SLEEP_BETWEEN_SECONDS)

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

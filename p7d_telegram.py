"""Posiela nové články z Plus 7 dní do Telegram kanála.

Najprv skúsi RSS feed, ak nefunguje, prečíta stránku /r/novinky.
Už odoslané články si pamätá v súbore seen.json.
"""
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# .strip() odstráni medzery/nové riadky, ktoré sa ľahko skopírujú do Secrets;
# odstráni aj prípadné <> a predponu "bot" (časté chyby pri kopírovaní tokenu)
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip().strip("<>").strip()
if BOT_TOKEN.lower().startswith("bot"):
    BOT_TOKEN = BOT_TOKEN[3:]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"].strip()  # napr. @mojkanal alebo -100123...

BASE = "https://plus7dni.pluska.sk"
RSS_URL = "https://nmh-production02-dfs.fra1.digitaloceanspaces.com/rss/echobox/rss-echobox-plus7.xml"
LIST_URL = BASE + "/r/novinky"
SEEN_FILE = "seen.json"
MAX_PER_RUN = 10  # ochrana pred zahltením kanála

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8",
}


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def from_rss():
    root = ET.fromstring(fetch(RSS_URL))
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or it.findtext("guid") or "").strip()
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        if link.startswith("http"):
            items.append({"url": link, "title": title, "desc": desc})
    if not items:
        raise ValueError("RSS je prázdne")
    return items


def from_page():
    page = fetch(LIST_URL)
    items, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="([^"#?]+)"[^>]*>(.*?)</a>', page, re.S):
        url = urllib.parse.urljoin(BASE, m.group(1))
        path = urllib.parse.urlparse(url).path.strip("/")
        if not url.startswith(BASE) or path.startswith(("r/", "tag/", "autor/")):
            continue
        parts = path.split("/")
        # článok = sekcia/dlhy-slug-s-pomlckami
        if len(parts) < 2 or parts[-1].count("-") < 3:
            continue
        title = html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        title = re.sub(r"\s+", " ", title).strip()
        if url in seen:
            # ten istý článok býva odkázaný viackrát (obrázok + nadpis)
            for i in items:
                if i["url"] == url and not i["title"] and title:
                    i["title"] = title
            continue
        seen.add(url)
        items.append({"url": url, "title": title})
    if not items:
        raise ValueError("Na stránke som nenašiel žiadne články")
    return items


def send(item):
    # do správy ide popis (perex) článku; ak chýba, použije sa titulok
    body = item.get("desc") or item["title"]
    text = f"{html.escape(body)}\n{item['url']}" if body else item["url"]
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        with urllib.request.urlopen(url, data=data, timeout=30) as r:
            r.read()
    except urllib.error.HTTPError as e:
        # Telegram v odpovedi napíše presný dôvod (zlý token, chat not found, ...)
        raise RuntimeError(f"Telegram {e.code}: {e.read().decode('utf-8', 'replace')}") from None


def main():
    try:
        items = from_rss()
        print(f"RSS: {len(items)} článkov")
    except Exception as e:
        print(f"RSS nefunguje ({e}), čítam stránku /r/novinky")
        items = from_page()
        print(f"Stránka: {len(items)} článkov")

    first_run = not os.path.exists(SEEN_FILE)
    seen = [] if first_run else json.load(open(SEEN_FILE, encoding="utf-8"))
    seen_set = set(seen)

    new = [i for i in items if i["url"] not in seen_set]
    if first_run:
        print("Prvé spustenie: iba si zapamätám existujúce články, nič neposielam.")
    else:
        # najstaršie najprv, aby poradie v kanáli sedelo
        for item in list(reversed(new))[-MAX_PER_RUN:]:
            send(item)
            print("Odoslané:", item["url"])

    seen = (seen + [i["url"] for i in new])[-500:]
    json.dump(seen, open(SEEN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"Nových: {len(new)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("CHYBA:", e)
        sys.exit(1)

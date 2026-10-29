"""楽天の商品検索APIで、ブランド×カテゴリごとの価格を取得し、data/history.json に貯める。

必要な環境変数（GitHub の Secrets に入れる）:
  RAKUTEN_APP_ID, RAKUTEN_ACCESS_KEY, RAKUTEN_AFFILIATE_ID
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
HISTORY = ROOT / "data" / "history.json"
ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
JST = timezone(timedelta(hours=9))


def env(name):
    # Secrets に貼るとき末尾に改行が入りやすいので取り除く
    return os.environ.get(name, "").strip()


def search(params, config):
    query = urllib.parse.urlencode({
        "applicationId": env("RAKUTEN_APP_ID"),
        "affiliateId": env("RAKUTEN_AFFILIATE_ID"),
        "format": "json",
        "hits": 30,
        "availability": 1,
        **params,
    })
    req = urllib.request.Request(f"{ENDPOINT}?{query}", headers={
        "accessKey": env("RAKUTEN_ACCESS_KEY"),
        # 2026年の刷新で、登録したサイトからのアクセスか確かめられるため付けておく
        "Referer": config["site_url"],
        "Origin": config["site_url"].rstrip("/"),
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.load(res)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            print(f"HTTP {e.code}: {e.read()[:300]!r}", file=sys.stderr)
            raise


def brand_pos(name, brand):
    """ブランド名が単語として出てくる位置（「CAPITA」が「CAPITAL」に当たらないように）。無ければ -1。"""
    m = re.search(rf"(?<![A-Z0-9]){re.escape(brand.upper())}(?![A-Z0-9])", name.upper())
    return m.start() if m else -1


def detect_brand(name, brands):
    """商品名に一番先に出てくるブランドを、その商品のブランドとみなす。"""
    found = [(brand_pos(name, b), b) for b in brands if brand_pos(name, b) >= 0]
    return min(found)[1] if found else None


def is_target(name, brand, price, category, config):
    upper = name.upper()
    if brand_pos(name, brand) < 0:
        return False
    if price < category["min_price"]:
        return False
    return not any(w.upper() in upper for w in config["exclude_words"])


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else {}
    today = datetime.now(JST).strftime("%Y-%m-%d")
    seen = 0

    for cat_key, category in config["categories"].items():
        for brand in config["brands"]:
            data = search({"keyword": f"{brand} {category['keyword']}"}, config)
            time.sleep(1.2)
            for wrapper in data.get("Items", []):
                item = wrapper.get("Item", wrapper)
                name, price = item["itemName"], int(item["itemPrice"])
                if not is_target(name, brand, price, category, config):
                    continue
                entry = history.setdefault(item["itemCode"], {"prices": {}})
                entry.update({
                    "name": name,
                    "brand": detect_brand(name, config["brands"]) or brand,
                    "category": cat_key,
                    "shop": item.get("shopName", ""),
                    "url": item.get("affiliateUrl") or item["itemUrl"],
                    "image": (item.get("mediumImageUrls") or [{}])[0].get("imageUrl", ""),
                    "point": int(item.get("pointRate") or 1),
                    # 0 なら送料込み、1 なら送料別
                    "postage": int(item.get("postageFlag") or 0),
                    "last_seen": today,
                })
                entry["prices"][today] = price
                seen += 1

    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{today}: {seen} 件を記録（累計 {len(history)} 商品）")


if __name__ == "__main__":
    main()

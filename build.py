"""data/history.json から値下がりを判定し、サイト（index.html）を作る。

同じモデルはお店違い・サイズ違いでも1枚にまとめ、一番安いお店だけを出す。
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fetch import detect_brand

ROOT = Path(__file__).parent
JST = timezone(timedelta(hours=9))

# 「25-26」「2025-2026」「24/25」などの年式。「23-26.5cm」のようなサイズは除く
SEASON_RE = re.compile(r"(?<!\d)(?:20)?(\d{2})\s*[-/／〜~]\s*(?:20)?(\d{2})(?![\d.]*\s*(?:cm|CM|ｃｍ))")
WORD_RE = re.compile(r"[A-Z][A-Z0-9+'.]*")
STOP = {
    "SNOWBOARD", "SNOWBOARDS", "SNOW", "BOARD", "BOARDS", "BINDING", "BINDINGS", "BOOTS", "BOOT",
    "MENS", "MEN'S", "MEN", "WOMENS", "WOMEN'S", "WOMEN", "LADIES", "UNISEX", "KIDS", "NEW", "MODEL",
    "SALE", "OFF", "FREE", "JAPAN", "LIMITED", "CAMBER", "ROCKER", "BOA", "PT", "P", "S", "M", "L",
    "XS", "XL", "XXL", "SM", "ML", "S/M", "M/L", "W", "AND", "THE", "FOR", "WITH", "ALL",
    "STEP", "ON",  # BURTON の STEP ON は仕組みの名前で、型番はその後ろに来る
}


def season(name):
    for a, b in SEASON_RE.findall(name):
        if int(b) == int(a) + 1 and 10 <= int(a) <= 40:
            return f"{a}-{b}"
    return ""


def current_season(today):
    y = int(today[2:4])
    m = int(today[5:7])
    start = y if m >= 7 else y - 1
    return f"{start:02d}-{start + 1:02d}"


def model_key(e):
    """同じモデルをまとめるための目印。ブランド・年式・型番らしい英単語の先頭2つ。"""
    name = re.sub(r"[【\[［(（].*?[】\]］)）]", " ", e["name"]).upper()
    brand_words = set(e["brand"].upper().split())
    words = [w.strip(".'") for w in WORD_RE.findall(name)]
    words = [w for w in words if w and w not in STOP and w not in brand_words
             and not re.fullmatch(r"\d+[A-Z]?", w)]
    seen = []
    for w in words:
        if w not in seen:
            seen.append(w)
    if not seen:
        return None
    return (e["category"], e["brand"], season(e["name"]), tuple(seen[:2]))


def group_today(history, today, brands, exclude_words=()):
    """今日価格がある商品を、モデルごとに最安のお店1つへまとめる。"""
    groups = {}
    for code, e in history.items():
        if today not in e["prices"]:
            continue
        # 取得済みのデータにはブランドの取り違えがあるので、作るたびに名前から判定し直す
        brand = detect_brand(e["name"], brands)
        if not brand or any(w.upper() in e["name"].upper() for w in exclude_words):
            continue
        e = {**e, "brand": brand}
        item = {**e, "code": code, "price": e["prices"][today], "season": season(e["name"])}
        key = model_key(e) or ("single", code)
        g = groups.setdefault(key, [])
        g.append(item)
    result = []
    for items in groups.values():
        items.sort(key=lambda x: x["price"])
        best = {**items[0], "shops": len({i["shop"] for i in items}), "members": items}
        result.append(best)
    return result


def drop_of(item, today, rule):
    past = [p for d, p in sorted(item["prices"].items()) if d < today]
    if not past:
        return None
    prev, now = past[-1], item["price"]
    diff = prev - now
    if diff >= rule["min_yen"] and diff / prev >= rule["min_rate"]:
        return {"prev": prev, "diff": diff, "rate": diff / prev, "lowest": now < min(past)}
    return None


def clean_name(name):
    """表示用の商品名。宣伝の【】や［］を外して短くする。"""
    name = re.sub(r"[【\[［].*?[】\]］]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:70]


def to_row(m, drop):
    """ページに埋め込む1モデル分のデータ。キーは短くしてページを軽くする。"""
    row = {
        "c": m["category"], "b": m["brand"], "s": m["season"], "n": clean_name(m["name"]),
        "p": m["price"], "h": m["shop"], "k": m["shops"], "u": m["url"], "i": m["image"],
    }
    if drop:
        row.update({"o": drop["prev"], "r": round(drop["rate"], 3), "l": int(drop["lowest"])})
    return row


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    history = json.loads((ROOT / "data" / "history.json").read_text(encoding="utf-8"))
    today = max(d for e in history.values() for d in e["prices"])
    now_season = current_season(today)
    models = group_today(history, today, config["brands"], config["exclude_words"])

    rows = []
    drops = 0
    for m in models:
        # 値下がりは、まとめる前の各お店の商品で判定し、モデルごとに一番下がったものを出す
        best = None
        for item in m["members"]:
            d = drop_of(item, today, config["drop"])
            if d and (best is None or d["rate"] > best[1]["rate"]):
                best = (item, d)
        if best:
            drops += 1
            rows.append(to_row({**best[0], "shops": m["shops"]}, best[1]))
        else:
            rows.append(to_row(m, None))

    data = {
        "date": today,
        "season": now_season,
        "categories": {k: v["label"] for k, v in config["categories"].items()},
        "items": rows,
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = (ROOT / "template.html").read_text(encoding="utf-8")
    page = (page.replace("{{DATE}}", today)
                .replace("{{COUNT}}", str(drops))
                .replace("{{MODELS}}", f"{len(models):,}")
                .replace("{{DATA}}", payload))
    (ROOT / "index.html").write_text(page, encoding="utf-8")
    print(f"{today}: {len(models)} モデル、値下がり {drops} 件")


if __name__ == "__main__":
    main()

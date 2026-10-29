"""data/history.json から値下がりを判定し、サイト（index.html）を作る。

同じモデルはお店違い・サイズ違いでも1枚にまとめ、一番安いお店だけを出す。
"""
import html
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


def group_today(history, today, brands):
    """今日価格がある商品を、モデルごとに最安のお店1つへまとめる。"""
    groups = {}
    for code, e in history.items():
        if today not in e["prices"]:
            continue
        # 取得済みのデータにはブランドの取り違えがあるので、作るたびに名前から判定し直す
        brand = detect_brand(e["name"], brands)
        if not brand:
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


def card(e):
    price = f"{e['price']:,}円"
    badges = ""
    if e.get("season"):
        badges += f'<span class="tag">{e["season"]}</span>'
    d = e.get("drop")
    if d:
        price = f'<s>{d["prev"]:,}円</s> → {price}'
        badges += f'<span class="badge">-{d["rate"]:.0%}（{d["diff"]:,}円）</span>'
        if d["lowest"]:
            badges += '<span class="badge low">記録上の最安</span>'
    shop = html.escape(e["shop"])
    if e["shops"] > 1:
        shop += f'（ほか{e["shops"] - 1}店）'
    img = f'<img src="{html.escape(e["image"])}" alt="" loading="lazy">' if e["image"] else ""
    return (f'<a class="card" href="{html.escape(e["url"])}" rel="sponsored noopener" target="_blank">'
            f'{img}<div><p class="name">{html.escape(e["name"][:80])}</p>'
            f'<p class="price">{price}</p><p>{badges}</p><p class="shop">{shop}</p></div></a>')


def grid(items):
    return '<div class="grid">' + "".join(card(i) for i in items) + "</div>"


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    history = json.loads((ROOT / "data" / "history.json").read_text(encoding="utf-8"))
    today = max(d for e in history.values() for d in e["prices"])
    now_season = current_season(today)
    models = group_today(history, today, config["brands"])

    # 値下がりは、まとめる前の各お店の商品で判定し、モデルごとに一番下がったものを出す
    drops = []
    for m in models:
        best = None
        for item in m["members"]:
            d = drop_of(item, today, config["drop"])
            if d and (best is None or d["rate"] > best[1]["rate"]):
                best = (item, d)
        if best:
            drops.append({**best[0], "shops": m["shops"], "drop": best[1]})
    drops.sort(key=lambda x: x["drop"]["rate"], reverse=True)

    sections = ['<section><h2>今日の値下がり</h2>']
    if drops:
        sections.append(grid(drops[:30]))
    else:
        sections.append('<p class="note">今日は前回から下がった商品はありませんでした。</p>')
    sections.append("</section>")

    for key, cat in config["categories"].items():
        cat_models = [m for m in models if m["category"] == key]
        latest = sorted((m for m in cat_models if m["season"] == now_season), key=lambda x: x["price"])
        older = sorted((m for m in cat_models if m["season"] and m["season"] < now_season),
                       key=lambda x: x["price"])
        sections.append(f'<section><h2>{cat["label"]}</h2>')
        sections.append(f'<h3>今季モデル（{now_season}）の安い順</h3>')
        sections.append(grid(latest[:8]) if latest else '<p class="note">まだ見つかっていません。</p>')
        sections.append('<h3>型落ちの安い順</h3>')
        sections.append(grid(older[:8]) if older else '<p class="note">まだ見つかっていません。</p>')
        sections.append("</section>")

    page = (ROOT / "template.html").read_text(encoding="utf-8")
    page = (page.replace("{{DATE}}", today)
                .replace("{{COUNT}}", str(len(drops)))
                .replace("{{MODELS}}", f"{len(models):,}")
                .replace("{{SECTIONS}}", "".join(sections)))
    (ROOT / "index.html").write_text(page, encoding="utf-8")
    print(f"{today}: {len(models)} モデル、値下がり {len(drops)} 件")


if __name__ == "__main__":
    main()

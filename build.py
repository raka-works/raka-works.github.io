"""data/history.json から値下がりを判定し、サイトを作る。

- index.html：全モデルの一覧（絞り込み・並べ替えはページ内の JS）
- m/<slug>.html：モデルごとのページ（価格の推移、お店ごとの価格）
- sitemap.xml / robots.txt：検索エンジン向け

同じモデルはお店違い・サイズ違いでも1つにまとめる。
"""
import hashlib
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
# 板の長さ（130〜175cm）。年号や価格に当たらないよう前後に数字が無いものだけ
BOARD_SIZE_RE = re.compile(r"(?<![\d.,])(1[3-6]\d|17[0-5])(?:\s*W)?(?![\d,]|\s*(?:円|%|％|g|mm))")
BOOTS_SIZE_RE = re.compile(r"(?<![\d.])(2[2-9](?:\.[05])?)(?=\s*(?:cm|CM|ｃｍ|㎝))")


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


def sizes(name, category):
    if category == "board":
        return {int(s) for s in BOARD_SIZE_RE.findall(name)}
    if category == "boots":
        return {float(s) for s in BOOTS_SIZE_RE.findall(name)}
    return set()


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


def slug_of(key):
    """モデルのページのファイル名。毎日同じ名前になるよう、目印から作る。"""
    if key[0] == "single":
        return "item-" + hashlib.sha1(key[1].encode()).hexdigest()[:10]
    cat, brand, sea, words = key
    raw = "-".join([brand, *words, sea, cat])
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "m-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def group_today(history, today, brands, exclude_words=()):
    """今日価格がある商品を、モデルごとにまとめる。安い順に並べた members を持つ。"""
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
        groups.setdefault(key, []).append(item)
    result = []
    for key, items in groups.items():
        items.sort(key=lambda x: x["price"])
        size_set = set()
        for i in items:
            size_set |= sizes(i["name"], i["category"])
        result.append({**items[0], "key": key, "slug": slug_of(key), "members": items,
                       "shops": len({i["shop"] for i in items}), "sizes": sorted(size_set)})
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


def model_title(m, cat_label):
    if m["key"][0] == "single":
        return clean_name(m["name"])[:40]
    _, brand, sea, words = m["key"]
    return " ".join([brand, *words, sea, cat_label]).replace("  ", " ")


def real_price(item):
    """ポイントを差し引いた目安の価格（通常ポイント1倍＝1%として計算）。"""
    return item["price"] - item["price"] * item.get("point", 1) // 100


def daily_min(m):
    """モデルの日ごとの最安値（お店をまたいで）。"""
    by_day = {}
    for i in m["members"]:
        for d, p in i["prices"].items():
            by_day[d] = min(by_day.get(d, p), p)
    return sorted(by_day.items())


def yen(n):
    return f"{n:,}円"


def chart_svg(points):
    """価格の推移の折れ線（外部のライブラリを使わない）。"""
    if len(points) < 2:
        return '<p class="note">価格の記録を始めたばかりです。毎朝1点ずつ増えていきます。</p>'
    w, h, pad = 600, 180, 28
    prices = [p for _, p in points]
    lo, hi = min(prices), max(prices)
    span = max(hi - lo, 1)
    xs = [pad + i * (w - pad * 2) / (len(points) - 1) for i in range(len(points))]
    ys = [pad + (hi - p) * (h - pad * 2) / span for p in prices]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="価格の推移">'
            f'<polyline points="{line}" fill="none" stroke="currentColor" stroke-width="2.5"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="4" fill="currentColor"/>'
            f'<text x="{pad}" y="16" class="lbl">最高 {yen(hi)}</text>'
            f'<text x="{pad}" y="{h - 6}" class="lbl">最安 {yen(lo)}</text>'
            f'<text x="{w - pad}" y="{h - 6}" class="lbl" text-anchor="end">{points[0][0]} 〜 {points[-1][0]}</text>'
            f'</svg>')


def model_page(m, cat_label, today, site_url):
    title = model_title(m, cat_label)
    hist = daily_min(m)
    now = m["price"]
    lowest = min(p for _, p in hist)
    if len(hist) < 2:
        verdict = "記録を始めたばかりなので、買い時の判定はまだできません。"
    elif now <= lowest:
        verdict = "今の価格は、記録の中で一番安い価格です。"
    else:
        verdict = f"記録上の最安（{yen(lowest)}）より {yen(now - lowest)} 高い状態です。"

    rows = []
    for i in m["members"]:
        postage = "送料込み" if i.get("postage", 0) == 0 else "送料別"
        rows.append(
            f'<tr><td>{html.escape(i["shop"])}</td><td class="num">{yen(i["price"])}</td>'
            f'<td class="num">{i.get("point", 1)}倍</td><td class="num">{yen(real_price(i))}</td>'
            f'<td>{postage}</td><td><a class="btn" href="{html.escape(i["url"])}" '
            f'rel="sponsored noopener" target="_blank">楽天で見る</a></td></tr>')
    size_text = ""
    if m["sizes"]:
        unit = "cm" if m["category"] == "boots" else ""
        size_text = "<p>商品名に書かれているサイズ：" + "、".join(f"{s:g}{unit}" for s in m["sizes"]) + "</p>"
    img = f'<img src="{html.escape(m["image"])}" alt="{html.escape(title)}">' if m["image"] else ""
    desc = f"{title}の楽天市場での最安値は{yen(now)}（{today}時点、{m['shops']}店を比較）。価格の推移とお店ごとのポイント込み価格を毎朝更新。"
    best = m["members"][0]

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} の最安値と価格推移｜DEEP DAYS</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{site_url}m/{m['slug']}.html">
<style>
:root {{ --bg:#f5f6f8; --card:#fff; --text:#1d2330; --sub:#667085; --accent:#d92d20; --line:#e4e7ec; --btn:#bf0000; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#12151b; --card:#1c2029; --text:#e8eaef; --sub:#98a2b3; --accent:#ff6b5e; --line:#2c313c; --btn:#d93030; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:system-ui,-apple-system,"Hiragino Sans","Yu Gothic UI",sans-serif; line-height:1.6; }}
.wrap {{ max-width:860px; margin:0 auto; padding:16px; }}
a {{ color:inherit; }}
.pr {{ display:inline-block; font-size:.7rem; border:1px solid var(--sub); color:var(--sub); padding:0 6px; border-radius:4px; }}
.back {{ font-size:.85rem; color:var(--sub); }}
h1 {{ font-size:1.3rem; margin:8px 0; }}
.top {{ display:grid; grid-template-columns:200px 1fr; gap:16px; align-items:start; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }}
.top img {{ width:100%; background:#fff; border-radius:8px; }}
.price {{ font-size:1.6rem; font-weight:700; margin:0; }}
.sub {{ color:var(--sub); font-size:.85rem; }}
.btn {{ display:inline-block; background:var(--btn); color:#fff; text-decoration:none; padding:8px 16px; border-radius:8px; font-weight:700; font-size:.9rem; white-space:nowrap; }}
.btn.big {{ padding:12px 24px; font-size:1rem; margin-top:8px; }}
h2 {{ font-size:1.05rem; margin:28px 0 8px; }}
.chart {{ width:100%; height:auto; color:var(--accent); background:var(--card); border:1px solid var(--line); border-radius:12px; }}
.chart .lbl {{ font-size:12px; fill:var(--sub); }}
.note {{ color:var(--sub); }}
.tbl {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
table {{ border-collapse:collapse; width:100%; font-size:.85rem; }}
th, td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; }}
th {{ color:var(--sub); font-weight:400; white-space:nowrap; }}
.num {{ text-align:right; white-space:nowrap; }}
footer {{ font-size:.8rem; color:var(--sub); margin-top:32px; }}
@media (max-width:600px) {{ .top {{ grid-template-columns:110px 1fr; gap:12px; padding:12px; }} .price {{ font-size:1.3rem; }} }}
</style>
</head>
<body>
<div class="wrap">
<span class="pr">PR</span> <a class="back" href="../">← 一覧に戻る</a>
<h1>{html.escape(title)}</h1>
<div class="top">
  <div>{img}</div>
  <div>
    <p class="sub">{m['shops']}店の中の最安（{today} 時点）</p>
    <p class="price">{yen(now)}</p>
    <p class="sub">ポイント込みの目安 {yen(real_price(best))}（{best.get('point', 1)}倍）</p>
    <p>{verdict}</p>
    {size_text}
    <a class="btn big" href="{html.escape(best['url'])}" rel="sponsored noopener" target="_blank">最安のお店を楽天で見る</a>
  </div>
</div>
<h2>価格の推移（毎朝の最安値）</h2>
{chart_svg(hist)}
<h2>お店ごとの価格</h2>
<div class="tbl"><table>
<tr><th>お店</th><th class="num">価格</th><th class="num">ポイント</th><th class="num">ポイント込み</th><th>送料</th><th></th></tr>
{''.join(rows)}
</table></div>
<p class="sub">ポイント込みの価格は、お店のポイント倍率だけで計算した目安です（楽天カードなどの特典は含みません）。サイズや色によって価格が違う場合があります。</p>
<footer>
<p>価格は楽天市場から毎朝自動で取得しています。最新の価格・在庫はリンク先でご確認ください。このページには楽天アフィリエイトの広告リンクが含まれています。</p>
<p><a href="https://developers.rakuten.com/" target="_blank" rel="noopener">Supported by Rakuten Developers</a></p>
</footer>
</div>
</body>
</html>
"""


def to_row(m, drop, best):
    """一覧ページに埋め込む1モデル分のデータ。キーは短くしてページを軽くする。"""
    row = {
        "c": m["category"], "b": m["brand"], "s": m["season"], "n": clean_name(m["name"]),
        "p": best["price"], "h": best["shop"], "k": m["shops"], "u": f"m/{m['slug']}.html",
        "i": m["image"], "z": m["sizes"],
    }
    if drop:
        row.update({"o": drop["prev"], "r": round(drop["rate"], 3), "l": int(drop["lowest"])})
    return row


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    history = json.loads((ROOT / "data" / "history.json").read_text(encoding="utf-8"))
    today = max(d for e in history.values() for d in e["prices"])
    now_season = current_season(today)
    site_url = config["site_url"]
    labels = {k: v["label"] for k, v in config["categories"].items()}
    models = group_today(history, today, config["brands"], config["exclude_words"])

    (ROOT / "m").mkdir(exist_ok=True)
    rows, drops, used = [], 0, set()
    # 「TLS」と「TLS+」のように記号だけ違うモデルは同じ名前になるので、後のほうに番号を付ける
    for m in sorted(models, key=lambda x: str(x["key"])):
        base, n = m["slug"], 2
        while m["slug"] in used:
            m["slug"], n = f"{base}-{n}", n + 1
        used.add(m["slug"])
    for m in models:
        # 値下がりは、まとめる前の各お店の商品で判定し、モデルごとに一番下がったものを出す
        best = None
        for item in m["members"]:
            d = drop_of(item, today, config["drop"])
            if d and (best is None or d["rate"] > best[1]["rate"]):
                best = (item, d)
        drops += bool(best)
        rows.append(to_row(m, best[1] if best else None, best[0] if best else m))
        page = model_page(m, labels[m["category"]], today, site_url)
        (ROOT / "m" / f"{m['slug']}.html").write_text(page, encoding="utf-8")

    data = {"date": today, "season": now_season, "categories": labels, "items": rows}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = (ROOT / "template.html").read_text(encoding="utf-8")
    page = (page.replace("{{DATE}}", today)
                .replace("{{COUNT}}", str(drops))
                .replace("{{MODELS}}", f"{len(models):,}")
                .replace("{{DATA}}", payload))
    (ROOT / "index.html").write_text(page, encoding="utf-8")

    # 過去に作ったモデルのページも検索から来る人のために残し、sitemap にも載せる
    urls = [f"<url><loc>{site_url}</loc><lastmod>{today}</lastmod></url>"]
    for p in sorted((ROOT / "m").glob("*.html")):
        urls.append(f"<url><loc>{site_url}m/{p.name}</loc></url>")
    (ROOT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(urls) + "</urlset>\n",
        encoding="utf-8")
    (ROOT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site_url}sitemap.xml\n", encoding="utf-8")
    print(f"{today}: {len(models)} モデル、値下がり {drops} 件")


if __name__ == "__main__":
    main()

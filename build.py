"""data/history.json から値下がりを判定し、サイト（index.html）を作る。"""
import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
JST = timezone(timedelta(hours=9))


def find_drops(history, today, rule):
    drops = []
    for code, e in history.items():
        prices = e["prices"]
        if today not in prices:
            continue
        past = [p for d, p in sorted(prices.items()) if d < today]
        if not past:
            continue
        now, prev = prices[today], past[-1]
        diff = prev - now
        if diff >= rule["min_yen"] and diff / prev >= rule["min_rate"]:
            drops.append({**e, "code": code, "now": now, "prev": prev, "diff": diff,
                          "rate": diff / prev, "lowest": now < min(past)})
    return sorted(drops, key=lambda x: x["rate"], reverse=True)


def card(e, drop=None):
    price = f"{e['now'] if drop else e['price']:,}円"
    badge = ""
    if drop:
        badge = f'<span class="badge">-{drop["rate"]:.0%}（{drop["diff"]:,}円）</span>'
        if drop["lowest"]:
            badge += '<span class="badge low">記録上の最安</span>'
        price = f'<s>{drop["prev"]:,}円</s> → {price}'
    img = f'<img src="{html.escape(e["image"])}" alt="" loading="lazy">' if e["image"] else ""
    return (f'<a class="card" href="{html.escape(e["url"])}" rel="sponsored noopener" target="_blank">'
            f'{img}<div><p class="name">{html.escape(e["name"][:80])}</p>'
            f'<p class="price">{price}</p>{badge}<p class="shop">{html.escape(e["shop"])}</p></div></a>')


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    history = json.loads((ROOT / "data" / "history.json").read_text(encoding="utf-8"))
    today = datetime.now(JST).strftime("%Y-%m-%d")
    drops = find_drops(history, today, config["drop"])

    sections = []
    for key, cat in config["categories"].items():
        cat_drops = [d for d in drops if d["category"] == key]
        if cat_drops:
            body = "".join(card(d, d) for d in cat_drops[:30])
        else:
            # 値下がりが無い日（集計の初日など）は、今日の安い順を出しておく
            items = [{**e, "price": e["prices"][today]} for e in history.values()
                     if e["category"] == key and today in e["prices"]]
            items.sort(key=lambda x: x["price"])
            body = '<p class="note">今日の値下がりはありません。参考に、安い順に表示しています。</p>'
            body += "".join(card(e) for e in items[:12])
        sections.append(f'<section><h2>{cat["label"]}</h2><div class="grid">{body}</div></section>')

    page = (ROOT / "template.html").read_text(encoding="utf-8")
    page = (page.replace("{{DATE}}", today)
                .replace("{{COUNT}}", str(len(drops)))
                .replace("{{SECTIONS}}", "".join(sections)))
    (ROOT / "index.html").write_text(page, encoding="utf-8")
    print(f"{today}: 値下がり {len(drops)} 件")


if __name__ == "__main__":
    main()

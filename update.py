"""S19 攻略站每日更新：抓 tft.ninja → 比對變動 → 翻譯新文字 → 產生 docs/index.html。
用法：python3 update.py [--no-scrape] [--no-translate]
"""
import datetime, hashlib, html, json, os, re, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
RAW, ZH, CHANGES = ROOT / "data/raw.json", ROOT / "data/zh.json", ROOT / "data/changes.json"
GLOSSARY = ROOT / "data/glossary.json"  # 官方繁中譯名：champions（Data Dragon／TFT 官方資料）、items（TFT 官方資料）
BASE = "https://tft.ninja/sets/19"
UA = {"User-Agent": "Mozilla/5.0 (personal TFT guide; daily)"}


def lines(url):
    for attempt in range(3):
        try:
            t = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode()
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5)
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    return [l.strip() for l in html.unescape(re.sub(r"<[^>]+>", "\n", t)).split("\n") if l.strip()]


def parse_unit(L):
    i = L.index("TFT Set 19")
    r = L.index("Range", i)
    ai = L.index("Ability", r)
    head = L[r + 3:ai]
    stop = next(k for k, x in enumerate(head) if x in ("Effects", "PBE stats in the Explorer"))
    eff = [x for x in head[stop + 1:] if not x.startswith(",") and x not in ("PBE stats in the Explorer", "→")] if head[stop] == "Effects" else []
    j, mana = ai + 2, ""
    if L[j].startswith("Mana:"):
        mana = L[j][5:].strip(); j += 1
    desc = []
    while L[j] != "Traits":
        desc.append(L[j]); j += 1
    unlock = []
    if "How to unlock" in desc:
        k = desc.index("How to unlock"); unlock, desc = desc[k + 1:], desc[:k]
    u = dict(name=L[i - 1], cost=int(L[L.index("Cost", i) + 1].split("-")[0]), role=L[L.index("Role", i) + 1],
             range=L[r + 1], traits=head[:stop], effects=eff, ability=L[ai + 1], mana=mana, desc=desc, unlock=unlock)
    traits = {}
    for t in u["traits"]:
        a = L.index(t, j)
        end = f"Other {t} champions"
        e = L.index(end, a) if end in L[a:] else next(k for k in range(a + 1, len(L)) if L[k].startswith("Other ") or L[k] == "← Previous")
        traits[t] = L[a + 1:e]
    return u, traits


def parse_augments(L):
    out = []
    for tier, zh in (("Silver", "銀"), ("Gold", "金"), ("Prismatic", "稜彩")):
        k = L.index(f"{tier} augments")
        n = int(L[k + 1].strip("()")); k += 2
        for _ in range(n):
            name = L[k]; k += 1; cats = []
            while L[k + 1].startswith(", show"):
                cats.append(L[k]); k += 2
            a = dict(tier=tier, name=name, cat=" / ".join(cats), desc=L[k], origin=""); k += 1
            if re.search(r", 20\d\d$", L[k]):  # 回鍋增幅的出處註記，例："Space Gods Tactician's Crown, 2026"
                a["origin"] = L[k]; k += 1
            out.append(a)
    assert sum(a["cat"].count("Emblem") for a in out) == sum(x == ", show Emblem augments" for x in L) - 1, "增幅分類對不上"
    return out


def scrape():
    slugs = sorted(set(re.findall(r'href="/sets/19/units/([^"]+)"', urllib.request.urlopen(urllib.request.Request(f"{BASE}/units", headers=UA), timeout=30).read().decode())))
    units, traits = [], {}
    for s in slugs:
        u, tr = parse_unit(lines(f"{BASE}/units/{s}"))
        u["slug"] = s
        units.append(u); traits.update({k: v for k, v in tr.items() if k not in traits})
        time.sleep(0.4)
    augs = parse_augments(lines(f"{BASE}/augments"))
    # 抓不完整就整輪作廢，不覆寫舊資料（「沒拋例外」不等於成功）
    assert len(units) >= 90 and all(u["ability"] and u["traits"] for u in units), f"units 不完整：{len(units)}"
    assert len(augs) >= 300 and all(a["desc"] for a in augs), f"augments 不完整：{len(augs)}"
    units.sort(key=lambda u: (u["cost"], u["name"]))
    return dict(units=units, traits=dict(sorted(traits.items())), augments=augs)


def fetch_assets(raw):
    """S19 戰棋圖（tft.ninja 轉存的 Riot 素材）：立繪、頭像、技能圖示、羈絆圖示。已下載的不重抓。"""
    jobs = [(k, u["slug"], "webp") for u in raw["units"] for k in ("splash", "champions", "abilities")]
    jobs += [("traits", tslug(t), "svg") for t in raw["traits"]]
    missing = []
    for kind, slug, ext in jobs:
        f = ROOT / f"docs/img/{kind}/{slug}.{ext}"
        if f.exists():
            continue
        try:
            d = urllib.request.urlopen(urllib.request.Request(f"https://tft.ninja/img/sets/19/{kind}/{slug}.{ext}", headers=UA), timeout=30).read()
        except Exception:
            d = b""
        if (ext == "webp" and d[8:12] == b"WEBP") or (ext == "svg" and b"<svg" in d[:200]):
            f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(d)
        else:
            missing.append(f"{kind}/{slug}")
        time.sleep(0.2)
    if missing:
        print(f"圖片缺 {len(missing)} 張：{missing[:10]}", file=sys.stderr)


tslug = lambda t: re.sub(r"[^a-z0-9]+", "-", t.lower().replace("&", " and ")).strip("-")


def diff(old, new):
    out = []
    ou, nu = {u["name"]: u for u in old.get("units", [])}, {u["name"]: u for u in new["units"]}
    for n in nu.keys() - ou.keys(): out.append(dict(kind="棋子", name=n, field="新增"))
    for n in ou.keys() - nu.keys(): out.append(dict(kind="棋子", name=n, field="移除"))
    for n in nu.keys() & ou.keys():
        for f in ("cost", "role", "range", "traits", "mana", "ability", "desc", "unlock"):
            if ou[n][f] != nu[n][f]:
                out.append(dict(kind="棋子", name=n, field=f, old=ou[n][f], new=nu[n][f]))
    ot, nt = old.get("traits", {}), new["traits"]
    for t in nt:
        if t in ot and ot[t] != nt[t]: out.append(dict(kind="羈絆", name=t, field="內容", old=ot[t], new=nt[t]))
        elif t not in ot: out.append(dict(kind="羈絆", name=t, field="新增"))
    oa, na = {a["name"]: a for a in old.get("augments", [])}, {a["name"]: a for a in new["augments"]}
    for n in na.keys() - oa.keys(): out.append(dict(kind="增幅", name=n, field="新增", new=na[n]["desc"]))
    for n in oa.keys() - na.keys(): out.append(dict(kind="增幅", name=n, field="移除", old=oa[n]["desc"]))
    for n in na.keys() & oa.keys():
        for f in ("tier", "desc"):
            if oa[n][f] != na[n][f]: out.append(dict(kind="增幅", name=n, field=f, old=oa[n][f], new=na[n][f]))
    return sorted(out, key=lambda c: (c["kind"], c["name"]))


def strings(raw, changes):
    s = set()
    for u in raw["units"]:
        s.update([u["name"], u["ability"], *u["desc"], *u["unlock"]])
    for L in raw["traits"].values():
        s.update(x for x in L if not x.isdigit())
    for a in raw["augments"]:
        s.update([a["name"], a["desc"]])
    for day in changes:
        for c in day["items"]:
            for v in (c.get("old"), c.get("new")):
                if isinstance(v, str): s.add(v)
                elif isinstance(v, list): s.update(x for x in v if isinstance(x, str) and not x.isdigit())
    return s


key = lambda s: hashlib.sha1(s.encode()).hexdigest()[:16]

PROMPT = """你是聯盟戰棋（TFT）台灣玩家社群的譯者。把下面 JSON 陣列裡每個英文字串翻成台灣繁體中文，輸出一個同樣長度、同樣順序的 JSON 字串陣列，只輸出 JSON。
規則：
- 英雄名、裝備名一律照下方「官方譯名對照」翻；對照沒列的英雄名保留英文原名。
- 羈絆名與專有歌名保留英文原文；增幅裝置名請意譯成簡短中文。
- 遊戲術語：Mana 魔力、Shield 護盾、Damage Amp 增傷、Durability 耐久、Omnivamp 全能吸血、Attack Speed 攻速、Ability Power 法術強度、Attack Damage 攻擊力、Armor 護甲、Magic Resist 魔抗、Stun 暈眩、Knock up 擊飛、Wound 重傷、Burn 灼燒、Shred 破魔、Sunder 破甲、Vulnerable 易傷、Precision 精準、reroll 刷新、Tactician／Player Health 玩家血量、Emblem 紋章、component 散件、completed item 成裝、Tank 坦克、Fighter 戰士、Caster 施法者、Marksman 射手、Assassin 刺客、Specialist 特化。
- 開頭的 Passive:／Active:／A-Side:／B-Side: 保留原樣再接譯文。
- 句子簡潔、照原意，不增減資訊。

"""


def glossary_for(batch, glos):
    text = "\n".join(batch)
    hits = {en: zh for en, zh in glos.items() if re.search(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", text)}
    return "官方譯名對照：\n" + "\n".join(f"{en} → {zh}" for en, zh in sorted(hits.items())) + "\n\n" if hits else ""


def flat_glossary():
    g = json.loads(GLOSSARY.read_text())
    return {**{n.split(" (")[0]: z.split("（")[0] for n, z in g["champions"].items()}, **g["items"]}


def ensure_champion_names(raw):
    """新棋子沒有官方譯名時，到 Data Dragon 補；還是找不到就留給 AI 翻並提醒。"""
    g = json.loads(GLOSSARY.read_text())
    missing = [u["name"] for u in raw["units"] if u["name"] not in g["champions"]]
    if not missing:
        return
    get = lambda u: json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read())
    v = get("https://ddragon.leagueoflegends.com/api/versions.json")[0]
    en, zh = (get(f"https://ddragon.leagueoflegends.com/cdn/{v}/data/{l}/champion.json")["data"] for l in ("en_US", "zh_TW"))
    dd = {en[k]["name"]: zh[k]["name"] for k in en}
    for n in missing:
        base, _, tag = n.partition(" (")
        if base in dd:
            g["champions"][n] = dd[base] + (f"（{tag.rstrip(')')}）" if tag else "")
    GLOSSARY.write_text(json.dumps(g, ensure_ascii=False, indent=1))
    still = [n for n in missing if n not in g["champions"]]
    if still:
        print(f"找不到官方譯名（改由 AI 翻）：{still}", file=sys.stderr)


def translate(todo):
    out = {}
    glos = flat_glossary()
    with tempfile.TemporaryDirectory() as cwd:  # 空資料夾跑 claude -p，避免載入專案 CLAUDE.md
        for i in range(0, len(todo), 60):
            batch = todo[i:i + 60]
            for attempt in range(2):
                r = subprocess.run(["claude", "-p", "--model", "sonnet", "--tools", ""], input=PROMPT + glossary_for(batch, glos) + json.dumps(batch, ensure_ascii=False),
                                   cwd=cwd, capture_output=True, text=True, timeout=600)
                m = re.search(r"\[.*\]", r.stdout, re.S)
                try:
                    zh = json.loads(m.group(0)) if m else None
                except json.JSONDecodeError:
                    zh = None
                if isinstance(zh, list) and len(zh) == len(batch) and all(isinstance(z, str) for z in zh):
                    out.update({key(e): z for e, z in zip(batch, zh)})
                    break
                print(f"翻譯批次 {i} 第 {attempt + 1} 次格式不符：rc={r.returncode} out={r.stdout[:300]!r} err={r.stderr[:300]!r}", file=sys.stderr)
            print(f"翻譯 {min(i + 60, len(todo))}/{len(todo)}", file=sys.stderr)
    return out


def build(raw, zh, changes):
    names = json.loads(GLOSSARY.read_text())["champions"]
    t = lambda s: names.get(s) or zh.get(key(s), "")
    tv = lambda v: t(v) if isinstance(v, str) else [t(x) if isinstance(x, str) and not x.isdigit() else x for x in v] if isinstance(v, list) else v
    changes = [dict(d, items=[dict(c, old_zh=tv(c.get("old")), new_zh=tv(c.get("new")), name_zh=t(c["name"])) for c in d["items"]]) for d in changes]
    data = dict(
        units=[dict(u, name_zh=t(u["name"]), ability_zh=t(u["ability"]), desc_zh=[t(x) for x in u["desc"]], unlock_zh=[t(x) for x in u["unlock"]]) for u in raw["units"]],
        traits={k: [[x, "" if x.isdigit() else t(x)] for x in L] for k, L in raw["traits"].items()},
        augments=[dict(a, name_zh=t(a["name"]), desc_zh=t(a["desc"])) for a in raw["augments"]],
        changes=changes[-30:], updated=raw["fetched"],
        glossary=flat_glossary(),
    )
    page = (ROOT / "template.html").read_text().replace("/*DATA*/", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (ROOT / "docs/index.html").write_text(page)


def selftest():
    u = dict(name="X", cost=1, role="r", range="1", traits=["T"], mana="0/30", ability="a", desc=["d"], unlock=[])
    old = dict(units=[u], traits={"T": ["2", "x"]}, augments=[dict(name="A", tier="Gold", desc="d")])
    new = json.loads(json.dumps(old)); new["units"][0]["mana"] = "0/40"; new["augments"].append(dict(name="B", tier="Silver", desc="e"))
    got = {(c["kind"], c["name"], c["field"]) for c in diff(old, new)}
    assert got == {("棋子", "X", "mana"), ("增幅", "B", "新增")}, got
    assert diff(old, old) == []
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    old =json.loads(RAW.read_text()) if RAW.exists() else {}
    zh = json.loads(ZH.read_text()) if ZH.exists() else {}
    changes = json.loads(CHANGES.read_text()) if CHANGES.exists() else []
    raw = old
    if "--no-scrape" not in sys.argv:
        raw = scrape()
        raw["fetched"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        items = diff(old, raw) if old else []
        if items:
            changes.append(dict(date=raw["fetched"][:10], items=items))
        RAW.write_text(json.dumps(raw, ensure_ascii=False, indent=1))
        CHANGES.write_text(json.dumps(changes, ensure_ascii=False, indent=1))
        ensure_champion_names(raw)
        fetch_assets(raw)
        print(f"抓取完成：{len(raw['units'])} 棋子、{len(raw['traits'])} 羈絆、{len(raw['augments'])} 增幅；變動 {len(items)} 項")
    todo = sorted(s for s in strings(raw, changes) if key(s) not in zh)
    if todo and "--no-translate" not in sys.argv:
        zh.update(translate(todo))
        ZH.write_text(json.dumps(zh, ensure_ascii=False, indent=0, sort_keys=True))
    print(f"未翻譯：{sum(key(s) not in zh for s in todo)} 條")
    build(raw, zh, changes)


if __name__ == "__main__":
    main()

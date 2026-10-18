"""Monta as playlists pessoais a partir do iptv-org (+ Pluto TV Brasil).

Uso:  python build.py [--strict] [--no-test] [--out DIR]
  --strict   descarta também streams que respondem 403/451 (usar rodando do Brasil)
  --no-test  não testa os streams (mais rápido)

Gera:
  brasil.m3u    -> Brasil + Portugal/lusófonos + Pluto TV BR
  completa.m3u  -> tudo acima + canais do mundo inteiro, agrupados por país
"""
import concurrent.futures as cf
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

API = "https://iptv-org.github.io/api/"
PLUTO = "https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master/PlutoTV/.channels.json.gz"
PLUTO_EPG = "https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master/PlutoTV/br.xml.gz"
COUNTRY_PT = "https://raw.githubusercontent.com/umpirsky/country-list/master/data/pt_BR/country.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

BR = "🇧🇷"
# ordem em que os grupos do Brasil aparecem
BR_GROUPS = ["Abertos e Regionais", "Notícias", "Esportes", "Filmes", "Séries", "Novelas",
             "Infantil", "Variedades", "Documentários e Natureza", "Música",
             "Educativos e Públicos", "Religiosos", "Compras", "Outros"]
CAT_TO_GROUP = [  # primeira categoria que bater define o grupo
    ("movies", "Filmes"), ("series", "Séries"), ("kids", "Infantil"), ("animation", "Infantil"),
    ("sports", "Esportes"), ("news", "Notícias"), ("business", "Notícias"), ("weather", "Notícias"),
    ("music", "Música"), ("religious", "Religiosos"), ("shop", "Compras"),
    ("legislative", "Educativos e Públicos"), ("education", "Educativos e Públicos"),
    ("public", "Educativos e Públicos"), ("culture", "Educativos e Públicos"),
    ("documentary", "Documentários e Natureza"), ("outdoor", "Documentários e Natureza"),
    ("travel", "Documentários e Natureza"), ("science", "Documentários e Natureza"),
    ("general", "Abertos e Regionais"),
    ("entertainment", "Variedades"), ("comedy", "Variedades"), ("classic", "Variedades"),
    ("family", "Variedades"), ("lifestyle", "Variedades"), ("cooking", "Variedades"),
    ("auto", "Variedades"), ("relax", "Variedades"), ("interactive", "Variedades"),
]
LUSO = {"PT": "🇵🇹 Portugal", "AO": "🇦🇴 Angola", "MZ": "🇲🇿 Moçambique", "CV": "🇨🇻 Cabo Verde",
        "GW": "🇬🇼 Guiné-Bissau", "ST": "🇸🇹 São Tomé e Príncipe", "TL": "🇹🇱 Timor-Leste",
        "MO": "🇲🇴 Macau"}
PLUTO_GROUP = {"Novelas": f"{BR} Novelas"}  # novelas do Pluto entram junto das brasileiras
QUALITY = {"2160p": 6, "1440p": 5, "1080p": 4, "1080i": 4, "720p": 3, "576p": 2, "576i": 2,
           "480p": 1, "480i": 1}


def get(url, cache):
    path = os.path.join(cache, hashlib.md5(url.encode()).hexdigest()[:12] + "_" + url.rsplit("/", 1)[1])
    if not os.path.exists(path) or time.time() - os.path.getmtime(path) > 6 * 3600:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
            f.write(r.read())
    with open(path, "rb") as f:
        data = f.read()
    if url.endswith(".gz"):
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def probe(s, strict):
    """True se o stream respondeu de forma utilizável."""
    headers = {"User-Agent": s.get("user_agent") or UA}
    if s.get("referrer"):
        headers["Referer"] = s["referrer"]
    try:
        req = urllib.request.Request(s["url"], headers=headers)
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read(2048)
            ctype = (r.headers.get("Content-Type") or "").lower()
        if b"#EXTM3U" in body[:64] or "mpegurl" in ctype or "video" in ctype or "octet" in ctype:
            return True
        return body[:1] == b"G"  # pacote MPEG-TS
    except urllib.error.HTTPError as e:
        return (not strict) and e.code in (401, 403, 451)
    except Exception:
        return False


def entry(name, url, group, tvg_id="", logo="", ua=None, ref=None):
    name = name.replace(",", " ")
    lines = [f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}']
    if ua:
        lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
    if ref:
        lines.append(f"#EXTVLCOPT:http-referrer={ref}")
    lines.append(url)
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    strict, test = "--strict" in args, "--no-test" not in args
    out = args[args.index("--out") + 1] if "--out" in args else os.path.dirname(os.path.abspath(__file__))
    cache = os.path.join(out, ".cache")
    os.makedirs(cache, exist_ok=True)

    channels = {c["id"]: c for c in get(API + "channels.json", cache)}
    feeds = {(f["channel"], f["id"]): f for f in get(API + "feeds.json", cache)}
    streams = get(API + "streams.json", cache)
    blocked = {b["channel"] for b in get(API + "blocklist.json", cache)}
    countries = {c["code"]: c for c in get(API + "countries.json", cache)}
    try:
        pt_names = get(COUNTRY_PT, cache)
    except Exception:
        pt_names = {}
    logos = {}
    for l in get(API + "logos.json", cache):
        logos.setdefault((l["channel"], l.get("feed")), l["url"])
        logos.setdefault((l["channel"], None), l["url"])

    # --- iptv-org: filtra, testa, escolhe o melhor stream de cada canal/feed ---
    cands = []
    for s in streams:
        cid = s.get("channel")
        if "jmp2.uk/plu-" in s["url"]:
            continue  # Pluto vem da fonte própria, já filtrada para o Brasil
        c = channels.get(cid)
        if c and (c.get("is_nsfw") or c.get("closed") or cid in blocked or "xxx" in c["categories"]):
            continue
        cands.append(s)
    print(f"{len(cands)} streams candidatos", flush=True)

    if test:
        ok = [False] * len(cands)
        with cf.ThreadPoolExecutor(128) as ex:
            futs = {ex.submit(probe, s, strict): i for i, s in enumerate(cands)}
            for n, fut in enumerate(cf.as_completed(futs), 1):
                ok[futs[fut]] = fut.result()
                if n % 1000 == 0:
                    print(f"  testados {n}/{len(cands)} - vivos {sum(ok)}", flush=True)
        cands = [s for s, k in zip(cands, ok) if k]
        print(f"{len(cands)} streams vivos", flush=True)

    best = {}
    for s in cands:
        key = (s.get("channel"), s.get("feed")) if s.get("channel") else ("url", s["url"])
        q = QUALITY.get(s.get("quality") or "", 0) + (0.5 if s["url"].startswith("https") else 0)
        if key not in best or q > best[key][0]:
            best[key] = (q, s)

    br_items, luso_items, world_items = {}, {}, {}
    for (cid, fid), (_, s) in best.items():
        c = channels.get(cid) if cid != "url" else None
        if c:
            f = feeds.get((cid, fid)) if fid else None
            name = c["name"] + (f" ({f['name']})" if f and not f.get("is_main") else "")
            if s.get("quality"):
                name += f" [{s['quality']}]"
            country, cats = c["country"], c["categories"]
            langs = (f or {}).get("languages") or []
            logo = logos.get((cid, fid)) or logos.get((cid, None), "")
            tvg = cid + (f"@{fid}" if fid else "")
        else:
            name, country, cats, langs, logo, tvg = s["title"], None, [], [], "", ""
        e = (name, s["url"], tvg, logo, s.get("user_agent"), s.get("referrer"))

        if country == "BR":
            grp = next((g for cat, g in CAT_TO_GROUP if cat in cats), "Abertos e Regionais")
            if re.search(r"novela", name, re.I):
                grp = "Novelas"
            br_items.setdefault(f"{BR} {grp}", []).append(e)
        elif country in LUSO or "por" in langs:
            grp = LUSO.get(country, "🌍 Outros em português")
            luso_items.setdefault(grp, []).append(e)
        else:
            cc = countries.get(country or "", {})
            label = f"{cc.get('flag', '🌎')} {pt_names.get(country, cc.get('name', 'Sem país'))}" if cc else "🌎 Sem classificação"
            world_items.setdefault(label, []).append(e)

    # --- Pluto TV Brasil ---
    pluto_items = {}
    try:
        pluto = get(PLUTO, cache)["regions"]["br"]["channels"]
        for pid, p in sorted(pluto.items(), key=lambda kv: int(kv[1].get("chno") or 9999)):
            grp = PLUTO_GROUP.get(p.get("group"), f"📺 Pluto TV | {p.get('group') or 'Outros'}")
            e = (p["name"], f"https://jmp2.uk/plu-{pid}.m3u8", pid, p.get("logo", ""), None, None)
            (br_items if grp.startswith(BR) else pluto_items).setdefault(grp, []).append(e)
    except Exception as ex:
        print("Pluto indisponível:", ex)

    def render(groups, order=None):
        keys = order or sorted(groups, key=lambda k: re.sub(r"^\W+", "", k))
        res = []
        for g in keys:
            for n, u, t, l, ua, rf in sorted(groups.get(g, []), key=lambda x: x[0].lower()):
                res.append(entry(n, u, g, t, l, ua, rf))
        return res

    br_order = [f"{BR} {g}" for g in BR_GROUPS]
    luso_order = [LUSO[k] for k in LUSO] + ["🌍 Outros em português"]
    head = f'#EXTM3U url-tvg="{PLUTO_EPG}"'
    main_part = render(br_items, br_order) + render(luso_items, luso_order) + render(pluto_items)
    full = main_part + render(world_items)

    for fname, body in (("brasil.m3u", main_part), ("completa.m3u", full)):
        with open(os.path.join(out, fname), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(head + "\n" + "\n".join(body) + "\n")
        print(f"{fname}: {len(body)} canais")
    summary = {g: len(v) for d in (br_items, luso_items, pluto_items) for g, v in d.items()}
    summary["(mundo - países)"] = len(world_items)
    summary["(mundo - canais)"] = sum(len(v) for v in world_items.values())
    with open(os.path.join(out, "resumo.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

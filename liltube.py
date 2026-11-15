import json
import os
import re
import struct
import sys
import webbrowser
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# your channels, by @handle
CHANNELS = {
    "maths": ["3blue1brown", "Mathologer", "standupmaths", "numberphile", "Reducible", "Mathemaniac", "aleph0", "MichaelPennMath"],
    "physics": ["pbsspacetime", "SteveMould", "minutephysics", "veritasium", "AlphaPhoenixChannel", "sixtysymbols", "ScienceClic", "PhysicsExplainedVideos"],
    "ml": ["AndrejKarpathy", "WelchLabs", "SebastianLague", "YannicKilcher", "umarjamilai", "AICoffeeBreak", "bycloudAI"],
    "building": ["StuffMadeHere", "AppliedScience", "BreakingTaps", "smartereveryday", "BenEater", "jamesbruton", "ThisOldTony", "TechIngredients", "HuygensOptics", "CNLohr", "DIYPerks", "greatscottlab", "ElectroBOOM", "TechnologyConnections", "PracticalEngineeringChannel", "HTXStudio"],
    "rockets": ["BPSspace", "scottmanley", "EverydayAstronaut", "BrianBDouglas", "Eigensteve"],
    "film": ["ThomasFlight", "LikeStoriesofOld", "LessonsfromtheScreenplay", "JacobGeller", "Nerdwriter1", "KaptainKristian", "NowYouSeeIt"],
    "cool": ["PrimerBlobs", "RealEngineering", "kurzgesagt", "NileRed", "CodeParade", "CGPGrey", "Wendoverproductions", "TomScottGo", "Asianometry", "BranchEducation", "Acerola_t", "EmergentGarden"],
    "fun": ["whirow", "Ididathing", "MichaelReeves", "WilliamOsman", "AllenPan", "DougDoug", "jetlagthegame", "CaptainDisillusion", "answerinprogress"],
}

SITE = "https://navthings.github.io/liltube/"
KEEP = 60
HOME = Path(os.environ.get("LILTUBE_DIR", Path.home() / ".liltube"))
IDS = HOME / "ids.json"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en"}
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "m": "http://search.yahoo.com/mrss/",
}


def get(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode()


def channel_id(handle: str, cache: dict) -> str:
    if handle in cache:
        return cache[handle]
    page = get(f"https://www.youtube.com/@{handle}")
    m = re.search(r'youtube\.com/channel/(UC[\w-]{22})', page) or re.search(r'"externalId":"(UC[\w-]{22})"', page)
    if not m:
        raise ValueError("no channel id on page")
    cache[handle] = m.group(1)
    return cache[handle]


def parse(xml: str, cat: str, handle: str) -> list[dict]:
    root = ET.fromstring(xml)
    channel = root.findtext("a:title", namespaces=NS)
    vids = []
    for e in root.findall("a:entry", NS):
        link = e.find("a:link", NS).get("href", "")
        title = e.findtext("a:title", namespaces=NS) or ""
        # no shorts
        if "/shorts/" in link or "#shorts" in title.lower():
            continue
        stats = e.find("m:group/m:community/m:statistics", NS)
        # no sponsor links
        lines = (e.findtext("m:group/m:description", namespaces=NS) or "").splitlines()
        desc = re.sub(r"\n{3,}", "\n\n", "\n".join(l for l in lines if "http" not in l).strip())
        if len(desc) > 320:
            desc = desc[:320].rsplit(" ", 1)[0] + "…"
        vids.append({
            "id": e.findtext("yt:videoId", namespaces=NS),
            "title": title,
            "channel": channel,
            "h": handle,
            "cat": cat,
            "date": e.findtext("a:published", namespaces=NS),
            "views": int(stats.get("views", 0)) if stats is not None else 0,
            "desc": desc,
            "dur": "",
        })
    return vids


def lengths(page: str) -> dict:
    out = {}
    for chunk in page.split('"lockupViewModel":')[1:]:
        vid = re.search(r'"contentId":"([\w-]{11})"', chunk)
        badge = re.search(r'"thumbnailBadgeViewModel":\{"text":"(\d+(?::\d+)+)"', chunk)
        if vid and badge:
            out.setdefault(vid.group(1), badge.group(1))
    return out


def fetch(handle: str, cat: str, cache: dict) -> tuple[list[dict], dict | None]:
    try:
        cid = channel_id(handle, cache)
        vids = parse(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"), cat, handle)
    except Exception as err:
        print(f"  skipped @{handle}: {err}")
        return [], None
    meta = {"name": vids[0]["channel"] if vids else handle, "avatar": ""}
    # lengths + avatar
    try:
        page = get(f"https://www.youtube.com/channel/{cid}/videos")
        durs = lengths(page)
        for v in vids:
            v["dur"] = durs.get(v["id"], "")
        av = re.search(r'<meta property="og:image" content="([^"]+)"', page)
        meta["avatar"] = av.group(1) if av else ""
    except Exception as err:
        print(f"  no lengths for @{handle}: {err}")
    return vids, meta


def previous() -> dict:
    f = HOME / "data.json"
    try:
        return json.loads(f.read_text()) if f.exists() else json.loads(get(SITE + "data.json"))
    except Exception as err:
        print(f"  no archive yet: {err}")
        return {}


def merge(new: list[dict], old: dict, cats: dict) -> list[dict]:
    byid = {v["id"]: v for v in old.get("vids", []) if v.get("h") in cats}
    for v in new:
        v["dur"] = v["dur"] or byid.get(v["id"], {}).get("dur", "")
        byid[v["id"]] = v
    out, count = [], {}
    for v in sorted(byid.values(), key=lambda v: v["date"], reverse=True):
        v["cat"] = cats[v["h"]]
        count[v["h"]] = count.get(v["h"], 0) + 1
        if count[v["h"]] <= KEEP:
            out.append(v)
    return out


def png(size: int) -> bytes:
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            u, v = x / size, y / size
            play = 0.38 <= u <= 0.74 and abs(v - 0.5) <= (0.74 - u) / 0.36 * 0.22
            rows += bytes((255, 255, 255) if play else (47, 91, 234))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


def build(vids: list[dict], metas: dict):
    old = previous()
    cats = {h: c for c, hs in CHANNELS.items() for h in hs}
    channels = {h: m for h, m in old.get("channels", {}).items() if h in cats}
    for h, m in metas.items():
        if m and (m["avatar"] or h not in channels):
            channels[h] = m
    data = {
        "cats": list(CHANNELS),
        "built": datetime.now(timezone.utc).isoformat(),
        "channels": channels,
        "vids": merge(vids, old, cats),
    }
    (HOME / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    (HOME / "index.html").write_text(PAGE)
    (HOME / "manifest.json").write_text(json.dumps({
        "name": "liltube", "short_name": "liltube", "start_url": ".", "display": "standalone",
        "background_color": "#f5f2ec", "theme_color": "#f5f2ec",
        "icons": [{"src": "icon.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    }))
    if not (HOME / "icon.png").exists():
        (HOME / "icon.png").write_bytes(png(512))
    return len(data["vids"])


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    HOME.mkdir(parents=True, exist_ok=True)
    cache = json.loads(IDS.read_text()) if IDS.exists() else {}
    jobs = [(h, c) for c, hs in CHANNELS.items() for h in hs]
    print(f"fetching {len(jobs)} channels...")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda j: fetch(j[0], j[1], cache), jobs))
    IDS.write_text(json.dumps(cache, indent=1))
    vids = [v for r, _ in results for v in r]
    metas = {h: m for (h, _), (_, m) in zip(jobs, results)}
    print(f"{build(vids, metas)} videos")

    if "--no-serve" in sys.argv:
        return
    # localhost so embeds work
    server = HTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(HOME)))
    url = f"http://localhost:{server.server_address[1]}/"
    print(f"open at {url}  (ctrl+c to quit)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>liltube</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='9' fill='%232f5bea'/%3E%3Cpath d='M12 9.5v13l10.5-6.5z' fill='white'/%3E%3C/svg%3E">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="liltube">
<meta name="theme-color" content="#f5f2ec" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0f1218" media="(prefers-color-scheme: dark)">
<style>
  :root { --bg:#f5f2ec; --card:#e8e3da; --line:#dcd5ca; --text:#1d1b18; --dim:#6f675e; --accent:#2f5bea; --on:#fff; --hover:#b9b0a3; }
  @media (prefers-color-scheme:dark) { :root { --bg:#0f1218; --card:#1a1f29; --line:#262d3a; --text:#e9ecf2; --dim:#8a93a4; --accent:#7c9cff; --on:#0f1218; --hover:#3b4456; } }
  * { box-sizing:border-box; margin:0; }
  html { scrollbar-color:var(--line) transparent; }
  body { background:var(--bg); color:var(--text); font:15px/1.4 ui-sans-serif, system-ui, -apple-system, sans-serif; -webkit-font-smoothing:antialiased; }
  button { font:inherit; cursor:pointer; color:inherit; }
  header { position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; align-items:center; gap:12px 20px; padding:16px clamp(16px,4vw,56px); padding-top:max(16px, env(safe-area-inset-top)); background:color-mix(in srgb, var(--bg) 85%, transparent); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--line); }
  .logo { display:flex; align-items:baseline; gap:10px; }
  h1 { font:italic 600 26px/1 ui-serif, 'New York', Georgia, serif; letter-spacing:-0.02em; }
  h1::after { content:""; display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--accent); margin-left:3px; }
  .sub { color:var(--dim); font-size:12px; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  .chip { background:none; border:1px solid var(--line); color:var(--dim); padding:5px 12px; border-radius:99px; font-size:13px; transition:color .15s, border-color .15s; }
  .chip:hover { color:var(--text); border-color:var(--hover); }
  .chip.on { background:var(--text); color:var(--bg); border-color:var(--text); }
  .chip.ch { background:var(--accent); color:var(--on); border-color:var(--accent); }
  .tools { margin-left:auto; display:flex; gap:8px; align-items:center; }
  .btn { background:none; border:1px solid var(--line); color:var(--dim); padding:6px 11px; border-radius:8px; font-size:13px; white-space:nowrap; }
  .btn:hover { color:var(--text); border-color:var(--hover); }
  .btn.on { color:var(--accent); border-color:var(--accent); }
  input { background:var(--card); border:1px solid var(--line); color:var(--text); padding:7px 12px; border-radius:8px; font:inherit; font-size:13px; width:180px; outline:none; }
  input:focus { border-color:var(--accent); }
  main { padding:8px clamp(16px,4vw,56px) 90px; }
  h2 { display:flex; align-items:center; gap:12px; font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.09em; color:var(--dim); margin:34px 0 16px; }
  h2::after { content:""; flex:1; height:1px; background:var(--line); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:28px 18px; }
  .vid { cursor:pointer; min-width:0; }
  .thumb { position:relative; aspect-ratio:16/9; border-radius:11px; overflow:hidden; background:var(--card); }
  .thumb img { width:100%; height:100%; object-fit:cover; opacity:0; transition:transform .3s ease, opacity .3s; }
  .thumb img.ok { opacity:1; }
  .vid:hover .thumb img { transform:scale(1.045); }
  .vid:hover h3 { color:var(--accent); }
  .tag { position:absolute; font-size:11px; font-weight:700; padding:2px 6px; border-radius:5px; }
  .new { top:8px; left:8px; background:var(--accent); color:#fff; }
  .dur { bottom:8px; right:8px; background:rgba(0,0,0,.75); color:#fff; font-variant-numeric:tabular-nums; }
  .save { position:absolute; top:7px; right:7px; width:30px; height:30px; border:0; border-radius:8px; background:rgba(0,0,0,.6); color:#fff; font-size:15px; opacity:0; transition:opacity .15s; }
  .vid:hover .save, .save.on { opacity:1; }
  .save.on { color:#ffd54a; }
  .bar { position:absolute; left:0; bottom:0; height:3px; background:var(--accent); }
  .vid h3 { font-size:14.5px; font-weight:600; margin:10px 0 5px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
  .meta { display:flex; align-items:center; gap:7px; color:var(--dim); font-size:13px; min-width:0; }
  .meta img { width:20px; height:20px; border-radius:50%; flex-shrink:0; background:var(--card); }
  .meta .name { background:none; border:0; padding:0; color:var(--dim); font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .meta .name:hover { color:var(--accent); }
  .meta span { white-space:nowrap; }
  .vid.seen { opacity:.38; }
  .vid.seen:hover { opacity:.8; }
  .empty { color:var(--dim); padding:80px 0; text-align:center; }
  #more { height:1px; }
  #player { position:fixed; inset:0; z-index:10; display:none; overflow-y:auto; padding:clamp(16px,4vw,48px); background:#0b0d11; --text:#e9ecf2; --dim:#8a93a4; --card:#1a1f29; --line:#262d3a; --hover:#3b4456; color:var(--text); }
  #player.on { display:block; }
  .stage { max-width:1100px; margin:0 auto; }
  .screen { aspect-ratio:16/9; border-radius:14px; overflow:hidden; background:#000; }
  .screen iframe, .screen div { width:100%; height:100%; border:0; }
  .info { display:flex; gap:20px; justify-content:space-between; align-items:flex-start; margin-top:16px; }
  .info h3 { font-size:19px; font-weight:650; letter-spacing:-0.01em; }
  .info .meta { margin-top:6px; }
  .info p { color:var(--dim); font-size:13.5px; margin-top:12px; white-space:pre-line; max-width:760px; }
  .side { display:flex; flex-direction:column; gap:8px; align-items:flex-end; flex-shrink:0; }
  .row { display:flex; gap:6px; }
  .row .btn { background:var(--card); color:var(--text); }
  .row .btn.on { color:var(--accent); }
  .btn:disabled { opacity:.35; cursor:default; }
  .sb { color:var(--dim); font-size:12px; min-height:16px; }
  .hint { color:#5a6272; font-size:12px; margin-top:22px; }
  #toast { position:fixed; left:50%; bottom:28px; transform:translate(-50%, 20px); z-index:20; background:var(--text); color:var(--bg); padding:8px 14px; border-radius:99px; font-size:13px; opacity:0; transition:.25s; pointer-events:none; }
  #toast.on { opacity:1; transform:translate(-50%, 0); }
  @media (hover:none) { .save { opacity:.85; } }
  @media (max-width:760px) { .tools { margin-left:0; width:100%; } input { flex:1; width:auto; } .info { flex-direction:column; } .side { align-items:flex-start; } }
</style>
</head>
<body>
<header>
  <div class="logo"><h1>liltube</h1><span class="sub" id="sub"></span></div>
  <div class="chips" id="chips"></div>
  <div class="tools">
    <button class="btn" id="surprise">surprise me</button>
    <button class="btn" id="hide">hide watched</button>
    <input id="q" placeholder="search  /" autocomplete="off">
  </div>
</header>
<main id="feed"><p class="empty">loading…</p></main>
<div id="more"></div>
<div id="player">
  <div class="stage">
    <div class="screen" id="screen"></div>
    <div class="info">
      <div><h3 id="ptitle"></h3><div class="meta" id="pmeta"></div><p id="pdesc"></p></div>
      <div class="side">
        <div class="row"><button class="btn" id="prev">← prev</button><button class="btn" id="next">next →</button><button class="btn" id="psave">☆ save</button><button class="btn" id="x">close</button></div>
        <div class="row" id="speeds"></div>
        <div class="sb" id="sb"></div>
        <a class="sb" id="yt" target="_blank" rel="noopener">open on youtube ↗</a>
      </div>
    </div>
    <div class="hint">esc close · ← → prev / next (click outside the video first)</div>
  </div>
</div>
<div id="toast"></div>
<script>
const $ = s => document.querySelector(s);
const DAY = 86400000, PAGE = 90, SPEEDS = [1, 1.25, 1.5, 2];
let D, cat = "all", ch = null, hide = false, speed = 1, limit = PAGE;
let seen = new Set(), saved = new Set(), prog = {};
let shown = [], queue = [], qi = -1, yt = null, apiReady = null, segs = [], curId = null;

const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
const store = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
const save = () => {
  store("seen", [...seen]); store("saved", [...saved]); store("prog", prog);
  store("prefs", { cat, hide, speed });
};
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));
const ago = d => {
  const s = (Date.now() - new Date(d)) / 1000;
  for (const [n, u] of [[31536000,"y"],[2592000,"mo"],[604800,"w"],[86400,"d"],[3600,"h"],[60,"m"]])
    if (s >= n) return Math.floor(s / n) + u + " ago";
  return "just now";
};
const views = n => n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : n;
const clock = t => { t = Math.floor(t); const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = String(t % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`; };
const bucket = d => {
  const t = Date.now() - new Date(d);
  return t < DAY ? "today" : t < 2 * DAY ? "yesterday" : t < 7 * DAY ? "this week" : t < 30 * DAY ? "this month" : "older";
};
const chan = v => D.channels[v.h] || { name: v.channel, avatar: "" };
const started = id => prog[id] && prog[id][0] > 30 && prog[id][0] < prog[id][1] - 60;

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("on");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("on"), 2200);
}

function chips() {
  let h = ["all", "saved", ...D.cats].map(c =>
    `<button class="chip ${c === cat && !ch ? "on" : ""}" data-c="${c}">${c === "saved" ? `★ saved${saved.size ? " " + saved.size : ""}` : c}</button>`).join("");
  if (ch) h += `<button class="chip ch" data-x="1">${esc(D.channels[ch]?.name || ch)} ×</button>`;
  $("#chips").innerHTML = h;
  $("#hide").classList.toggle("on", hide);
}

function card(v, i) {
  const c = chan(v), p = prog[v.id];
  const fresh = Date.now() - new Date(v.date) < 2 * DAY && !seen.has(v.id);
  return `<article class="vid ${seen.has(v.id) && !started(v.id) ? "seen" : ""}" data-i="${i}">
    <div class="thumb"><img loading="lazy" src="https://i.ytimg.com/vi/${v.id}/hqdefault.jpg" alt="" onload="this.classList.add('ok')">
      ${fresh ? '<span class="tag new">new</span>' : ""}${v.dur ? `<span class="tag dur">${v.dur}</span>` : ""}
      <button class="save ${saved.has(v.id) ? "on" : ""}" data-s="${v.id}" title="save">${saved.has(v.id) ? "★" : "☆"}</button>
      ${p && p[1] ? `<div class="bar" style="width:${Math.min(100, p[0] / p[1] * 100)}%"></div>` : ""}</div>
    <h3>${esc(v.title)}</h3>
    <div class="meta">${c.avatar ? `<img src="${esc(c.avatar)}" alt="" loading="lazy">` : ""}<button class="name" data-h="${esc(v.h)}">${esc(c.name)}</button><span>· ${views(v.views)} · ${ago(v.date)}</span></div>
  </article>`;
}

function filtered() {
  const q = $("#q").value.trim().toLowerCase();
  return D.vids.filter(v =>
    (ch ? v.h === ch : cat === "all" || (cat === "saved" ? saved.has(v.id) : v.cat === cat)) &&
    !(hide && seen.has(v.id) && !started(v.id)) &&
    (!q || (v.title + " " + chan(v).name).toLowerCase().includes(q)));
}

function render() {
  const all = filtered(), q = $("#q").value.trim();
  const resume = cat === "all" && !ch && !q ? D.vids.filter(v => started(v.id)).sort((a, b) => prog[b.id][2] - prog[a.id][2]).slice(0, 8) : [];
  shown = [...resume, ...all.slice(0, limit)];
  let h = "", last = null;
  shown.forEach((v, i) => {
    const b = i < resume.length ? "continue watching" : bucket(v.date);
    if (b !== last) { h += (last ? "</div>" : "") + `<h2>${b}</h2><div class="grid">`; last = b; }
    h += card(v, i);
  });
  $("#feed").innerHTML = last ? h + "</div>" : `<p class="empty">${cat === "saved" ? "nothing saved yet. tap ☆ on a video" : "nothing here"}</p>`;
  const week = D.vids.filter(v => Date.now() - new Date(v.date) < 7 * DAY && !seen.has(v.id)).length;
  $("#sub").textContent = `${week} new this week · updated ${ago(D.built)}`;
  render.more = all.length > limit;
}

function api() {
  return apiReady ||= new Promise(res => {
    window.onYouTubeIframeAPIReady = res;
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    document.head.append(s);
  });
}

async function play(list, i) {
  if (i < 0 || i >= list.length) return;
  queue = list; qi = i;
  const v = list[i], c = chan(v), p = prog[v.id];
  curId = v.id; segs = [];
  $("#ptitle").textContent = v.title;
  $("#pmeta").innerHTML = `${c.avatar ? `<img src="${esc(c.avatar)}" alt="">` : ""}<span>${esc(c.name)} · ${views(v.views)} views · ${ago(v.date)}${v.dur ? " · " + v.dur : ""}</span>`;
  $("#pdesc").textContent = v.desc;
  $("#yt").href = `https://www.youtube.com/watch?v=${v.id}`;
  $("#prev").disabled = i === 0;
  $("#next").disabled = i === list.length - 1;
  $("#psave").textContent = saved.has(v.id) ? "★ saved" : "☆ save";
  $("#sb").textContent = "";
  $("#player").classList.add("on");
  $("#player").scrollTop = 0;
  document.body.style.overflow = "hidden";
  seen.add(v.id); save();

  // sponsorblock
  fetch(`https://sponsor.ajay.app/api/skipSegments?videoID=${v.id}&categories=${encodeURIComponent('["sponsor","selfpromo","interaction"]')}`)
    .then(r => r.ok ? r.json() : []).then(j => {
      if (curId !== v.id) return;
      segs = j.map(s => s.segment);
      $("#sb").textContent = segs.length ? `skipping ${segs.length} sponsor bit${segs.length > 1 ? "s" : ""}` : "no sponsor bits found";
    }).catch(() => {});

  const start = p && p[0] < p[1] - 30 ? Math.floor(p[0]) : 0;
  await api();
  if (curId !== v.id) return;
  if (yt) { yt.destroy(); yt = null; }
  $("#screen").innerHTML = '<div id="frame"></div>';
  yt = new YT.Player("frame", {
    videoId: v.id,
    host: "https://www.youtube-nocookie.com",
    playerVars: { autoplay: 1, rel: 0, start, playsinline: 1, modestbranding: 1 },
    events: {
      onReady: e => { e.target.setPlaybackRate(speed); e.target.playVideo(); if (start) toast(`picking up at ${clock(start)}`); },
      onStateChange: e => { if (e.data === 0) { delete prog[curId]; save(); } },
    },
  });
}

function close() {
  if (!$("#player").classList.contains("on")) return;
  tick(true);
  $("#player").classList.remove("on");
  if (yt) { yt.destroy(); yt = null; }
  $("#screen").innerHTML = "";
  curId = null;
  document.body.style.overflow = "";
  render();
}

function tick(force) {
  if (!yt || !yt.getCurrentTime || !curId) return;
  const t = yt.getCurrentTime(), d = yt.getDuration();
  for (const [a, b] of segs)
    if (t >= a && t < b - 0.5) { yt.seekTo(b, true); toast(`skipped sponsor (${clock(b - a)})`); return; }
  if (d > 0 && (force || ++tick.n % 10 === 0)) {
    if (t > d - 30) delete prog[curId];
    else if (t > 5) prog[curId] = [t, d, Date.now()];
    save();
  }
}
tick.n = 0;
setInterval(tick, 500);

function setSpeed(s) {
  speed = s; save();
  if (yt && yt.setPlaybackRate) yt.setPlaybackRate(s);
  $("#speeds").innerHTML = SPEEDS.map(x => `<button class="btn ${x === speed ? "on" : ""}" data-sp="${x}">${x}×</button>`).join("");
}

function toggleSave(id) {
  saved.has(id) ? saved.delete(id) : saved.add(id);
  save(); chips();
  toast(saved.has(id) ? "saved for later" : "removed from saved");
}

function reset() { limit = PAGE; render(); scrollTo(0, 0); }

function init() {
  seen = new Set(load("seen", []));
  saved = new Set(load("saved", []));
  prog = load("prog", {});
  const p = load("prefs", {});
  if (D.cats.includes(p.cat) || p.cat === "saved") cat = p.cat;
  hide = !!p.hide;
  setSpeed(SPEEDS.includes(p.speed) ? p.speed : 1);
  chips(); render();
  new IntersectionObserver(e => {
    if (e[0].isIntersecting && render.more) { limit += PAGE; render(); }
  }, { rootMargin: "800px" }).observe($("#more"));
}

$("#chips").onclick = e => {
  const c = e.target.closest(".chip");
  if (!c) return;
  if (c.dataset.x) ch = null; else { cat = c.dataset.c; ch = null; }
  save(); chips(); reset();
};
$("#feed").onclick = e => {
  const s = e.target.closest(".save");
  if (s) { toggleSave(s.dataset.s); s.classList.toggle("on", saved.has(s.dataset.s)); s.textContent = saved.has(s.dataset.s) ? "★" : "☆"; if (cat === "saved") render(); return; }
  const n = e.target.closest(".name");
  if (n) { ch = n.dataset.h; chips(); reset(); return; }
  const v = e.target.closest(".vid");
  if (v) play(shown, +v.dataset.i);
};
$("#surprise").onclick = () => {
  const pool = D.vids.filter(v => !seen.has(v.id));
  for (let i = pool.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [pool[i], pool[j]] = [pool[j], pool[i]]; }
  pool.length ? play(pool, 0) : toast("you've watched everything. touch grass");
};
$("#hide").onclick = () => { hide = !hide; save(); chips(); reset(); };
$("#q").oninput = () => { limit = PAGE; render(); };
$("#prev").onclick = () => play(queue, qi - 1);
$("#next").onclick = () => play(queue, qi + 1);
$("#psave").onclick = () => { toggleSave(curId); $("#psave").textContent = saved.has(curId) ? "★ saved" : "☆ save"; };
$("#speeds").onclick = e => { if (e.target.dataset.sp) setSpeed(+e.target.dataset.sp); };
$("#x").onclick = close;
$("#player").onclick = e => { if (e.target.id === "player") close(); };
addEventListener("pagehide", () => tick(true));
addEventListener("keydown", e => {
  const open = $("#player").classList.contains("on");
  if (e.key === "Escape") { open ? close() : ($("#q").value = "", $("#q").blur(), reset()); }
  else if (open && e.key === "ArrowRight") play(queue, qi + 1);
  else if (open && e.key === "ArrowLeft") play(queue, qi - 1);
  else if (!open && e.key === "/" && document.activeElement !== $("#q")) { e.preventDefault(); $("#q").focus(); }
});

fetch("data.json", { cache: "no-cache" }).then(r => r.json()).then(d => { D = d; init(); })
  .catch(() => { $("#feed").innerHTML = '<p class="empty">couldn\'t load videos</p>'; });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()

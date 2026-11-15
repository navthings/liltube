import json
import os
import re
import sys
import webbrowser
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# your channels, by @handle
CHANNELS = {
    "maths": ["3blue1brown", "Mathologer", "standupmaths", "numberphile", "Reducible", "Mathemaniac", "aleph0", "MichaelPennMath"],
    "physics": ["pbsspacetime", "SteveMould", "minutephysics", "veritasium", "AlphaPhoenixChannel", "sixtysymbols", "ScienceClic", "PhysicsExplainedVideos"],
    "ml": ["AndrejKarpathy", "WelchLabs", "SebastianLague", "YannicKilcher", "umarjamilai", "AICoffeeBreak"],
    "building": ["StuffMadeHere", "AppliedScience", "BreakingTaps", "smartereveryday", "BenEater", "jamesbruton", "ThisOldTony", "TechIngredients", "HuygensOptics", "CNLohr"],
    "rockets": ["BPSspace", "scottmanley", "EverydayAstronaut"],
    "film": ["ThomasFlight", "LikeStoriesofOld", "LessonsfromtheScreenplay", "JacobGeller", "Nerdwriter1", "KaptainKristian", "NowYouSeeIt"],
    "cool": ["PrimerBlobs", "RealEngineering", "kurzgesagt", "NileRed", "CodeParade", "CGPGrey", "Wendoverproductions", "TomScottGo"],
    "fun": ["whirow"],
}

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


def parse(xml: str, cat: str) -> list[dict]:
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
        # skip sponsor/link lines
        lines = (e.findtext("m:group/m:description", namespaces=NS) or "").splitlines()
        desc = "\n".join(l for l in lines if "http" not in l).strip()
        desc = re.sub(r"\n{3,}", "\n\n", desc)
        if len(desc) > 320:
            desc = desc[:320].rsplit(" ", 1)[0] + "…"
        vids.append({
            "id": e.findtext("yt:videoId", namespaces=NS),
            "title": title,
            "channel": channel,
            "cat": cat,
            "date": e.findtext("a:published", namespaces=NS),
            "views": int(stats.get("views", 0)) if stats is not None else 0,
            "desc": desc,
        })
    return vids


def fetch(handle: str, cat: str, cache: dict) -> list[dict]:
    try:
        cid = channel_id(handle, cache)
        return parse(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"), cat)
    except Exception as err:
        print(f"  skipped @{handle}: {err}")
        return []


def build(vids: list[dict]) -> str:
    vids.sort(key=lambda v: v["date"], reverse=True)
    built = datetime.now(timezone.utc).isoformat()
    data = json.dumps({"cats": list(CHANNELS), "built": built, "vids": vids}).replace("</", "<\\/")
    return PAGE.replace("__DATA__", data)


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    HOME.mkdir(parents=True, exist_ok=True)
    cache = json.loads(IDS.read_text()) if IDS.exists() else {}
    jobs = [(h, c) for c, hs in CHANNELS.items() for h in hs]
    print(f"fetching {len(jobs)} channels...")
    with ThreadPoolExecutor(8) as pool:
        results = pool.map(lambda j: fetch(j[0], j[1], cache), jobs)
        vids = [v for r in results for v in r]
    IDS.write_text(json.dumps(cache, indent=1))
    (HOME / "index.html").write_text(build(vids))
    print(f"{len(vids)} videos")

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
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='9' fill='%23ff7a45'/%3E%3Cpath d='M12 9.5v13l10.5-6.5z' fill='%23140f0c'/%3E%3C/svg%3E">
<style>
  :root { --bg:#13100e; --card:#1e1916; --line:#2d2622; --text:#f4ede7; --dim:#968a81; --accent:#ff7a45; }
  * { box-sizing:border-box; margin:0; }
  html { scrollbar-color:var(--line) transparent; }
  body { background:var(--bg); color:var(--text); font:15px/1.4 ui-sans-serif, system-ui, -apple-system, sans-serif; -webkit-font-smoothing:antialiased; }
  header { position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; align-items:center; gap:12px 20px; padding:16px clamp(16px,4vw,56px); background:rgba(19,16,14,.82); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--line); }
  .logo { display:flex; align-items:baseline; gap:10px; }
  h1 { font-size:24px; letter-spacing:-0.04em; font-weight:750; }
  h1 span { color:var(--accent); }
  .sub { color:var(--dim); font-size:12px; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  button { font:inherit; cursor:pointer; }
  .chip { background:none; border:1px solid var(--line); color:var(--dim); padding:5px 12px; border-radius:99px; font-size:13px; transition:color .15s, border-color .15s; }
  .chip:hover { color:var(--text); border-color:#4a3f38; }
  .chip.on { background:var(--text); color:var(--bg); border-color:var(--text); }
  .chip.ch { background:var(--accent); color:#1a0d06; border-color:var(--accent); }
  .tools { margin-left:auto; display:flex; gap:8px; align-items:center; }
  .toggle { background:none; border:1px solid var(--line); color:var(--dim); padding:6px 11px; border-radius:8px; font-size:13px; }
  .toggle.on { color:var(--accent); border-color:var(--accent); }
  input { background:var(--card); border:1px solid var(--line); color:var(--text); padding:7px 12px; border-radius:8px; font:inherit; font-size:13px; width:190px; outline:none; }
  input:focus { border-color:var(--accent); }
  main { padding:8px clamp(16px,4vw,56px) 90px; }
  h2 { display:flex; align-items:center; gap:12px; font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.09em; color:var(--dim); margin:34px 0 16px; }
  h2::after { content:""; flex:1; height:1px; background:var(--line); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:28px 18px; }
  .vid { cursor:pointer; animation:in .3s ease both; }
  @keyframes in { from { opacity:0; transform:translateY(6px); } }
  .thumb { position:relative; aspect-ratio:16/9; border-radius:11px; overflow:hidden; background:var(--card); }
  .thumb img { width:100%; height:100%; object-fit:cover; transition:transform .3s ease; }
  .vid:hover img { transform:scale(1.045); }
  .vid:hover h3 { color:#fff; }
  .new { position:absolute; top:8px; left:8px; background:var(--accent); color:#1a0d06; font-size:11px; font-weight:700; padding:2px 7px; border-radius:5px; }
  .vid h3 { font-size:14.5px; font-weight:600; margin:10px 0 4px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
  .meta { color:var(--dim); font-size:13px; }
  .meta .name { background:none; border:0; padding:0; color:var(--dim); font-size:13px; }
  .meta .name:hover { color:var(--accent); }
  .vid.seen { opacity:.38; }
  .vid.seen:hover { opacity:.8; }
  .empty { color:var(--dim); padding:80px 0; text-align:center; }
  #player { position:fixed; inset:0; z-index:10; background:rgba(8,6,5,.97); display:none; overflow-y:auto; padding:clamp(16px,4vw,48px); }
  #player.on { display:block; }
  .stage { max-width:1100px; margin:0 auto; }
  .stage iframe { width:100%; aspect-ratio:16/9; border:0; border-radius:14px; background:#000; }
  .info { display:flex; gap:20px; justify-content:space-between; align-items:flex-start; margin-top:16px; }
  .info h3 { font-size:19px; font-weight:650; letter-spacing:-0.01em; }
  .info .meta { margin-top:4px; }
  .info p { color:var(--dim); font-size:13.5px; margin-top:12px; white-space:pre-line; max-width:760px; }
  .nav { display:flex; gap:6px; flex-shrink:0; }
  .nav button, .nav a { background:var(--card); border:1px solid var(--line); color:var(--text); padding:7px 12px; border-radius:8px; font-size:13px; text-decoration:none; }
  .nav button:hover, .nav a:hover { border-color:var(--accent); }
  .hint { color:#5d534c; font-size:12px; margin-top:22px; }
  @media (max-width:700px) { .tools { margin-left:0; width:100%; } input { flex:1; } .info { flex-direction:column; } }
</style>
</head>
<body>
<header>
  <div class="logo"><h1>lil<span>tube</span></h1><span class="sub" id="sub"></span></div>
  <div class="chips" id="chips"></div>
  <div class="tools">
    <button class="toggle" id="hide">hide watched</button>
    <input id="q" placeholder="search  /" autocomplete="off">
  </div>
</header>
<main id="feed"></main>
<div id="player">
  <div class="stage">
    <iframe id="frame" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>
    <div class="info">
      <div><h3 id="ptitle"></h3><div class="meta" id="pmeta"></div><p id="pdesc"></p></div>
      <div class="nav"><button id="prev">← prev</button><button id="next">next →</button><a id="yt" target="_blank" rel="noopener">youtube ↗</a><button id="x">close</button></div>
    </div>
    <div class="hint">esc close · ← → prev / next</div>
  </div>
</div>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const DAY = 86400000;
let cat = "all", ch = null, hide = false, seen = new Set(), list = [], cur = -1;

try {
  seen = new Set(JSON.parse(localStorage.getItem("seen") || "[]"));
  const s = JSON.parse(localStorage.getItem("prefs") || "{}");
  if (D.cats.includes(s.cat)) cat = s.cat;
  hide = !!s.hide;
} catch (e) {}

const save = () => {
  try {
    localStorage.setItem("seen", JSON.stringify([...seen]));
    localStorage.setItem("prefs", JSON.stringify({ cat, hide }));
  } catch (e) {}
};
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));
const ago = d => {
  const s = (Date.now() - new Date(d)) / 1000;
  for (const [n, u] of [[31536000,"y"],[2592000,"mo"],[604800,"w"],[86400,"d"],[3600,"h"],[60,"m"]])
    if (s >= n) return Math.floor(s / n) + u + " ago";
  return "just now";
};
const views = n => n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : n;
const bucket = d => {
  const t = Date.now() - new Date(d);
  return t < DAY ? "today" : t < 2 * DAY ? "yesterday" : t < 7 * DAY ? "this week" : t < 30 * DAY ? "this month" : "older";
};

function chips() {
  let h = ["all", ...D.cats].map(c => `<button class="chip ${c === cat && !ch ? "on" : ""}" data-c="${c}">${c}</button>`).join("");
  if (ch) h += `<button class="chip ch" data-c="${esc(cat)}">${esc(ch)} ×</button>`;
  $("#chips").innerHTML = h;
  $("#hide").classList.toggle("on", hide);
}

function card(v, i) {
  const fresh = Date.now() - new Date(v.date) < 2 * DAY && !seen.has(v.id);
  return `<article class="vid ${seen.has(v.id) ? "seen" : ""}" data-i="${i}" style="animation-delay:${Math.min(i, 24) * 15}ms">
    <div class="thumb"><img loading="lazy" src="https://i.ytimg.com/vi/${v.id}/hqdefault.jpg" alt="">${fresh ? '<span class="new">new</span>' : ""}</div>
    <h3>${esc(v.title)}</h3>
    <div class="meta"><button class="name" data-ch="${esc(v.channel)}">${esc(v.channel)}</button> · ${views(v.views)} views · ${ago(v.date)}</div>
  </article>`;
}

function render() {
  const q = $("#q").value.trim().toLowerCase();
  list = D.vids.filter(v =>
    (cat === "all" || v.cat === cat || ch) && (!ch || v.channel === ch) &&
    !(hide && seen.has(v.id)) && (!q || (v.title + " " + v.channel).toLowerCase().includes(q)));
  let h = "", last = null;
  list.forEach((v, i) => {
    const b = bucket(v.date);
    if (b !== last) { h += (last ? "</div>" : "") + `<h2>${b}</h2><div class="grid">`; last = b; }
    h += card(v, i);
  });
  $("#feed").innerHTML = last ? h + "</div>" : '<p class="empty">nothing here</p>';
  const week = D.vids.filter(v => Date.now() - new Date(v.date) < 7 * DAY && !seen.has(v.id)).length;
  $("#sub").textContent = `${week} new this week · updated ${ago(D.built)}`;
}

function play(i) {
  if (i < 0 || i >= list.length) return;
  cur = i;
  const v = list[i];
  $("#frame").src = `https://www.youtube-nocookie.com/embed/${v.id}?autoplay=1&rel=0`;
  $("#ptitle").textContent = v.title;
  $("#pmeta").textContent = `${v.channel} · ${views(v.views)} views · ${ago(v.date)}`;
  $("#pdesc").textContent = v.desc;
  $("#yt").href = `https://www.youtube.com/watch?v=${v.id}`;
  $("#prev").disabled = i === 0;
  $("#next").disabled = i === list.length - 1;
  $("#player").classList.add("on");
  $("#player").scrollTop = 0;
  document.body.style.overflow = "hidden";
  seen.add(v.id);
  save();
  document.querySelector(`.vid[data-i="${i}"]`)?.classList.add("seen");
}

function close() {
  if (!$("#player").classList.contains("on")) return;
  $("#player").classList.remove("on");
  $("#frame").src = "";
  document.body.style.overflow = "";
  render();
}

$("#chips").onclick = e => {
  const c = e.target.closest(".chip");
  if (!c) return;
  if (c.classList.contains("ch")) ch = null; else { cat = c.dataset.c; ch = null; }
  save(); chips(); render(); scrollTo(0, 0);
};
$("#feed").onclick = e => {
  const n = e.target.closest(".name");
  if (n) { ch = n.dataset.ch; chips(); render(); scrollTo(0, 0); return; }
  const v = e.target.closest(".vid");
  if (v) play(+v.dataset.i);
};
$("#hide").onclick = () => { hide = !hide; save(); chips(); render(); };
$("#q").oninput = render;
$("#prev").onclick = () => play(cur - 1);
$("#next").onclick = () => play(cur + 1);
$("#x").onclick = close;
$("#player").onclick = e => { if (e.target.id === "player") close(); };
addEventListener("keydown", e => {
  const open = $("#player").classList.contains("on");
  if (e.key === "Escape") { open ? close() : ($("#q").value = "", $("#q").blur(), render()); }
  else if (open && e.key === "ArrowRight") play(cur + 1);
  else if (open && e.key === "ArrowLeft") play(cur - 1);
  else if (!open && e.key === "/" && document.activeElement !== $("#q")) { e.preventDefault(); $("#q").focus(); }
});
chips(); render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()

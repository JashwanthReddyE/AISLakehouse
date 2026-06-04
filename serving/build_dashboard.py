"""Render serving/metrics.json (from databricks/export_metrics.py) into a self-contained,
interactive serving/index.html. Pure stdlib + inline CSS/JS — no external dependencies, so it
works offline and on GitHub Pages / Vercel.

Design goals: colorful dark theme, faint background ship, vanilla-JS interactivity (animated
counters, sortable + filterable tables, an interactive flow chart, info pop-overs), and
plain-English explanations + a glossary so a non-AIS-expert can read every element.

Usage:
    python serving/build_dashboard.py            # reads serving/metrics.json
    python serving/build_dashboard.py path.json  # or an explicit metrics file
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def esc(v: object) -> str:
    return html.escape("" if v is None else str(v))


def num(value: object, dec: int, suffix: str = "") -> str:
    """An animated number span (counts up on load; degrades to the final value with no JS)."""
    try:
        to = float(value)
    except (TypeError, ValueError):
        to = 0.0
    if dec == 0:
        shown = f"{int(round(to)):,}{suffix}"
    else:
        shown = f"{to:.{dec}f}{suffix}"
    return f'<span class="num" data-to="{to}" data-dec="{dec}" data-suffix="{esc(suffix)}">{esc(shown)}</span>'


def kpi(label: str, value_html: str, sub: str, color: str, explain: str) -> str:
    return (
        f'<div class="kpi kpi-{color}">'
        f'<button class="info" data-tip="{esc(explain)}" aria-label="What is this?">i</button>'
        f'<div class="kpi-val">{value_html}</div>'
        f'<div class="kpi-label">{esc(label)}</div>'
        f'<div class="kpi-sub">{esc(sub)}</div></div>'
    )


def table(headers: list[str], rows: list[list[object]], empty: str, *, numeric: set[int] | None = None,
          sortable: bool = True, filterable: bool = False, tid: str = "") -> str:
    numeric = numeric or set()
    if not rows:
        return f'<p class="empty">{esc(empty)}</p>'
    ths = "".join(
        f'<th data-num="{1 if i in numeric else 0}">{esc(h)}<span class="sort"></span></th>'
        for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    cls = "data-table" + (" sortable" if sortable else "")
    search = (
        f'<input class="filter" data-target="{tid}" placeholder="Filter rows…" aria-label="Filter">'
        if filterable else ""
    )
    return (
        f"{search}<div class=\"table-wrap\"><table id=\"{tid}\" class=\"{cls}\">"
        f"<thead><tr>{ths}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def bars(rows: list[dict], label_key: str, count_key: str, color: str) -> str:
    if not rows:
        return '<p class="empty">no data</p>'
    top = max((r[count_key] for r in rows), default=1) or 1
    out = []
    for r in rows:
        pct = round(100 * r[count_key] / top, 1)
        out.append(
            f'<div class="bar-row"><span class="bar-label">{esc(r[label_key])}</span>'
            f'<span class="bar-track"><span class="bar-fill bar-{color}" style="width:{pct}%"></span></span>'
            f'<span class="bar-num">{esc(r[count_key])}</span></div>'
        )
    return '<div class="bars">' + "".join(out) + "</div>"


def section(title: str, desc: str, body: str, accent: str) -> str:
    return (
        f'<section><h2 class="acc-{accent}">{esc(title)}</h2>'
        f'<p class="sec-desc">{esc(desc)}</p>{body}</section>'
    )


def commodity_block(c: dict) -> str:
    if not c:
        return ""
    share = (c.get("avg_tanker_share", 0) or 0) * 100
    kpis = (
        kpi("Tankers", num(c.get("distinct_tankers", 0), 0), "oil / gas / chemical carriers", "teal",
            "Vessels identified as tankers from their AIS ship-type code (80–89). These carry crude "
            "oil, refined products, gas or chemicals.")
        + kpi("Tanker share", num(share, 1, "%"), "of all vessels (avg per hour)", "green",
              "What fraction of ships in the strait are tankers. A rough gauge of oil & gas traffic.")
        + kpi("Floating storage", num(c.get("floating_storage_candidates", 0), 0), "idle-tanker candidates",
              "gold",
              "Tankers sitting nearly still in one small area for a long time — they may be storing "
              "cargo offshore, which often signals oversupply (a bearish crude signal).")
    )
    flow_rows = [
        [r.get("hour"), r.get("vessels"), r.get("tankers"), r.get("tanker_share")]
        for r in c.get("flow", [])
    ]
    flow_tbl = table(
        ["Hour (UTC)", "Vessels", "Tankers", "Tanker share"], flow_rows, "no flow data",
        numeric={1, 2, 3}, tid="flowtable",
    )
    disclaimer = (
        '<p class="disclaimer"><b>Indicator, not a price predictor.</b> Tanker throughput and '
        'floating storage are <i>inputs</i> commodity desks use — but one chokepoint and a short '
        'history make this illustrative, not market-grade. Real forecasting needs price history, '
        'global coverage and a backtested model.</p>'
    )
    body = (
        f'<div class="grid">{kpis}</div>'
        '<div class="panel"><h3>Hourly tanker flow</h3>'
        '<p class="hint">Distinct vessels vs tankers seen each hour, with tanker share. '
        'Click a column header to sort.</p>'
        f'{flow_tbl}{disclaimer}</div>'
    )
    return section("🛢️ Commodity indicators — tanker flow & floating storage",
                   "Reading ship movements as an early signal of oil & gas activity.", body, "teal")


GLOSSARY = [
    ("AIS (Automatic Identification System)",
     "A radio system ships broadcast to announce their identity, position, speed and heading — "
     "like an aircraft transponder, but for vessels."),
    ("MMSI",
     "A ship's 9-digit radio ID. The first 3 digits (the MID) reveal the country the ship is "
     "registered under (its flag state)."),
    ("SOG (Speed Over Ground)",
     "How fast the vessel is actually moving across the water, in knots."),
    ("Bronze / Silver / Gold (Medallion architecture)",
     "Three refinement layers. Bronze = raw data exactly as received. Silver = cleaned, "
     "de-duplicated and enriched. Gold = business-ready analytics."),
    ("De-duplication & watermarking",
     "Ships repeat messages and they arrive out of order. We drop duplicate (ship, time) records "
     "and use a time 'watermark' to handle late-arriving data correctly."),
    ("Quarantine",
     "Records that fail automated quality checks (impossible coordinates, absurd speed) are set "
     "aside with a reason code instead of being silently deleted."),
    ("Dark vessel / dark event",
     "When a ship stops broadcasting AIS for an unusually long time and then reappears. It can be "
     "an innocent coverage gap — or a vessel deliberately 'going dark'."),
    ("Confidence score",
     "How unusual a dark event is compared with that ship's own normal reporting rhythm, from 0 "
     "(routine) to 1 (highly anomalous)."),
    ("Tanker (ship type 80–89)",
     "AIS classifies ships by a type code; 80–89 are tankers carrying oil, gas or chemicals."),
    ("Floating storage",
     "Full tankers anchored offshore acting as temporary storage — a classic sign of oversupply "
     "and weak demand."),
]


def build(metrics: dict) -> str:
    layers = metrics.get("layers", {})
    de = metrics.get("dark_events", {}) or {}
    fresh = metrics.get("freshness", {}) or {}
    qrate = metrics.get("quarantine_rate", 0.0)

    kpis = (
        kpi("Bronze (raw)", num(layers.get("bronze", 0), 0), "immutable AIS records", "bronze",
            "Every raw AIS message exactly as received — the untouched source of truth before any "
            "cleaning. Nothing is ever edited here.")
        + kpi("Silver positions", num(layers.get("silver_positions", 0), 0),
              "clean · de-duplicated · enriched", "cyan",
              "Ship position reports after cleaning, removing duplicates and adding the country "
              "(flag) of each ship. This is the analysis-ready dataset.")
        + kpi("Quarantine rate", num(qrate * 100, 2, "%"), f"{layers.get('silver_quarantine', 0)} flagged",
              "red",
              "Share of position reports rejected by data-quality checks — bad coordinates, "
              "impossible speeds, missing IDs. Lower is better.")
        + kpi("Ship profiles", num(layers.get("silver_ship_static", 0), 0), "normalized destinations",
              "blue",
              "Vessel identity records (name, type, declared destination) — the 'who and what' of "
              "each ship, with messy destination text cleaned to standard ports.")
        + kpi("Dark events", num(de.get("count", 0), 0), f"{de.get('vessels', 0)} vessels", "pink",
              "Times a ship stopped broadcasting for an unusually long gap and then reappeared. "
              "Could be a coverage gap or a vessel going dark.")
        + kpi("Avg confidence", num(de.get("avg_confidence", 0), 3), "dark-event anomaly score", "purple",
              "On average, how anomalous the detected dark events are versus each ship's normal "
              "reporting rhythm (0 = routine, 1 = highly unusual).")
    )

    dark_rows = [
        [e.get("mmsi"), e.get("flag_country"), e.get("dark_start"), e.get("dark_end"),
         e.get("gap_minutes"), e.get("confidence")]
        for e in metrics.get("top_dark_events", [])
    ]
    quar_rows = [[r.get("reason_code"), r.get("count")] for r in metrics.get("quarantine_reasons", [])]

    dark_section = section(
        "🛰️ Dark-vessel events (highest confidence first)",
        "Ships that went silent, then reappeared. Click a column header to sort.",
        table(["MMSI", "Flag", "Dark start", "Dark end", "Gap (min)", "Confidence"], dark_rows,
              "no dark events detected", numeric={4, 5}, tid="darktable")
        + f'<p class="hint">Longest silence: {esc(de.get("max_gap_minutes", 0))} min. '
          'Terrestrial AIS has real coverage dead zones, so a dark event is a <i>candidate</i> '
          'signal, not proof of intent.</p>',
        "pink",
    )

    two = (
        '<div class="two">'
        '<div><h3>Flag states (where ships are registered)</h3>'
        f'{bars(metrics.get("top_flags", []), "flag_country", "count", "cyan")}</div>'
        '<div><h3>Top destinations (cleaned from free-text)</h3>'
        f'{bars(metrics.get("top_destinations", []), "destination", "count", "teal")}</div></div>'
    )
    flags_section = section(
        "🌍 Where ships come from & go",
        "Registration country (from the MMSI) and declared destination (normalized from messy "
        "hand-typed text like 'AE DXB' → 'DUBAI').", two, "cyan",
    )

    quar_section = section(
        "🧪 Data-quality quarantine",
        "Records caught by automated checks and set aside (not deleted), each with a reason code.",
        table(["Reason code", "Count"], quar_rows, "no quarantined records", numeric={1}, tid="quartable"),
        "red",
    )

    glossary = "".join(
        f"<details class='gloss'><summary>{esc(t)}</summary><p>{esc(d)}</p></details>"
        for t, d in GLOSSARY
    )
    gloss_section = (
        '<section><h2 class="acc-blue">📖 Glossary — plain-English definitions</h2>'
        '<p class="sec-desc">New to ship tracking? Expand any term.</p>'
        f'<div class="gloss-grid">{glossary}</div></section>'
    )

    data_json = json.dumps(metrics).replace("</", "<\\/")

    return (
        HEAD
        + SHIP_SVG
        + '<div class="wrap">'
        + HEADER
        + '<section class="intro"><h2 class="acc-blue">How to read this dashboard</h2>'
          '<p>This project watches ships near the <b>Singapore Strait</b> — one of the world\'s '
          'busiest shipping lanes — in real time, and looks for <b>unusual behaviour</b> rather than '
          'just plotting dots on a map. Raw radio messages flow through three refinement stages '
          '(<span class="tag t-bronze">Bronze</span> → <span class="tag t-cyan">Silver</span> → '
          '<span class="tag t-gold">Gold</span>) and end as the analytics below. Hover the '
          '<span class="ipill">i</span> on any card for a plain-English explanation, and see the '
          '<a href="#glossary-anchor">glossary</a> for terms.</p></section>'
        + '<div class="grid">' + kpis + '</div>'
        + commodity_block(metrics.get("commodity", {}))
        + dark_section
        + flags_section
        + quar_section
        + '<a id="glossary-anchor"></a>' + gloss_section
        + FOOTER_OPEN
        + f'Generated {esc(metrics.get("generated_at", ""))} · latest ship event '
          f'{esc(fresh.get("latest_event_time") or "—")} · gold computed '
          f'{esc(fresh.get("latest_gold_computed") or "—")}'
        + FOOTER_CLOSE
        + '</div>'
        + f'<script type="application/json" id="metrics">{data_json}</script>'
        + SCRIPT
        + "</body></html>"
    )


HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AISLakehouse — Maritime Behavior Dashboard</title>
<style>
  :root {
    --bg:#070b18; --bg2:#0d1430; --card:#121a33; --card2:#0f1730; --line:#26314f;
    --fg:#eaf0ff; --mut:#93a0c6;
    --bronze:#e0913e; --cyan:#37d6e6; --gold:#ffd24a; --red:#ff6b81; --blue:#5aa6ff;
    --pink:#ff79c6; --purple:#b08cff; --teal:#2dd4bf; --green:#4ade80;
  }
  * { box-sizing:border-box; }
  html { scroll-behavior:smooth; }
  body { margin:0; color:var(--fg); position:relative; min-height:100vh;
    font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    background:
      radial-gradient(900px 500px at 12% -8%, rgba(58,123,213,.18), transparent 60%),
      radial-gradient(800px 500px at 100% 0%, rgba(45,212,191,.12), transparent 55%),
      radial-gradient(700px 600px at 80% 100%, rgba(176,140,255,.10), transparent 55%),
      linear-gradient(180deg, var(--bg2), var(--bg)); }
  .ship { position:fixed; inset:0; width:100%; height:100%; z-index:0; pointer-events:none;
    display:flex; align-items:center; justify-content:center; overflow:hidden; }
  .ship svg { width:min(1100px,95vw); opacity:.07; }
  .wrap { position:relative; z-index:1; max-width:1120px; margin:0 auto; padding:34px 20px 70px; }
  header { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  h1 { margin:0; font-size:28px; letter-spacing:.2px;
    background:linear-gradient(90deg,#7fd3ff,#37d6e6,#2dd4bf); -webkit-background-clip:text;
    background-clip:text; color:transparent; }
  .live { display:inline-flex; align-items:center; gap:7px; font-size:12px; color:var(--mut);
    border:1px solid var(--line); border-radius:999px; padding:4px 10px; background:rgba(20,28,52,.6); }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--green);
    box-shadow:0 0 0 0 rgba(74,222,128,.7); animation:pulse 1.8s infinite; }
  @keyframes pulse { 70%{ box-shadow:0 0 0 9px rgba(74,222,128,0);} 100%{ box-shadow:0 0 0 0 rgba(74,222,128,0);} }
  .subtitle { color:var(--mut); width:100%; margin-top:2px; }
  section { margin-top:30px; }
  .intro { background:linear-gradient(180deg,rgba(20,28,52,.7),rgba(15,23,48,.55));
    border:1px solid var(--line); border-radius:14px; padding:16px 18px; }
  .intro p { margin:6px 0 0; color:#cdd7f5; }
  h2 { font-size:17px; margin:0 0 4px; display:flex; align-items:center; gap:8px; }
  h2::before { content:""; width:10px; height:20px; border-radius:3px; background:var(--blue); }
  h2.acc-bronze::before{background:var(--bronze);} h2.acc-cyan::before{background:var(--cyan);}
  h2.acc-gold::before{background:var(--gold);} h2.acc-red::before{background:var(--red);}
  h2.acc-pink::before{background:var(--pink);} h2.acc-teal::before{background:var(--teal);}
  h2.acc-blue::before{background:var(--blue);}
  h3 { font-size:14px; margin:0 0 8px; color:var(--fg); }
  .sec-desc { color:var(--mut); margin:0 0 14px; font-size:13.5px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(168px,1fr)); gap:14px; }
  .kpi { position:relative; background:linear-gradient(180deg,var(--card),var(--card2));
    border:1px solid var(--line); border-radius:14px; padding:18px 16px; overflow:hidden;
    transition:transform .15s ease, box-shadow .15s ease; }
  .kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:var(--blue); }
  .kpi:hover { transform:translateY(-3px); box-shadow:0 10px 30px rgba(0,0,0,.35); }
  .kpi-val { font-size:30px; font-weight:800; letter-spacing:.5px; }
  .kpi-label { color:#dbe4ff; font-size:13px; margin-top:3px; font-weight:600; }
  .kpi-sub { color:var(--mut); font-size:11px; margin-top:6px; }
  .kpi-bronze::before{background:var(--bronze);} .kpi-bronze .kpi-val{color:var(--bronze);}
  .kpi-cyan::before{background:var(--cyan);} .kpi-cyan .kpi-val{color:var(--cyan);}
  .kpi-red::before{background:var(--red);} .kpi-red .kpi-val{color:var(--red);}
  .kpi-blue::before{background:var(--blue);} .kpi-blue .kpi-val{color:var(--blue);}
  .kpi-pink::before{background:var(--pink);} .kpi-pink .kpi-val{color:var(--pink);}
  .kpi-purple::before{background:var(--purple);} .kpi-purple .kpi-val{color:var(--purple);}
  .kpi-teal::before{background:var(--teal);} .kpi-teal .kpi-val{color:var(--teal);}
  .kpi-green::before{background:var(--green);} .kpi-green .kpi-val{color:var(--green);}
  .kpi-gold::before{background:var(--gold);} .kpi-gold .kpi-val{color:var(--gold);}
  .info, .ipill { width:20px; height:20px; border-radius:50%; border:1px solid var(--line);
    background:rgba(90,166,255,.12); color:var(--blue); font:700 12px/18px Georgia,serif;
    text-align:center; cursor:help; }
  .info { position:absolute; top:10px; right:10px; }
  .ipill { display:inline-block; }
  .panel { background:linear-gradient(180deg,rgba(18,26,51,.85),rgba(15,23,48,.7));
    border:1px solid var(--line); border-radius:14px; padding:18px; margin-top:14px; }
  .two { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .panel, .two > div { background:linear-gradient(180deg,rgba(18,26,51,.85),rgba(15,23,48,.7));
    border:1px solid var(--line); border-radius:14px; padding:16px; }
  @media (max-width:720px){ .two{ grid-template-columns:1fr; } }
  .table-wrap { overflow-x:auto; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { color:var(--mut); font-weight:600; cursor:pointer; user-select:none; position:relative; }
  .sortable th:hover { color:var(--fg); }
  th .sort { margin-left:6px; opacity:.5; font-size:10px; }
  td:last-child, th:last-child { text-align:right; }
  tbody tr:hover { background:rgba(90,166,255,.06); }
  .filter { width:100%; max-width:280px; margin-bottom:10px; padding:8px 11px; border-radius:9px;
    border:1px solid var(--line); background:#0c1430; color:var(--fg); font-size:13px; }
  .bars { display:flex; flex-direction:column; gap:9px; }
  .bar-row { display:grid; grid-template-columns:120px 1fr 46px; align-items:center; gap:10px; }
  .bar-label { color:var(--mut); font-size:12px; overflow:hidden; text-overflow:ellipsis; }
  .bar-track { background:#0b1230; border-radius:6px; height:11px; overflow:hidden; }
  .bar-fill { display:block; height:100%; width:0; transition:width 1s ease; }
  .bar-cyan { background:linear-gradient(90deg,#1f8fb5,#37d6e6); }
  .bar-teal { background:linear-gradient(90deg,#0f9b8e,#2dd4bf); }
  .bar-num { text-align:right; font-variant-numeric:tabular-nums; font-size:12px; }
  .hint, .disclaimer { color:var(--mut); font-size:12px; margin:8px 0 0; }
  .disclaimer { border-left:3px solid var(--gold); padding:8px 12px; background:rgba(255,210,74,.06);
    border-radius:0 8px 8px 0; margin-top:12px; }
  .empty { color:var(--mut); font-style:italic; }
  .tag { font-size:11px; padding:1px 8px; border-radius:999px; font-weight:700; }
  .t-bronze{ background:rgba(224,145,62,.18); color:var(--bronze); }
  .t-cyan{ background:rgba(55,214,230,.16); color:var(--cyan); }
  .t-gold{ background:rgba(255,210,74,.16); color:var(--gold); }
  .gloss-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  @media (max-width:720px){ .gloss-grid{ grid-template-columns:1fr; } }
  .gloss { background:rgba(18,26,51,.7); border:1px solid var(--line); border-radius:10px; padding:10px 14px; }
  .gloss summary { cursor:pointer; font-weight:600; color:#dbe4ff; }
  .gloss p { margin:8px 0 0; color:var(--mut); font-size:13px; }
  footer { color:var(--mut); font-size:12px; margin-top:34px; border-top:1px solid var(--line); padding-top:14px; }
  a { color:var(--cyan); }
  #tooltip { position:fixed; z-index:50; max-width:260px; background:#0b1330; color:var(--fg);
    border:1px solid var(--line); border-radius:10px; padding:9px 12px; font-size:12.5px;
    box-shadow:0 12px 30px rgba(0,0,0,.5); pointer-events:none; opacity:0; transition:opacity .12s; }
</style></head><body>
"""

SHIP_SVG = """
<div class="ship" aria-hidden="true"><svg viewBox="0 0 1000 360" fill="none" xmlns="http://www.w3.org/2000/svg">
  <defs><linearGradient id="hull" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#7fd3ff"/><stop offset="1" stop-color="#2dd4bf"/></linearGradient></defs>
  <!-- containers -->
  <g fill="url(#hull)">
    <rect x="180" y="150" width="70" height="46" rx="4"/><rect x="256" y="150" width="70" height="46" rx="4"/>
    <rect x="332" y="150" width="70" height="46" rx="4"/><rect x="408" y="150" width="70" height="46" rx="4"/>
    <rect x="484" y="150" width="70" height="46" rx="4"/><rect x="560" y="150" width="70" height="46" rx="4"/>
    <rect x="218" y="116" width="70" height="40" rx="4"/><rect x="294" y="116" width="70" height="40" rx="4"/>
    <rect x="370" y="116" width="70" height="40" rx="4"/><rect x="446" y="116" width="70" height="40" rx="4"/>
    <rect x="522" y="116" width="70" height="40" rx="4"/>
    <rect x="294" y="86" width="70" height="34" rx="4"/><rect x="370" y="86" width="70" height="34" rx="4"/>
    <rect x="446" y="86" width="70" height="34" rx="4"/>
  </g>
  <!-- bridge + funnel -->
  <rect x="650" y="92" width="80" height="104" rx="6" fill="url(#hull)"/>
  <rect x="676" y="60" width="30" height="40" rx="5" fill="url(#hull)"/>
  <!-- hull -->
  <path d="M120 196 H792 L740 270 H190 Z" fill="url(#hull)"/>
  <path d="M792 196 H862 L800 256 L740 256 Z" fill="url(#hull)"/>
  <!-- waterline waves -->
  <g stroke="#5aa6ff" stroke-width="3" opacity=".8" fill="none">
    <path d="M60 292 q30 -16 60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0"/>
    <path d="M40 312 q30 -16 60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0 t60 0" opacity=".5"/>
  </g>
</svg></div>
"""

HEADER = """
<header>
  <h1>AISLakehouse</h1>
  <span class="live"><span class="dot"></span> 24/7 ingestion live</span>
  <div class="subtitle">Real-time maritime <b>behaviour</b> analytics · Singapore Strait ·
    dark-vessel detection &amp; commodity signals</div>
</header>
"""

FOOTER_OPEN = '<footer>'
FOOTER_CLOSE = ('<br>Source: <a href="https://github.com/JashwanthReddyE/AISLakehouse">'
                'github.com/JashwanthReddyE/AISLakehouse</a> · '
                'AISStream → Event Hubs → Databricks (Bronze/Silver/Gold Delta on ADLS Gen2)</footer>')

SCRIPT = """
<script>
const METRICS = JSON.parse(document.getElementById('metrics').textContent);

/* Animated counters */
function animate(el){
  const to=parseFloat(el.dataset.to||'0'), dec=parseInt(el.dataset.dec||'0'), suf=el.dataset.suffix||'';
  const dur=900, t0=performance.now();
  function fmt(v){ return (dec===0 ? Math.round(v).toLocaleString() : v.toFixed(dec)) + suf; }
  function step(t){ let p=Math.min(1,(t-t0)/dur); p=1-Math.pow(1-p,3);
    el.textContent=fmt(to*p); if(p<1) requestAnimationFrame(step); else el.textContent=fmt(to); }
  requestAnimationFrame(step);
}
const io=new IntersectionObserver((es)=>es.forEach(e=>{ if(e.isIntersecting){ animate(e.target); io.unobserve(e.target);} }));
document.querySelectorAll('.num').forEach(n=>io.observe(n));
/* animate bar fills */
new IntersectionObserver((es)=>es.forEach(e=>{ if(e.isIntersecting){ const f=e.target; f.style.width=f.dataset.w||f.style.width; } })
);
document.querySelectorAll('.bar-fill').forEach(f=>{ const w=f.style.width; f.dataset.w=w; f.style.width='0';
  new IntersectionObserver((es,o)=>es.forEach(e=>{ if(e.isIntersecting){ f.style.width=w; o.disconnect(); } })).observe(f); });

/* Tooltip (info pop-overs + chart) */
const tip=document.createElement('div'); tip.id='tooltip'; document.body.appendChild(tip);
function showTip(txt,x,y){ tip.textContent=txt; tip.style.opacity='1';
  tip.style.left=Math.min(x+14, innerWidth-tip.offsetWidth-12)+'px'; tip.style.top=(y+16)+'px'; }
function hideTip(){ tip.style.opacity='0'; }
document.querySelectorAll('[data-tip]').forEach(el=>{
  el.addEventListener('mouseenter',e=>showTip(el.dataset.tip,e.clientX,e.clientY));
  el.addEventListener('mousemove',e=>showTip(el.dataset.tip,e.clientX,e.clientY));
  el.addEventListener('mouseleave',hideTip);
  el.addEventListener('click',e=>showTip(el.dataset.tip,e.clientX,e.clientY));
});

/* Sortable tables */
document.querySelectorAll('table.sortable').forEach(tb=>{
  tb.querySelectorAll('th').forEach((th,idx)=>{
    let dir=1;
    th.addEventListener('click',()=>{
      const isNum=th.dataset.num==='1';
      const rows=[...tb.tBodies[0].rows];
      rows.sort((a,b)=>{ let x=a.cells[idx].textContent.trim(), y=b.cells[idx].textContent.trim();
        if(isNum){ x=parseFloat(x.replace(/[^0-9.\\-]/g,''))||0; y=parseFloat(y.replace(/[^0-9.\\-]/g,''))||0; return (x-y)*dir; }
        return x.localeCompare(y)*dir; });
      rows.forEach(r=>tb.tBodies[0].appendChild(r));
      tb.querySelectorAll('.sort').forEach(s=>s.textContent='');
      th.querySelector('.sort').textContent = dir>0?'▲':'▼'; dir*=-1;
    });
  });
});

/* Filterable tables */
document.querySelectorAll('.filter').forEach(inp=>{
  inp.addEventListener('input',()=>{ const q=inp.value.toLowerCase();
    const tb=document.getElementById(inp.dataset.target);
    [...tb.tBodies[0].rows].forEach(r=>{ r.style.display = r.textContent.toLowerCase().includes(q)?'':'none'; });
  });
});
</script>
"""


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "metrics.json"
    metrics = json.loads(src.read_text(encoding="utf-8"))
    out = HERE / "index.html"
    out.write_text(build(metrics), encoding="utf-8")
    print(f"Wrote {out} from {src}")


if __name__ == "__main__":
    main()

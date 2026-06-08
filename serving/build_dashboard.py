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


# Vessel-class colours — shared by the live map dots and the "what's being carried" bars.
CATEGORY_COLORS = {
    "Tanker (oil · gas · chemical)": "#ffd24a",
    "Cargo (container · bulk)": "#37d6e6",
    "Passenger": "#ff79c6",
    "High-speed craft": "#b08cff",
    "Fishing": "#4ade80",
    "Tug & tow": "#5aa6ff",
    "Service / port craft": "#2dd4bf",
    "Pleasure / sailing": "#9fb0d8",
    "Unknown / other": "#7c8ab5",
}

# Monitored chokepoints drawn on the map (name, lat_min, lat_max, lon_min, lon_max).
REGION_BOXES = [
    ("Singapore", 0.4, 2.0, 102.8, 105.4),
    ("Rotterdam", 50.6, 52.8, 2.0, 5.2),
    ("Houston", 26.8, 30.4, -96.2, -92.6),
    ("Hormuz", 24.2, 27.8, 53.6, 58.2),
]

# Coarse continent silhouettes (lon, lat) — context only, not cartographically precise.
CONTINENTS = [
    [(-168, 65), (-140, 70), (-95, 72), (-60, 68), (-52, 47), (-66, 45), (-80, 25),
     (-97, 18), (-105, 23), (-117, 32), (-125, 40), (-140, 58)],
    [(-80, 8), (-60, 10), (-50, 0), (-35, -8), (-40, -23), (-58, -40), (-72, -52),
     (-75, -40), (-70, -20), (-80, -5)],
    [(-10, 43), (-9, 38), (3, 40), (18, 40), (28, 41), (40, 46), (40, 60), (30, 70),
     (10, 71), (5, 62), (-5, 58), (-10, 50)],
    [(-17, 21), (-16, 14), (-8, 4), (8, 4), (10, -1), (13, -10), (20, -34), (26, -34),
     (33, -26), (40, -15), (51, 12), (43, 11), (33, 30), (20,32), (10, 37), (-6, 36)],
    [(40, 46), (50, 42), (60, 25), (77, 8), (80, 15), (90, 22), (100, 5), (105, 1),
     (120, 5), (122, 15), (140, 35), (143, 45), (160, 60), (180, 68), (170, 70),
     (140, 73), (100, 78), (70, 76), (60, 68), (50, 60), (40, 60)],
    [(113, -22), (122, -18), (130, -12), (142, -11), (150, -22), (153, -28),
     (146, -39), (138, -35), (129, -32), (115, -34)],
]

MAP_W, MAP_H = 1000, 500


def _proj(lon: float, lat: float) -> tuple[float, float]:
    """Equirectangular projection → SVG coordinates."""
    return ((lon + 180) / 360 * MAP_W, (90 - lat) / 180 * MAP_H)


def cat_color(category: str) -> str:
    return CATEGORY_COLORS.get(category, "#7c8ab5")


def vessel_map(metrics: dict) -> str:
    pos = metrics.get("vessel_positions", []) or []
    if not pos:
        return ""
    # Continents.
    conts = ""
    for poly in CONTINENTS:
        pts = " ".join(f"{_proj(lo, la)[0]:.1f},{_proj(lo, la)[1]:.1f}" for lo, la in poly)
        conts += (f'<polygon points="{pts}" fill="#16223f" stroke="#243156" '
                  f'stroke-width="1" opacity="0.6"/>')
    # Graticule.
    grat = ""
    for lon in range(-150, 151, 30):
        x = _proj(lon, 0)[0]
        grat += (f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{MAP_H}" '
                 f'stroke="#1b2647" stroke-width="0.8"/>')
    for lat in range(-60, 61, 30):
        y = _proj(0, lat)[1]
        grat += (f'<line x1="0" y1="{y:.1f}" x2="{MAP_W}" y2="{y:.1f}" '
                 f'stroke="#1b2647" stroke-width="0.8"/>')
    # Monitored region boxes + labels.
    boxes = ""
    for name, la0, la1, lo0, lo1 in REGION_BOXES:
        x0, y0 = _proj(lo0, la1)  # top-left (max lat)
        x1, y1 = _proj(lo1, la0)  # bottom-right (min lat)
        w = max(x1 - x0, 7)
        h = max(y1 - y0, 7)
        boxes += (
            f'<rect x="{x0 - 3:.1f}" y="{y0 - 3:.1f}" width="{w + 6:.1f}" height="{h + 6:.1f}" '
            f'rx="3" fill="none" stroke="#5aa6ff" stroke-width="1.3" stroke-dasharray="4,3" opacity="0.85"/>'
            f'<text x="{x0 - 2:.1f}" y="{y0 - 7:.1f}" fill="#9cc2ff" font-size="12" '
            f'font-family="Segoe UI, sans-serif" font-weight="600">{esc(name)}</text>'
        )
    # Vessel dots.
    dots = ""
    for v in pos:
        try:
            x, y = _proj(float(v.get("lon", 0)), float(v.get("lat", 0)))
        except (TypeError, ValueError):
            continue
        cat = v.get("category", "Unknown / other")
        name = v.get("name") or str(v.get("mmsi", ""))
        dest = v.get("destination") or "—"
        sog = v.get("sog", 0)
        tip = f"{name} · {cat} · {v.get('flag_country', '?')} · {sog} kn · → {dest}"
        dots += (
            f'<circle class="vdot" cx="{x:.1f}" cy="{y:.1f}" r="3.1" fill="{cat_color(cat)}" '
            f'fill-opacity="0.92" data-tip="{esc(tip)}"/>'
        )
    # Legend from category counts.
    cats = metrics.get("vessel_categories", []) or []
    legend = "".join(
        f'<span class="legend-item"><span class="swatch" style="background:{cat_color(c["category"])}"></span>'
        f'{esc(c["category"])} <b>({esc(c["count"])})</b></span>'
        for c in cats
    )
    svg = (
        f'<div class="mapwrap"><svg viewBox="0 0 {MAP_W} {MAP_H}" '
        f'role="img" aria-label="World map of current vessel positions">'
        f"{grat}{conts}{boxes}{dots}</svg></div>"
        f'<div class="legend">{legend}</div>'
        f'<p class="hint">Each dot is one ship\'s most recent reported position '
        f'({len(pos)} shown), coloured by vessel class. Dashed boxes are the four monitored '
        f'regions. Hover a dot for its name, type, flag, speed and destination.</p>'
    )
    return svg


def category_bars(cats: list[dict]) -> str:
    if not cats:
        return '<p class="empty">no data</p>'
    top = max((c["count"] for c in cats), default=1) or 1
    out = []
    for c in cats:
        pct = round(100 * c["count"] / top, 1)
        col = cat_color(c["category"])
        out.append(
            f'<div class="bar-row"><span class="bar-label">{esc(c["category"])}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct}%;background:{col}"></span></span>'
            f'<span class="bar-num">{esc(c["count"])}</span></div>'
        )
    return '<div class="bars">' + "".join(out) + "</div>"


def region_cards(regions: list[dict]) -> str:
    if not regions:
        return ""
    out = []
    for r in regions:
        v = r.get("vessels", 0) or 0
        idle = r.get("idle", 0) or 0
        moving = r.get("moving", 0) or 0
        spd = r.get("avg_sog", 0) or 0
        tot = max(v, 1)
        mp = round(100 * moving / tot, 1)
        ip = round(100 * idle / tot, 1)
        read = "Congested / anchorage" if spd < 3 else "Mixed transit" if spd < 8 else "Free-flowing"
        out.append(
            f'<div class="region-card"><div class="rc-name">{esc(r["region"])}</div>'
            f'<div class="rc-big">{num(v, 0)}<span>vessels</span></div>'
            f'<div class="rc-seg"><span class="seg-mv" style="width:{mp}%"></span>'
            f'<span class="seg-id" style="width:{ip}%"></span></div>'
            f'<div class="rc-meta">{esc(moving)} moving · {esc(idle)} idle · avg {esc(spd)} kn</div>'
            f'<div class="rc-read">{esc(read)}</div></div>'
        )
    return '<div class="region-cards">' + "".join(out) + "</div>"


def operational_brief(metrics: dict) -> str:
    regions = metrics.get("regions", []) or []
    cats = metrics.get("vessel_categories", []) or []
    dests = metrics.get("top_destinations", []) or []
    de = metrics.get("dark_events", {}) or {}
    com = metrics.get("commodity", {}) or {}
    anom = metrics.get("anomalies", {}) or {}
    pos = metrics.get("vessel_positions", []) or []
    if not (regions or cats or pos):
        return ""
    parts = []
    if regions:
        busiest = max(regions, key=lambda r: r.get("vessels", 0))
        spd = busiest.get("avg_sog", 0) or 0
        read = ("heavy queuing / anchorage activity" if spd < 3
                else "a mix of transit and waiting" if spd < 8 else "free-flowing transit")
        parts.append(
            f"Right now we're tracking <b>{len(pos)}</b> vessels across the four monitored "
            f"chokepoints. <b>{esc(busiest['region'])}</b> is the busiest with "
            f"<b>{esc(busiest['vessels'])}</b> vessels at an average <b>{esc(spd)} kn</b> — "
            f"{read}."
        )
    if cats:
        top_cat = cats[0]
        tankers = next((c["count"] for c in cats if "Tanker" in c["category"]), 0)
        parts.append(
            f"The fleet is led by <b>{esc(top_cat['category'])}</b> "
            f"(<b>{esc(top_cat['count'])}</b> vessels); tankers carrying oil, gas or chemicals "
            f"account for <b>{esc(tankers)}</b>."
        )
    if dests:
        d0 = dests[0]
        tail = f", ahead of {esc(dests[1]['destination'])}" if len(dests) > 1 else ""
        parts.append(
            f"The most-declared destination is <b>{esc(d0['destination'])}</b> "
            f"(<b>{esc(d0['count'])}</b> vessels){tail} — the busiest current corridor."
        )
    parts.append(
        f"Anomaly scan flags <b>{esc(de.get('count', 0))}</b> dark-vessel events (long AIS "
        f"silences), <b>{esc(com.get('floating_storage_candidates', 0))}</b> idle-tanker / "
        f"floating-storage candidates and <b>{esc(anom.get('overspeed_count', 0))}</b> over-speed "
        f"outliers. Each is a <i>candidate</i> signal worth a look — not proof of intent."
    )
    return '<div class="panel brief">' + "".join(f"<p>{p}</p>" for p in parts) + "</div>"


def anomaly_block(metrics: dict) -> str:
    de = metrics.get("dark_events", {}) or {}
    com = metrics.get("commodity", {}) or {}
    anom = metrics.get("anomalies", {}) or {}
    layers = metrics.get("layers", {}) or {}
    if not (anom or de or com):
        return ""
    items = [
        ("HIGH", "red", "Dark-vessel events", de.get("count", 0),
         "Ships that went silent for an unusually long gap, then reappeared."),
        ("WATCH", "gold", "Idle tankers (floating storage)", com.get("floating_storage_candidates", 0),
         "Tankers nearly motionless in one spot for hours — possible offshore storage."),
        ("WATCH", "purple", "Over-speed outliers", anom.get("overspeed_count", 0),
         "Vessels reporting >30 kn — rare for large ships; usually a data glitch or a fast craft."),
        ("INFO", "blue", "Quarantined bad records", layers.get("silver_quarantine", 0),
         "Messages rejected by quality checks (impossible coordinates, absurd speed, bad ID)."),
    ]
    cards = "".join(
        f'<div class="anom anom-{color}"><span class="sev sev-{color}">{sev}</span>'
        f'<div class="anom-n">{num(count, 0)}</div><div class="anom-t">{esc(label)}</div>'
        f'<div class="anom-d">{esc(note)}</div></div>'
        for sev, color, label, count, note in items
    )
    ov = anom.get("overspeed_sample", []) or []
    ov_tbl = ""
    if ov:
        ov_rows = [
            [r.get("name") or r.get("mmsi"), r.get("flag_country"), r.get("category"), r.get("sog")]
            for r in ov
        ]
        ov_tbl = (
            '<div class="panel"><h3>Fastest over-speed outliers</h3>'
            '<p class="hint">Highest reported speeds — worth checking for GPS/AIS glitches.</p>'
            + table(["Vessel", "Flag", "Type", "SOG (kn)"], ov_rows, "none", numeric={3}, tid="ovtable")
            + "</div>"
        )
    return f'<div class="anoms">{cards}</div>{ov_tbl}'


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
    ("Ship-type code",
     "AIS includes a number (0–99) for the kind of vessel — e.g. 70–79 cargo, 80–89 tanker, "
     "60–69 passenger. It tells you the vessel class, not the actual cargo on board."),
    ("Knot",
     "A unit of speed at sea: one nautical mile per hour (about 1.85 km/h)."),
    ("Over-speed outlier",
     "A position report with an implausibly high speed for the vessel class — usually a GPS/AIS "
     "glitch, occasionally a genuinely fast craft."),
    ("COG (Course Over Ground)",
     "The actual direction a vessel is travelling across the water, in degrees (0–360)."),
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

    # --- New visual sections (render only when the data is present) ---
    brief_body = operational_brief(metrics)
    brief_section = section(
        "🧭 Operational brief — routes & anomalies at a glance",
        "An auto-generated read of current traffic, cargo mix, busiest corridors and anomalies.",
        brief_body, "blue",
    ) if brief_body else ""

    map_body = vessel_map(metrics)
    map_section = section(
        "🗺️ Live vessel positions",
        "Where every tracked ship most recently reported, across the four monitored regions. "
        "Hover a dot for details; the dashed boxes are the watched chokepoints.",
        map_body + region_cards(metrics.get("regions", [])),
        "cyan",
    ) if map_body else ""

    cats = metrics.get("vessel_categories", [])
    cargo_section = section(
        "📦 What's being carried — fleet by ship type",
        "AIS broadcasts a ship-type code, not a cargo manifest — so this is the mix of vessel "
        "classes (a proxy for what's moving), counting distinct vessels.",
        f'<div class="panel">{category_bars(cats)}'
        '<p class="hint">Tankers (oil · gas · chemical) and cargo ships (containers · bulk) are the '
        'workhorses of seaborne trade; the rest are service, fishing and passenger craft.</p></div>',
        "gold",
    ) if cats else ""

    anom_body = anomaly_block(metrics)
    anomaly_section = section(
        "🚨 Anomaly detection",
        "Automated flags across behaviour, identity and data quality — a triage view before the "
        "detailed dark-vessel table below.",
        anom_body, "red",
    ) if anom_body else ""

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
          '<p>This project watches ships across four of the world\'s busiest maritime regions '
          '(<b>Singapore, Rotterdam, Houston</b> and the <b>Strait of Hormuz</b>) in real time, and '
          'looks for <b>unusual behaviour</b> rather than just plotting dots on a map. Raw radio '
          'messages flow through three refinement stages '
          '(<span class="tag t-bronze">Bronze</span> → <span class="tag t-cyan">Silver</span> → '
          '<span class="tag t-gold">Gold</span>) and end as the analytics below. Hover the '
          '<span class="ipill">i</span> on any card for a plain-English explanation, and see the '
          '<a href="#glossary-anchor">glossary</a> for terms.</p></section>'
        + '<div class="grid">' + kpis + '</div>'
        + brief_section
        + map_section
        + cargo_section
        + commodity_block(metrics.get("commodity", {}))
        + anomaly_section
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
  .kpi { position:relative; isolation:isolate; overflow:hidden; border-radius:16px;
    padding:18px 16px; border:1px solid rgba(255,255,255,.10);
    background:linear-gradient(158deg, rgba(40,52,90,.55), rgba(13,21,44,.40));
    backdrop-filter:blur(11px) saturate(140%); -webkit-backdrop-filter:blur(11px) saturate(140%);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.20), inset 0 0 22px rgba(255,255,255,.03),
      0 8px 26px rgba(2,6,20,.45);
    transition:transform .18s ease, box-shadow .18s ease; }
  .kpi > * { position:relative; z-index:2; }
  .kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:var(--blue); z-index:2; }
  /* glossy diagonal sheen */
  .kpi::after { content:""; position:absolute; inset:0; z-index:1; pointer-events:none;
    background:linear-gradient(125deg, rgba(255,255,255,.18) 0%, rgba(255,255,255,.06) 16%,
      transparent 38%, transparent 100%); }
  .kpi:hover { transform:translateY(-4px);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.30), 0 16px 42px rgba(2,6,20,.55); }
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
  .two { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .panel, .two > div, .intro {
    background:linear-gradient(160deg, rgba(26,36,66,.55), rgba(11,19,40,.42));
    backdrop-filter:blur(13px) saturate(135%); -webkit-backdrop-filter:blur(13px) saturate(135%);
    border:1px solid rgba(255,255,255,.09); border-radius:16px;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.14), 0 10px 30px rgba(2,6,20,.40); }
  .panel { padding:18px; margin-top:14px; }
  .two > div { padding:16px; }
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
  /* live vessel map */
  .mapwrap { margin-top:6px; }
  .mapwrap svg { width:100%; height:auto; display:block; border-radius:14px;
    border:1px solid var(--line);
    background:radial-gradient(120% 130% at 50% 0%, #0c1838 0%, #081027 60%, #060c1f 100%); }
  .vdot { cursor:pointer; transition:stroke-width .1s ease; }
  .vdot:hover { stroke:#ffffff; stroke-width:1.6; }
  .legend { display:flex; flex-wrap:wrap; gap:9px 16px; margin-top:12px; }
  .legend-item { display:flex; align-items:center; gap:7px; font-size:12px; color:var(--mut); }
  .legend-item b { color:#dbe4ff; font-weight:700; }
  .swatch { width:12px; height:12px; border-radius:3px; display:inline-block;
    box-shadow:0 0 6px rgba(0,0,0,.45); }
  .region-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
    gap:14px; margin-top:18px; }
  .region-card { padding:14px 16px; border-radius:14px;
    background:linear-gradient(160deg, rgba(26,36,66,.55), rgba(11,19,40,.42));
    backdrop-filter:blur(11px) saturate(135%); -webkit-backdrop-filter:blur(11px) saturate(135%);
    border:1px solid rgba(255,255,255,.09);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.13), 0 8px 24px rgba(2,6,20,.4); }
  .rc-name { font-weight:700; color:#dbe4ff; font-size:13.5px; }
  .rc-big { font-size:27px; font-weight:800; color:var(--cyan); margin:6px 0 4px; }
  .rc-big span { font-size:12px; color:var(--mut); font-weight:600; margin-left:6px; }
  .rc-seg { display:flex; height:8px; border-radius:6px; overflow:hidden; background:#0b1230; margin:7px 0; }
  .rc-seg span { display:block; height:100%; }
  .rc-seg .seg-mv { background:linear-gradient(90deg,#1f8fb5,#37d6e6); }
  .rc-seg .seg-id { background:linear-gradient(90deg,#caa033,#ffd24a); }
  .rc-meta { font-size:11.5px; color:var(--mut); }
  .rc-read { margin-top:4px; font-size:12px; font-weight:700; color:#cdd7f5; }
  /* anomaly cards */
  .anoms { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:14px; }
  .anom { position:relative; padding:16px; border-radius:14px;
    background:linear-gradient(160deg, rgba(26,36,66,.55), rgba(11,19,40,.42));
    backdrop-filter:blur(11px) saturate(135%); -webkit-backdrop-filter:blur(11px) saturate(135%);
    border:1px solid rgba(255,255,255,.09);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.13), 0 8px 24px rgba(2,6,20,.4); }
  .anom-n { font-size:30px; font-weight:800; margin-top:8px; }
  .anom-red .anom-n{ color:var(--red); } .anom-gold .anom-n{ color:var(--gold); }
  .anom-purple .anom-n{ color:var(--purple); } .anom-blue .anom-n{ color:var(--blue); }
  .anom-t { font-weight:600; color:#dbe4ff; font-size:13px; margin-top:2px; }
  .anom-d { color:var(--mut); font-size:11.5px; margin-top:6px; line-height:1.45; }
  .sev { font-size:10px; font-weight:800; letter-spacing:.6px; padding:2px 9px; border-radius:999px; }
  .sev-red{ background:rgba(255,107,129,.16); color:var(--red); }
  .sev-gold{ background:rgba(255,210,74,.16); color:var(--gold); }
  .sev-purple{ background:rgba(176,140,255,.16); color:var(--purple); }
  .sev-blue{ background:rgba(90,166,255,.16); color:var(--blue); }
  .brief { padding:18px; }
  .brief p { margin:0 0 10px; color:#cdd7f5; line-height:1.6; }
  .brief p:last-child { margin-bottom:0; }
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
  <div class="subtitle">Real-time maritime <b>behaviour</b> analytics · Singapore · Rotterdam ·
    Houston · Hormuz · dark-vessel detection &amp; commodity signals</div>
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

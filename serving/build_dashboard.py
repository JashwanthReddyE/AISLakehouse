"""Render serving/metrics.json (exported by databricks/export_metrics.py) into a
self-contained serving/dashboard.html. No external dependencies — pure stdlib + inline CSS.

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


def kpi(label: str, value: object, sub: str = "") -> str:
    sub_html = f'<div class="kpi-sub">{esc(sub)}</div>' if sub else ""
    return (
        f'<div class="kpi"><div class="kpi-val">{esc(value)}</div>'
        f'<div class="kpi-label">{esc(label)}</div>{sub_html}</div>'
    )


def table(headers: list[str], rows: list[list[object]], empty: str = "no rows") -> str:
    if not rows:
        return f'<p class="empty">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def bars(rows: list[dict], label_key: str, count_key: str) -> str:
    if not rows:
        return '<p class="empty">no data</p>'
    top = max((r[count_key] for r in rows), default=1) or 1
    out = []
    for r in rows:
        pct = round(100 * r[count_key] / top, 1)
        out.append(
            f'<div class="bar-row"><span class="bar-label">{esc(r[label_key])}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct}%"></span></span>'
            f'<span class="bar-num">{esc(r[count_key])}</span></div>'
        )
    return '<div class="bars">' + "".join(out) + "</div>"


def commodity_section(c: dict) -> str:
    if not c:
        return ""
    share = (c.get("avg_tanker_share", 0) or 0) * 100
    kpis = "".join([
        kpi("Tankers", c.get("distinct_tankers", 0), "AIS type 80-89"),
        kpi("Tanker share", f"{share:.1f}%", "of vessels (avg/hr)"),
        kpi("Floating storage", c.get("floating_storage_candidates", 0), "idle-tanker candidates"),
    ])
    flow_rows = [
        [r.get("hour"), r.get("vessels"), r.get("tankers"), r.get("tanker_share")]
        for r in c.get("flow", [])
    ]
    flow_tbl = table(["Hour (UTC)", "Vessels", "Tankers", "Tanker share"], flow_rows, "no flow data")
    return (
        '<section><h2>Commodity indicators — tanker flow &amp; floating storage</h2>'
        f'<div class="grid">{kpis}</div>'
        f'<div class="panel" style="margin-top:14px">{flow_tbl}'
        '<div class="note"><b>Indicator, not a price predictor.</b> Tanker throughput and floating '
        'storage (idle tankers) are established inputs to crude/gas analysis. A single chokepoint '
        'and short history make this illustrative, not market-grade.</div></div></section>'
    )


def build(metrics: dict) -> str:
    layers = metrics.get("layers", {})
    de = metrics.get("dark_events", {}) or {}
    fresh = metrics.get("freshness", {}) or {}
    qrate = metrics.get("quarantine_rate", 0.0)

    kpis = "".join([
        kpi("Bronze (raw)", f"{layers.get('bronze', 0):,}", "immutable AIS records"),
        kpi("Silver positions", f"{layers.get('silver_positions', 0):,}", "clean · deduped · enriched"),
        kpi("Quarantine rate", f"{qrate * 100:.2f}%", f"{layers.get('silver_quarantine', 0)} flagged"),
        kpi("Ship static", f"{layers.get('silver_ship_static', 0):,}", "normalized destinations"),
        kpi("Dark events", f"{de.get('count', 0):,}", f"{de.get('vessels', 0)} vessels"),
        kpi("Avg confidence", de.get("avg_confidence", 0), "dark-event score"),
    ])

    dark_rows = [
        [e.get("mmsi"), e.get("flag_country"), e.get("dark_start"), e.get("dark_end"),
         e.get("gap_minutes"), e.get("confidence")]
        for e in metrics.get("top_dark_events", [])
    ]
    quar_rows = [[r.get("reason_code"), r.get("count")] for r in metrics.get("quarantine_reasons", [])]

    return TEMPLATE.format(
        generated=esc(metrics.get("generated_at", "")),
        latest_event=esc(fresh.get("latest_event_time") or "—"),
        latest_gold=esc(fresh.get("latest_gold_computed") or "—"),
        kpis=kpis,
        flags=bars(metrics.get("top_flags", []), "flag_country", "count"),
        dests=bars(metrics.get("top_destinations", []), "destination", "count"),
        dark_table=table(
            ["MMSI", "Flag", "Dark start", "Dark end", "Gap (min)", "Confidence"],
            dark_rows, "no dark events detected",
        ),
        quar_table=table(["Reason code", "Count"], quar_rows, "no quarantined records"),
        max_gap=esc(de.get("max_gap_minutes", 0)),
        commodity=commodity_section(metrics.get("commodity", {})),
    )


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AISLakehouse — Maritime Behavior Dashboard</title>
<style>
  :root {{ --bg:#0b1020; --card:#151c33; --line:#243049; --fg:#e8edff; --mut:#8a97bd;
    --accent:#4ea1ff; --warn:#ffb454; --bad:#ff6b6b; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
    font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:32px 20px 64px; }}
  h1 {{ margin:0 0 4px; font-size:26px; letter-spacing:.2px; }}
  .sub {{ color:var(--mut); margin-bottom:8px; }}
  .flow {{ color:var(--mut); font-size:13px; margin:14px 0 26px; }}
  .flow b {{ color:var(--accent); }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:14px; }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .kpi-val {{ font-size:26px; font-weight:700; }}
  .kpi-label {{ color:var(--mut); font-size:13px; margin-top:2px; }}
  .kpi-sub {{ color:var(--mut); font-size:11px; margin-top:6px; opacity:.8; }}
  section {{ margin-top:30px; }}
  h2 {{ font-size:16px; margin:0 0 12px; color:var(--fg); }}
  .panel {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px; }}
  .two {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:720px) {{ .two {{ grid-template-columns:1fr; }} }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--mut); font-weight:600; }}
  td:last-child, th:last-child {{ text-align:right; }}
  .bars {{ display:flex; flex-direction:column; gap:8px; }}
  .bar-row {{ display:grid; grid-template-columns:130px 1fr 44px; align-items:center; gap:10px; }}
  .bar-label {{ color:var(--mut); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .bar-track {{ background:#0d142a; border-radius:6px; height:10px; overflow:hidden; }}
  .bar-fill {{ display:block; height:100%; background:linear-gradient(90deg,#3a7bd5,#4ea1ff); }}
  .bar-num {{ text-align:right; font-variant-numeric:tabular-nums; color:var(--fg); font-size:12px; }}
  .empty {{ color:var(--mut); font-style:italic; }}
  .note {{ color:var(--mut); font-size:12px; margin-top:10px; }}
  footer {{ color:var(--mut); font-size:12px; margin-top:34px; border-top:1px solid var(--line); padding-top:14px; }}
  a {{ color:var(--accent); }}
</style></head>
<body><div class="wrap">
  <h1>AISLakehouse — Maritime Behavior Dashboard</h1>
  <div class="sub">Real-time AIS lakehouse · Singapore Strait · dark-vessel analytics</div>
  <div class="flow"><b>AISStream</b> → consumer → <b>Event Hubs</b> → <b>bronze</b> → <b>silver</b>
    (clean · dedup · enrich · DQ) → <b>gold</b> (dark events)</div>

  <div class="grid">{kpis}</div>

  {commodity}

  <section><h2>Dark-vessel events (highest confidence)</h2>
    <div class="panel">{dark_table}
      <div class="note">Longest silence: {max_gap} min. Caveat: terrestrial AIS has genuine
      coverage dead zones — a dark event is a candidate signal, not proof of intent.</div>
    </div>
  </section>

  <section class="two">
    <div><h2>Flag states (positions)</h2><div class="panel">{flags}</div></div>
    <div><h2>Top destinations (normalized)</h2><div class="panel">{dests}</div></div>
  </section>

  <section><h2>Data-quality quarantine</h2><div class="panel">{quar_table}</div></section>

  <footer>
    Generated {generated} · latest event {latest_event} · gold computed {latest_gold}<br>
    Source: <a href="https://github.com/JashwanthReddyE/AISLakehouse">github.com/JashwanthReddyE/AISLakehouse</a>
  </footer>
</div></body></html>
"""


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "metrics.json"
    metrics = json.loads(src.read_text(encoding="utf-8"))
    out = HERE / "index.html"  # index.html so it serves at the root of GitHub Pages / Vercel
    out.write_text(build(metrics), encoding="utf-8")
    print(f"Wrote {out} from {src}")


if __name__ == "__main__":
    main()

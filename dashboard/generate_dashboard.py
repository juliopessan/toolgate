#!/usr/bin/env python3
"""Gera dashboard HTML self-contained a partir do store, no Ledger Design System.

Uso: python3 dashboard/generate_dashboard.py [--days 30] [--out dashboard.html]
"""
import argparse
import datetime
import html
import json
import os
import sys
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from context_ledger.governance.store import db  # noqa: E402
from waste_ledger_metrics import load_waste_ledger_metrics  # noqa: E402


def q(conn, sql, params):
    return conn.execute(sql, params).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default=str(Path(__file__).parent / "dashboard.html"))
    args = ap.parse_args()
    conn = db.connect()
    since = [f"-{args.days} days"]

    by_project = q(conn, "SELECT project, SUM(cost_usd), SUM(input_tokens+output_tokens) FROM usage WHERE ts>=datetime('now',?) GROUP BY project ORDER BY 2 DESC LIMIT 15", since)
    by_model = q(conn, "SELECT model, SUM(cost_usd) FROM usage WHERE ts>=datetime('now',?) GROUP BY model ORDER BY 2 DESC", since)
    by_day = q(conn, "SELECT date(ts), SUM(cost_usd) FROM usage WHERE ts>=datetime('now',?) GROUP BY 1 ORDER BY 1", since)
    savings = q(conn, "SELECT source, SUM(tokens_saved), SUM(usd_saved) FROM savings WHERE ts>=datetime('now',?) GROUP BY source", since)
    registry = q(conn, "SELECT name, project, model, status, owner FROM agent_registry ORDER BY updated_at DESC", [])
    total = sum(r[1] or 0 for r in by_project)
    saved = sum(r[2] or 0 for r in savings)
    conn.close()

    ledger_db = Path(os.environ.get("AGENT_FINOPS_DB", "~/.agent-finops/telemetry.db")).expanduser()
    try:
        ledger = load_waste_ledger_metrics(ledger_db)
    except Exception:
        ledger = {
            "summary": {
                "artifacts": 0, "tokens_candidate": 0, "tokens_transmitted": 0,
                "tokens_rejected": 0, "actual_cost_usd": 0, "blocked_events": 0,
                "admitted_events": 0, "blended_reduction_pct": 0.0, "active_reservations": 0,
            },
            "by_tier": [], "by_reason": [],
        }

    def table(headers, rows, fmt=None):
        h = "".join(f"<th>{x}</th>" for x in headers)
        body = ""
        for r in rows:
            cells = "".join(f"<td>{html.escape(str((fmt or (lambda i, v: v))(i, v)))}</td>" for i, v in enumerate(r))
            body += f"<tr>{cells}</tr>"
        thead = f"<thead><tr>{h}</tr></thead>" if h else ""
        tbod = f"<tbody>{body or '<tr><td colspan=99>sem dados</td></tr>'}</tbody>"
        return f"<table>{thead}{tbod}</table>"

    def dict_table(headers, rows, keys, fmt=None):
        return table(headers, [[r.get(k) for k in keys] for r in rows], fmt)

    money = lambda i, v: f"US$ {v:.2f}" if isinstance(v, float) else (f"{v:,}" if isinstance(v, int) else v)
    tokens_fmt = lambda i, v: f"{v:,}" if isinstance(v, int) else v
    days_labels = json.dumps([r[0] for r in by_day])
    days_values = json.dumps([round(r[1] or 0, 2) for r in by_day])

    s = ledger["summary"]
    candidate = int(s["tokens_candidate"])
    transmitted = int(s["tokens_transmitted"])
    rejected = int(s["tokens_rejected"])
    reduction_pct = s["blended_reduction_pct"]
    bar_pct = round((transmitted / candidate) * 100, 1) if candidate else 0.0

    page = Template("""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Context Ledger · Waste Ledger Dashboard</title>
<style>
  :root{
    --bg:#F0EEE6; --ink:#15140F; --ink-soft:#5B584E; --line:#D8D4C6; --line-strong:#B8B39F;
    --card-dark:#14140F; --orange:#FF5A36; --green:#7FD79A; --green-bg:#1C2A1E; --green-ink:#BFF0CE;
    --mono:'IBM Plex Mono','JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.6}
  .wrap{max-width:1180px;margin:0 auto;padding:0 40px}
  nav.top{display:flex;align-items:center;justify-content:space-between;padding:26px 40px;max-width:1180px;margin:0 auto}
  .brand{display:flex;align-items:center;gap:12px;font-family:var(--mono);font-weight:700;letter-spacing:.06em;font-size:14px}
  .brand .mark{width:30px;height:30px;background:var(--ink);color:var(--bg);display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-weight:700;font-size:14px}
  .badge{font-family:var(--mono);font-size:11px;border:1px solid var(--line-strong);padding:7px 12px;letter-spacing:.04em}

  .hero{padding:20px 0 40px}
  .eyebrow{display:flex;align-items:center;gap:12px;font-family:var(--mono);font-size:12px;letter-spacing:.14em;color:var(--ink-soft);margin-bottom:16px}
  .eyebrow .rule{width:26px;height:1px;background:var(--line-strong)}
  h1{font-family:var(--sans);font-weight:800;font-size:40px;letter-spacing:-.02em;margin-bottom:10px}
  .sub{font-size:15px;color:var(--ink-soft);max-width:70ch}

  .ledger{background:var(--card-dark);color:#EDEBDF;padding:26px;margin:36px 0;border-radius:2px}
  .ledger-head{display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:#A9A692;border-bottom:1px solid #2B2A21;padding-bottom:16px;margin-bottom:20px}
  .ledger-head .dot{width:8px;height:8px;border-radius:50%;background:var(--green);display:inline-block;margin-right:8px}
  .ledger-head .title{color:#EDEBDF;font-weight:700}
  .row-label{font-family:var(--mono);font-size:13px;color:#A9A692;display:flex;justify-content:space-between;margin-bottom:8px}
  .row-label .num{font-family:var(--mono);font-weight:700;font-size:22px}
  .num.orange{color:var(--orange)} .num.green{color:var(--green)}
  .bar-track{height:6px;background:#2B2A21;margin-bottom:22px;overflow:hidden}
  .bar-fill{height:100%} .bar-fill.orange{background:var(--orange);width:100%}
  .bar-fill.green{background:var(--green);width:$bar_pct%}
  .stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px 12px;padding:20px 0;border-top:1px solid #2B2A21;border-bottom:1px solid #2B2A21;margin-bottom:8px}
  .stat-grid .num{font-family:var(--mono);font-weight:700;font-size:24px;display:block}
  .stat-grid .lab{font-family:var(--mono);font-size:11px;color:#A9A692}
  @media (max-width:820px){.stat-grid{grid-template-columns:repeat(2,1fr)}}

  section{padding:44px 0;border-top:1px solid var(--line)}
  .sec-head{display:flex;align-items:baseline;gap:16px;margin-bottom:24px}
  .sec-num{font-family:var(--mono);font-size:12px;color:var(--ink-soft)}
  h2{font-family:var(--sans);font-weight:800;font-size:26px;letter-spacing:-.01em}

  .stat-tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0 28px}
  .stat-tile{border:1px solid var(--line-strong);padding:18px 16px}
  .stat-tile .v{font-family:var(--mono);font-size:26px;font-weight:700}
  .stat-tile .l{font-family:var(--mono);font-size:11px;color:var(--ink-soft);letter-spacing:.05em;margin-top:4px}
  @media (max-width:820px){.stat-tiles{grid-template-columns:repeat(2,1fr)}}

  table{width:100%;border-collapse:collapse;margin:12px 0 8px;font-size:13.5px;font-family:var(--mono)}
  thead{border-bottom:1px solid var(--ink)}
  th{text-align:left;padding:10px 14px;font-size:11px;letter-spacing:.06em;color:var(--ink-soft);font-weight:600}
  tbody tr{border-bottom:1px solid var(--line)}
  td{padding:10px 14px;color:var(--ink)}

  .chart{border:1px solid var(--line-strong);padding:20px;margin-top:12px}
  .chart canvas{width:100%;height:auto;display:block}

  footer{padding:40px 0 60px;border-top:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;font-family:var(--mono);font-size:12px;color:var(--ink-soft);flex-wrap:wrap;gap:12px}
</style>
</head>
<body>
<nav class="top">
  <div class="brand"><span class="mark">C</span> CONTEXT LEDGER</div>
  <div class="badge">WASTE LEDGER DASHBOARD</div>
</nav>
<div class="wrap">
  <div class="hero">
    <div class="eyebrow"><span class="rule"></span> GOVERNANCE &amp; OBSERVABILITY · PLANE 4</div>
    <h1>Waste Ledger</h1>
    <p class="sub">Custo, tokens e decisões do Guardian nos últimos $days dias — gerado localmente a partir do SQLite em <code>$db_path</code>.</p>
  </div>

  <div class="ledger">
    <div class="ledger-head">
      <span><span class="dot"></span><span class="title">ENFORCEMENT SUMMARY</span></span>
      <span>$artifacts artefatos · $admitted_events admitidos · $blocked_events bloqueados</span>
    </div>
    <div class="row-label"><span>Candidato · o que os agentes pediram</span><span class="num orange">$tokens_candidate</span></div>
    <div class="bar-track"><div class="bar-fill orange"></div></div>
    <div class="row-label"><span>Transmitido · o que passou pelo Guardian</span><span class="num green">$tokens_transmitted</span></div>
    <div class="bar-track"><div class="bar-fill green"></div></div>
    <div class="stat-grid">
      <div><span class="num">$reduction_pct%</span><span class="lab">redução blended</span></div>
      <div><span class="num">$tokens_rejected</span><span class="lab">tokens rejeitados</span></div>
      <div><span class="num">$active_reservations</span><span class="lab">reservas ativas</span></div>
      <div><span class="num">US$ $actual_cost_usd</span><span class="lab">custo medido (audit)</span></div>
    </div>
  </div>

  <section>
    <div class="sec-head"><span class="sec-num">01</span><h2>Resumo de custo</h2></div>
    <div class="stat-tiles">
      <div class="stat-tile"><div class="v">US$ $total</div><div class="l">CUSTO TOTAL</div></div>
      <div class="stat-tile"><div class="v">US$ $saved</div><div class="l">ECONOMIA REGISTRADA</div></div>
      <div class="stat-tile"><div class="v">$len_by_project</div><div class="l">PROJETOS ATIVOS</div></div>
      <div class="stat-tile"><div class="v">$len_registry</div><div class="l">AGENTES NO REGISTRY</div></div>
    </div>
  </section>

  <section>
    <div class="sec-head"><span class="sec-num">02</span><h2>Consumo</h2></div>
    <div class="chart"><canvas id="c"></canvas></div>
    <h2 style="margin-top:28px;font-size:18px">Por projeto</h2>
    $table_project
    <h2 style="margin-top:24px;font-size:18px">Por modelo</h2>
    $table_model
  </section>

  <section>
    <div class="sec-head"><span class="sec-num">03</span><h2>Guardian por tier</h2></div>
    $table_tier
    <h2 style="margin-top:24px;font-size:18px">Por reason code</h2>
    $table_reason
  </section>

  <section>
    <div class="sec-head"><span class="sec-num">04</span><h2>Economia por camada</h2></div>
    $table_savings
  </section>

  <section>
    <div class="sec-head"><span class="sec-num">05</span><h2>Agent registry</h2></div>
    $table_registry
  </section>
</div>
<footer>
  <span>context-ledger runtime</span>
  <span>Gerado localmente em $today</span>
  <span>Deterministic before probabilistic.</span>
</footer>
<script>
const L=$days_labels,V=$days_values;
const canvas=document.getElementById('c');
if(canvas&&L.length>0&&V.length>0){
  const rect=canvas.parentElement.getBoundingClientRect();
  canvas.width=Math.max(rect.width-32,600);
  canvas.height=220;
  const c=canvas.getContext('2d');
  const W=canvas.width,H=canvas.height,m=Math.max(...V,1);
  c.strokeStyle='#FF5A36';c.lineWidth=2.5;c.beginPath();
  V.forEach((v,i)=>{const x=40+i*(W-80)/Math.max(V.length-1,1),y=H-40-(v/m)*(H-80);i?c.lineTo(x,y):c.moveTo(x,y);});
  c.stroke();
  c.font='11px monospace';c.textAlign='center';c.fillStyle='#5B584E';
  L.forEach((l,i)=>{if(i%Math.ceil(L.length/8)===0)c.fillText(l.slice(5),40+i*(W-80)/Math.max(L.length-1,1),H-10);});
}
</script>
</body>
</html>""")
    page = page.safe_substitute(
        days=args.days,
        db_path=str(ledger_db),
        total=f"{total:.2f}",
        saved=f"{saved:.2f}",
        len_by_project=len(by_project),
        len_registry=len(registry),
        table_project=table(["projeto", "custo", "tokens"], by_project, money),
        table_model=table(["modelo", "custo"], by_model, money),
        table_savings=table(["camada", "tokens poupados", "USD"], savings, money),
        table_registry=table(["agente", "projeto", "modelo", "status", "owner"], registry),
        table_tier=dict_table(
            ["tier", "eventos", "tokens transmitidos", "custo medido"],
            ledger["by_tier"], ["tier", "events", "tokens_transmitted", "actual_cost_usd"], money,
        ),
        table_reason=dict_table(
            ["reason code", "eventos"], ledger["by_reason"], ["reason_code", "events"],
        ),
        artifacts=s["artifacts"],
        admitted_events=s["admitted_events"],
        blocked_events=s["blocked_events"],
        tokens_candidate=f"{candidate:,}",
        tokens_transmitted=f"{transmitted:,}",
        tokens_rejected=f"{rejected:,}",
        reduction_pct=reduction_pct,
        active_reservations=s["active_reservations"],
        actual_cost_usd=f"{float(s['actual_cost_usd']):.2f}",
        bar_pct=bar_pct,
        days_labels=days_labels,
        days_values=days_values,
        today=datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
    )
    Path(args.out).write_text(page, encoding='utf-8')
    print(f"Dashboard gerado: {args.out}")


if __name__ == "__main__":
    main()

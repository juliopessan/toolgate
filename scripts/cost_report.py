#!/usr/bin/env python3
"""Relatório FinOps: custo x projeto x modelo x período.

Uso:
  python3 scripts/cost_report.py [--days 30] [--project NOME] [--by model|project|day]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from tollgate.governance.store import db  # noqa: E402

GROUPS = {"project": "project", "model": "model", "day": "date(ts)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--project")
    ap.add_argument("--by", choices=GROUPS, default="project")
    args = ap.parse_args()

    conn = db.connect()
    where = "WHERE ts >= datetime('now', ?)"
    params: list = [f"-{args.days} days"]
    if args.project:
        where += " AND project = ?"
        params.append(args.project)
    g = GROUPS[args.by]
    rows = conn.execute(
        f"""SELECT {g} AS grp,
               SUM(input_tokens), SUM(output_tokens),
               SUM(cache_read_tokens), SUM(cache_write_tokens),
               SUM(cost_usd), COUNT(*)
        FROM usage {where} GROUP BY grp ORDER BY SUM(cost_usd) DESC""",
        params,
    ).fetchall()

    total = sum(r[5] or 0 for r in rows)
    print(f"\n== tollgate · custo últimos {args.days} dias · por {args.by} ==\n")
    print(f"{'grupo':<32}{'in':>12}{'out':>12}{'cache_r':>12}{'cache_w':>12}{'USD':>10}{'msgs':>7}")
    for grp, i, o, cr, cw, usd, n in rows:
        print(f"{str(grp)[:31]:<32}{i or 0:>12,}{o or 0:>12,}{cr or 0:>12,}{cw or 0:>12,}{usd or 0:>10.2f}{n:>7}")
    print(f"\nTOTAL: US$ {total:.2f}")

    # Economia registrada (headroom/ast/rightsizing)
    sav = conn.execute(
        "SELECT source, SUM(tokens_saved), SUM(usd_saved) FROM savings"
        " WHERE ts >= datetime('now', ?) GROUP BY source",
        [f"-{args.days} days"],
    ).fetchall()
    if sav:
        print("\n-- economia registrada --")
        for src, tk, usd in sav:
            print(f"  {src:<14} {tk or 0:>12,} tokens  US$ {usd or 0:.2f}")
    conn.close()


if __name__ == "__main__":
    main()

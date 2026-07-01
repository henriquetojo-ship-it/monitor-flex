#!/usr/bin/env python3
"""
twilio_query.py — Consulta e agrega relatórios históricos de atendimento Twilio

Modos:
  --mode list           Lista relatórios dos últimos N dias (padrão: 60)
  --mode day            Mostra relatório de um dia específico
  --mode weekly         Gera fechamento semanal (última semana ou --week YYYY-WNN)
  --mode monthly        Gera fechamento mensal (último mês ou --month YYYY-MM)
  --mode search         Busca relatórios por texto livre na avaliação

Exemplos:
  python3 scripts/twilio_query.py --mode list
  python3 scripts/twilio_query.py --mode list --days 30
  python3 scripts/twilio_query.py --mode day --date 2026-06-17
  python3 scripts/twilio_query.py --mode weekly
  python3 scripts/twilio_query.py --mode weekly --week 2026-W25
  python3 scripts/twilio_query.py --mode monthly --month 2026-06
  python3 scripts/twilio_query.py --mode search --query "fila"
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent
ATENDIMENTO_DIR = BASE_DIR / "estudos" / "atendimento"

BRT = timezone(timedelta(hours=-3))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def brt_now() -> datetime:
    return datetime.now(BRT)


def parse_report_filename(name: str) -> Optional[tuple]:
    """Returns (date_str, session) from filename like '2026-06-17_14h.json'."""
    stem = name.replace(".json", "").replace(".md", "")
    parts = stem.rsplit("_", 1)
    if len(parts) != 2:
        return None
    date_str, session = parts
    if not session.endswith("h"):
        return None
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    return date_str, session


def load_report(path: Path) -> Optional[dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] Não foi possível carregar {path.name}: {e}", file=sys.stderr)
        return None


def list_json_reports(days: int = 60) -> list[Path]:
    """Returns all JSON report files within the last N days, sorted desc."""
    cutoff = (brt_now() - timedelta(days=days)).date()
    reports = []
    for p in ATENDIMENTO_DIR.glob("????-??-??_??h.json"):
        parsed = parse_report_filename(p.name)
        if not parsed:
            continue
        date_str, _ = parsed
        if date.fromisoformat(date_str) >= cutoff:
            reports.append(p)
    return sorted(reports, reverse=True)


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def fmt_duration(seconds: int) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def safe_avg(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def safe_sum(values: list) -> int:
    return sum(v for v in values if v is not None)


# ---------------------------------------------------------------------------
# Mode: list
# ---------------------------------------------------------------------------

def mode_list(days: int, output_json: bool):
    reports = list_json_reports(days)
    if not reports:
        print(f"Nenhum relatório encontrado nos últimos {days} dias.")
        return

    rows = []
    for p in reports:
        parsed = parse_report_filename(p.name)
        if not parsed:
            continue
        date_str, session = parsed
        r = load_report(p)
        has_alerts = (r or {}).get("metrics", {}).get("has_alerts", "?")
        ev = (r or {}).get("evaluation", {})
        vulns = len(ev.get("tactical_vulnerabilities", []))
        rows.append({
            "report_id": f"{date_str}_{session}",
            "date": date_str,
            "session": session,
            "has_alerts": has_alerts,
            "vulnerabilities": vulns,
            "file": str(p.relative_to(BASE_DIR)),
        })

    if output_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'Relatórios de Atendimento — últimos ' + str(days) + ' dias':=<60}")
        print(f"{'ID':<28} {'Alertas':<10} {'Vulns':<6}")
        print("-" * 45)
        for r in rows:
            icon = "🔴" if r["has_alerts"] is True else ("🟢" if r["has_alerts"] is False else "?")
            print(f"{r['report_id']:<28} {icon:<10} {r['vulnerabilities']:<6}")
        print(f"\nTotal: {len(rows)} relatório(s)\n")


# ---------------------------------------------------------------------------
# Mode: day
# ---------------------------------------------------------------------------

def mode_day(target_date: str, output_json: bool):
    sessions = ["11h", "14h", "18h"]
    found = []
    for session in sessions:
        p = ATENDIMENTO_DIR / f"{target_date}_{session}.json"
        if p.exists():
            r = load_report(p)
            if r:
                found.append(r)

    if not found:
        print(f"Nenhum relatório encontrado para {target_date}.")
        return

    if output_json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return

    for r in found:
        _print_report_summary(r)


def _print_report_summary(r: dict):
    print(f"\n{'=' * 60}")
    print(f"  {r.get('report_id', '?')}  —  {r.get('generated_at', '?')[:19]}")
    print(f"{'=' * 60}")
    m = r.get("metrics", {})
    ag = m.get("agents", {})
    cum = m.get("cumulative", {})
    rt = m.get("tasks_rt", {})
    print(f"  Agentes: {ag.get('online','?')}/{ag.get('total','?')} online · "
          f"{ag.get('available','?')} disp. · {ag.get('utilization_pct','?')}% util.")
    print(f"  Tarefas: {cum.get('tasks_created','?')} criadas · "
          f"{cum.get('tasks_completed','?')} concluídas · "
          f"aceitação {cum.get('acceptance_rate_pct','?')}%")
    print(f"  Espera: média {fmt_duration(cum.get('avg_wait_sec'))} · "
          f"máx {fmt_duration(cum.get('max_wait_sec'))}")
    ev = r.get("evaluation", {})
    print(f"\n  Resumo Executivo:\n  {ev.get('executive_summary', '—')}")
    vulns = ev.get("tactical_vulnerabilities", [])
    if vulns:
        print(f"\n  Vulnerabilidades ({len(vulns)}):")
        for v in vulns[:3]:
            pri = v.get("priority", "?")
            issue = v.get("issue", v.get("description", "?"))
            print(f"    [{pri}] {issue}")
    qw = ev.get("quick_wins", [])
    if qw:
        print(f"\n  Quick Wins ({len(qw)}):")
        for q in qw[:3]:
            action = q.get("action", "?")
            print(f"    • {action}")
    print()


# ---------------------------------------------------------------------------
# Mode: weekly
# ---------------------------------------------------------------------------

def mode_weekly(week: Optional[str], output_json: bool, save: bool):
    if week:
        year, wnum = week.split("-W")
        year, wnum = int(year), int(wnum)
        target_monday = date.fromisocalendar(year, wnum, 1)
    else:
        today = brt_now().date()
        y, w, wd = today.isocalendar()
        target_monday = date.fromisocalendar(y, w, 1)

    target_week = iso_week(target_monday)
    week_dates = [target_monday + timedelta(days=i) for i in range(5)]  # Mon-Fri

    reports = []
    for d in week_dates:
        date_str = d.isoformat()
        # Prefer 18h (end-of-day) then 14h then 11h
        for session in ["18h", "14h", "11h"]:
            p = ATENDIMENTO_DIR / f"{date_str}_{session}.json"
            if p.exists():
                r = load_report(p)
                if r:
                    reports.append(r)
                    break

    if not reports:
        print(f"Nenhum relatório encontrado para a semana {target_week}.")
        return

    summary = _aggregate_reports(reports, f"Fechamento Semanal {target_week}", target_week)

    if save:
        out_path = ATENDIMENTO_DIR / "weekly" / f"{target_week}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"Fechamento semanal salvo em: {out_path.relative_to(BASE_DIR)}", file=sys.stderr)

    if output_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_closing_summary(summary)


# ---------------------------------------------------------------------------
# Mode: monthly
# ---------------------------------------------------------------------------

def mode_monthly(month: Optional[str], output_json: bool, save: bool):
    if month:
        year, mon = map(int, month.split("-"))
    else:
        today = brt_now().date()
        year, mon = today.year, today.month

    month_str = f"{year}-{mon:02d}"
    cutoff_start = date(year, mon, 1)
    if mon == 12:
        cutoff_end = date(year + 1, 1, 1)
    else:
        cutoff_end = date(year, mon + 1, 1)

    # Collect all 18h reports for the month (end-of-day snapshots)
    reports = []
    current = cutoff_start
    while current < cutoff_end:
        if current.weekday() < 5:  # Mon-Fri
            date_str = current.isoformat()
            for session in ["18h", "14h", "11h"]:
                p = ATENDIMENTO_DIR / f"{date_str}_{session}.json"
                if p.exists():
                    r = load_report(p)
                    if r:
                        reports.append(r)
                        break
        current += timedelta(days=1)

    if not reports:
        print(f"Nenhum relatório encontrado para {month_str}.")
        return

    summary = _aggregate_reports(reports, f"Fechamento Mensal {month_str}", month_str)

    if save:
        out_path = ATENDIMENTO_DIR / "monthly" / f"{month_str}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"Fechamento mensal salvo em: {out_path.relative_to(BASE_DIR)}", file=sys.stderr)

    if output_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_closing_summary(summary)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate_reports(reports: list[dict], title: str, period_key: str) -> dict:
    """Aggregate a list of daily reports into a period summary."""
    cumulative_keys = [
        "tasks_created", "tasks_completed", "tasks_canceled",
    ]
    avg_keys = [
        "acceptance_rate_pct", "abandonment_rate_pct", "avg_wait_sec",
    ]

    # Accumulate metrics
    totals = {k: [] for k in cumulative_keys + avg_keys}
    agent_samples = []
    all_alerts = []

    for r in reports:
        m = r.get("metrics", {})
        cum = m.get("cumulative", {})
        ag = m.get("agents", {})

        for k in cumulative_keys:
            if cum.get(k) is not None:
                totals[k].append(cum[k])

        for k in avg_keys:
            if cum.get(k) is not None:
                totals[k].append(cum[k])

        agent_samples.append({
            "online": ag.get("online"),
            "available": ag.get("available"),
            "utilization_pct": ag.get("utilization_pct"),
        })

        if m.get("has_alerts"):
            all_alerts.extend(m.get("alerts", []))

    # Collect all unique issues from evaluations
    all_vulns = []
    all_qw = []
    all_structural = []
    for r in reports:
        ev = r.get("evaluation", {})
        all_vulns.extend(ev.get("tactical_vulnerabilities", []))
        all_qw.extend(ev.get("quick_wins", []))
        all_structural.extend(ev.get("structural_measures", []))

    return {
        "period": period_key,
        "title": title,
        "generated_at": datetime.now(BRT).isoformat(),
        "days_sampled": len(reports),
        "reports_included": [r.get("report_id", "?") for r in reports],
        "aggregated_metrics": {
            "total_tasks_created": safe_sum(totals["tasks_created"]),
            "total_tasks_completed": safe_sum(totals["tasks_completed"]),
            "total_tasks_canceled": safe_sum(totals["tasks_canceled"]),
            "avg_acceptance_rate_pct": safe_avg(totals["acceptance_rate_pct"]),
            "avg_abandonment_rate_pct": safe_avg(totals["abandonment_rate_pct"]),
            "avg_wait_sec": safe_avg(totals["avg_wait_sec"]),
            "avg_agents_online": safe_avg([s["online"] for s in agent_samples]),
            "avg_agents_available": safe_avg([s["available"] for s in agent_samples]),
            "avg_utilization_pct": safe_avg([s["utilization_pct"] for s in agent_samples]),
            "days_with_alerts": sum(
                1 for r in reports if r.get("metrics", {}).get("has_alerts")
            ),
        },
        "recurrent_issues": {
            "tactical_vulnerabilities": all_vulns,
            "quick_wins": all_qw,
            "structural_measures": all_structural,
        },
        "alert_episodes": all_alerts,
    }


def _print_closing_summary(s: dict):
    print(f"\n{'=' * 60}")
    print(f"  {s['title']}")
    print(f"  {s['days_sampled']} dias amostrados · Gerado {s['generated_at'][:19]}")
    print(f"{'=' * 60}")
    m = s["aggregated_metrics"]
    print(f"\n  Volume:")
    print(f"    Tarefas criadas:    {m.get('total_tasks_created', '—'):>6}")
    print(f"    Tarefas concluídas: {m.get('total_tasks_completed', '—'):>6}")
    print(f"    Tarefas canceladas: {m.get('total_tasks_canceled', '—'):>6}")
    print(f"\n  Qualidade (média do período):")
    print(f"    Taxa de aceitação:  {m.get('avg_acceptance_rate_pct', '—'):>6}%")
    print(f"    Taxa de abandono:   {m.get('avg_abandonment_rate_pct', '—'):>6}%")
    print(f"    Espera média:       {fmt_duration(m.get('avg_wait_sec'))}")
    print(f"\n  Agentes (média do período):")
    print(f"    Online:             {m.get('avg_agents_online', '—')}")
    print(f"    Disponíveis:        {m.get('avg_agents_available', '—')}")
    print(f"    Utilização:         {m.get('avg_utilization_pct', '—')}%")
    print(f"\n  Dias com alertas: {m.get('days_with_alerts', 0)}/{s['days_sampled']}\n")


# ---------------------------------------------------------------------------
# Mode: search
# ---------------------------------------------------------------------------

def mode_search(query: str, days: int, output_json: bool):
    reports = list_json_reports(days)
    results = []
    q = query.lower()

    for p in reports:
        r = load_report(p)
        if not r:
            continue
        ev = r.get("evaluation", {})
        text = json.dumps(ev, ensure_ascii=False).lower()
        if q in text:
            results.append({
                "report_id": r.get("report_id"),
                "generated_at": r.get("generated_at", "")[:19],
                "executive_summary": ev.get("executive_summary", ""),
                "match_count": text.count(q),
            })

    if output_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        if not results:
            print(f'Nenhum resultado para "{query}".')
        else:
            print(f'\n{len(results)} relatório(s) mencionam "{query}":\n')
            for r in results:
                print(f"  [{r['report_id']}] ({r['match_count']}x) {r['executive_summary'][:80]}…")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Consulta histórica de relatórios de atendimento Twilio")
    parser.add_argument("--mode", choices=["list", "day", "weekly", "monthly", "search"],
                        default="list", help="Modo de operação")
    parser.add_argument("--days", type=int, default=60,
                        help="Janela em dias para list/search (padrão: 60)")
    parser.add_argument("--date", type=str, help="Data para --mode day (YYYY-MM-DD)")
    parser.add_argument("--week", type=str, help="Semana para --mode weekly (YYYY-WNN)")
    parser.add_argument("--month", type=str, help="Mês para --mode monthly (YYYY-MM)")
    parser.add_argument("--query", type=str, help="Texto para --mode search")
    parser.add_argument("--json", action="store_true", help="Saída em JSON")
    parser.add_argument("--save", action="store_true",
                        help="Salvar fechamento semanal/mensal em arquivo")
    args = parser.parse_args()

    if args.mode == "list":
        mode_list(args.days, args.json)
    elif args.mode == "day":
        if not args.date:
            today = brt_now().date().isoformat()
            print(f"[info] --date não especificado, usando hoje: {today}", file=sys.stderr)
            args.date = today
        mode_day(args.date, args.json)
    elif args.mode == "weekly":
        mode_weekly(args.week, args.json, args.save)
    elif args.mode == "monthly":
        mode_monthly(args.month, args.json, args.save)
    elif args.mode == "search":
        if not args.query:
            print("[erro] --query é obrigatório para --mode search", file=sys.stderr)
            sys.exit(1)
        mode_search(args.query, args.days, args.json)


if __name__ == "__main__":
    main()

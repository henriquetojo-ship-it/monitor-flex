#!/usr/bin/env python3
"""
Agente Twilio Flex — Mecanizou
Consulta e analisa métricas do Flex/TaskRouter em tempo real e acumulado.

Uso:
    python3 scripts/twilio_agent.py
    python3 scripts/twilio_agent.py --minutes 60
    python3 scripts/twilio_agent.py --minutes 480 --output-dir estudos/snapshots

Saídas:
    - Relatório markdown em estudos/snapshots/
    - Snapshot JSON em estudos/snapshots/
    - JSON resumo no stdout (para integração com Claude Code / agendador)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent.parent
ENV_PATH = BASE_DIR / ".env.local"

TASKROUTER_BASE = "https://taskrouter.twilio.com/v1"

# Filas a ignorar na análise detalhada (internas/teste)
QUEUE_IGNORE = {"everyone", "fila-teste"}

# Limite de espera para alertar (segundos)
ALERT_WAIT_THRESHOLD_SEC = 120

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env() -> dict:
    env = dotenv_values(ENV_PATH)
    required = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WORKSPACE_SID"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        print(f"[ERRO] Variáveis ausentes no .env.local: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return env


def tw_get(url: str, auth: tuple, params: dict = None) -> dict:
    resp = requests.get(url, auth=auth, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def pct(num: int, den: int) -> str:
    if den == 0:
        return "—"
    return f"{num / den * 100:.1f}%"


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_all(env: dict, minutes: int) -> dict:
    sid = env["TWILIO_ACCOUNT_SID"]
    token = env["TWILIO_AUTH_TOKEN"]
    ws = env["TWILIO_WORKSPACE_SID"]
    auth = (sid, token)
    base = f"{TASKROUTER_BASE}/Workspaces/{ws}"

    print("[1/5] Workspace real-time...", file=sys.stderr)
    workspace_rt = tw_get(f"{base}/RealTimeStatistics", auth)

    print("[2/5] Workers real-time...", file=sys.stderr)
    workers_rt = tw_get(f"{base}/Workers/RealTimeStatistics", auth)

    print("[3/5] Estatísticas acumuladas...", file=sys.stderr)
    cumulative = tw_get(f"{base}/CumulativeStatistics", auth, {"Minutes": minutes})

    print("[4/5] Lista de filas...", file=sys.stderr)
    queues_list = tw_get(f"{base}/TaskQueues", auth, {"PageSize": 100})
    queues = [q for q in queues_list.get("task_queues", [])
              if q["friendly_name"] not in QUEUE_IGNORE]

    print("[5/5] Stats por fila...", file=sys.stderr)
    queues_rt = []
    for q in queues:
        try:
            stats = tw_get(f"{base}/TaskQueues/{q['sid']}/RealTimeStatistics", auth)
            queues_rt.append({
                "sid": q["sid"],
                "name": q["friendly_name"],
                "stats": stats,
            })
        except Exception as e:
            print(f"  [warn] {q['friendly_name']}: {e}", file=sys.stderr)

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "minutes_window": minutes,
        "workspace_rt": workspace_rt,
        "workers_rt": workers_rt,
        "cumulative": cumulative,
        "queues_rt": queues_rt,
    }


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------

def analyze(data: dict) -> dict:
    wrt = data["workspace_rt"]
    cum = data["cumulative"]
    workers_rt = data["workers_rt"]

    # Agentes por atividade
    agents = {a["friendly_name"]: a["workers"] for a in workers_rt["activity_statistics"]}
    total_agents = workers_rt["total_workers"]
    available = agents.get("Available", 0)
    on_break = agents.get("Break", 0)
    offline = agents.get("Offline", 0)
    unavailable = agents.get("Unavailable", 0)
    online = total_agents - offline
    utilization = (online - available) / online if online > 0 else 0

    # Tarefas em tempo real
    tasks_status = wrt.get("tasks_by_status", {})
    assigned = tasks_status.get("assigned", 0)
    pending = tasks_status.get("pending", 0)
    reserved = tasks_status.get("reserved", 0)
    wrapping = tasks_status.get("wrapping", 0)
    total_tasks = wrt.get("total_tasks", 0)
    longest_wait = wrt.get("longest_task_waiting_age", 0)

    # Acumulado
    created = cum.get("tasks_created", 0)
    completed = cum.get("tasks_completed", 0)
    canceled = cum.get("tasks_canceled", 0)
    accepted = cum.get("reservations_accepted", 0)
    timed_out = cum.get("reservations_timed_out", 0)
    rejected = cum.get("reservations_rejected", 0)
    wait_stats = cum.get("wait_duration_until_accepted", {})
    avg_wait = wait_stats.get("avg", 0)
    max_wait = wait_stats.get("max", 0)
    acceptance_rate = accepted / (accepted + rejected + timed_out) if (accepted + rejected + timed_out) > 0 else 0
    abandonment_rate = canceled / created if created > 0 else 0

    # Filas com backlog ou espera alta
    alert_queues = []
    for q in data["queues_rt"]:
        s = q["stats"]
        p = s.get("tasks_by_status", {}).get("pending", 0)
        lw = s.get("longest_task_waiting_age", 0)
        if p > 0 or lw > ALERT_WAIT_THRESHOLD_SEC:
            alert_queues.append({
                "name": q["name"],
                "pending": p,
                "longest_wait_sec": lw,
            })

    return {
        "agents": {
            "total": total_agents,
            "online": online,
            "available": available,
            "on_break": on_break,
            "offline": offline,
            "unavailable": unavailable,
            "utilization_pct": round(utilization * 100, 1),
        },
        "tasks_rt": {
            "total": total_tasks,
            "assigned": assigned,
            "pending": pending,
            "reserved": reserved,
            "wrapping": wrapping,
            "longest_wait_sec": longest_wait,
        },
        "cumulative": {
            "window_minutes": data["minutes_window"],
            "tasks_created": created,
            "tasks_completed": completed,
            "tasks_canceled": canceled,
            "reservations_accepted": accepted,
            "acceptance_rate_pct": round(acceptance_rate * 100, 1),
            "abandonment_rate_pct": round(abandonment_rate * 100, 1),
            "avg_wait_sec": avg_wait,
            "max_wait_sec": max_wait,
        },
        "alerts": alert_queues,
        "has_alerts": len(alert_queues) > 0 or pending > 0 or longest_wait > ALERT_WAIT_THRESHOLD_SEC,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def render_report(data: dict, analysis: dict) -> str:
    ts = datetime.now().strftime("%d/%m/%Y às %H:%M")
    win = data["minutes_window"]
    a = analysis
    ag = a["agents"]
    rt = a["tasks_rt"]
    cum = a["cumulative"]

    status_icon = "🟢" if not a["has_alerts"] else "🔴"

    lines = [
        f"# {status_icon} Flex — Painel Mecanizou",
        f"_Gerado em {ts} · Janela acumulada: últimos {win} min_",
        "",
        "---",
        "",
        "## Agentes",
        "",
        f"| Status       | Qtd |",
        f"|---|---|",
        f"| Online       | **{ag['online']}** / {ag['total']} |",
        f"| Disponível   | {ag['available']} |",
        f"| Em pausa     | {ag['on_break']} |",
        f"| Offline      | {ag['offline']} |",
        f"| Utilização   | **{ag['utilization_pct']}%** |",
        "",
        "## Tarefas (tempo real)",
        "",
        f"| Métrica              | Valor |",
        f"|---|---|",
        f"| Total ativas         | **{rt['total']}** |",
        f"| Em atendimento       | {rt['assigned']} |",
        f"| Aguardando (pending) | {rt['pending']} |",
        f"| Em wrapup            | {rt['wrapping']} |",
        f"| Espera mais longa    | **{fmt_duration(rt['longest_wait_sec'])}** |",
    ]

    if rt["pending"] > 0 or rt["longest_wait_sec"] > ALERT_WAIT_THRESHOLD_SEC:
        lines.append("")
        lines.append(f"> ⚠️ **Atenção**: {rt['pending']} tarefa(s) aguardando agente. Espera mais longa: {fmt_duration(rt['longest_wait_sec'])}.")

    lines += [
        "",
        f"## Acumulado — últimos {win} min",
        "",
        f"| Métrica                  | Valor |",
        f"|---|---|",
        f"| Tarefas criadas          | **{cum['tasks_created']}** |",
        f"| Tarefas concluídas       | {cum['tasks_completed']} |",
        f"| Tarefas canceladas       | {cum['tasks_canceled']} |",
        f"| Taxa de aceitação        | **{cum['acceptance_rate_pct']}%** |",
        f"| Taxa de abandono         | {cum['abandonment_rate_pct']}% |",
        f"| Tempo médio de espera    | **{fmt_duration(cum['avg_wait_sec'])}** |",
        f"| Tempo máximo de espera   | {fmt_duration(cum['max_wait_sec'])} |",
        "",
    ]

    # Detalhe por fila
    lines += [
        "## Filas (tempo real)",
        "",
        "| Fila | Ativas | Pendentes | Disponíveis | Espera Máx |",
        "|---|---|---|---|---|",
    ]
    for q in data["queues_rt"]:
        s = q["stats"]
        ts_status = s.get("tasks_by_status", {})
        ativas = ts_status.get("assigned", 0) + ts_status.get("reserved", 0) + ts_status.get("wrapping", 0)
        pend = ts_status.get("pending", 0)
        disp = s.get("total_available_workers", 0)
        lw = s.get("longest_task_waiting_age", 0)
        pend_str = f"**{pend}** ⚠️" if pend > 0 else str(pend)
        lw_str = f"**{fmt_duration(lw)}** ⚠️" if lw > ALERT_WAIT_THRESHOLD_SEC else fmt_duration(lw)
        lines.append(f"| {q['name']} | {ativas} | {pend_str} | {disp} | {lw_str} |")

    lines += [
        "",
        "---",
        f"_Dados: Twilio TaskRouter · Workspace `{data['workspace_rt']['workspace_sid']}`_",
    ]

    return "\n".join(lines)


def render_slack_message(analysis: dict, data: dict) -> str:
    a = analysis
    ag = a["agents"]
    rt = a["tasks_rt"]
    cum = a["cumulative"]
    win = data["minutes_window"]
    ts = datetime.now().strftime("%d/%m %H:%M")

    icon = ":large_green_circle:" if not a["has_alerts"] else ":red_circle:"
    lines = [
        f"{icon} *Flex Mecanizou — {ts}*",
        "",
        f"*Agentes:* {ag['online']}/{ag['total']} online · {ag['available']} disponíveis · {ag['utilization_pct']}% utilização",
        f"*Tarefas agora:* {rt['total']} ativas · {rt['pending']} pendentes · espera máx {fmt_duration(rt['longest_wait_sec'])}",
        f"*Últimos {win}min:* {cum['tasks_created']} criadas · {cum['tasks_completed']} concluídas · aceitação {cum['acceptance_rate_pct']}% · espera média {fmt_duration(cum['avg_wait_sec'])}",
    ]

    if a["alerts"]:
        lines.append("")
        lines.append("*⚠️ Filas com atenção:*")
        for aq in a["alerts"]:
            lines.append(f"  • {aq['name']}: {aq['pending']} pendentes, espera {fmt_duration(aq['longest_wait_sec'])}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_outputs(data: dict, analysis: dict, report_md: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    snapshot_path = output_dir / f"twilio_{stamp}.json"
    report_path = output_dir / f"twilio_{stamp}.md"

    with open(snapshot_path, "w") as f:
        json.dump({"data": data, "analysis": analysis}, f, indent=2, ensure_ascii=False)

    with open(report_path, "w") as f:
        f.write(report_md)

    return {
        "snapshot": str(snapshot_path),
        "report": str(report_path),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Agente Twilio Flex — Mecanizou")
    parser.add_argument("--minutes", type=int, default=480,
                        help="Janela de tempo para estatísticas acumuladas (padrão: 480 = 8h)")
    parser.add_argument("--output-dir", type=str, default="estudos/snapshots",
                        help="Diretório de saída para relatórios e snapshots")
    args = parser.parse_args()

    output_dir = BASE_DIR / args.output_dir

    env = load_env()
    data = fetch_all(env, args.minutes)
    analysis = analyze(data)
    report_md = render_report(data, analysis)
    slack_msg = render_slack_message(analysis, data)
    paths = save_outputs(data, analysis, report_md, output_dir)

    # Stdout: JSON estruturado para uso pelo agente/agendador
    result = {
        "ok": True,
        "fetched_at": data["fetched_at"],
        "paths": paths,
        "slack_message": slack_msg,
        "analysis": analysis,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

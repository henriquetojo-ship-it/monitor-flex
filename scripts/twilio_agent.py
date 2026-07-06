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

# Força máxima de trabalho do time de atendimento (denominador de "X/N online").
# NÃO é o total_workers do Twilio (que inclui bots, engenheiros e workers de teste).
# Ajustável via env FORCA_MAXIMA; padrão 9.
FORCA_MAXIMA_DEFAULT = 9

# Piso saudável da taxa de aceitação (abaixo disso, sinaliza vermelho na leitura).
ACEITACAO_ALVO_PCT = 85.0

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


def _pct_br(valor) -> str:
    """Formata um percentual no padrão brasileiro (vírgula decimal). 63.4 -> '63,4'."""
    return f"{valor}".replace(".", ",")


def _leitura_rapida(analysis: dict, saldo: int, conclusao_pct: float, util_pct: float) -> str:
    """Leitura em linguagem natural, por regras, do 1-2 pontos mais importantes
    da janela. Sem IA — determinística e barata.

    util_pct é a utilização pautada na força máxima (padrão 9), calculada em
    render_slack_full — NÃO usar ag['utilization_pct'] (que é sobre o online-base)."""
    a = analysis
    ag = a["agents"]
    cum = a["cumulative"]
    acc = cum["acceptance_rate_pct"]
    frases = []

    if acc < ACEITACAO_ALVO_PCT:
        frases.append(
            f"Aceitação em {_pct_br(acc)}% é o ponto de atenção — abaixo do saudável "
            f"(>{int(ACEITACAO_ALVO_PCT)}%). Como a conta é aceitas ÷ (aceitas + recusadas + "
            f"estouradas por timeout), o número baixo aponta recusa/timeout de reservas, "
            f"não falta de gente."
        )
    else:
        frases.append(f"Aceitação saudável em {_pct_br(acc)}%.")

    if saldo > 0:
        frases.append(
            f"A fila subiu {saldo} tarefas na janela (criou mais do que concluiu) — "
            f"backlog crescendo."
        )
    elif saldo < 0:
        frases.append(
            f"A fila caiu {abs(saldo)} tarefas na janela (concluiu mais do que criou) — "
            f"backlog drenando."
        )
    else:
        frases.append("Fila estável na janela (criadas ≈ concluídas).")

    if ag["available"] > 0 and util_pct < 50:
        frases.append(
            f"Com {ag['available']} disponíveis e utilização em {_pct_br(util_pct)}%, "
            f"há braço livre."
        )

    return " ".join(frases)


def render_slack_full(analysis: dict, data: dict, forca_maxima: int) -> str:
    """Mensagem 1 — versão revisada/completa dos indicadores, amigável ao Slack.
    Substitui o corte de uma linha (render_slack_message) na publicação automática.

    Correções pedidas pelo Henrique:
      • Denominador é a força de trabalho real (padrão 9), não o total_workers (24).
      • Foto é PARCIAL (não fechamento) e carimbada em horário de Brasília.
      • Aceitação NÃO é SLA de 30s — é aceitas ÷ (aceitas + recusadas + timeouts).
      • Espera máxima é o PICO do dia (cum.max_wait_sec), não a fila-instantânea.
    """
    a = analysis
    ag = a["agents"]
    rt = a["tasks_rt"]
    cum = a["cumulative"]
    win = data["minutes_window"]
    ts = datetime.now().strftime("%d/%m %H:%M")  # TZ da máquina; no CI, forçar TZ=America/Sao_Paulo

    created = cum["tasks_created"]
    completed = cum["tasks_completed"]
    conclusao = round(completed / created * 100, 1) if created > 0 else 0.0
    saldo = created - completed
    saldo_str = f"+{saldo}" if saldo >= 0 else str(saldo)
    saldo_nota = " (backlog cresceu)" if saldo > 0 else (" (backlog caiu)" if saldo < 0 else "")

    acc = cum["acceptance_rate_pct"]
    acc_flag = "" if acc >= ACEITACAO_ALVO_PCT else " 🔴"

    horas = round(win / 60, 1) if win % 60 else win // 60

    # Utilização pautada na força máxima (padrão 9), não no online-base.
    # Ocupados = online - disponíveis; denominador = forca_maxima. Clampeado em 100%.
    ocupados = max(0, ag["online"] - ag["available"])
    util_forca = round(min(ocupados / forca_maxima, 1.0) * 100, 1) if forca_maxima > 0 else 0.0

    lines = [
        f"*Flex Mecanizou — {ts} BRT (parcial)*",
        "",
        "*Força agora*",
        f"• {ag['online']}/{forca_maxima} online · {ag['available']} disponíveis · {_pct_br(util_forca)}% utilização",
        f"• {rt['total']} tarefas ativas · {rt['pending']} pendentes",
        "",
        f"*Janela (últimos {win}min ≈ {horas}h)*",
        f"• Criadas: {created} · Concluídas: {completed}",
        f"• Conclusão: {_pct_br(conclusao)}% · Saldo da janela: {saldo_str}{saldo_nota}",
        f"• Aceitação: {_pct_br(acc)}%{acc_flag} · Espera média: {fmt_duration(cum['avg_wait_sec'])} · Espera máx (dia): {fmt_duration(cum['max_wait_sec'])}",
        "",
        "*Leitura rápida*",
        _leitura_rapida(a, saldo, conclusao, util_forca),
    ]

    if a["alerts"]:
        lines.append("")
        lines.append("*⚠️ Filas com atenção:*")
        for aq in a["alerts"]:
            lines.append(f"• {aq['name']}: {aq['pending']} pendentes, espera {fmt_duration(aq['longest_wait_sec'])}")

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
    forca_maxima = int(env.get("FORCA_MAXIMA") or FORCA_MAXIMA_DEFAULT)
    data = fetch_all(env, args.minutes)
    analysis = analyze(data)
    report_md = render_report(data, analysis)
    slack_msg = render_slack_message(analysis, data)
    slack_full = render_slack_full(analysis, data, forca_maxima)
    paths = save_outputs(data, analysis, report_md, output_dir)

    # Stdout: JSON estruturado para uso pelo agente/agendador
    result = {
        "ok": True,
        "fetched_at": data["fetched_at"],
        "paths": paths,
        "slack_message": slack_msg,        # corte de uma linha (legado)
        "slack_message_full": slack_full,  # versão revisada/completa (padrão a publicar)
        "analysis": analysis,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

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
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent.parent
ENV_PATH = BASE_DIR / ".env.local"

TASKROUTER_BASE = "https://taskrouter.twilio.com/v1"

# Finance-Atendimento excluída de todos os indicadores (RT + acumulado)
QUEUE_FINANCE = "Finance-Atendimento"
QUEUE_IGNORE = {"everyone", "fila-teste", QUEUE_FINANCE}

# Limite de espera para alertar (segundos)
ALERT_WAIT_THRESHOLD_SEC = 120

# Força máxima de trabalho do time de atendimento (denominador de "X/N online").
# NÃO é o total_workers do Twilio (que inclui bots, engenheiros e workers de teste).
# Ajustável via env FORCA_MAXIMA; padrão 9.
FORCA_MAXIMA_DEFAULT = 9

# Piso saudável da taxa de aceitação (abaixo disso, sinaliza vermelho na leitura).
ACEITACAO_ALVO_PCT = 85.0

# Timezone Brasília
BRT = timezone(timedelta(hours=-3))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env() -> dict:
    env = dict(dotenv_values(ENV_PATH)) if ENV_PATH.exists() else {}
    # Fallback: lê METABASE_* do projeto mecani-metabase vizinho
    alt = BASE_DIR.parent / "mecani-metabase" / ".env.local"
    if alt.exists():
        alt_env = dict(dotenv_values(alt))
        for k in ("METABASE_URL", "METABASE_API_KEY"):
            if k not in env and k in alt_env:
                env[k] = alt_env[k]
    # Variáveis de ambiente sobrescrevem .env.local
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WORKSPACE_SID",
              "SLACK_BOT_TOKEN", "TWILIO_SLACK_CHANNEL_ID",
              "METABASE_URL", "METABASE_API_KEY", "FORCA_MAXIMA"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    required = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WORKSPACE_SID"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        print(f"[ERRO] Variáveis ausentes: {', '.join(missing)}", file=sys.stderr)
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


def _pct_br(valor) -> str:
    """Formata número no padrão brasileiro (vírgula decimal). 63.4 -> '63,4'."""
    return f"{valor}".replace(".", ",")


# ---------------------------------------------------------------------------
# TMA do Metabase
# ---------------------------------------------------------------------------

def fetch_tma_metabase(env: dict) -> dict | None:
    """Busca TMA da question 11523 (últimas 2 semanas).

    Retorna {'medio_min': float, 'medio_min_pw': float | None, 'faixas': dict} ou None.
    'medio_min'    = média da semana corrente (últimos 7 dias)
    'medio_min_pw' = média da semana anterior (7-14 dias atrás)
    'faixas'       = {faixa: pct_medio} da semana corrente
    """
    base_url = (env.get("METABASE_URL") or "").rstrip("/")
    api_key = env.get("METABASE_API_KEY") or ""
    if not base_url or not api_key:
        return None
    try:
        resp = requests.post(
            f"{base_url}/api/card/11523/query",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"parameters": [
                {"type": "date/all-options",
                 "target": ["dimension", ["template-tag", "base_date"]],
                 "value": "past2weeks~"},
                {"type": "category",
                 "target": ["variable", ["template-tag", "dimensao"]],
                 "value": "Dia"},
            ]},
            timeout=30,
        )
        if resp.status_code not in (200, 202):
            return None
        d = resp.json().get("data", {})
        cols = [c.get("name", "").lower() for c in d.get("cols", [])]
        rows = d.get("rows", [])
        if not rows or not cols:
            return None

        tma_idx   = next((i for i, c in enumerate(cols) if "tma" in c or "tmo" in c), None)
        faixa_idx = next((i for i, c in enumerate(cols) if "faixa" in c), None)
        pct_idx   = next((i for i, c in enumerate(cols) if "pct" in c or "percent" in c or "proporcao" in c), None)
        date_idx  = next((i for i, c in enumerate(cols) if "periodo" in c or "data" in c or "date" in c), None)

        today    = datetime.now(BRT).date()
        week_ago = today - timedelta(days=7)

        def row_date(row):
            if date_idx is None:
                return None
            try:
                val = row[date_idx]
                if isinstance(val, str):
                    return datetime.fromisoformat(val[:10]).date()
            except Exception:
                pass
            return None

        rows_curr = [r for r in rows if (row_date(r) or today) >= week_ago]
        rows_prev = [r for r in rows if (rd := row_date(r)) and rd < week_ago]

        def tma_medio(subset):
            if tma_idx is None or not subset:
                return None
            vals = [r[tma_idx] for r in subset if r[tma_idx] is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        def faixas_pct(subset):
            if faixa_idx is None or pct_idx is None or not subset:
                return None
            from collections import defaultdict
            acc = defaultdict(list)
            for r in subset:
                f = str(r[faixa_idx] or "").strip()
                p = r[pct_idx]
                if f and p is not None:
                    try:
                        acc[f].append(float(p))
                    except Exception:
                        pass
            return {f: round(sum(v) / len(v), 1) for f, v in sorted(acc.items())} if acc else None

        return {
            "medio_min":    tma_medio(rows_curr),
            "medio_min_pw": tma_medio(rows_prev),
            "faixas":       faixas_pct(rows_curr),
        }
    except Exception as e:
        print(f"  [warn] TMA Metabase: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Snapshot histórico (comparação semana passada)
# ---------------------------------------------------------------------------

def load_prior_snapshot(output_dir: Path) -> dict | None:
    """Busca snapshot de 7 dias atrás no mesmo horário (±30min). Só funciona
    em execuções locais com histórico persistido — CI não tem isso."""
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    candidates = sorted(output_dir.glob(f"twilio_{week_ago}_*.json"))
    if not candidates:
        return None
    now_hm = int(datetime.now().strftime("%H%M"))
    best = min(candidates,
               key=lambda p: abs(int(p.stem.split("_")[2][:4]) - now_hm),
               default=None)
    if not best:
        return None
    try:
        with open(best) as f:
            return json.load(f).get("analysis", {}).get("cumulative")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_all(env: dict, minutes: int) -> dict:
    auth = (env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"])
    base = f"{TASKROUTER_BASE}/Workspaces/{env['TWILIO_WORKSPACE_SID']}"

    print("[1/6] Workspace real-time...", file=sys.stderr)
    workspace_rt = tw_get(f"{base}/RealTimeStatistics", auth)

    print("[2/6] Workers real-time...", file=sys.stderr)
    workers_rt = tw_get(f"{base}/Workers/RealTimeStatistics", auth)

    print("[3/6] Lista de filas...", file=sys.stderr)
    queues_list = tw_get(f"{base}/TaskQueues", auth, {"PageSize": 100})
    queues = [q for q in queues_list.get("task_queues", [])
              if q["friendly_name"] not in QUEUE_IGNORE]

    print("[4/6] Stats real-time por fila (excl. Finance)...", file=sys.stderr)
    queues_rt = []
    for q in queues:
        try:
            stats = tw_get(f"{base}/TaskQueues/{q['sid']}/RealTimeStatistics", auth)
            queues_rt.append({"sid": q["sid"], "name": q["friendly_name"], "stats": stats})
        except Exception as e:
            print(f"  [warn] RT {q['friendly_name']}: {e}", file=sys.stderr)

    print("[5/6] Stats acumuladas por fila (excl. Finance)...", file=sys.stderr)
    queues_cum = []
    for q in queues:
        try:
            stats = tw_get(f"{base}/TaskQueues/{q['sid']}/CumulativeStatistics",
                           auth, {"Minutes": minutes})
            queues_cum.append({"name": q["friendly_name"], "stats": stats})
        except Exception as e:
            print(f"  [warn] CUM {q['friendly_name']}: {e}", file=sys.stderr)

    print("[6/6] TMA do Metabase...", file=sys.stderr)
    tma = fetch_tma_metabase(env)

    return {
        "fetched_at":     datetime.now(timezone.utc).isoformat(),
        "minutes_window": minutes,
        "workspace_rt":   workspace_rt,
        "workers_rt":     workers_rt,
        "queues_rt":      queues_rt,
        "queues_cum":     queues_cum,
        "tma":            tma,
    }


# ---------------------------------------------------------------------------
# Aggregate per-queue cumulative stats
# ---------------------------------------------------------------------------

def agg_queue_cum(queues_cum: list) -> dict:
    """Agrega cumulativas de múltiplas filas (Finance já excluída no fetch).
    tasks_entered por fila = equivalente a tasks_created no workspace-wide."""
    tasks_entered = tasks_completed = tasks_canceled = 0
    res_accepted = res_rejected = res_timeout = 0
    wait_weighted = 0.0
    max_wait = 0

    for q in queues_cum:
        s = q["stats"]
        tasks_entered   += s.get("tasks_entered", 0)
        tasks_completed += s.get("tasks_completed", 0)
        tasks_canceled  += s.get("tasks_canceled", 0)
        ra = s.get("reservations_accepted", 0)
        res_accepted += ra
        res_rejected += s.get("reservations_rejected", 0)
        res_timeout  += s.get("reservations_timed_out", 0)
        wait = s.get("wait_duration_until_accepted") or {}
        wait_weighted += (wait.get("avg") or 0) * ra
        max_wait = max(max_wait, wait.get("max") or 0)

    total_res = res_accepted + res_rejected + res_timeout
    return {
        "tasks_created":         tasks_entered,
        "tasks_completed":       tasks_completed,
        "tasks_canceled":        tasks_canceled,
        "reservations_accepted": res_accepted,
        "acceptance_rate_pct":   round(res_accepted / total_res * 100, 1) if total_res > 0 else 0.0,
        "abandonment_rate_pct":  round(tasks_canceled / tasks_entered * 100, 1) if tasks_entered > 0 else 0.0,
        "avg_wait_sec":          round(wait_weighted / res_accepted) if res_accepted > 0 else 0,
        "max_wait_sec":          max_wait,
    }


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------

def analyze(data: dict) -> dict:
    workers_rt = data["workers_rt"]

    # Agentes por atividade
    agents = {a["friendly_name"]: a["workers"] for a in workers_rt["activity_statistics"]}
    total_agents = workers_rt["total_workers"]
    available   = agents.get("Available", 0)
    on_break    = agents.get("Break", 0)
    offline     = agents.get("Offline", 0)
    unavailable = agents.get("Unavailable", 0)
    online = total_agents - offline

    # Utilização: disponíveis / online (nova fórmula)
    util_pct = round(available / online * 100, 1) if online > 0 else 0.0

    # Tarefas RT: soma das filas (Finance excluída em fetch_all)
    tasks_assigned = tasks_pending = tasks_wrapping = tasks_reserved = 0
    longest_wait = 0
    for q in data["queues_rt"]:
        s  = q["stats"]
        ts = s.get("tasks_by_status", {})
        tasks_assigned += ts.get("assigned", 0)
        tasks_pending  += ts.get("pending", 0)
        tasks_wrapping += ts.get("wrapping", 0)
        tasks_reserved += ts.get("reserved", 0)
        longest_wait = max(longest_wait, s.get("longest_task_waiting_age", 0))
    total_tasks = tasks_assigned + tasks_pending + tasks_wrapping + tasks_reserved

    # Cumulativo por fila agregado (Finance excluída)
    cum = agg_queue_cum(data["queues_cum"])

    # Filas com backlog ou espera alta
    alert_queues = []
    for q in data["queues_rt"]:
        s  = q["stats"]
        p  = s.get("tasks_by_status", {}).get("pending", 0)
        lw = s.get("longest_task_waiting_age", 0)
        if p > 0 or lw > ALERT_WAIT_THRESHOLD_SEC:
            alert_queues.append({"name": q["name"], "pending": p, "longest_wait_sec": lw})

    return {
        "agents": {
            "total":           total_agents,
            "online":          online,
            "available":       available,
            "on_break":        on_break,
            "offline":         offline,
            "unavailable":     unavailable,
            "utilization_pct": util_pct,
        },
        "tasks_rt": {
            "total":            total_tasks,
            "assigned":         tasks_assigned,
            "pending":          tasks_pending,
            "reserved":         tasks_reserved,
            "wrapping":         tasks_wrapping,
            "longest_wait_sec": longest_wait,
        },
        "cumulative": cum,
        "alerts":     alert_queues,
        "has_alerts": len(alert_queues) > 0 or tasks_pending > 0 or longest_wait > ALERT_WAIT_THRESHOLD_SEC,
    }


# ---------------------------------------------------------------------------
# Report (markdown)
# ---------------------------------------------------------------------------

def render_report(data: dict, analysis: dict) -> str:
    ts  = datetime.now(BRT).strftime("%d/%m/%Y às %H:%M")
    win = data["minutes_window"]
    a   = analysis
    ag  = a["agents"]
    rt  = a["tasks_rt"]
    cum = a["cumulative"]

    status_icon = "🟢" if not a["has_alerts"] else "🔴"

    lines = [
        f"# {status_icon} Flex — Painel Mecanizou",
        f"_Gerado em {ts} BRT · Janela acumulada: últimos {win} min_",
        "",
        "---",
        "",
        "## Agentes",
        "",
        "| Status       | Qtd |",
        "|---|---|",
        f"| Online       | **{ag['online']}** / {ag['total']} |",
        f"| Disponível   | {ag['available']} |",
        f"| Em pausa     | {ag['on_break']} |",
        f"| Offline      | {ag['offline']} |",
        f"| Utilização   | **{ag['utilization_pct']}%** (disponíveis/online) |",
        "",
        "## Tarefas (tempo real, excl. Finance)",
        "",
        "| Métrica              | Valor |",
        "|---|---|",
        f"| Total ativas         | **{rt['total']}** |",
        f"| Em atendimento       | {rt['assigned']} |",
        f"| Aguardando (pending) | {rt['pending']} |",
        f"| Em wrapup            | {rt['wrapping']} |",
        f"| Espera mais longa    | **{fmt_duration(rt['longest_wait_sec'])}** |",
    ]

    if rt["pending"] > 0 or rt["longest_wait_sec"] > ALERT_WAIT_THRESHOLD_SEC:
        lines.append("")
        lines.append(f"> ⚠️ **Atenção**: {rt['pending']} tarefa(s) aguardando agente. "
                     f"Espera mais longa: {fmt_duration(rt['longest_wait_sec'])}.")

    lines += [
        "",
        f"## Acumulado — últimos {win} min (excl. Finance)",
        "",
        "| Métrica                  | Valor |",
        "|---|---|",
        f"| Tarefas criadas          | **{cum['tasks_created']}** |",
        f"| Tarefas concluídas       | {cum['tasks_completed']} |",
        f"| Tarefas canceladas       | {cum['tasks_canceled']} |",
        f"| Taxa de aceitação        | **{cum['acceptance_rate_pct']}%** |",
        f"| Taxa de abandono         | {cum['abandonment_rate_pct']}% |",
        f"| Tempo médio de espera    | **{fmt_duration(cum['avg_wait_sec'])}** |",
        f"| Tempo máximo de espera   | {fmt_duration(cum['max_wait_sec'])} |",
        "",
    ]

    # TMA
    tma = data.get("tma")
    if tma and tma.get("medio_min") is not None:
        lines += [
            "## TMA (Metabase)",
            "",
            f"TMA médio (semana corrente): **{_pct_br(tma['medio_min'])}min**",
        ]
        if tma.get("medio_min_pw") is not None:
            lines.append(f"TMA médio (semana anterior): {_pct_br(tma['medio_min_pw'])}min")
        if tma.get("faixas"):
            lines.append("")
            lines.append("| Faixa | % médio |")
            lines.append("|---|---|")
            for faixa, val in sorted(tma["faixas"].items()):
                lines.append(f"| {faixa} | {_pct_br(val)}% |")
        lines.append("")

    # Detalhe por fila
    lines += [
        "## Filas (tempo real, excl. Finance)",
        "",
        "| Fila | Ativas | Pendentes | Disponíveis | Espera Máx |",
        "|---|---|---|---|---|",
    ]
    for q in data["queues_rt"]:
        s        = q["stats"]
        ts_s     = s.get("tasks_by_status", {})
        ativas   = ts_s.get("assigned", 0) + ts_s.get("reserved", 0) + ts_s.get("wrapping", 0)
        pend     = ts_s.get("pending", 0)
        disp     = s.get("total_available_workers", 0)
        lw       = s.get("longest_task_waiting_age", 0)
        pend_str = f"**{pend}** ⚠️" if pend > 0 else str(pend)
        lw_str   = f"**{fmt_duration(lw)}** ⚠️" if lw > ALERT_WAIT_THRESHOLD_SEC else fmt_duration(lw)
        lines.append(f"| {q['name']} | {ativas} | {pend_str} | {disp} | {lw_str} |")

    lines += [
        "",
        "---",
        f"_Dados: Twilio TaskRouter · Workspace `{data['workspace_rt']['workspace_sid']}`_",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Slack messages
# ---------------------------------------------------------------------------

def render_slack_message(analysis: dict, data: dict) -> str:
    """Versão resumida (legado — uma linha por bloco)."""
    a   = analysis
    ag  = a["agents"]
    rt  = a["tasks_rt"]
    cum = a["cumulative"]
    win = data["minutes_window"]
    ts  = datetime.now(BRT).strftime("%d/%m %H:%M")

    icon = ":large_green_circle:" if not a["has_alerts"] else ":red_circle:"
    lines = [
        f"{icon} *Flex Mecanizou — {ts} BRT*",
        "",
        f"*Agentes:* {ag['online']} online · {ag['available']} disponíveis · {_pct_br(ag['utilization_pct'])}% utilização",
        f"*Tarefas agora:* {rt['total']} ativas · {rt['pending']} pendentes · espera máx {fmt_duration(rt['longest_wait_sec'])}",
        f"*Últimos {win}min:* {cum['tasks_created']} criadas · {cum['tasks_completed']} concluídas · "
        f"aceitação {_pct_br(cum['acceptance_rate_pct'])}% · espera média {fmt_duration(cum['avg_wait_sec'])}",
    ]

    if a["alerts"]:
        lines.append("")
        lines.append("*⚠️ Filas com atenção:*")
        for aq in a["alerts"]:
            lines.append(f"  • {aq['name']}: {aq['pending']} pendentes, espera {fmt_duration(aq['longest_wait_sec'])}")

    return "\n".join(lines)


def _leitura_rapida(analysis: dict, saldo: int, tma: dict | None) -> str:
    """Leitura em linguagem natural do 1-2 pontos mais importantes da janela."""
    cum = analysis["cumulative"]
    ag  = analysis["agents"]
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

    # TMA vs semana passada (substitui comentário de backlog)
    if tma and tma.get("medio_min") is not None:
        tma_curr = tma["medio_min"]
        tma_prev = tma.get("medio_min_pw")
        if tma_prev is not None:
            diff = round(tma_curr - tma_prev, 1)
            if diff > 0:
                diff_str = f"+{_pct_br(diff)}min vs. semana passada"
            elif diff < 0:
                diff_str = f"{_pct_br(diff)}min vs. semana passada"
            else:
                diff_str = "estável vs. semana passada"
            frases.append(f"TMA médio em {_pct_br(tma_curr)}min ({diff_str}).")
        else:
            frases.append(f"TMA médio em {_pct_br(tma_curr)}min.")
    elif ag["available"] > 0 and ag["utilization_pct"] > 50:
        frases.append(
            f"Com {ag['available']} disponíveis e {_pct_br(ag['utilization_pct'])}% "
            f"dos logins online disponíveis, há capacidade livre."
        )

    return " ".join(frases)


def render_slack_full(analysis: dict, data: dict, forca_maxima: int,
                      prior_cum: dict | None = None) -> str:
    """Mensagem completa publicada pelo agendador no canal Slack."""
    a   = analysis
    ag  = a["agents"]
    rt  = a["tasks_rt"]
    cum = a["cumulative"]
    tma = data.get("tma")
    win = data["minutes_window"]
    ts  = datetime.now(BRT).strftime("%d/%m %H:%M")

    created   = cum["tasks_created"]
    completed = cum["tasks_completed"]
    conclusao = round(completed / created * 100, 1) if created > 0 else 0.0
    saldo     = created - completed
    saldo_str = f"+{saldo}" if saldo >= 0 else str(saldo)

    acc      = cum["acceptance_rate_pct"]
    acc_flag = "" if acc >= ACEITACAO_ALVO_PCT else " 🔴"

    horas = round(win / 60, 1) if win % 60 else win // 60

    # Utilização: disponíveis / online (nova fórmula)
    util_pct = ag["utilization_pct"]

    # Comparação semana passada (Criadas / Concluídas)
    criadas_cmp = ""
    if prior_cum:
        pc = prior_cum.get("tasks_created")
        pp = prior_cum.get("tasks_completed")
        if pc is not None and pp is not None:
            criadas_cmp = f" (vs. {pc} cri · {pp} conc semana passada)"

    # TMA line
    tma_parts = []
    if tma:
        if tma.get("medio_min") is not None:
            tma_parts.append(f"TMA médio: {_pct_br(tma['medio_min'])}min")
        if tma.get("faixas"):
            fx = " · ".join(f"{k}: {_pct_br(v)}%" for k, v in sorted(tma["faixas"].items()))
            tma_parts.append(fx)

    lines = [
        f"*Flex Mecanizou — {ts} BRT (parcial)*",
        "",
        "*Força agora*",
        f"• {ag['online']}/{forca_maxima} online · {ag['available']} disponíveis · {_pct_br(util_pct)}% utilização",
        f"• {rt['total']} tarefas ativas · {rt['pending']} pendentes",
        "",
        f"*Janela (últimos {win}min ≈ {horas}h)*",
        f"• Criadas: {created} · Concluídas: {completed}{criadas_cmp}",
        f"• Conclusão: {_pct_br(conclusao)}% · Saldo da janela: {saldo_str}",
        f"• Aceitação: {_pct_br(acc)}%{acc_flag} · Espera média: {fmt_duration(cum['avg_wait_sec'])} · Espera máx (dia): {fmt_duration(cum['max_wait_sec'])}",
    ]

    if tma_parts:
        lines.append(f"• {' · '.join(tma_parts)}")

    lines += [
        "",
        "*Leitura rápida*",
        _leitura_rapida(a, saldo, tma),
    ]

    if a["alerts"]:
        lines.append("")
        lines.append("*Filas com atenção:*")
        for aq in a["alerts"]:
            lines.append(f"• {aq['name']}: {aq['pending']} pendentes, espera {fmt_duration(aq['longest_wait_sec'])}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_outputs(data: dict, analysis: dict, report_md: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(BRT).strftime("%Y-%m-%d_%H%M%S")

    snapshot_path = output_dir / f"twilio_{stamp}.json"
    report_path   = output_dir / f"twilio_{stamp}.md"

    with open(snapshot_path, "w") as f:
        json.dump({"data": data, "analysis": analysis}, f, indent=2, ensure_ascii=False)

    with open(report_path, "w") as f:
        f.write(report_md)

    return {
        "snapshot": str(snapshot_path),
        "report":   str(report_path),
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

    output_dir   = BASE_DIR / args.output_dir
    env          = load_env()
    forca_maxima = int(env.get("FORCA_MAXIMA") or FORCA_MAXIMA_DEFAULT)

    data      = fetch_all(env, args.minutes)
    analysis  = analyze(data)
    prior_cum = load_prior_snapshot(output_dir)

    report_md  = render_report(data, analysis)
    slack_msg  = render_slack_message(analysis, data)
    slack_full = render_slack_full(analysis, data, forca_maxima, prior_cum)
    paths      = save_outputs(data, analysis, report_md, output_dir)

    result = {
        "ok":                 True,
        "fetched_at":         data["fetched_at"],
        "paths":              paths,
        "slack_message":      slack_msg,
        "slack_message_full": slack_full,
        "analysis":           analysis,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

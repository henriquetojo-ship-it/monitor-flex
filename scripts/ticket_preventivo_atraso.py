#!/usr/bin/env python3
"""
Agente de Ticket Preventivo — Hypercare / Atraso de Entrega (B1)
Mecanizou

Lê a question 11609 do Metabase (previsão de entrega com hypercare),
agrupa por pedido (order_id) e abre tickets preventivos na plataforma
de suporte (ops.mecanizou.com / customer-support-service) quando:

  - o pedido/item está com a label HC (hypercare = 'HC') -> 100% precisa de ticket
  - OU o item não está em HC mas o atraso previsto é >= 30 minutos

Regras de negócio (confirmadas por Henrique em 2026-07-01):
  - Título: "Hypercare pedido {mecani_id}" se qualquer item do pedido tiver HC,
    senão "Atraso pedido {mecani_id}" (quando o motivo é só atraso >=30min)
  - Canal: sempre "Plataforma"
  - Categoria: sempre "Preventivo"
  - Descrição: título + lista de order_item_id que motivaram a abertura
  - Vários order_item_id do mesmo order_id -> UM único ticket (itens listados na descrição)
  - Antes de abrir, checar tickets já abertos na plataforma para o mesmo pedido
    (dedupe via título, buscando "pedido {mecani_id}" nos tickets em aberto)

ATENÇÃO — partes ainda NÃO confirmadas por engenharia (ver docs/SPEC-ticket-preventivo-atraso-B1.md):
  - Endpoint e payload exatos para localizar a oficina (workshop) a partir do fantasy_name
  - Endpoint e payload exatos para popular o dropdown "últimos pedidos da oficina"
    e mapear o Mecani ID para o identificador que o payload de criação espera
  - Nomes de campo exatos do payload de POST /v1/tickets (ex.: workshop_id, segment)
  - Mecanismo de autenticação exato (Authorization: Bearer vs apikey vs ambos)

Por segurança, o script roda em modo --dry-run por padrão: ele identifica o que
PRECISARIA ser feito (tickets a abrir, com título/descrição prontos) mas só
executa a chamada real de criação se receber --live E as variáveis de ambiente
do customer-support-service estiverem configuradas. As buscas de oficina/pedido
(search_workshop / find_order_option) lançam erro claro em modo --live até
serem confirmadas e implementadas de verdade.

Uso:
    python3 scripts/ticket_preventivo_atraso.py                 # dry-run (padrão)
    python3 scripts/ticket_preventivo_atraso.py --live          # tenta abrir tickets de verdade
    python3 scripts/ticket_preventivo_atraso.py --self-test     # roda com dados fixos, sem rede
    python3 scripts/ticket_preventivo_atraso.py --force         # ignora a janela de horário (08:20-17:30 BRT)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent.parent
ENV_PATH = BASE_DIR / ".env.local"

METABASE_BASE = "https://metabase.tools.mecanizou.com"
QUESTION_ID = 11609  # [Cotações?] Previsão de entrega com hypercare

ATRASO_MINUTOS_LIMIAR = 30

# Janela de execução em horário de Brasília (fixo, UTC-3, sem horário de verão)
BRT_OFFSET = timezone(timedelta(hours=-3))
JANELA_INICIO = (8, 20)   # 08:20 BRT
JANELA_FIM = (17, 30)     # 17:30 BRT

TITULO_HYPERCARE = "Hypercare pedido {mecani_id}"
TITULO_ATRASO = "Atraso pedido {mecani_id}"

CANAL = "Plataforma"
CATEGORIA = "Preventivo"


# ---------------------------------------------------------------------------
# Helpers — env / janela de horário
# ---------------------------------------------------------------------------

def load_env() -> dict:
    env = dotenv_values(ENV_PATH)
    required = ["METABASE_API_KEY"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        print(f"[ERRO] Variáveis ausentes no .env.local: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    optional = [
        "CUSTOMER_SUPPORT_API_URL",
        "CUSTOMER_SUPPORT_API_KEY",     # possível header `apikey`
        "CUSTOMER_SUPPORT_API_TOKEN",   # possível header `Authorization: Bearer`
        "CUSTOMER_SUPPORT_ASSIGNEE_UID",
    ]
    for k in optional:
        env.setdefault(k, None)

    return env


def dentro_da_janela(now_utc: datetime = None) -> bool:
    """Confere se o horário atual está dentro de 08:20-17:30 BRT, seg-sex."""
    now_utc = now_utc or datetime.now(timezone.utc)
    now_brt = now_utc.astimezone(BRT_OFFSET)

    if now_brt.weekday() >= 5:  # 5=sábado, 6=domingo
        return False

    inicio = now_brt.replace(hour=JANELA_INICIO[0], minute=JANELA_INICIO[1], second=0, microsecond=0)
    fim = now_brt.replace(hour=JANELA_FIM[0], minute=JANELA_FIM[1], second=0, microsecond=0)
    return inicio <= now_brt <= fim


# ---------------------------------------------------------------------------
# Fetch — Metabase (question 11609)
# ---------------------------------------------------------------------------

def fetch_question_rows(env: dict, question_id: int = QUESTION_ID) -> list:
    """Executa a question salva no Metabase e devolve as linhas como lista de dicts."""
    url = f"{METABASE_BASE}/api/card/{question_id}/query"
    headers = {"x-api-key": env["METABASE_API_KEY"], "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json={"parameters": []}, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("error"):
        raise RuntimeError(f"Metabase retornou erro: {payload['error']}")

    cols = [c["name"] for c in payload["data"]["cols"]]
    rows = payload["data"]["rows"]
    return [dict(zip(cols, row)) for row in rows]


# ---------------------------------------------------------------------------
# Regra de negócio — agrupamento e decisão
# ---------------------------------------------------------------------------

def item_precisa_ticket(row: dict) -> bool:
    is_hc = (row.get("hypercare") or "").strip().upper() == "HC"
    atraso = row.get("atraso_em_minutos") or 0
    return is_hc or (not is_hc and atraso >= ATRASO_MINUTOS_LIMIAR)


def montar_candidatos(rows: list) -> list:
    """
    Agrupa linhas por order_id e monta um candidato a ticket por pedido
    (um único ticket mesmo que vários order_item_id do mesmo pedido precisem
    de acompanhamento).
    """
    por_pedido = {}
    for row in rows:
        if not item_precisa_ticket(row):
            continue

        order_id = row.get("order_id")
        por_pedido.setdefault(order_id, {
            "order_id": order_id,
            "mecani_id": row.get("reference_id"),
            "fantasy_name": row.get("fantasy_name"),
            "link_ops": row.get("link_ops"),
            "itens": [],
            "tem_hc": False,
        })

        grupo = por_pedido[order_id]
        is_hc = (row.get("hypercare") or "").strip().upper() == "HC"
        grupo["tem_hc"] = grupo["tem_hc"] or is_hc
        grupo["itens"].append({
            "order_item_id": row.get("order_item_id"),
            "hypercare": row.get("hypercare"),
            "atraso_em_minutos": row.get("atraso_em_minutos"),
        })

    candidatos = []
    for grupo in por_pedido.values():
        motivo = "hypercare" if grupo["tem_hc"] else "atraso"
        titulo_tpl = TITULO_HYPERCARE if motivo == "hypercare" else TITULO_ATRASO
        titulo = titulo_tpl.format(mecani_id=grupo["mecani_id"])
        item_ids = ", ".join(str(i["order_item_id"]) for i in grupo["itens"])
        descricao = (
            f"{titulo}\n\n"
            f"Itens monitorados (order_item_id): {item_ids}\n"
            f"Oficina: {grupo['fantasy_name']}\n"
            + (f"Link: {grupo['link_ops']}\n" if grupo.get("link_ops") else "")
        )
        candidatos.append({
            **grupo,
            "motivo": motivo,
            "titulo": titulo,
            "descricao": descricao,
        })

    return candidatos


# ---------------------------------------------------------------------------
# Customer support service — tickets (Supabase Edge Function)
# ATENÇÃO: base URL e exemplo de GET confirmados por Henrique; payload de
# POST e autenticação exata ainda NÃO confirmados (ver SPEC no docs/).
# ---------------------------------------------------------------------------

def cs_headers(env: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if env.get("CUSTOMER_SUPPORT_API_KEY"):
        headers["apikey"] = env["CUSTOMER_SUPPORT_API_KEY"]
    if env.get("CUSTOMER_SUPPORT_API_TOKEN"):
        headers["Authorization"] = f"Bearer {env['CUSTOMER_SUPPORT_API_TOKEN']}"
    return headers


def list_open_tickets(env: dict, limit: int = 200) -> list:
    """
    Busca tickets em aberto na plataforma para checar duplicidade.
    NÃO CONFIRMADO: filtro de status/categoria e paginação exatos — usamos
    `needs_action=true` (exemplo capturado por Henrique) como aproximação de
    "em aberto". Se o limite for atingido, avisamos que pode haver truncamento.
    """
    base = env.get("CUSTOMER_SUPPORT_API_URL")
    if not base:
        raise RuntimeError("CUSTOMER_SUPPORT_API_URL não configurado — não é possível checar duplicidade.")

    params = {"needs_action": "true", "limit": limit}
    if env.get("CUSTOMER_SUPPORT_ASSIGNEE_UID"):
        params["assignee_uid"] = env["CUSTOMER_SUPPORT_ASSIGNEE_UID"]

    resp = requests.get(f"{base}/tickets", headers=cs_headers(env), params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    tickets = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(tickets, list) and len(tickets) >= limit:
        print(f"[AVISO] Lista de tickets em aberto atingiu o limite ({limit}) — "
              f"pode haver truncamento (paginação ainda não confirmada).", file=sys.stderr)
    return tickets if isinstance(tickets, list) else []


def ja_tem_ticket_aberto(open_tickets: list, mecani_id) -> bool:
    """Dedupe por substring no título: evita depender de order_id/workshop_id
    reais da API — o próprio script controla o formato do título que gera."""
    marcador = f"pedido {mecani_id}".lower()
    for t in open_tickets:
        titulo = (t.get("title") or t.get("subject") or "").lower()
        if marcador in titulo:
            return True
    return False


def search_workshop(env: dict, fantasy_name: str):
    """
    NÃO CONFIRMADO. Deveria buscar a oficina no campo "oficina" (lista de
    opções) usando fantasy_name em CAIXA ALTA e devolver o identificador
    esperado pelo payload de criação de ticket (workshop_id ou similar).
    Henrique se ofereceu para capturar essa requisição via DevTools.
    """
    raise NotImplementedError(
        "search_workshop() ainda não implementado — falta confirmar o endpoint "
        "de busca de oficina (fantasy_name em caixa alta -> workshop_id)."
    )


def find_order_option(env: dict, workshop_id, mecani_id):
    """
    NÃO CONFIRMADO. Deveria popular o dropdown "últimos pedidos da oficina"
    e selecionar a opção cujo Mecani ID bate com `mecani_id`, devolvendo o
    identificador de pedido esperado pelo payload de criação de ticket.
    """
    raise NotImplementedError(
        "find_order_option() ainda não implementado — falta confirmar o endpoint "
        "que popula o dropdown de pedidos da oficina."
    )


def create_ticket(env: dict, candidato: dict, live: bool) -> dict:
    """
    Cria o ticket de verdade quando `live=True`. Em dry-run, apenas devolve
    o payload que seria enviado, sem chamar a API.
    """
    payload_preview = {
        "title": candidato["titulo"],
        "channel": CANAL,
        "category": CATEGORIA,
        "description": candidato["descricao"],
        # campos abaixo dependem de search_workshop/find_order_option
        # (ainda não confirmados):
        "workshop": candidato["fantasy_name"],
        "mecani_id": candidato["mecani_id"],
    }

    if not live:
        return {"criado": False, "modo": "dry-run", "payload_preview": payload_preview}

    base = env.get("CUSTOMER_SUPPORT_API_URL")
    if not base:
        raise RuntimeError("CUSTOMER_SUPPORT_API_URL não configurado — não é possível criar ticket em modo --live.")

    workshop_id = search_workshop(env, candidato["fantasy_name"])
    order_option = find_order_option(env, workshop_id, candidato["mecani_id"])

    payload = {
        "title": candidato["titulo"],
        "channel": CANAL,
        "category": CATEGORIA,
        "description": candidato["descricao"],
        "workshop_id": workshop_id,
        "order_id": order_option,
    }
    resp = requests.post(f"{base}/tickets", headers=cs_headers(env), json=payload, timeout=30)
    resp.raise_for_status()
    return {"criado": True, "modo": "live", "resposta": resp.json()}


# ---------------------------------------------------------------------------
# Self-test — dados fixos, sem rede
# ---------------------------------------------------------------------------

def fixture_rows() -> list:
    return [
        # 1) Item em HC -> precisa de ticket "Hypercare pedido MEC1001"
        {
            "reference_id": "MEC1001", "order_item_id": 5001, "order_id": 1001,
            "link_ops": "https://ops.mecanizou.com/tickets/new?referenceId=MEC1001",
            "fantasy_name": "OFICINA TESTE", "hypercare": "HC", "atraso_em_minutos": 5,
        },
        # 2) Item sem HC, atraso >= 30 -> precisa de ticket "Atraso pedido MEC1002"
        {
            "reference_id": "MEC1002", "order_item_id": 5002, "order_id": 1002,
            "link_ops": "https://ops.mecanizou.com/tickets/new?referenceId=MEC1002",
            "fantasy_name": "OFICINA B", "hypercare": None, "atraso_em_minutos": 45,
        },
        # 3) Item em HC, mas já existe ticket aberto para esse pedido -> não deve reabrir
        {
            "reference_id": "MEC1003", "order_item_id": 5003, "order_id": 1003,
            "link_ops": "https://ops.mecanizou.com/tickets/new?referenceId=MEC1003",
            "fantasy_name": "OFICINA C", "hypercare": "HC", "atraso_em_minutos": 0,
        },
        # 4) Dois itens do mesmo pedido, ambos com atraso >= 30 -> UM único ticket com os 2 itens
        {
            "reference_id": "MEC1004", "order_item_id": 5004, "order_id": 1004,
            "link_ops": "https://ops.mecanizou.com/tickets/new?referenceId=MEC1004",
            "fantasy_name": "OFICINA D", "hypercare": None, "atraso_em_minutos": 32,
        },
        {
            "reference_id": "MEC1004", "order_item_id": 5005, "order_id": 1004,
            "link_ops": "https://ops.mecanizou.com/tickets/new?referenceId=MEC1004",
            "fantasy_name": "OFICINA D", "hypercare": None, "atraso_em_minutos": 60,
        },
        # 5) Item sem HC e sem atraso relevante -> não precisa de ticket
        {
            "reference_id": "MEC1005", "order_item_id": 5006, "order_id": 1005,
            "link_ops": "https://ops.mecanizou.com/tickets/new?referenceId=MEC1005",
            "fantasy_name": "OFICINA E", "hypercare": None, "atraso_em_minutos": 10,
        },
    ]


def fixture_open_tickets() -> list:
    return [
        {"title": "Hypercare pedido MEC1003", "needs_action": True},
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ticket preventivo — hypercare / atraso de entrega (B1)")
    parser.add_argument("--live", action="store_true",
                        help="Cria os tickets de verdade (padrão: dry-run, só mostra o que seria feito)")
    parser.add_argument("--self-test", action="store_true",
                        help="Roda com dados fixos, sem nenhuma chamada de rede")
    parser.add_argument("--force", action="store_true",
                        help="Ignora a checagem de janela de horário (08:20-17:30 BRT, seg-sex)")
    args = parser.parse_args()

    if not args.force and not args.self_test and not dentro_da_janela():
        result = {"ok": True, "skipped": True, "motivo": "fora da janela 08:20-17:30 BRT (seg-sex)"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.self_test:
        rows = fixture_rows()
        open_tickets = fixture_open_tickets()
        env = {"CUSTOMER_SUPPORT_API_URL": None}
    else:
        env = load_env()
        rows = fetch_question_rows(env)
        try:
            open_tickets = list_open_tickets(env)
        except RuntimeError as e:
            print(f"[AVISO] {e} Prosseguindo sem checagem de duplicidade.", file=sys.stderr)
            open_tickets = []

    candidatos = montar_candidatos(rows)

    resultado_por_pedido = []
    for candidato in candidatos:
        duplicado = ja_tem_ticket_aberto(open_tickets, candidato["mecani_id"])
        item_resultado = {
            "order_id": candidato["order_id"],
            "mecani_id": candidato["mecani_id"],
            "motivo": candidato["motivo"],
            "titulo": candidato["titulo"],
            "order_item_ids": [i["order_item_id"] for i in candidato["itens"]],
        }

        if duplicado:
            item_resultado["acao"] = "ignorado_ja_tem_ticket_aberto"
            resultado_por_pedido.append(item_resultado)
            continue

        try:
            criacao = create_ticket(env, candidato, live=args.live and not args.self_test)
        except NotImplementedError as e:
            criacao = {"criado": False, "erro": str(e)}
        except RuntimeError as e:
            criacao = {"criado": False, "erro": str(e)}

        item_resultado["acao"] = "criado" if criacao.get("criado") else "nao_criado"
        item_resultado["detalhe"] = criacao
        resultado_por_pedido.append(item_resultado)

    result = {
        "ok": True,
        "checado_em": datetime.now(timezone.utc).isoformat(),
        "modo": "self-test" if args.self_test else ("live" if args.live else "dry-run"),
        "pedidos_avaliados": len(candidatos),
        "resultado_por_pedido": resultado_por_pedido,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

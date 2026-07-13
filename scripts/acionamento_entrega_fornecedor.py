#!/usr/bin/env python3
"""
Agente de Acionamento a Fornecedores e Entregadores (B2) — Fase 0 (diagnóstico)
Mecanizou

Extensão do B1 (ticket_preventivo_atraso.py): lê as mesmas fontes de dado do
painel de estimativas de entrega (question 11609) e as questions de tempo de
rota (9319, 9324) para avaliar os NOVOS gatilhos definidos no redesenho do
processo de acompanhamento de entregas — sem disparar nenhuma ação real ainda.

Gatilhos avaliados nesta fase (ver docs/SPEC-acionamento-entrega-fornecedor-B2.md):

  1. "Em Compras" com atraso previsto >= 15min -> avisaria o fornecedor
     (bootstrap V0: aviso no Slack #logistica, não WhatsApp ainda)
  2. Expectativa de entrega recalculada (estimated_calc_time) após as 18h ->
     avisaria o cliente pelo canal já existente
  3. Desvio de rota "a caminho da entrega" (question 9319) e "a caminho do
     fornecedor/coleta" (question 9324) -> acionaria ligação Pitchyes quando
     o desvio for explícito. AINDA PENDENTE: nomes exatos das colunas dessas
     duas questions não foram confirmados — ver `avaliar_desvio_rota()`.

Modo de operação: SEMPRE dry-run nesta fase (Fase 0). O script só loga o que
faria (stdout, formato JSON) para comparação manual com o que o time de Ops
fez de verdade naquele intervalo. Não posta no Slack, não liga para ninguém,
não manda WhatsApp. Isso é intencional — ver "Fase 0" no plano de implantação.

Uso:
    python3 scripts/acionamento_entrega_fornecedor.py                # dry-run real (lê Metabase)
    python3 scripts/acionamento_entrega_fornecedor.py --self-test    # dados fixos, sem rede
    python3 scripts/acionamento_entrega_fornecedor.py --force        # ignora a janela de horário
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Reaproveita load_env / fetch_question_rows já validados pelo B1 — mesma
# fonte de credenciais (.env.local), mesmo helper de leitura de question.
sys.path.insert(0, str(Path(__file__).parent))
from ticket_preventivo_atraso import load_env, fetch_question_rows  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

QUESTION_ENTREGA = 11609        # previsão de entrega com hypercare (mesma do B1)
QUESTION_ROTA_CHEGADA = 9319    # route_time_to_arrival_address — a caminho da oficina
QUESTION_ROTA_SAIDA = 9324      # route_time_to_departure_address — a caminho do fornecedor

# Limiar de atraso para AÇÃO DE OPS (fornecedor) — mais sensível que o do B1
# (que é 30min e abre ticket ao cliente). Confirmado por Henrique: 15min.
ATRASO_MINUTOS_LIMIAR_OPS = 15

# A partir de qual hora (hora local, estimativa recalculada) avisar o cliente
# que a entrega pode chegar tarde.
HORA_LIMIAR_CHEGADA_TARDIA = 18

# Limiares de desvio de rota — confirmados por Henrique em 2026-07-13, a
# partir das questions 9319 (chegada/oficina) e 9324 (saída/fornecedor).
ROTA_CHEGADA_RISCO_MIN_POR_KM = 5      # acima disso = risco
ROTA_CHEGADA_DESVIO_MIN_POR_KM = 7     # acima disso = desvio explícito
ROTA_SAIDA_RISCO_MINUTOS = 15          # acima disso = risco
ROTA_SAIDA_DESVIO_MINUTOS = 22         # acima disso = desvio explícito

STATUS_EM_COMPRAS = "Em Compras"

BRT_OFFSET = timezone(timedelta(hours=-3))
JANELA_INICIO = (8, 0)     # 08:00 BRT
JANELA_FIM = (17, 30)      # 17:30 BRT


# ---------------------------------------------------------------------------
# Janela de horário — igual em espírito ao B1, mas 08:00-17:30 (não 08:20)
# ---------------------------------------------------------------------------

def dentro_da_janela(now_utc: datetime = None) -> bool:
    now_utc = now_utc or datetime.now(timezone.utc)
    now_brt = now_utc.astimezone(BRT_OFFSET)

    if now_brt.weekday() >= 5:  # sábado/domingo
        return False

    inicio = now_brt.replace(hour=JANELA_INICIO[0], minute=JANELA_INICIO[1], second=0, microsecond=0)
    fim = now_brt.replace(hour=JANELA_FIM[0], minute=JANELA_FIM[1], second=0, microsecond=0)
    return inicio <= now_brt <= fim


# ---------------------------------------------------------------------------
# Gatilho 1 — "Em Compras" com atraso >= 15min -> avisar fornecedor
# ---------------------------------------------------------------------------

def avaliar_fornecedor_compras(rows: list) -> list:
    """
    Pedidos na etapa 'Em Compras' com atraso previsto >= 15min. Bootstrap V0
    (decisão de Henrique): a ação real (quando sair do dry-run) é um aviso no
    Slack #logistica, marcando os responsáveis — NÃO WhatsApp ainda.

    PENDENTE (decisão #3, ainda não resolvida): a question 11609 não traz o
    fornecedor pretendido para pedidos ainda em 'waiting_purchase' (a compra
    não foi efetivada, então não há stock_uid/dim_stock_provider.contact_phone
    disponível neste ponto). Por isso o campo 'fornecedor' abaixo sempre
    aparece como pendente de mapeamento — não inventar um valor.
    """
    eventos = []
    for row in rows:
        if (row.get("status") or "").strip() != STATUS_EM_COMPRAS:
            continue
        atraso = row.get("atraso_em_minutos") or 0
        if atraso < ATRASO_MINUTOS_LIMIAR_OPS:
            continue
        eventos.append({
            "trigger": "fornecedor_compras_atraso",
            "order_id": row.get("order_id"),
            "mecani_id": row.get("reference_id"),
            "order_item_id": row.get("order_item_id"),
            "fantasy_name": row.get("fantasy_name"),
            "atraso_em_minutos": round(atraso, 1),
            "fornecedor": "PENDENTE — decisão #3 (mapear fornecedor pretendido em 'Em Compras')",
            "acao_simulada": "aviso no Slack #logistica (bootstrap V0, marcando responsáveis)",
            "link_ops": row.get("link_ops"),
        })
    return eventos


# ---------------------------------------------------------------------------
# Gatilho 2 — expectativa de entrega recalculada após as 18h -> avisar cliente
# ---------------------------------------------------------------------------

def _parse_timestamp(valor):
    if not valor:
        return None
    texto = str(valor).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def avaliar_chegada_apos_18h(rows: list) -> list:
    """
    Usa `estimated_calc_time` (estimativa recalculada em tempo real, já
    presente na question 11609) — não o `estimated_delivery_at` original —
    porque é o que reflete a expectativa atual, igual ao que o time de Ops
    olha hoje. Ação simulada: reaproveitar o canal de comunicação com cliente
    já existente (Twilio/Z-API).
    """
    eventos = []
    for row in rows:
        if (row.get("status") or "").strip() == STATUS_EM_COMPRAS:
            continue  # ainda não faz sentido avisar cliente nesta etapa
        estimado = _parse_timestamp(row.get("estimated_calc_time"))
        if not estimado:
            continue
        if estimado.hour < HORA_LIMIAR_CHEGADA_TARDIA:
            continue
        eventos.append({
            "trigger": "cliente_chegada_apos_18h",
            "order_id": row.get("order_id"),
            "mecani_id": row.get("reference_id"),
            "order_item_id": row.get("order_item_id"),
            "status": row.get("status"),
            "estimativa_recalculada": estimado.isoformat(),
            "acao_simulada": "avisar cliente via canal já existente (Twilio/Z-API)",
            "link_ops": row.get("link_ops"),
        })
    return eventos


# ---------------------------------------------------------------------------
# Gatilho 3 — desvio de rota (questions 9319 / 9324) -> ligação Pitchyes
# ---------------------------------------------------------------------------

# Nomes de coluna candidatos — AINDA NÃO CONFIRMADOS. Ajustar assim que
# Henrique confirmar o schema real dessas duas questions.
CANDIDATOS_COLUNA_MIN_POR_KM = ["route_time_to_arrival_address_min_per_km", "min_per_km", "tempo_por_km"]
CANDIDATOS_COLUNA_MINUTOS_SAIDA = ["route_time_to_departure_address", "minutos_deslocamento", "tempo_deslocamento_min"]


def _primeira_coluna_disponivel(row: dict, candidatos: list):
    for nome in candidatos:
        if nome in row:
            return nome, row[nome]
    return None, None


def avaliar_desvio_rota(rows_9319: list, rows_9324: list) -> dict:
    """
    PENDENTE: o schema exato das questions 9319 e 9324 ainda não foi
    confirmado por Henrique (só temos os limiares de negócio, não os nomes
    de coluna). Em vez de arriscar uma lógica errada bem no momento em que
    o objetivo é validar a detecção, este gatilho roda em modo
    'somente diagnóstico': reporta as colunas realmente disponíveis nas
    duas questions para confirmação, e só aplica a regra se encontrar uma
    coluna candidata reconhecida.
    """
    diagnostico = {
        "colunas_9319_disponiveis": sorted(rows_9319[0].keys()) if rows_9319 else [],
        "colunas_9324_disponiveis": sorted(rows_9324[0].keys()) if rows_9324 else [],
        "pendencia": (
            "Nomes de coluna de 9319/9324 não confirmados — ajustar "
            "CANDIDATOS_COLUNA_MIN_POR_KM / CANDIDATOS_COLUNA_MINUTOS_SAIDA "
            "assim que confirmados."
        ),
    }

    eventos = []

    for row in rows_9319:
        col, valor = _primeira_coluna_disponivel(row, CANDIDATOS_COLUNA_MIN_POR_KM)
        if col is None or valor is None:
            continue
        if valor <= ROTA_CHEGADA_RISCO_MIN_POR_KM:
            continue
        nivel = "desvio_explicito" if valor > ROTA_CHEGADA_DESVIO_MIN_POR_KM else "risco"
        eventos.append({
            "trigger": "desvio_rota_chegada_oficina",
            "nivel": nivel,
            "min_por_km": valor,
            "order_id": row.get("order_id"),
            "mecani_id": row.get("reference_id") or row.get("mecaniid"),
            "acao_simulada": (
                "ligação automática via Pitchyes (desvio explícito)" if nivel == "desvio_explicito"
                else "aviso de risco — chat do provedor (RPA, ainda não construído)"
            ),
        })

    for row in rows_9324:
        col, valor = _primeira_coluna_disponivel(row, CANDIDATOS_COLUNA_MINUTOS_SAIDA)
        if col is None or valor is None:
            continue
        if valor <= ROTA_SAIDA_RISCO_MINUTOS:
            continue
        nivel = "desvio_explicito" if valor > ROTA_SAIDA_DESVIO_MINUTOS else "risco"
        eventos.append({
            "trigger": "desvio_rota_saida_fornecedor",
            "nivel": nivel,
            "minutos": valor,
            "order_id": row.get("order_id"),
            "mecani_id": row.get("reference_id") or row.get("mecaniid"),
            "acao_simulada": (
                "ligação automática via Pitchyes (desvio explícito)" if nivel == "desvio_explicito"
                else "aviso de risco — chat do provedor (RPA, ainda não construído)"
            ),
        })

    return {"eventos": eventos, "diagnostico": diagnostico}


# ---------------------------------------------------------------------------
# Self-test — dados fixos, sem rede
# ---------------------------------------------------------------------------

def fixture_rows_11609() -> list:
    agora = datetime.now(timezone.utc).astimezone(BRT_OFFSET)
    hoje_19h = agora.replace(hour=19, minute=0, second=0, microsecond=0).isoformat()
    hoje_14h = agora.replace(hour=14, minute=0, second=0, microsecond=0).isoformat()
    return [
        # 1) Em Compras, atraso 20min -> deve disparar aviso a fornecedor
        {
            "reference_id": "MEC2001", "order_item_id": 6001, "order_id": 2001,
            "status": "Em Compras", "atraso_em_minutos": 20.0, "fantasy_name": "OFICINA F",
            "link_ops": "https://ops.mecanizou.com/deliveries/tracking-single-express?referenceId=MEC2001",
            "estimated_calc_time": hoje_14h,
        },
        # 2) Em Compras, atraso 10min -> não dispara (abaixo do limiar de 15min)
        {
            "reference_id": "MEC2002", "order_item_id": 6002, "order_id": 2002,
            "status": "Em Compras", "atraso_em_minutos": 10.0, "fantasy_name": "OFICINA G",
            "link_ops": "https://ops.mecanizou.com/deliveries/tracking-single-express?referenceId=MEC2002",
            "estimated_calc_time": hoje_14h,
        },
        # 3) Rota para Oficina, estimativa recalculada às 19h -> avisar cliente
        {
            "reference_id": "MEC2003", "order_item_id": 6003, "order_id": 2003,
            "status": "Rota para Oficina", "atraso_em_minutos": 5.0, "fantasy_name": "OFICINA H",
            "link_ops": "https://ops.mecanizou.com/deliveries/tracking-single-express?referenceId=MEC2003",
            "estimated_calc_time": hoje_19h,
        },
        # 4) Esperando no Fornecedor, estimativa às 14h -> não dispara aviso de chegada tardia
        {
            "reference_id": "MEC2004", "order_item_id": 6004, "order_id": 2004,
            "status": "Esperando no Fornecedor", "atraso_em_minutos": 0.0, "fantasy_name": "OFICINA I",
            "link_ops": "https://ops.mecanizou.com/deliveries/tracking-single-express?referenceId=MEC2004",
            "estimated_calc_time": hoje_14h,
        },
    ]


def fixture_rows_rota() -> list:
    return [
        {"order_id": 3001, "reference_id": "MEC3001", "route_time_to_arrival_address_min_per_km": 8.5},
        {"order_id": 3002, "reference_id": "MEC3002", "route_time_to_arrival_address_min_per_km": 3.0},
    ], [
        {"order_id": 3003, "reference_id": "MEC3003", "route_time_to_departure_address": 25.0},
        {"order_id": 3004, "reference_id": "MEC3004", "route_time_to_departure_address": 10.0},
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Acionamento fornecedor/entregador (B2) — Fase 0, diagnóstico")
    parser.add_argument("--self-test", action="store_true", help="Roda com dados fixos, sem rede")
    parser.add_argument("--force", action="store_true", help="Ignora a checagem de janela de horário (08:00-17:30 BRT, seg-sex)")
    args = parser.parse_args()

    if not args.force and not args.self_test and not dentro_da_janela():
        print(json.dumps({"ok": True, "skipped": True, "motivo": "fora da janela 08:00-17:30 BRT (seg-sex)"},
                          ensure_ascii=False, indent=2))
        return

    if args.self_test:
        rows_11609 = fixture_rows_11609()
        rows_9319, rows_9324 = fixture_rows_rota()
    else:
        env = load_env()
        rows_11609 = fetch_question_rows(env, QUESTION_ENTREGA)
        rows_9319 = fetch_question_rows(env, QUESTION_ROTA_CHEGADA)
        rows_9324 = fetch_question_rows(env, QUESTION_ROTA_SAIDA)

    eventos_fornecedor = avaliar_fornecedor_compras(rows_11609)
    eventos_cliente = avaliar_chegada_apos_18h(rows_11609)
    resultado_rota = avaliar_desvio_rota(rows_9319, rows_9324)

    result = {
        "ok": True,
        "checado_em": datetime.now(timezone.utc).isoformat(),
        "modo": "self-test" if args.self_test else "dry-run",
        "fase": "Fase 0 — diagnóstico, nenhuma ação real disparada",
        "pedidos_avaliados": len(rows_11609),
        "eventos_fornecedor_compras": eventos_fornecedor,
        "eventos_cliente_chegada_tardia": eventos_cliente,
        "eventos_desvio_rota": resultado_rota["eventos"],
        "diagnostico_colunas_rota": resultado_rota["diagnostico"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

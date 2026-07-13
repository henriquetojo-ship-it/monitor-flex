# SPEC — Acionamento Automático de Fornecedores e Entregadores (B2)

Status: **Fase 0 implementada em modo diagnóstico** (dry-run/log-only, nenhuma
ação real é disparada). Extensão do B1 (`ticket_preventivo_atraso.py`) para o
redesenho do processo de acompanhamento de entregas e comunicação com
entregadores/fornecedores.

## Contexto

Hoje o time de Ops monitora o painel de estimativas de entrega (question
[11609](https://metabase.tools.mecanizou.com/question/11609)) e age
manualmente por status: identifica fornecedor no OPS e manda WhatsApp (etapa
Compras), envia mensagem padrão no chat do provedor logístico (a caminho da
coleta/entrega), ou liga para o entregador em caso de desvio de rota. O
objetivo do B2 é automatizar a **detecção** desses gatilhos por completo e,
progressivamente por fase, a **ação**.

## O que o agente faz (Fase 0)

Fonte de dados:
- Question 11609 (Beta Lake) — mesma do B1, `estudos/previsao-entrega-com-hypercare.sql`.
- Question [9319](https://metabase.tools.mecanizou.com/question/9319-route-time-to-arrival-address-economics) — tempo de rota "a caminho da oficina" (min/km).
- Question [9324](https://metabase.tools.mecanizou.com/question/9324-route-time-to-departure-address-ms) — tempo de rota "a caminho do fornecedor/coleta" (min).

A cada 5 minutos, seg-sex, 08:00–17:30 (America/Sao_Paulo):

1. Busca as três questions.
2. Avalia 3 gatilhos independentes (ver `scripts/acionamento_entrega_fornecedor.py`):
   - **`fornecedor_compras_atraso`** — status `Em Compras` com `atraso_em_minutos >= 15`.
   - **`cliente_chegada_apos_18h`** — `estimated_calc_time` (estimativa recalculada, não o SLA original) com hora >= 18h, para pedidos fora da etapa Compras.
   - **`desvio_rota_chegada_oficina`** / **`desvio_rota_saida_fornecedor`** — ver limiares abaixo. Roda em modo "somente diagnóstico" até o schema de 9319/9324 ser confirmado (ver Pendências).
3. Imprime um JSON com todos os eventos detectados. Não posta no Slack, não liga, não manda mensagem — só loga, para comparação manual com o que o time de Ops fez naquele intervalo.

## Regras de negócio confirmadas (Henrique, 2026-07-13)

- **Limiar de atraso para ação de Ops (fornecedor):** 15 minutos — mais sensível que o limiar de 30min do B1 (que abre ticket ao cliente).
- **Desvio de rota "a caminho da oficina"** (question 9319, min/km): >5min/km = risco; >7min/km = desvio explícito.
- **Desvio de rota "a caminho do fornecedor"** (question 9324, minutos): >15min = risco; >22min = desvio explícito.
- **Pitchyes já tem API/webhook pronto** — ligação automática por desvio explícito é o item de menor risco técnico do pacote.
- **WhatsApp fornecedor (etapa Compras) — bootstrap V0:** aviso em grupo do Slack (`#logistica`) para acionamento manual pelo time, **não** WhatsApp via API ainda. Migrar para Twilio WhatsApp Business API é evolução de fase posterior (ver Fase 2 no plano de implantação), custo estimado ≈ R$150–210/mês para 100 acionamentos/dia.
- **WhatsApp cliente (chegada após 18h):** reaproveita o canal já existente (Twilio/Z-API do `twilio-integration-service`), sem custo novo.
- **Fila de decisão humana (N2, casos ambíguos):** Slack, grupo **Logística**, marcando **Topete e Maria**.
- **Infra:** servidor AWS + Lambda já contratados — sem custo adicional de hospedagem. RPA do chat dos provedores logísticos (Fase 4) roda como serviço persistente no servidor AWS, não em runner efêmero do GitHub Actions.
- **State store (dedupe):** projeto Supabase dedicado (separado do projeto do `customer-support-service`) — a criar na Fase 1.

## Arquivos

- `scripts/acionamento_entrega_fornecedor.py` — agente de diagnóstico (Fase 0). Suporta `--self-test` (sem rede) e `--force` (ignora a janela de horário). Sempre roda em modo log-only nesta fase — não há flag `--live`.
- `.github/workflows/acionamento-entrega-fornecedor.yml` — agendamento (5min, 08:00–17:30 BRT, seg-sex).

Rodar localmente:
```
python3 scripts/acionamento_entrega_fornecedor.py --self-test --force   # sem rede, valida a lógica
python3 scripts/acionamento_entrega_fornecedor.py                        # dry-run real (lê Metabase, não age)
```

## O que ainda falta confirmar (bloqueia as próximas fases)

1. **Schema das questions 9319 e 9324** — nomes exatos das colunas de tempo de rota (min/km e minutos). O script já loga as colunas reais disponíveis em cada execução (`diagnostico_colunas_rota`) para facilitar o ajuste. Bloqueia a Fase 1 (Pitchyes) até ser confirmado. → função `avaliar_desvio_rota()`.
2. **Contrato exato da API do Pitchyes** (endpoint, auth, payload). Henrique vai capturar. Bloqueia a Fase 1.
3. **Telefone e nome do driver no Metabase** — adicionar essas colunas às questions relevantes para simplificar a integração com o Pitchyes (decisão de Henrique, substitui a necessidade de consultar o OPS manualmente para obter o contato do motorista). Bloqueia a Fase 1.
4. **Fornecedor pretendido na etapa `Em Compras`** — a question 11609 não expõe `stock_uid`/`dim_stock_provider.contact_phone` para pedidos ainda em `waiting_purchase` (a compra não foi efetivada). Bloqueia a Fase 2 se for automatizar o envio direto ao fornecedor (não bloqueia o bootstrap V0 via Slack, que não depende desse dado).
5. **Projeto Supabase dedicado para o state store** (dedupe de ações já tomadas). A criar. Bloqueia a Fase 1 em diante (sem dedupe, risco de ligar/avisar repetidamente para o mesmo pedido a cada execução de 5min).
6. **Viabilidade técnica do RPA no servidor AWS** (Fase 4) — validar com o time de tech como publicar um serviço Playwright persistente na infra já contratada.
7. **IDs do Slack** (canal `#logistica`, member IDs de Topete e Maria para @mention) — necessários para a Fase 1 em diante, quando a fila N2 passar a postar de verdade.
8. **Secrets novos** (`PITCHYES_API_KEY`, credenciais do projeto Supabase dedicado, `SLACK_LOGISTICA_CHANNEL_ID`) — a mapear com engenharia antes da Fase 1/2.

## Relação com o B1

Este agente é uma extensão do mesmo padrão do B1 (`ticket_preventivo_atraso.py`):
mesma fonte de dado (11609), mesmo helper de leitura de question (`fetch_question_rows`,
importado diretamente do módulo do B1), mesmo formato de log JSON, mesmo padrão de
workflow (cron amplo + checagem fina de janela no script, alerta de falha no Slack).
O B1 continua responsável exclusivamente pela abertura de ticket ao cliente
(hypercare / atraso >= 30min); o B2 cobre os gatilhos de ação a fornecedor/entregador.
As duas frentes podem, no futuro, ser fundidas num único cron que lê 11609 uma vez.

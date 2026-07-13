# SPEC — Ticket Preventivo por Hypercare / Atraso de Entrega (B1)

Status: **implementado em modo dry-run**. A abertura real de tickets (`--live`)
depende de 3 confirmações de engenharia listadas no final deste documento.

## O que o agente faz

Fonte de dados: [question 11609 do Metabase](https://metabase.tools.mecanizou.com/question/11609)
(`estudos/previsao-entrega-com-hypercare.sql`, banco Beta Lake).

A cada 10 minutos, seg-sex, das 08:20 às 17:30 (America/Sao_Paulo):

1. Busca as linhas da question 11609 (uma linha por `order_item_id`).
2. Agrupa por `order_id`. Um pedido com vários itens precisando de acompanhamento
   gera **um único ticket**, com todos os `order_item_id` listados na descrição.
3. Decide se o pedido precisa de ticket:
   - **100% dos itens com `hypercare = 'HC'`** → sempre precisa.
   - Itens **sem** `HC` mas com `atraso_em_minutos >= 30` → precisa.
4. Define o título:
   - `Hypercare pedido {mecani_id}` — se qualquer item do pedido está em HC.
   - `Atraso pedido {mecani_id}` — se o motivo é só atraso ≥ 30min (sem HC).
   - `{mecani_id}` = coluna `reference_id` da question (é o Mecani ID, o mesmo
     número usado no link de rastreio `ops.mecanizou.com/...?referenceId=`).
5. Monta a descrição = título + lista de `order_item_id` monitorados + oficina + link.
6. Canal = sempre **Plataforma**. Categoria = sempre **Preventivo**.
7. Antes de abrir, verifica se já existe um ticket **em aberto** na plataforma
   para o mesmo pedido — via busca por `"pedido {mecani_id}"` no título dos
   tickets retornados por `GET /v1/tickets` (ver seção de API abaixo). Se já
   existe, não abre de novo.
8. Cria o ticket (ou, em dry-run, só mostra o payload que seria enviado).

## Arquivos

- `scripts/ticket_preventivo_atraso.py` — agente completo (fetch, regra,
  dedupe, criação). Suporta `--dry-run` (padrão), `--live`, `--self-test`,
  `--force` (ignora a janela de horário).
- `.github/workflows/ticket-preventivo.yml` — roda a cada 10min via cron
  amplo (`*/10 11-20 * * 1-5` UTC) e deixa o script decidir o corte fino
  de 08:20–17:30 BRT internamente (o cron do GitHub Actions não expressa
  esse corte com precisão de minuto).

Rodar localmente:
```
python3 scripts/ticket_preventivo_atraso.py --self-test --force   # sem rede, valida a lógica
python3 scripts/ticket_preventivo_atraso.py                        # dry-run real (lê Metabase, não abre ticket)
python3 scripts/ticket_preventivo_atraso.py --live                 # abre ticket de verdade (só depois das confirmações abaixo)
```

## API de tickets (ops.mecanizou.com / customer-support-service)

Reverse-engenharia feita por Henrique em 2026-07-01, via Supabase Edge Function:

- **Base confirmada:** `https://yqodvydsibtfntnrwyjb.supabase.co/functions/v1/api/v1/tickets`
  (mesmo projeto Supabase já registrado na memória do projeto: `yqodvydsibtfntnrwyjb`.)
- **GET (listagem) — exemplo capturado:**
  `GET .../v1/tickets?assignee_uid=b312cc10-8bbc-42d2-be95-6657f442b8bb&needs_action=true&limit=1`
- **POST (criação) — endpoint provável:** `POST .../v1/tickets`, campos supostos
  no payload: `title`, `channel`, `category`, `segment`, `workshop_id` (ou
  similar), `order_id` (opcional), `assignee_uid` (opcional), `tags`
  (opcional), `description`, `attachments` (opcional).
- **Autenticação:** não confirmada — provavelmente `Authorization: Bearer <token>`
  e/ou `apikey: <supabase-anon-key>` (comum em Edge Functions do Supabase, que
  costumam exigir os dois headers: um de gateway e um de app).

O script já está preparado para usar `CUSTOMER_SUPPORT_API_URL`,
`CUSTOMER_SUPPORT_API_KEY` (header `apikey`) e `CUSTOMER_SUPPORT_API_TOKEN`
(header `Authorization: Bearer`) assim que forem confirmados — só faltam
os valores reais.

## O que ainda falta confirmar (bloqueia `--live`)

O script já roda em dry-run e mostra exatamente o payload que enviaria. Faltam
3 confirmações concretas, todas capturáveis pelo DevTools do navegador ao usar
o formulário de abertura de ticket em ops.mecanizou.com (Henrique já se
ofereceu para capturar isso):

1. **Busca de oficina** — ao digitar o `fantasy_name` em CAIXA ALTA no campo
   "oficina", qual requisição é feita (endpoint, método, parâmetros) e qual
   o formato da resposta (o campo que identifica a oficina, ex.: `workshop_id`)?
   → função `search_workshop()` em `ticket_preventivo_atraso.py`, hoje lança
   `NotImplementedError` de propósito.
2. **Dropdown "últimos pedidos da oficina"** — ao selecionar a oficina, qual
   requisição popula a lista de pedidos, e qual campo da resposta corresponde
   ao Mecani ID (para selecionar a opção certa)?
   → função `find_order_option()`, também travada com `NotImplementedError`.
3. **Payload exato do POST de criação + headers de autenticação** — capturar
   uma criação de ticket real (Network tab) para confirmar nomes de campo,
   valores exatos de `channel`/`category` (string livre? slug? id numérico?)
   e o(s) header(s) de autenticação usados.

Assim que essas 3 coisas forem confirmadas, basta implementar
`search_workshop()`/`find_order_option()` de verdade, ajustar o payload em
`create_ticket()` se necessário, cadastrar os secrets no GitHub
(`CUSTOMER_SUPPORT_API_URL`, `CUSTOMER_SUPPORT_API_KEY`/`_TOKEN`,
`CUSTOMER_SUPPORT_ASSIGNEE_UID` se aplicável) e trocar o workflow para
`--live` (input `live: "true"` no `workflow_dispatch`, ou remover o dry-run
padrão).

## Regra de negócio — decisões já fechadas com Henrique (2026-07-01)

- Rodar a cada 10 minutos, seg-sex, 08:20–17:30 (America/Sao_Paulo).
- 100% dos itens/pedidos com label HC precisam de ticket.
- Itens fora de HC com atraso previsto ≥ 30min precisam de ticket.
- Nome da oficina buscado em CAIXA ALTA.
- Título por motivo: `Hypercare pedido {mecani_id}` / `Atraso pedido {mecani_id}`.
- Canal sempre "Plataforma", categoria sempre "Preventivo".
- Pedido selecionado via Mecani ID (== `reference_id` da question 11609).
- Descrição = título + todos os `order_item_id` a monitorar.
- Vários itens do mesmo pedido → um único ticket.
- Antes de criar, checar tickets abertos na plataforma para o mesmo pedido;
  se já existe, não duplicar.

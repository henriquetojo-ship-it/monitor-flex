# monitor-flex

Monitor do Twilio Flex/TaskRouter da Mecanizou, rodando no GitHub Actions 3x/dia
em dias úteis (10h30, 14h e 18h, America/Sao_Paulo) e publicando o resumo no Slack.

Também roda aqui o agente de **ticket preventivo por hypercare/atraso de
entrega (B1)**, que abre tickets na plataforma de suporte automaticamente.

## Estrutura
- `scripts/twilio_agent.py` — coleta e analisa métricas do TaskRouter, monta o
  relatório e o texto do Slack. (Inalterado em relação ao uso local.)
- `.github/workflows/twilio-monitor.yml` — agendamento + publicação no Slack.
- `scripts/ticket_preventivo_atraso.py` — lê a question 11609 do Metabase
  (previsão de entrega com hypercare) e abre tickets preventivos quando um
  pedido está em hypercare (HC) ou com atraso previsto ≥ 30min. Roda em
  dry-run até a API de tickets ser confirmada com engenharia — ver
  `docs/SPEC-ticket-preventivo-atraso-B1.md`.
- `.github/workflows/ticket-preventivo.yml` — agendamento a cada 10min,
  seg-sex, 08:20–17:30 (America/Sao_Paulo).
- `requirements.txt` — dependências (`requests`, `python-dotenv`).
- `docs/RUNBOOK-monitor-github-actions.md` — passo a passo de instalação.
- `docs/SPEC-ticket-preventivo-atraso-B1.md` — regras de negócio, API de
  tickets e o que ainda falta confirmar para o agente B1 rodar em modo `--live`.

## Como colocar no ar
Siga `docs/RUNBOOK-monitor-github-actions.md`. Resumo:
1. Crie um repositório **privado** no GitHub e dê push desta pasta.
2. Cadastre os secrets do monitor do Flex: `TWILIO_ACCOUNT_SID`,
   `TWILIO_AUTH_TOKEN`, `TWILIO_WORKSPACE_SID`, `TWILIO_SLACK_CHANNEL_ID`,
   `SLACK_BOT_TOKEN`.
3. Para o ticket preventivo (B1): cadastre `METABASE_API_KEY` (já necessário
   para outros scripts). Os secrets `CUSTOMER_SUPPORT_API_URL`,
   `CUSTOMER_SUPPORT_API_KEY`/`CUSTOMER_SUPPORT_API_TOKEN` e
   `CUSTOMER_SUPPORT_ASSIGNEE_UID` só são necessários para rodar em `--live`
   (ver SPEC) — sem eles, o workflow roda em dry-run e só reporta o que faria.
4. Rode manualmente em **Actions → Run workflow** para testar cada um.

> Os secrets do Flex saem do seu `.env.local`. O `SLACK_BOT_TOKEN` (`xoxb-...`,
> escopo `chat:write`) você gera no Slack App — veja o Passo 4 do runbook.

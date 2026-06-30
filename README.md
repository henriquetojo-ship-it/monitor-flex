# monitor-flex

Monitor do Twilio Flex/TaskRouter da Mecanizou, rodando no GitHub Actions 3x/dia
em dias úteis (10h30, 14h e 18h, America/Sao_Paulo) e publicando o resumo no Slack.

## Estrutura
- `scripts/twilio_agent.py` — coleta e analisa métricas do TaskRouter, monta o
  relatório e o texto do Slack. (Inalterado em relação ao uso local.)
- `.github/workflows/twilio-monitor.yml` — agendamento + publicação no Slack.
- `requirements.txt` — dependências (`requests`, `python-dotenv`).
- `docs/RUNBOOK-monitor-github-actions.md` — passo a passo de instalação.

## Como colocar no ar
Siga `docs/RUNBOOK-monitor-github-actions.md`. Resumo:
1. Crie um repositório **privado** no GitHub e dê push desta pasta.
2. Cadastre os 5 secrets: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
   `TWILIO_WORKSPACE_SID`, `TWILIO_SLACK_CHANNEL_ID`, `SLACK_BOT_TOKEN`.
3. Rode manualmente em **Actions → Run workflow** para testar.

> Os 4 primeiros secrets saem do seu `.env.local`. O `SLACK_BOT_TOKEN` (`xoxb-...`,
> escopo `chat:write`) você gera no Slack App — veja o Passo 4 do runbook.

# Runbook — Migrar o monitor do Flex para o GitHub Actions

Objetivo: tirar o `scripts/twilio_agent.py` da sua máquina e rodá-lo 3x/dia
automaticamente no GitHub Actions, publicando o resumo no Slack. Tudo abaixo
roda **na sua máquina** uma única vez (o sandbox do Claude não tem acesso de
saída ao GitHub, então o push é seu).

Tempo estimado: ~15 minutos.

---

## O que já está pronto no projeto

Estes arquivos já foram criados na pasta `mecani-metabase` e só precisam ser
versionados e enviados:

- `.github/workflows/twilio-monitor.yml` — o workflow agendado (3x/dia, seg–sex).
- `requirements.txt` — dependências (`requests`, `python-dotenv`).
- `scripts/twilio_agent.py` — **inalterado**; continua funcionando igual na sua máquina.

O workflow:
1. monta um `.env.local` temporário a partir dos *secrets* do GitHub;
2. roda o monitor (`--minutes 480`, mesma janela de hoje);
3. lê o campo `slack_message` da saída e **publica no Slack** (isso é novo — o
   script só montava o texto, não postava; o post foi adicionado como um passo
   do workflow, não no Python);
4. apaga o `.env.local` no fim.

Horários do agendamento (cron em UTC, convertido de America/Sao_Paulo, UTC-3):
`13:30`, `17:00`, `21:00` UTC = **10h30, 14h00 e 18h00** em dias úteis.
Também dá pra rodar sob demanda em **Actions → Monitor Flex (Twilio) → Run workflow**.

---

## Passo 1 — Escolher o repositório de destino

`mecani-metabase` ainda **não é um repositório git**. Duas opções:

**Opção A — repositório novo dedicado** (recomendado para começar simples).
**Opção B — adicionar a um repo já existente** da Mecanizou (ex.: um repo de
automações/ops). Se for B, copie `.github/workflows/twilio-monitor.yml`,
`requirements.txt` e `scripts/twilio_agent.py` para lá e pule para o Passo 3.

Os comandos abaixo assumem a Opção A.

---

## Passo 2 — Versionar e enviar (Opção A)

No terminal, dentro da pasta do projeto:

```bash
cd /caminho/para/mecani-metabase

# Confirme que segredos NÃO vão junto (o .gitignore já cobre .env.local)
cat .gitignore

git init
git add .github/ requirements.txt scripts/twilio_agent.py
git commit -m "CI: monitor do Flex (Twilio) via GitHub Actions"

# Crie o repo PRIVADO no GitHub (via gh CLI):
gh repo create mecanizou/monitor-flex --private --source=. --remote=origin --push
```

> Sem o `gh` instalado? Crie o repositório privado pela web em
> github.com/new, depois:
> ```bash
> git remote add origin git@github.com:mecanizou/monitor-flex.git
> git branch -M main
> git push -u origin main
> ```

> **Importante:** mantenha o repositório **privado**. E confira que você está
> commitando só os 3 arquivos acima — não faça `git add .`, para não subir
> `.env.local`, snapshots ou a base de dados local.

---

## Passo 3 — Cadastrar os secrets

O workflow precisa de **5 secrets**. Quatro são os mesmos do seu `.env.local`;
o quinto (`SLACK_BOT_TOKEN`) é novo, porque agora o post no Slack acontece no CI.

Via `gh` (rode dentro do repo):

```bash
gh secret set TWILIO_ACCOUNT_SID
gh secret set TWILIO_AUTH_TOKEN
gh secret set TWILIO_WORKSPACE_SID
gh secret set TWILIO_SLACK_CHANNEL_ID
gh secret set SLACK_BOT_TOKEN
```

Cada comando pede o valor de forma interativa (não fica no histórico do shell).

Ou pela web: **Settings → Secrets and variables → Actions → New repository secret**.

Valores:
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WORKSPACE_SID`,
  `TWILIO_SLACK_CHANNEL_ID` → copie do seu `.env.local`.
- `SLACK_BOT_TOKEN` → token de um Slack App (começa com `xoxb-`). Veja o Passo 4.

---

## Passo 4 — Token do Slack (se ainda não tiver)

O `TWILIO_SLACK_CHANNEL_ID` você já tem; falta o token do bot que vai postar.

1. Acesse api.slack.com/apps → **Create New App** → *From scratch* (ou use um
   app já existente da Mecanizou).
2. Em **OAuth & Permissions → Scopes → Bot Token Scopes**, adicione
   `chat:write`.
3. **Install to Workspace** e copie o **Bot User OAuth Token** (`xoxb-...`).
4. No Slack, convide o bot para o canal de destino:
   `/invite @nome-do-bot` no canal cujo ID está em `TWILIO_SLACK_CHANNEL_ID`.
5. Use esse `xoxb-...` como o secret `SLACK_BOT_TOKEN`.

---

## Passo 5 — Testar

Em **Actions → Monitor Flex (Twilio) → Run workflow** (botão "Run workflow").
Acompanhe o log:
- o passo *Roda o monitor* deve terminar sem erro;
- o passo *Publica resumo no Slack* deve imprimir `Mensagem publicada em ...`;
- confira a mensagem no canal do Slack.

Se o Slack responder `not_in_channel`, falta convidar o bot para o canal
(Passo 4.4). Se responder `invalid_auth`, o `SLACK_BOT_TOKEN` está errado.

---

## Depois que estiver funcionando

- Pode parar de rodar o script manualmente na sua máquina.
- Próximo passo natural (quando crescer): mover de GitHub Actions para
  **Lambda + EventBridge**, igual ao Airton — sem mudar o Python.

---

## Lembrete de segurança

O token do GitHub (`ghp_...`) que você colou no chat foi usado só para leitura
da base de conhecimento. **Revogue-o** em github.com/settings/tokens assim que
terminar — ele não é necessário para nada deste runbook (aqui você usa seu
acesso normal / `gh auth login`).

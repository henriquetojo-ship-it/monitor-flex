# Guia de Produção — Monitor (Mensagem 1) + Auditoria (Mensagem 2)

Passo a passo para colocar no ar, via GitHub Actions, o que ajustamos e construímos hoje.
Garante que o GitHub vai espelhar exatamente a versão que testamos juntos.

> Regra de ouro: **as chaves (secrets) você configura/cola direto no GitHub.** Nada de
> credencial vai no código nem neste guia.

---

## 1. O que mudou hoje

| Arquivo | Status | O que é |
|---|---|---|
| `scripts/twilio_agent.py` | alterado | Mensagem 1 revisada: força máxima = 9, rótulo "(parcial)", horário BRT, espera máx do dia, aceitação e leitura corrigidas. Nova saída `slack_message_full`. |
| `scripts/audit_agent.py` | **novo** | Auditoria do dia + geração da Mensagem 2 (resumo consolidado) e publicação no Slack (`--post-slack`). |
| `.github/workflows/twilio-monitor.yml` | alterado | Agora publica a `slack_message_full` (a versão completa) e roda no fuso de Brasília (`TZ`). |
| `.github/workflows/audit-agent.yml` | **novo** | Roda a auditoria 1x/dia (18h30 BRT, seg-sex), grava na planilha e posta a Mensagem 2. |
| `.gitignore` | alterado | Ignora `google-sa.json` (usado no CI) e a pasta local de auditorias. |
| `requirements.txt` | alterado | Inclui `google-auth` (usado pela auditoria para gravar na planilha). |

> **Fora do escopo de hoje:** `.github/workflows/ticket-preventivo.yml`,
> `scripts/ticket_preventivo_atraso.py` e os docs de SPEC do ticket preventivo continuam
> soltos de propósito. Aquilo é outra iniciativa (em espera) — **não suba junto** para
> não ligar um workflow que ainda está em desenvolvimento.

---

## 2. Testar localmente ANTES de subir

Na sua máquina, dentro de `monitor-flex/`, com o `.env.local` já preenchido (chaves da Twilio,
Anthropic, Google e Slack).

### 2.1 Mensagem 1 (monitor)
```bash
python3 scripts/twilio_agent.py --minutes 480 > result.json
python3 -c "import json; print(json.load(open('result.json'))['slack_message_full'])"
```
Confira: "X/9 online", "(parcial)", horário em BRT, "Espera máx (dia)", aceitação com 🔴 se baixa.
**Não posta nada** — só imprime.

### 2.2 Mensagem 2 (auditoria) — modo seguro, sem gravar nem postar
```bash
python3 scripts/audit_agent.py --sample 10 --post-slack --summary-only --dry-run
```
- `--summary-only` → imprime o resumo, **não** publica no Slack.
- `--dry-run` → **não** grava na planilha.

Confira as notas por conversa e o bloco "RESUMO DO DIA (Mensagem 2)".

### 2.3 Rodada real local (opcional — grava na planilha E posta no Slack)
Só quando as notas e o resumo fizerem sentido:
```bash
python3 scripts/audit_agent.py --sample 10 --post-slack
```

---

## 3. Configurar os secrets no GitHub

Repositório → **Settings → Secrets and variables → Actions → New repository secret**.

**Já existem** (usados pelo monitor de hoje): `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_WORKSPACE_SID`, `TWILIO_SLACK_CHANNEL_ID`, `SLACK_BOT_TOKEN`.

**Adicionar para a auditoria:**

| Secret | Valor |
|---|---|
| `ANTHROPIC_API_KEY` | sua chave da Anthropic (a mesma do `.env.local`). |
| `GOOGLE_SHEET_ID` | o ID da planilha de auditorias (ou cole a URL inteira — o script extrai o ID). |
| `GOOGLE_SA_JSON` | **conteúdo inteiro** do arquivo `mecanizou-*.json` (a conta de serviço). Abra o arquivo, copie tudo e cole no campo do secret. |

> O workflow escreve esse JSON num arquivo temporário `google-sa.json` só durante a execução
> e o apaga no fim. Por isso `GOOGLE_SA_JSON` guarda o conteúdo, não o caminho.

---

## 4. Subir para o GitHub (você roda)

Commit **apenas** dos arquivos de hoje:

```bash
cd monitor-flex

git add \
  scripts/twilio_agent.py \
  scripts/audit_agent.py \
  .github/workflows/twilio-monitor.yml \
  .github/workflows/audit-agent.yml \
  .gitignore \
  requirements.txt \
  README.md \
  docs/GUIA-producao-github.md \
  docs/GUIA-auditoria-passo-a-passo.md

git commit -m "Monitor: Mensagem 1 revisada + Auditoria automática (Mensagem 2) no Slack"

git push origin main
```

> Antes de commitar, confirme que nenhum segredo entrou:
> ```bash
> git status
> git diff --cached --name-only
> ```
> Não pode aparecer `.env.local`, `mecanizou-*.json` nem `google-sa.json` (o `.gitignore` já bloqueia).

---

## 5. Validar no GitHub Actions

Aba **Actions** do repositório.

1. **Monitor Flex (Twilio)** → **Run workflow** → `minutes = 480` → Run.
   Deve postar a Mensagem 1 completa no canal.
2. **Auditoria de Atendimento** → **Run workflow** → `dry_run = true`, `sample = 10` → Run.
   Roda sem gravar/postar; confira o log ("RESUMO DO DIA").
3. Se o log estiver bom, rode de novo com `dry_run = false` para a rodada real
   (grava na planilha + posta a Mensagem 2).

Agendamentos automáticos (dias úteis, horário de Brasília):
- Monitor: 10h30, 14h00, 18h00.
- Auditoria: 18h30 (logo após o último corte do monitor).

---

## 6. Pendência conhecida (fast-follow)

Na rodada de teste, 1 de 10 conversas caiu com `'str' object has no attribute 'get'`
(a auditoria seguiu com as outras 9). Subimos a versão testada assim mesmo, conforme
combinado, e essa correção fica como próximo passo — não bloqueia a automação.

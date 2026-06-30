# Guia passo a passo (sem programação) — colocar o Monitor do Flex no ar

Este guia foi escrito para qualquer pessoa conseguir executar, **sem usar terminal
e sem digitar comandos**. Tudo é feito clicando no site do GitHub e no site do Slack.

Você vai usar os arquivos que já estão prontos na pasta **`monitor-flex`** (dentro
da pasta do projeto, no seu computador).

Tempo estimado: 20 a 30 minutos. Faça uma vez só; depois roda sozinho.

> O que isso faz, em uma frase: tira o monitor de dentro do seu computador e passa
> a rodá-lo "na nuvem" do GitHub, 3 vezes por dia, postando o resumo no Slack —
> mesmo com o seu computador desligado.

---

## Antes de começar — tenha estas 5 informações em mãos

Quatro delas estão em um arquivo chamado **`.env.local`**, dentro da pasta do
projeto. Esse arquivo é "escondido". Para abri-lo:

- **No Mac (Finder):** abra a pasta do projeto e aperte as teclas
  **Command + Shift + Ponto (.)** ao mesmo tempo. Os arquivos escondidos vão
  aparecer em cinza claro. Clique com o botão direito em `.env.local` →
  **Abrir com → TextEdit**.

Dentro dele você verá linhas como `TWILIO_ACCOUNT_SID=ABC123...`. O que vem
**depois do sinal de igual (=)** é o valor que você vai precisar copiar. Anote
(ou deixe esse arquivo aberto) os valores de:

1. `TWILIO_ACCOUNT_SID`
2. `TWILIO_AUTH_TOKEN`
3. `TWILIO_WORKSPACE_SID`
4. `TWILIO_SLACK_CHANNEL_ID`

A quinta informação (`SLACK_BOT_TOKEN`) a gente cria na **Parte 4** deste guia.

> ⚠️ Esses valores são senhas. Não cole em e-mail, chat ou documento público.
> Você vai colá-los apenas dentro das telas seguras do GitHub (Parte 3).

---

## Parte 1 — Criar o repositório no GitHub

Pense no "repositório" como uma pasta na nuvem que guarda os arquivos do monitor.

1. Acesse **github.com** e faça login (se a Mecanizou ainda não tem conta, crie uma
   gratuita com o e-mail da empresa).
2. No canto superior direito, clique no sinal de **+** e escolha **New repository**
   (Novo repositório).
3. Preencha:
   - **Repository name:** `monitor-flex`
   - **Owner:** escolha a organização da Mecanizou, se ela aparecer. Senão, deixe
     a sua conta pessoal.
   - Marque a opção **Private** (Privado). É importante que seja privado.
   - **Não marque** nada em "Add a README", "Add .gitignore" nem "license" —
     deixe tudo desmarcado.
4. Clique no botão verde **Create repository**.

Pronto, você verá uma página dizendo que o repositório está vazio. Deixe essa
aba aberta.

---

## Parte 2 — Subir os arquivos

São duas etapas: primeiro os arquivos "normais" (arrastando), depois um arquivo
especial (criando direto no site, porque ele fica em uma pasta escondida).

### 2A) Subir os arquivos normais (arrastar e soltar)

1. Na página do repositório vazio, clique no link **uploading an existing file**
   (enviar um arquivo existente). Se não vir o link, clique em **Add file →
   Upload files**.
2. Abra a pasta `monitor-flex` no seu computador (Finder).
3. Arraste para a área de upload do site, de uma vez:
   - a pasta **`scripts`**
   - a pasta **`docs`**
   - o arquivo **`requirements.txt`**
   - o arquivo **`README.md`**
4. Espere o site mostrar os nomes dos arquivos na lista.
5. Lá embaixo, em "Commit changes", clique no botão verde **Commit changes**.

> Não precisa subir a pasta `.github` nem o `.env.local`. A pasta `.github` a
> gente cria na próxima etapa, e o `.env.local` **nunca** deve ir para o GitHub.

### 2B) Criar o arquivo do agendamento (o "robô")

Este arquivo precisa ficar numa pasta com nome especial. O jeito mais fácil é
criá-lo direto no site:

1. No repositório, clique em **Add file → Create new file** (Criar novo arquivo).
2. No campo do nome do arquivo (lá em cima, onde fica o cursor), digite
   **exatamente** isto, com as barras:

   ```
   .github/workflows/twilio-monitor.yml
   ```

   Conforme você digita as barras `/`, o site vai criando as pastas sozinho.
3. Na área grande de texto abaixo, **cole todo o conteúdo** que está entre as
   linhas pontilhadas mais adiante (seção **"Conteúdo para colar"**).
4. Lá embaixo, clique no botão verde **Commit changes** e confirme novamente em
   **Commit changes** na janelinha que abrir.

---

## Parte 3 — Cadastrar as senhas (secrets)

"Secret" é um cofre do GitHub onde você guarda senhas com segurança. O robô lê
de lá, mas ninguém consegue ver o valor depois de salvo.

1. No repositório, clique em **Settings** (Configurações), no menu de cima.
2. Na coluna da esquerda, clique em **Secrets and variables** e depois em
   **Actions**.
3. Clique no botão **New repository secret** (Novo secret).
4. Em **Name**, digite o nome **exatamente** como abaixo, e em **Secret** cole o
   valor correspondente. Clique em **Add secret** e repita para cada um:

   | Name (digite assim)         | Valor (de onde tirar)                          |
   |-----------------------------|------------------------------------------------|
   | `TWILIO_ACCOUNT_SID`        | do arquivo `.env.local`                        |
   | `TWILIO_AUTH_TOKEN`         | do arquivo `.env.local`                        |
   | `TWILIO_WORKSPACE_SID`      | do arquivo `.env.local`                        |
   | `TWILIO_SLACK_CHANNEL_ID`   | do arquivo `.env.local`                        |
   | `SLACK_BOT_TOKEN`           | você cria na Parte 4 (começa com `xoxb-`)      |

   Ao final você deve ter **5 secrets** na lista. Os nomes precisam estar idênticos
   (maiúsculas e sublinhados iguais), senão o robô não acha.

---

## Parte 4 — Criar o acesso do Slack (SLACK_BOT_TOKEN)

Isso autoriza o robô a postar a mensagem no Slack. Faça uma vez.

1. Acesse **api.slack.com/apps** e faça login no Slack da Mecanizou.
2. Clique em **Create New App** → **From scratch**.
   - **App Name:** `Monitor Flex` (pode ser qualquer nome)
   - **Workspace:** escolha o Slack da Mecanizou. Clique em **Create App**.
3. No menu da esquerda, clique em **OAuth & Permissions**.
4. Role até **Scopes → Bot Token Scopes**. Clique em **Add an OAuth Scope** e
   adicione **`chat:write`**.
5. Suba de volta ao topo da mesma página e clique em **Install to Workspace**
   (Instalar). Confirme em **Allow** (Permitir).
6. Vai aparecer um **Bot User OAuth Token** que começa com **`xoxb-`**. Clique em
   **Copy** (Copiar). **Esse é o valor do secret `SLACK_BOT_TOKEN`** — volte na
   Parte 3 e cadastre-o.
7. Por último, no Slack, entre no canal onde a mensagem deve aparecer (o mesmo do
   `TWILIO_SLACK_CHANNEL_ID`), clique no nome do canal → **Integrations** →
   **Add apps**, e adicione o app **Monitor Flex**. (Ou digite no canal:
   `/invite @Monitor Flex`.) Sem isso o Slack recusa a mensagem.

---

## Parte 5 — Ligar e testar

1. No repositório, clique em **Actions**, no menu de cima.
2. Se aparecer um aviso amarelo perguntando se quer habilitar os workflows,
   clique em **I understand my workflows, go ahead and enable them**.
3. Na coluna da esquerda, clique em **Monitor Flex (Twilio)**.
4. À direita, clique no botão **Run workflow** e, na caixinha que abrir, clique de
   novo em **Run workflow** (pode deixar o campo de minutos como está).
5. Espere cerca de 1 minuto e atualize a página. Vai aparecer uma execução.
   Clique nela: se tudo deu certo, todos os passos ficam com um **✓ verde**.
6. Confira o Slack: a mensagem do monitor deve ter chegado no canal.

A partir daqui, ele roda sozinho às **10h30, 14h e 18h**, de segunda a sexta.
Você pode rodar manualmente quando quiser, repetindo os passos 3 e 4.

---

## Se algo der errado

- **No passo do Slack aparece `not_in_channel`:** faltou adicionar o app ao canal
  (Parte 4, item 7).
- **Aparece `invalid_auth`:** o `SLACK_BOT_TOKEN` foi copiado errado. Refaça a
  Parte 4 a partir do item 6 e atualize o secret.
- **O passo "Roda o monitor" falha:** confira se os 4 secrets do Twilio foram
  digitados com o nome exato e o valor certo (sem espaços sobrando).
- **Qualquer outra coisa:** abra a execução em **Actions**, clique no passo
  vermelho para ver a mensagem e me mande o texto que aparecer — eu te ajudo a
  resolver.

---

## Conteúdo para colar (arquivo `.github/workflows/twilio-monitor.yml`)

Copie tudo entre as linhas pontilhadas e cole na Parte 2B.

------------------------------------------------------------
```yaml
name: Monitor Flex (Twilio)

# Roda o monitor do Flex/TaskRouter 3x/dia em dias úteis e publica o
# resumo no Slack. Substitui a execução manual na máquina do Henrique.
#
# Horários (America/Sao_Paulo, UTC-3):
#   10h30  -> 13:30 UTC
#   14h00  -> 17:00 UTC
#   18h00  -> 21:00 UTC
on:
  schedule:
    - cron: "30 13 * * 1-5"   # 10h30 BRT, seg-sex
    - cron: "0 17 * * 1-5"    # 14h00 BRT, seg-sex
    - cron: "0 21 * * 1-5"    # 18h00 BRT, seg-sex
  workflow_dispatch:           # permite rodar manualmente pela aba Actions
    inputs:
      minutes:
        description: "Janela acumulada em minutos"
        required: false
        default: "480"

permissions:
  contents: read

concurrency:
  group: twilio-monitor
  cancel-in-progress: false

jobs:
  monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Monta .env.local a partir dos secrets
        env:
          TWILIO_ACCOUNT_SID: ${{ secrets.TWILIO_ACCOUNT_SID }}
          TWILIO_AUTH_TOKEN: ${{ secrets.TWILIO_AUTH_TOKEN }}
          TWILIO_WORKSPACE_SID: ${{ secrets.TWILIO_WORKSPACE_SID }}
          TWILIO_SLACK_CHANNEL_ID: ${{ secrets.TWILIO_SLACK_CHANNEL_ID }}
        run: |
          cat > .env.local <<EOF
          TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}
          TWILIO_AUTH_TOKEN=${TWILIO_AUTH_TOKEN}
          TWILIO_WORKSPACE_SID=${TWILIO_WORKSPACE_SID}
          TWILIO_SLACK_CHANNEL_ID=${TWILIO_SLACK_CHANNEL_ID}
          EOF

      - name: Roda o monitor
        run: |
          MINUTES="${{ github.event.inputs.minutes || '480' }}"
          python3 scripts/twilio_agent.py --minutes "$MINUTES" > result.json
          echo "Resultado salvo em result.json"

      - name: Publica resumo no Slack
        env:
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.TWILIO_SLACK_CHANNEL_ID }}
        run: |
          MSG=$(python3 -c "import json,sys; print(json.load(open('result.json'))['slack_message'])")
          python3 - "$MSG" <<'PY'
          import json, os, sys, urllib.request
          msg = sys.argv[1]
          token = os.environ["SLACK_BOT_TOKEN"]
          channel = os.environ["SLACK_CHANNEL_ID"]
          body = json.dumps({"channel": channel, "text": msg, "mrkdwn": True}).encode()
          req = urllib.request.Request(
              "https://slack.com/api/chat.postMessage",
              data=body,
              headers={
                  "Authorization": f"Bearer {token}",
                  "Content-Type": "application/json; charset=utf-8",
              },
          )
          resp = json.load(urllib.request.urlopen(req))
          if not resp.get("ok"):
              print(f"[ERRO] Slack respondeu: {resp}", file=sys.stderr)
              sys.exit(1)
          print(f"Mensagem publicada em {channel} (ts={resp.get('ts')})")
          PY

      - name: Limpa .env.local
        if: always()
        run: rm -f .env.local
```
------------------------------------------------------------

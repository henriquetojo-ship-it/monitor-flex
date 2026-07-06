# Guia passo a passo — Agente de Auditoria de Atendimento

Este guia é para quem **não é programador**. Ele explica como colocar o agente de
auditoria (`scripts/audit_agent.py`) para funcionar e como fazer o primeiro teste
pequeno, com calma.

## O que esse agente faz

Ele pega conversas de atendimento do **Twilio**, pede para o **Claude** (a IA da
Anthropic) ler cada conversa e dar uma nota seguindo a nossa
`rubrica_auditoria.md` (etapa por etapa, de E0 a E5, total 100 pontos), e grava o
resultado numa **planilha do Google** — uma linha por conversa.

Tudo roda no **seu Terminal**, do mesmo jeito que o monitor do Flex já roda. Você
nunca precisa me mandar nenhuma senha: as chaves ficam só no seu computador, no
arquivo `.env.local`.

---

## Parte 1 — Preparação (só precisa fazer uma vez)

### 1.1 — Instalar a nova dependência

O agente usa uma biblioteca a mais para escrever na planilha. No Terminal, dentro
da pasta do projeto, rode:

```
pip3 install -r requirements.txt
```

> No Mac o comando costuma ser `pip3` (não `pip`). Se `pip3` também não for
> encontrado, use `python3 -m pip install -r requirements.txt`.

### 1.2 — Chave da API da Anthropic (o "cérebro")

1. Acesse **console.anthropic.com** e faça login.
2. Vá em **API Keys** → **Create Key**. Dê um nome (ex.: `auditoria-mecanizou`).
3. **Copie a chave** (ela começa com `sk-ant-...`). Ela só aparece uma vez.
4. O arquivo `.env.local` já existe na pasta `monitor-flex` (com os espaços prontos).
   Abra ele pelo Terminal com:

   ```
   open -e .env.local
   ```

   Isso abre no TextEdit. Troque o texto `COLE_SUA_CHAVE_ANTHROPIC_AQUI` pela sua
   chave, salve e feche.

> **Não** digite a chave direto no Terminal (escrever `ANTHROPIC_API_KEY=...` numa
> linha sozinha no zsh não funciona e ainda deixa a chave no histórico). O lugar
> certo é dentro do arquivo `.env.local`.
>
> Os campos do Twilio e da planilha ficam no mesmo arquivo, mas só são necessários
> a partir do Teste 1 — para o Teste 0 pode deixá-los como estão.

### 1.3 — Planilha do Google (onde o resultado é gravado)

Vamos **reaproveitar a planilha de auditoria que já existe** (aquela com as colunas
`data`, `canal`, `responsável`, `score`, `evidência`, etc.). O agente foi feito para
respeitar as colunas que você já montou: ele **preenche as que já existem** e só
**acrescenta à direita** as colunas novas que a rubrica exige (classificação, resumo
inteligente da conversa, e a nota de cada etapa E0–E5). Nada do que você já tem é
apagado ou reordenado.

1. Abra a planilha de auditoria existente.
2. Clique em **Compartilhar** e compartilhe com este e-mail, como **Editor** (se já
   não estiver compartilhado):

   ```
   robo-auditoria@mecanizou-315418.iam.gserviceaccount.com
   ```

   Esse é o "robô" que vai escrever na planilha. Sem esse compartilhamento, ele
   não consegue gravar.
3. Copie o endereço (URL) da planilha, ou só o **ID** que fica no meio dele:

   ```
   https://docs.google.com/spreadsheets/d/AQUI_NO_MEIO_ESTÁ_O_ID/edit
   ```

   Pode colar a **URL inteira** ou só o **ID** — o agente entende os dois.

4. Adicione ao `.env.local`:

   ```
   GOOGLE_SHEET_ID=cole-a-url-ou-o-id-aqui
   ```

> O agente grava na aba `Auditorias`. Se a sua planilha usa outra aba (por exemplo
> `Página1`), ele detecta sozinho e usa a primeira aba — não precisa renomear nada.
> Se preferir fixar uma aba específica, adicione `AUDIT_SHEET_TAB=NomeDaAba` no
> `.env.local`.

Pronto. Preparação concluída.

---

## Parte 2 — O primeiro teste (com calma, em etapas)

A ideia é ir do mais seguro para o mais completo. Faça uma etapa de cada vez.

### Teste 0 — Validar a nota com uma conversa de exemplo (nem precisa do Twilio)

Isso testa só o "cérebro": pega uma conversa de mentira que já deixei pronta e vê
se o Claude dá notas que fazem sentido. Só precisa da chave da Anthropic (passo 1.2).

```
python3 scripts/audit_agent.py --dry-run --fixture estudos/exemplo_conversa.json
```

Você vai ver na tela a nota de cada etapa e a nota total. Essa conversa de exemplo
tem **uma falha proposital** (a atendente demorou e o cliente teve que cobrar), então
o esperado é a etapa **E4 (Follow-up Proativo) perder pontos**. Se isso aparecer, o
cérebro está avaliando direito. 👍

### Teste 1 — Conferir o formato das conversas reais do Twilio

Antes de auditar de verdade, vamos só **olhar** como uma conversa real chega do
Twilio. Isso não usa IA nem escreve em lugar nenhum:

```
python3 scripts/audit_agent.py --raw --sample 1
```

**Me mande aqui no chat o que aparecer.** Eu confiro se o formato bateu com o que o
código espera (quem é cliente, quem é atendente, onde está o texto das mensagens) e,
se precisar, faço um ajuste fino. Esse passo evita surpresa.

### Teste 2 — Auditar conversas reais, só mostrando na tela

Agora sim a IA entra, mas **ainda sem gravar na planilha**. Por padrão o robô só
audita conversas **encerradas** (`state=closed`) — nunca as que ainda estão
acontecendo ao vivo, porque não dá para avaliar de forma justa uma conversa que não
terminou. Ele também prioriza conversas mais longas (>1h), mas inclui variedade, e
sorteia aleatoriamente até 10 por rodada.

Para o teste, use uma amostra pequena:

```
python3 scripts/audit_agent.py --dry-run --sample 2
```

Confira as notas. Fazem sentido para você? Se quiser, me mande o resultado que a
gente analisa junto se a rubrica está calibrada.

### Teste 3 — Rodar de verdade (grava na planilha)

Quando as notas estiverem boas, é só tirar o `--dry-run`:

```
python3 scripts/audit_agent.py --sample 2
```

Abra a planilha do Google: devem aparecer 2 linhas novas, uma por conversa. 🎉
Na **primeira** gravação, dê uma olhada no cabeçalho: as colunas novas da rubrica
(classificação, `historico_task`, `nota_E0`…`nota_E5`) devem ter entrado à direita
das que você já tinha. Se alguma coluna sua ficou vazia sem querer, me avisa que eu
ajusto o mapeamento de nomes.

---

## Comandos úteis do dia a dia

```
# Rodada padrão do dia: 10 conversas encerradas, sorteadas (grava na planilha)
python3 scripts/audit_agent.py

# Auditar uma conversa específica (você pega o SID no Twilio, começa com CH)
python3 scripts/audit_agent.py --dry-run --conversation-sid CHxxxxxxxxxxxx

# Mudar o tamanho da amostra
python3 scripts/audit_agent.py --sample 5

# Incluir mais conversas curtas (menos peso nas longas)
python3 scripts/audit_agent.py --long-share 0.5

# Guardar também uma cópia de cada avaliação em arquivo (estudos/auditorias/)
python3 scripts/audit_agent.py --sample 3 --save-local
```

> Por padrão o robô audita **até 10 conversas encerradas por rodada**, priorizando
> as mais longas (>1h) mas com variedade, e sorteando aleatoriamente. A escolha é
> reprodutível dentro do mesmo dia (a semente é a data). Para forçar outra seleção,
> use `--seed algumtexto`.

---

## Se der erro (problemas comuns)

- **"faltam no .env.local: ANTHROPIC_API_KEY"** → você ainda não colou a chave da
  Anthropic (passo 1.2).
- **"faltam no .env.local: GOOGLE_SHEET_ID"** → falta o ID da planilha (passo 1.3).
  Só aparece quando você tenta gravar de verdade (sem `--dry-run`).
- **"Biblioteca google-auth ausente"** → rode `pip install -r requirements.txt`
  (passo 1.1).
- **Erro da Sheets API com "permission"** → a planilha não foi compartilhada com o
  e-mail do robô, ou não foi como **Editor** (passo 1.3).
- **Erro 401 do Anthropic** → a chave está errada ou incompleta. Gere outra.
- **Nenhuma conversa encontrada** → tente sem filtro de estado, ou aumente a
  amostra: `--sample 10`.

---

## O que vem depois (quando você quiser)

Quando o teste manual estiver redondo, o próximo passo é **automatizar**: colocar
esse agente para rodar sozinho no GitHub Actions, num horário fixo, igual o monitor
do Flex faz hoje. Isso é um passo separado — só mexemos nele quando você disser que
as notas estão confiáveis.

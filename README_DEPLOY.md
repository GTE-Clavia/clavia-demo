# Clavia -- demonstracao publica (FastAPI) -- como rodar e publicar

App web com a POC real de correcao por foto (`../poc_correcao/`, versao de 22/10/2026). **Nada foi publicado**: este documento descreve o deploy para quando houver decisao de ir ao ar.

Rotas: `/` (institucional), `/demo` (folha -> foto -> correcao -> microdiagnostico -> turma -> Agir), `/evidencia` (bancada), `/folha.pdf`, `POST /api/corrigir` (multipart, campo `foto`; `?imagem=1` devolve a leitura desenhada em PNG/base64), `GET /api/saude`.

## Estrutura

```
demo_app/
  app/main.py            rotas, limites de upload (413/408/415) e de taxa (429), cabecalhos de seguranca (CSP etc.)
  app/isolamento.py      correcao em processo filho com kill duro no timeout (504) e rlimit de CPU/memoria
  app/servico.py         ponte com a POC: decodifica em memoria, corrige, desenha, diagnostica
  app/poc/               copia dos modulos da POC (ver "Origem do codigo")
  app/templates/         base, index, demo, evidencia (Jinja2)
  app/static/            css, js, fontes OFL (Bricolage, Lexend, JetBrains Mono), logo, mockups, figuras, fotos de exemplo
  tests/test_api.py      13 testes (3 fotos de exemplo, paginas, limites, upload nunca vai a disco)
  tests/test_seguranca.py  isolamento (504 + worker recriado), decodificacao, middlewares, cabecalhos
  Dockerfile  render.yaml  requirements.txt  requirements.lock (versoes exatas usadas no Docker)  requirements-dev.txt
  _qa/                   screenshots e logs da verificacao local (fora da imagem Docker)
```

## Rodar localmente

```
cd demo_app
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m uvicorn app.main:app --port 8765
```

Abrir http://127.0.0.1:8765. Com Docker: `docker build -t clavia-demo . && docker run --rm -p 10000:10000 clavia-demo`.

## Privacidade e seguranca (o que o codigo garante)

- **A foto nunca vai a disco.** O buffer do multipart (`MultiPartParser.spool_max_size`) cobre o limite inteiro, entao o arquivo nao transborda para arquivo temporario; a decodificacao e em memoria; a imagem de leitura e PNG em memoria. O teste `test_upload_nunca_vai_para_disco` falha se houver transbordo (conferido desligando a protecao).
- O nome do arquivo enviado e ignorado (pode conter nome de aluno). A foto nao e registrada nem gravada; o uvicorn roda com `--no-access-log` e erros internos registram so o tipo da excecao. A plataforma de hospedagem (Render) pode manter registros tecnicos de acesso (IP, rota, status); o aviso na tela diz isso.
- Rota de upload: so POST `multipart/form-data` (405/415 antes de ler o corpo), limite de 8 MB (413 antes do parsing, com ou sem Content-Length), envio em ate 15 s (408). Taxa: 10 correcoes/min por IP e 60/min no total (429 com `Retry-After`).
- Decodificacao: so JPEG e PNG (HEIC desligado), ate 20 MP, lado minimo 300 px e maior lado ate 5x o menor (checado no cabecalho, antes de decodificar), PNG ate 12 MP, JPEG grande decodificado em escala reduzida; fotos maiores que 4.000 px reduzidas antes da correcao.
- Isolamento: a correcao roda num processo filho persistente (`app/isolamento.py`). Passou de 20 s, o processo e morto (504) e um novo nasce na proxima chamada; em Linux o filho ainda tem `RLIMIT_CPU` e `RLIMIT_AS` (~700 MB). Uma correcao por vez (503 se ocupado). Erro inesperado: 500 com JSON `{"erro": ...}`, sem traceback.
- Cabecalhos: CSP, HSTS, `X-Frame-Options: DENY`, COOP/CORP `same-origin`, `nosniff`, `no-referrer`. `/api/saude` devolve so `{"status":"ok"}`. Uvicorn com `--limit-concurrency 32 --timeout-keep-alive 5`; container sem home e sem shell de login.
- Sem banco, cookies, analytics, fontes ou scripts de terceiros (CSP `default-src 'self'`). `noindex` nas paginas.
- O servidor da demo fica nos EUA (Render). O aviso LGPD na tela diz isso e pede para nao enviar dado pessoal. O produto, segundo a proposta, sera hospedado no Brasil; a demo nao e o produto.

## Origem do codigo (`app/poc/`)

Copias de `../poc_correcao/`: `layout.py` e `diagnosticar.py` identicos; duas alteracoes minimas marcadas com `[demo_app]`:

1. `corrigir.py`: `corrigir_imagem(..., devolver_debug=True)` devolve a imagem de depuracao em memoria em vez de grava-la.
2. `gerar_folha.py`: procura as fontes tambem em `app/static/fonts/` (no container Linux nao ha fontes do Windows).

Dados: `gabarito_P2026-MAT6-01.json` e `demo_turma_resultados.jsonl` (30 alunos sinteticos, saida do `corrigir.py --pasta`). OpenCV fixado em 4.13.0.92, a versao usada na bancada. Ao atualizar a POC, recopiar os 4 modulos e reaplicar as 2 alteracoes.

## Deploy no Render

### Opcao A -- repositorio GitHub + Blueprint (recomendada)

1. Criar um repositorio **privado** so com o conteudo de `demo_app/` (o `render.yaml` precisa ficar na raiz). Ex.: `gte-solucoes/clavia-demo`. Nao versionar `.venv/` (ja no `.gitignore`).
2. No Render: **New > Blueprint**, conectar o repositorio. O `render.yaml` cria o web service `clavia-demo` (Docker, plano Starter, regiao Virginia, health check `/api/saude`, dominio `clavia.gtesolucoes.com.br`).
3. `autoDeploy: false`: cada deploy e manual (**Manual Deploy > Deploy latest commit**). Mudar para `true` se quiser deploy a cada push.
4. Conferir `https://clavia-demo.onrender.com/api/saude` -> `{"status":"ok"}`.

Alternativa sem Blueprint: **New > Web Service > repositorio > Runtime: Docker**, plano Starter, Health Check Path `/api/saude`, e as variaveis abaixo.

### Opcao B -- imagem Docker pronta

```
docker build -t ghcr.io/<org>/clavia-demo:2026-10-22 .
docker push ghcr.io/<org>/clavia-demo:2026-10-22
```

No Render: **New > Web Service > Existing image**, informar a imagem (e a credencial do registry, se privado), plano Starter, Health Check Path `/api/saude`. Atualizar = push de nova tag + trocar a tag no servico.

### Variaveis de ambiente

| Variavel | Padrao | Para que serve |
|---|---|---|
| `PORT` | 10000 | porta do uvicorn (o Render injeta) |
| `CLAVIA_LIMITE_MB` | 8 | tamanho maximo da foto |
| `CLAVIA_TIMEOUT_S` | 20 | tempo maximo de correcao por imagem |
| `CLAVIA_CONCORRENCIA` | 1 | correcoes simultaneas (cada uma e um processo filho de ~150 MB) |
| `CLAVIA_MEMORIA_MB` | 700 | teto de memoria do processo de correcao (so Linux) |
| `CLAVIA_POR_MINUTO` / `CLAVIA_GLOBAL_POR_MINUTO` | 10 / 60 | correcoes por minuto por IP / no total |

Nenhum segredo e necessario.

### Custo

- **Starter: US$ 7/mes** por servico (512 MB de RAM, 0,5 CPU), sempre ligado. Valor de referencia; conferir em https://render.com/pricing antes de contratar.
- O plano **Free** dorme depois de cerca de 15 minutos sem acesso e leva perto de 1 minuto para acordar: ruim para avaliador que abre o link uma vez. Por isso o Starter.
- Uso medido localmente: correcao de 0,08 a 0,11 s por foto de exemplo e 0,3 a 0,5 s de resposta total (inclui abrir a imagem e gerar o PNG). Em 0,5 CPU deve ficar perto do dobro; o timeout de 20 s tem folga.

## Dominio `clavia.gtesolucoes.com.br` (DNS na Hostinger, dns-parking)

O dominio `gtesolucoes.com.br` usa os nameservers da Hostinger (`ns1.dns-parking.com` / `ns2.dns-parking.com`), entao a zona e editada no hPanel.

1. No Render, no servico: **Settings > Custom Domains > Add** `clavia.gtesolucoes.com.br` (o Blueprint ja declara). O Render mostra o alvo do CNAME: `clavia-demo.onrender.com`.
2. Na Hostinger: **hPanel > Dominios > gtesolucoes.com.br > DNS / Nameservers > Gerenciar registros DNS**. Apagar qualquer registro A, AAAA ou CNAME ja existente para `clavia`. Adicionar:

   | Tipo | Nome | Aponta para | TTL |
   |---|---|---|---|
   | CNAME | `clavia` | `clavia-demo.onrender.com` | 3600 |

3. Voltar ao Render e clicar **Verify**. Com o DNS propagado (minutos a algumas horas), o Render emite o certificado TLS sozinho. Testar `https://clavia.gtesolucoes.com.br/api/saude`.
4. Nao mexer no registro raiz (`@`) nem nos MX do dominio: o subdominio e independente do site e do e-mail da GTE.

## Antes de publicar (checklist)

- [ ] Ler as 3 paginas contra `../CLAVIA_Fase2_Campos_FINAL.md` (nada alem do texto: MVP = Matematica 6o/7o; Agir em desenvolvimento; sem correcao offline).
- [ ] Se o teste com fotos reais (`../poc_correcao/PROTOCOLO_TESTE_FOTOS_REAIS.md`) for executado, atualizar `/evidencia` com a tabela real.
- [ ] Trocar "Codigo-fonte: em breve" pelo link quando o repositorio for aberto.
- [ ] Atualizar a data de versao em `app/main.py` (`VERSAO`).

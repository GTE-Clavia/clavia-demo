"""Clavia -- demonstracao publica da correcao por foto (FastAPI).

Rotas: /  /demo  /evidencia  /folha.pdf  POST /api/corrigir  GET /api/saude
Privacidade: a foto enviada fica so em memoria durante a correcao e e descartada;
nao ha banco, cookies, analytics nem log do conteudo enviado.
"""
import asyncio
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.formparsers import MultiPartParser

from app import isolamento
from app import servico as S

log = logging.getLogger("clavia")

VERSAO = "22/10/2026 b"
LIMITE_MB = float(os.environ.get("CLAVIA_LIMITE_MB", "8"))
LIMITE_BYTES = int(LIMITE_MB * 1024 * 1024)
FOLGA_MULTIPART = 64 * 1024  # cabecalhos e delimitadores do multipart
PRAZO_ENVIO_S = 15.0  # upload mais lento que isso prende conexao e memoria: 408

# O upload nunca transborda para arquivo temporario: o buffer em memoria cobre o limite inteiro.
MultiPartParser.spool_max_size = LIMITE_BYTES + FOLGA_MULTIPART + 1

AQUI = Path(__file__).resolve().parent
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def _erro(status, mensagem, cabecalhos=None):
    return JSONResponse({"erro": mensagem}, status_code=status,
                        headers={"Cache-Control": "no-store", **(cabecalhos or {})})


class LimiteDeTaxa:
    """Limite por IP (N/min) e teto global (M/min) para a rota de correcao (memoria do processo)."""

    def __init__(self, app, caminhos, por_minuto=10, global_por_minuto=60):
        self.app, self.caminhos, self.max, self.max_global, self.janela = (
            app, caminhos, por_minuto, global_por_minuto, 60.0)
        self.hist = {}
        self.todos = deque()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] in self.caminhos and scope.get("method") == "POST":
            hdrs = dict(scope.get("headers") or [])
            ip = (hdrs.get(b"x-forwarded-for", b"").decode("latin-1").split(",")[0].strip()
                  or (scope.get("client") or ("?",))[0])
            agora = time.monotonic()
            while self.todos and agora - self.todos[0] > self.janela:
                self.todos.popleft()
            fila = self.hist.setdefault(ip, deque())
            while fila and agora - fila[0] > self.janela:
                fila.popleft()
            espera = {"Retry-After": str(int(self.janela))}
            if len(self.todos) >= self.max_global:
                return await _erro(429, "A demonstração recebeu muitas correções agora. Tente de novo em um minuto.",
                                   espera)(scope, receive, send)
            if len(fila) >= self.max:
                return await _erro(429, "Muitas correções em um minuto. Aguarde um pouco e tente de novo.",
                                   espera)(scope, receive, send)
            fila.append(agora)
            self.todos.append(agora)
            if len(self.hist) > 5000:  # evita crescimento sem fim
                self.hist = {k: v for k, v in self.hist.items() if v and agora - v[-1] < self.janela}
        return await self.app(scope, receive, send)


class LimiteDeCorpo:
    """Na rota de upload: so POST multipart; recusa (413) corpo acima do limite antes de qualquer parsing,
    com ou sem Content-Length; recusa (408) envio mais lento que o prazo."""

    def __init__(self, app, limite, caminhos, prazo_s=PRAZO_ENVIO_S):
        self.app, self.limite, self.caminhos, self.prazo_s = app, limite, caminhos, prazo_s

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in self.caminhos:
            return await self.app(scope, receive, send)
        if scope.get("method") != "POST":
            return await _erro(405, "Use POST.", {"Allow": "POST"})(scope, receive, send)
        hdrs = dict(scope["headers"])
        if not hdrs.get(b"content-type", b"").lower().startswith(b"multipart/form-data"):
            return await _erro(415, "Envie a foto como multipart/form-data, no campo foto.")(scope, receive, send)
        cl = hdrs.get(b"content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > self.limite):
            return await _erro(413, f"Arquivo acima de {LIMITE_MB:g} MB.")(scope, receive, send)

        partes = deque()  # os pedacos sao reentregues como chegaram: sem copia do corpo

        async def ler():
            total = 0
            while True:
                msg = await receive()
                if msg["type"] == "http.disconnect":
                    return "desconectou"
                pedaco = msg.get("body", b"")
                total += len(pedaco)
                if total > self.limite:
                    return "grande"
                if pedaco:
                    partes.append(pedaco)
                if not msg.get("more_body"):
                    return "ok"

        try:
            estado = await asyncio.wait_for(ler(), self.prazo_s)
        except asyncio.TimeoutError:
            partes.clear()
            return await _erro(408, "O envio da foto demorou demais. Tente de novo.",
                               {"Connection": "close"})(scope, receive, send)
        if estado == "desconectou":
            return None
        if estado == "grande":
            partes.clear()
            return await _erro(413, f"Arquivo acima de {LIMITE_MB:g} MB.")(scope, receive, send)

        async def reenviar():
            if partes:
                return {"type": "http.request", "body": partes.popleft(), "more_body": bool(partes)}
            return await receive()

        if not partes:  # corpo vazio: o app ainda precisa de uma mensagem http.request
            partes.append(b"")
        await self.app(scope, reenviar, send)


@asynccontextmanager
async def ciclo_de_vida(_app):
    S.folha_pdf()  # aquece os caches e falha cedo se faltar fonte/dado
    S.turma_demo()
    await asyncio.to_thread(isolamento.iniciar)  # processo de correcao pronto antes da primeira foto
    try:
        yield
    finally:
        isolamento.encerrar()


app = FastAPI(title="Clavia - demonstracao", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=ciclo_de_vida)
app.add_middleware(LimiteDeCorpo, limite=LIMITE_BYTES + FOLGA_MULTIPART, caminhos={"/api/corrigir"})
app.add_middleware(LimiteDeTaxa, caminhos={"/api/corrigir"},
                   por_minuto=int(os.environ.get("CLAVIA_POR_MINUTO", "10")),
                   global_por_minuto=int(os.environ.get("CLAVIA_GLOBAL_POR_MINUTO", "60")))
app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")
templates = Jinja2Templates(directory=AQUI / "templates")


@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    return resp


@app.exception_handler(RequestValidationError)
async def requisicao_invalida(_request: Request, _exc: RequestValidationError):
    return _erro(422, "Requisição inválida: envie a foto no campo foto (multipart/form-data).")


def _pagina(request, nome, ativo, **ctx):
    return templates.TemplateResponse(request, nome, {"ativo": ativo, "versao": VERSAO, "limite_mb": LIMITE_MB,
                                                      "heic": S.HEIC, **ctx})


@app.get("/")
async def inicio(request: Request):
    return _pagina(request, "index.html", "inicio", turma=S.turma_demo())


@app.get("/demo")
async def demo(request: Request):
    return _pagina(request, "demo.html", "demo", turma=S.turma_demo(), gabarito=S.GABARITO)


@app.get("/evidencia")
async def evidencia(request: Request):
    return _pagina(request, "evidencia.html", "evidencia")


@app.get("/folha.pdf")
async def folha():
    return Response(S.folha_pdf(), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="clavia_folha_P2026-MAT6-01.pdf"'})


@app.post("/api/corrigir")
async def api_corrigir(foto: UploadFile = File(...), imagem: bool = False):
    """multipart/form-data com o campo `foto`. `?imagem=1` inclui a leitura desenhada (PNG em data URL)."""
    dados = await foto.read()
    await foto.close()
    if not dados:
        return _erro(400, "Arquivo vazio.")
    if len(dados) > LIMITE_BYTES:
        return _erro(413, f"Arquivo acima de {LIMITE_MB:g} MB.")
    try:
        saida = await asyncio.to_thread(isolamento.executar, dados, imagem)
    except S.ImagemInvalida as e:
        return _erro(e.status, str(e))
    except isolamento.Ocupado:
        return _erro(503, "Servidor ocupado com outras correções. Tente de novo em alguns segundos.")
    except isolamento.TempoEsgotado:
        return _erro(504, f"A correção passou de {isolamento.TIMEOUT_S:g} s e foi interrompida. Tente outra foto.")
    except Exception as e:  # noqa: BLE001  (sem traceback: so o tipo vai para o log)
        log.error("falha na correcao: %s", type(e).__name__)
        return _erro(500, "Erro interno ao corrigir a foto. Tente de novo com outra foto.")
    finally:
        del dados
    return JSONResponse(saida, headers={"Cache-Control": "no-store"})


@app.get("/api/saude")
async def saude():
    return {"status": "ok"}

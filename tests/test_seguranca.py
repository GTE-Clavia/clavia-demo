"""Testes das correcoes de seguranca: isolamento com kill duro, decodificacao, middlewares e cabecalhos."""
import asyncio
import itertools
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import isolamento
from app.main import LimiteDeCorpo, LimiteDeTaxa, app

EXEMPLO = (Path(__file__).resolve().parents[1] / "app" / "static" / "exemplos" / "exemplo_leve_foto.jpg").read_bytes()
_ips = itertools.count(1)


@pytest.fixture(scope="module")
def cliente():
    with TestClient(app) as c:
        yield c


def _enviar(cliente, conteudo, nome="foto.jpg", tipo="image/jpeg"):
    """Cada envio com IP proprio: o limite de 10/min por IP nao interfere nos testes."""
    n = next(_ips)
    ip = f"10.0.{n // 250}.{n % 250}"
    return cliente.post("/api/corrigir", files={"foto": (nome, conteudo, tipo)}, headers={"X-Forwarded-For": ip})


def _cpu_alta(dados, com_imagem):
    """Simula a imagem com milhares de padroes de QR: 5 s de CPU sem devolver o controle."""
    fim = time.perf_counter() + 5
    while time.perf_counter() < fim:
        pass
    return {}


# ---------------------------------------------------------------- B2: isolamento

def test_timeout_mata_o_worker_e_a_proxima_correcao_funciona(cliente, monkeypatch):
    monkeypatch.setattr(isolamento, "TIMEOUT_S", 1.0)
    monkeypatch.setattr(isolamento, "ALVO", f"{__name__}:_cpu_alta")
    isolamento.encerrar()
    isolamento.iniciar()  # worker lento ja aquecido: a medicao abaixo e so do pedido
    preso = isolamento._livres[0].p

    t0 = time.perf_counter()
    r = _enviar(cliente, EXEMPLO)
    dt = time.perf_counter() - t0
    assert r.status_code == 504 and "erro" in r.json()
    assert dt < 2.5, f"504 demorou {dt:.1f} s"
    assert not preso.is_alive()  # kill duro: o processo preso nao sobrevive

    monkeypatch.undo()  # volta a correcao real e o timeout normal
    r = _enviar(cliente, EXEMPLO)
    assert r.status_code == 200 and r.json()["aluno"] == "EX0"
    assert isolamento._livres[0].p.pid != preso.pid  # worker recriado


def test_sem_vaga_devolve_503(cliente, monkeypatch):
    monkeypatch.setattr(isolamento, "_vagas", isolamento.threading.BoundedSemaphore(1))
    isolamento._vagas.acquire()
    try:
        r = _enviar(cliente, EXEMPLO)
    finally:
        isolamento._vagas.release()
    assert r.status_code == 503 and "erro" in r.json()


# ---------------------------------------------------------------- B1: decodificacao

def test_png_1x10000_e_422(cliente):
    ok, buf = cv2.imencode(".png", np.full((10000, 1, 3), 255, np.uint8))
    r = _enviar(cliente, buf.tobytes(), nome="x.png", tipo="image/png")
    assert r.status_code == 422 and "erro" in r.json()


def test_formato_fora_da_lista_e_recusado(cliente):
    ok, buf = cv2.imencode(".bmp", np.full((800, 600, 3), 200, np.uint8))
    r = _enviar(cliente, buf.tobytes(), nome="x.bmp", tipo="image/bmp")
    assert r.status_code == 422


# ---------------------------------------------------------------- A1: corpo e taxa

def test_get_e_corpo_nao_multipart_recusados_sem_ler(cliente):
    assert cliente.get("/api/corrigir").status_code == 405
    r = cliente.post("/api/corrigir", content=EXEMPLO, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 415 and "erro" in r.json()


def test_envio_lento_recebe_408():
    async def app_nunca_chamado(scope, receive, send):
        raise AssertionError("o app nao deveria receber o pedido")

    mw = LimiteDeCorpo(app_nunca_chamado, limite=1000, caminhos={"/x"}, prazo_s=0.2)
    escopo = {"type": "http", "path": "/x", "method": "POST",
              "headers": [(b"content-type", b"multipart/form-data; boundary=b")]}
    enviados = []

    async def receive():
        await asyncio.sleep(10)  # cliente que nao manda o corpo

    async def send(msg):
        enviados.append(msg)

    asyncio.run(mw(escopo, receive, send))
    assert enviados[0]["status"] == 408


def test_teto_global_com_retry_after():
    mini = FastAPI()

    @mini.post("/x")
    async def x():
        return {"ok": True}

    mini.add_middleware(LimiteDeTaxa, caminhos={"/x"}, por_minuto=100, global_por_minuto=3)
    with TestClient(mini) as c:
        codigos = [c.post("/x", headers={"X-Forwarded-For": f"1.1.1.{i}"}) for i in range(4)]
    assert [r.status_code for r in codigos] == [200, 200, 200, 429]
    assert codigos[-1].headers["retry-after"] == "60" and "erro" in codigos[-1].json()


# ---------------------------------------------------------------- M2, M4

def test_erro_de_validacao_vem_como_erro(cliente):
    r = cliente.post("/api/corrigir", files={"outro": ("x.jpg", b"123", "image/jpeg")},
                     headers={"X-Forwarded-For": "10.9.9.9"})
    assert r.status_code == 422 and set(r.json()) == {"erro"}


def test_erro_interno_vira_500_json_sem_detalhe(cliente, monkeypatch):
    def quebra(dados, com_imagem):
        raise RuntimeError("detalhe interno que nao pode vazar")
    monkeypatch.setattr(isolamento, "executar", quebra)
    r = _enviar(cliente, EXEMPLO)
    assert r.status_code == 500 and set(r.json()) == {"erro"}
    assert "detalhe interno" not in r.text


def test_cabecalhos_e_saude_enxuta(cliente):
    r = cliente.get("/api/saude")
    assert r.json() == {"status": "ok"}
    h = r.headers
    assert h["strict-transport-security"] == "max-age=31536000"
    assert h["x-frame-options"] == "DENY"
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert h["cross-origin-resource-policy"] == "same-origin"

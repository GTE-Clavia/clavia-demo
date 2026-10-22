"""Testes da demo: API de correcao com as 3 fotos de exemplo, paginas, limites e privacidade."""
import tempfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import LIMITE_BYTES, app

EXEMPLOS = Path(__file__).resolve().parents[1] / "app" / "static" / "exemplos"


@pytest.fixture(scope="module")
def cliente():
    with TestClient(app) as c:
        yield c


def _enviar(cliente, conteudo, nome="foto.jpg", tipo="image/jpeg", query=""):
    return cliente.post("/api/corrigir" + query, files={"foto": (nome, conteudo, tipo)})


@pytest.mark.parametrize("nivel,aluno", [("leve", "EX0"), ("medio", "EX1"), ("severo", "EX2")])
def test_corrige_fotos_de_exemplo(cliente, nivel, aluno):
    r = _enviar(cliente, (EXEMPLOS / f"exemplo_{nivel}_foto.jpg").read_bytes(), query="?imagem=1")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] in ("ok", "revisar")
    assert j["prova"] == "P2026-MAT6-01" and j["prova_reconhecida"] is True
    assert j["aluno"] == aluno and j["id_fonte"] == "qr"
    assert len(j["questoes"]) == 20
    for q in j["questoes"]:
        assert {"q", "resposta", "confianca", "flags", "gabarito", "habilidade", "sinalizada", "pendente"} <= q.keys()
        assert 0.0 <= q["confianca"] <= 1.0
    assert j["sinalizadas"] == [q["q"] for q in j["questoes"] if q["sinalizada"]]
    d = j["diagnostico"]
    assert set(d["habilidades"]) == {"EF06MA01", "EF06MA07", "EF06MA13", "EF06MA24"}
    assert 0 <= d["nota_0a10"] <= 10
    assert all(h["nivel"] in ("consolidada", "em desenvolvimento", "lacuna", "insuficiente") for h in d["habilidades"].values())
    assert j["tempo_ms"] > 0 and j["tempo_total_ms"] >= j["tempo_ms"]
    assert j["imagem_leitura"].startswith("data:image/png;base64,")


def test_sem_imagem_por_padrao(cliente):
    j = _enviar(cliente, (EXEMPLOS / "exemplo_leve_foto.jpg").read_bytes()).json()
    assert "imagem_leitura" not in j


def test_saude(cliente):
    r = cliente.get("/api/saude")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("rota", ["/", "/demo", "/evidencia"])
def test_paginas(cliente, rota):
    r = cliente.get(rota)
    assert r.status_code == 200
    assert "Centelha 3 RJ" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_folha_pdf(cliente):
    r = cliente.get("/folha.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_arquivo_grande_recusado(cliente):
    r = _enviar(cliente, b"0" * (LIMITE_BYTES + 1))
    assert r.status_code == 413


def test_arquivo_que_nao_e_imagem(cliente):
    r = _enviar(cliente, b"isto nao e uma imagem", nome="x.txt", tipo="text/plain")
    assert r.status_code == 422


def test_foto_sem_folha_vira_falha(cliente):
    import cv2
    ok, buf = cv2.imencode(".png", np.full((800, 600, 3), 200, np.uint8))
    j = _enviar(cliente, buf.tobytes(), nome="x.png", tipo="image/png").json()
    assert j["status"] == "falha" and j["diagnostico"] is None


def test_upload_nunca_vai_para_disco(cliente, monkeypatch):
    """Um arquivo de ~3 MB (acima do buffer padrao de 1 MB) nao pode transbordar para arquivo temporario."""
    def proibido(self):
        raise AssertionError("upload transbordou para o disco")
    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", proibido)
    ruido = np.random.default_rng(0).integers(0, 255, (1000, 1000, 3), dtype=np.uint8)
    import cv2
    ok, buf = cv2.imencode(".png", ruido)
    assert len(buf) > 2 * 1024 * 1024
    r = _enviar(cliente, buf.tobytes(), nome="x.png", tipo="image/png")
    assert r.status_code == 200 and r.json()["status"] == "falha"

"""Ponte entre a API e os modulos da POC.

Decodifica a foto em memoria, chama o corrigir.py, desenha a leitura (PNG em
memoria) e calcula o microdiagnostico com o diagnosticar.py. Nada e gravado em
disco e nada da foto e registrado em log.
"""
import base64
import json
import time
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app import poc  # noqa: F401  (poe app/poc no sys.path)
import corrigir as C  # noqa: E402
import diagnosticar as D  # noqa: E402
import gerar_folha as G  # noqa: E402

HEIC = False  # HEIC desligado de proposito: libheif tem historico de CVEs e o iOS converte para JPEG no envio pelo navegador

DADOS = Path(__file__).resolve().parent / "poc" / "dados"
GABARITO = json.loads((DADOS / "gabarito_P2026-MAT6-01.json").read_text(encoding="utf-8"))
PROVA = GABARITO["prova"]
ALUNO_FOLHA = "A001"  # aluno de exemplo da folha para download

MAX_PIXELS = 20_000_000  # protege contra "bomba de descompressao" (celulares comuns: 12 MP)
MIN_LADO_PX = 300  # folha fotografada inteira nunca e menor que isso
MAX_PROPORCAO = 5  # maior lado <= 5x o menor (A4 e ~1,41); barra 1x10000 e recusada antes do decode
MAX_LADO_PX = 4000  # fotos maiores sao reduzidas antes da correcao
LARGURA_IMAGEM_LEITURA = 1000
FORMATOS = {"JPEG", "PNG"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

MOTIVOS_QUESTAO = {
    "em_branco": "em branco (conta como erro)",
    "dupla_marcacao": "duas bolhas marcadas",
    "marcacao_incerta": "marca fraca, na zona de dúvida",
    "ambigua": "duas bolhas com marca parecida",
    "segunda_bolha_suja": "resíduo em outra bolha (rasura?)",
}
MOTIVOS_FOLHA = {
    "fiduciais_insuficientes": "Não encontramos os quadrados pretos dos cantos (são necessários pelo menos 3 dos 4). "
                               "Fotografe a folha inteira, com os 4 cantos visíveis.",
    "fiducial_ausente": "Um dos 4 quadrados dos cantos não apareceu: a folha inteira vai para a conferência do professor.",
    "alinhamento_suspeito": "Papel muito deformado na foto: a folha inteira vai para a conferência do professor.",
    "id_divergente": "O QR Code e o código em bolhas indicam alunos diferentes: conferir.",
    "id_ilegivel": "Não foi possível ler o QR Code nem o código do aluno em bolhas.",
}


class ImagemInvalida(ValueError):
    def __init__(self, mensagem, status=422):
        super().__init__(mensagem)
        self.status = status


def decodificar(dados: bytes) -> np.ndarray:
    """Bytes da foto -> imagem BGR, sem tocar o disco."""
    try:
        with Image.open(BytesIO(dados), formats=sorted(FORMATOS)) as im:  # le so o cabecalho
            formato, (w, h) = im.format, im.size
        if formato == "MPO":  # JPEG de celular com imagens extras (MPF): o OpenCV le a principal
            formato = "JPEG"
        if formato not in FORMATOS:
            raise ImagemInvalida("Formato não aceito. Envie JPG ou PNG.", status=415)
        if min(w, h) < MIN_LADO_PX or max(w, h) > MAX_PROPORCAO * min(w, h):
            raise ImagemInvalida("Foto com tamanho ou proporção fora do esperado: fotografe a folha inteira.")
        if w * h > MAX_PIXELS:
            raise ImagemInvalida("Imagem com resolução acima do limite da demonstração.")
        if formato == "PNG" and w * h > 12_000_000:
            raise ImagemInvalida("PNG acima de 12 MP: envie em JPG.")
        # JPEG grande e decodificado em escala reduzida (DCT) para limitar o pico de memoria.
        flag = cv2.IMREAD_COLOR
        if formato == "JPEG" and w * h > 8_000_000:
            flag = cv2.IMREAD_REDUCED_COLOR_2
        if formato == "JPEG" and w * h > 16_000_000:
            flag = cv2.IMREAD_REDUCED_COLOR_4
        img = cv2.imdecode(np.frombuffer(dados, dtype=np.uint8), flag)
    except ImagemInvalida:  # e subclasse de ValueError: nao pode cair no except generico abaixo
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError,
            ValueError, MemoryError, cv2.error) as e:
        raise ImagemInvalida("Não foi possível abrir a imagem. Envie JPG ou PNG.") from e
    if img is None or img.size == 0:
        raise ImagemInvalida("Não foi possível abrir a imagem.")
    lado = max(img.shape[:2])
    if lado > MAX_LADO_PX:
        f = MAX_LADO_PX / lado
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return img


def _png_data_url(img_bgr: np.ndarray) -> str:
    h, w = img_bgr.shape[:2]
    if w > LARGURA_IMAGEM_LEITURA:
        f = LARGURA_IMAGEM_LEITURA / w
        img_bgr = cv2.resize(img_bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", img_bgr, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


def _motivo_folha(flag: str) -> str:
    for prefixo, texto in MOTIVOS_FOLHA.items():
        if flag.startswith(prefixo):
            return texto
    return flag


def montar_resposta(res: dict) -> dict:
    """Resultado do corrigir.py + gabarito + microdiagnostico do diagnosticar.py."""
    gab = {it["q"]: it for it in GABARITO["questoes"]}
    folha_inteira = res["status"] == "falha" or any(f.startswith(D.FOLHA_INTEIRA) for f in res["flags_folha"])
    # QR ilegivel (id lido pelas bolhas): a prova e a do lote em correcao, como na POC
    prova_reconhecida = res.get("prova") in (None, PROVA)
    questoes = []
    for item in res["questoes"]:
        g = gab[item["q"]]
        pendente = folha_inteira or any(f != "em_branco" for f in item["flags"])
        questoes.append({**item, "gabarito": g["gabarito"], "habilidade": g["habilidade"],
                         "sinalizada": folha_inteira or bool(item["flags"]), "pendente": pendente,
                         "acertou": None if pendente else item["resposta"] == g["gabarito"],
                         "motivos": [MOTIVOS_QUESTAO.get(f, f) for f in item["flags"]]})

    diagnostico = None
    if res["status"] != "falha" and prova_reconhecida:
        diag = D.diagnosticar(GABARITO, D.respostas_de_resultados([res], {}))
        aluno = next(iter(diag["alunos"].values()))
        diagnostico = {
            "nota_0a10": aluno["nota_0a10"],
            "lacunas": aluno["lacunas"],
            "pendentes_revisao": aluno["pendentes_revisao"],
            "habilidades": {c: {**h, "rotulo": GABARITO["habilidades"][c]["rotulo_curto"]}
                            for c, h in aluno["habilidades"].items()},
        }
    return {
        "status": res["status"],
        "prova": res.get("prova") or (PROVA if res.get("aluno") else None),
        "prova_reconhecida": prova_reconhecida,
        "aluno": res.get("aluno"),
        "aluno_codigo": res.get("aluno_codigo"),
        "id_fonte": res.get("id_fonte"),
        "flags_folha": res["flags_folha"],
        "motivos_folha": [_motivo_folha(f) for f in res["flags_folha"]],
        "questoes": questoes,
        "sinalizadas": [q["q"] for q in questoes if q["sinalizada"]],
        "diagnostico": diagnostico,
        "tempo_ms": res.get("tempo_ms"),
    }


def processar(dados: bytes, com_imagem: bool = False) -> dict:
    """Roda no processo filho de app/isolamento.py (vaga e timeout ficam no pai)."""
    t0 = time.perf_counter()
    img = decodificar(dados)
    res, debug = C.corrigir_imagem(img, "upload", devolver_debug=True)
    del img
    saida = montar_resposta(res)
    if com_imagem and debug is not None:
        saida["imagem_leitura"] = _png_data_url(debug)
    saida["tempo_total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return saida


@lru_cache(maxsize=1)
def folha_pdf() -> bytes:
    """Folha de respostas da prova P2026-MAT6-01 para 1 aluno de exemplo (gerar_folha.py)."""
    buf = BytesIO()
    G.render_folha(PROVA, ALUNO_FOLHA).save(buf, format="PDF", resolution=G.DPI)
    return buf.getvalue()


@lru_cache(maxsize=1)
def turma_demo() -> dict:
    """Microdiagnostico da turma-demo (30 alunos sinteticos) a partir do JSONL da POC."""
    linhas = (DADOS / "demo_turma_resultados.jsonl").read_text(encoding="utf-8").splitlines()
    resultados = [json.loads(x) for x in linhas if x.strip()]
    diag = D.diagnosticar(GABARITO, D.respostas_de_resultados(resultados, {}))
    codigos = list(GABARITO["habilidades"])
    grupos = sorted(diag["grupos_por_lacuna"].items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return {
        "codigos": codigos,
        "rotulos": {c: GABARITO["habilidades"][c]["rotulo_curto"] for c in codigos},
        "alunos": diag["alunos"],
        "turma": diag["turma"],
        "grupos": [{"perfil": g, "n": len(a)} for g, a in grupos],
        "n_alunos": len(diag["alunos"]),
        "pendentes": sum(a["pendentes_revisao"] for a in diag["alunos"].values()),
    }

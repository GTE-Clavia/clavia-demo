"""Corrige uma folha de respostas Clavia a partir de uma foto.

Etapas: fiduciais ArUco -> homografia (retifica) -> ancoras por linha (compensa
papel amassado) -> QR (prova|aluno), com codigo do aluno em bolhas como
redundancia -> preenchimento de cada bolha normalizado pela iluminacao local ->
decisao por questao, sinalizando para revisao humana o que for duvidoso.

Uso:
  python corrigir.py foto.jpg [--debug retificada.png]
  python corrigir.py --pasta fotos/ --saida resultados.jsonl [--debug-dir debug/]

Saida (uma folha):
  {"arquivo", "prova", "aluno", "aluno_codigo", "id_fonte": "qr"|"bolhas"|null,
   "status": "ok"|"revisar"|"falha", "flags_folha": [...],
   "questoes": [{"q", "resposta", "confianca", "flags", "preenchimento"}], "tempo_ms"}
  resposta: letra, "BRANCO" ou "MULT". confianca: escore heuristico 0-1 (nao e probabilidade calibrada).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import layout as L

DPI_RET = 150  # resolucao da imagem retificada
W_RET = int(round(L.mm2px(L.PAGINA_MM[0], DPI_RET)))
H_RET = int(round(L.mm2px(L.PAGINA_MM[1], DPI_RET)))

# Limiares sobre o preenchimento relativo r = escuridao_media_da_bolha - linha_de_base_da_folha.
# Fixados antes da primeira rodada e conferidos com a semente de DESENVOLVIMENTO (7);
# a semente de TESTE (2026) nunca foi usada para ajuste.
T_MARCADA = 0.20  # acima: bolha marcada
T_VAZIA = 0.09    # abaixo: vazia; entre os dois: zona de duvida -> revisao humana
RAZAO_SEGUNDA = 0.55  # segunda bolha >= 55% da primeira (e fora da zona vazia) -> ambigua

FLAGS_FOLHA_INTEIRA = ("fiduciais", "alinhamento", "fiducial_ausente")  # todas as questoes vao a revisao

_DIC = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
_PARAMS = cv2.aruco.DetectorParameters()
_PARAMS.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
_DETECTOR = cv2.aruco.ArucoDetector(_DIC, _PARAMS)


def _p(mm):
    return L.mm2px(mm, DPI_RET)


# ---------------------------------------------------------------- fiduciais
def _detectar_aruco(gray):
    """Devolve {id: cantos(4x2)} em coordenadas da imagem original."""
    achados = {}
    escala = 1.0
    img = gray
    if max(gray.shape) > 2600:  # fotos grandes: detectar em versao reduzida (mais rapido)
        escala = 2600 / max(gray.shape)
        img = cv2.resize(gray, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    for t in (img, cv2.createCLAHE(2.0, (8, 8)).apply(img), cv2.GaussianBlur(img, (5, 5), 0)):
        cantos, ids, _ = _DETECTOR.detectMarkers(t)
        if ids is not None:
            for c, i in zip(cantos, ids.flatten()):
                if int(i) in L.ARUCO_IDS and int(i) not in achados:
                    achados[int(i)] = c.reshape(4, 2) / escala
        if len(achados) == 4:
            break
    return achados


def _homografia(achados):
    src, dst = [], []
    for mid, cantos in achados.items():
        src.extend(cantos)
        dst.extend([(_p(x), _p(y)) for x, y in L.aruco_cantos_mm(mid)])
    H, _ = cv2.findHomography(np.float32(src), np.float32(dst), 0)
    return H


# ---------------------------------------------------------------- iluminacao
def _escuridao(gray_ret):
    """1 - (imagem / fundo do papel). Fundo estimado por fechamento morfologico em
    baixa resolucao (apaga marcas e texto, preserva gradiente de luz e sombra)."""
    peq = cv2.resize(gray_ret, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
    fundo = cv2.morphologyEx(peq, cv2.MORPH_CLOSE, k)
    fundo = cv2.GaussianBlur(fundo, (0, 0), 3)
    fundo = cv2.resize(fundo, (gray_ret.shape[1], gray_ret.shape[0]), interpolation=cv2.INTER_LINEAR)
    esc = 1.0 - gray_ret.astype(np.float32) / np.maximum(fundo.astype(np.float32), 1.0)
    return np.clip(esc, 0, 1)


# ---------------------------------------------------------------- ancoras
def _achar_ancora(esc, cx, cy):
    """Centroide do quadradinho preto mais proximo de (cx, cy); None se nao achar."""
    raio = int(_p(4.5))
    x0, y0 = int(cx) - raio, int(cy) - raio
    jan = esc[max(y0, 0):y0 + 2 * raio, max(x0, 0):x0 + 2 * raio]
    if jan.size == 0:
        return None
    n, _, stats, cents = cv2.connectedComponentsWithStats((jan > 0.35).astype(np.uint8))
    area_esp = _p(L.ANCORA_LADO_MM) ** 2
    melhor, melhor_custo = None, 1e9
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if not 0.35 * area_esp < area < 2.5 * area_esp:
            continue
        px, py = cents[i][0] + max(x0, 0), cents[i][1] + max(y0, 0)
        custo = np.hypot(px - cx, py - cy) / raio + abs(np.log(area / area_esp))
        if custo < melhor_custo:
            melhor, melhor_custo = (px, py), custo
    return melhor


def _centros_linha(esc, linha):
    """Centros (px) das bolhas de uma linha, interpolando entre as 2 ancoras achadas.
    Devolve (centros, n_ancoras_faltando, maior_desvio_px)."""
    (exm, eym), (dxm, dym), bolhas_mm = linha
    esp_e, esp_d = (_p(exm), _p(eym)), (_p(dxm), _p(dym))
    e, d = _achar_ancora(esc, *esp_e), _achar_ancora(esc, *esp_d)
    faltando = (e is None) + (d is None)
    if e is None and d is None:
        e, d = esp_e, esp_d
    elif e is None:  # usa o deslocamento da outra ancora
        e = (esp_e[0] + d[0] - esp_d[0], esp_e[1] + d[1] - esp_d[1])
    elif d is None:
        d = (esp_d[0] + e[0] - esp_e[0], esp_d[1] + e[1] - esp_e[1])
    desvio = max(np.hypot(e[0] - esp_e[0], e[1] - esp_e[1]), np.hypot(d[0] - esp_d[0], d[1] - esp_d[1]))
    centros = []
    for bx, _ in bolhas_mm:
        t = (bx - exm) / (dxm - exm)
        centros.append((e[0] + t * (d[0] - e[0]), e[1] + t * (d[1] - e[1])))
    return centros, faltando, desvio


# ---------------------------------------------------------------- bolhas
_R_INT = _p(L.BOLHA_DIAM_MM / 2) * 0.70  # disco interno: exclui o contorno impresso


def _preenchimento(esc, cx, cy):
    """Escuridao media no disco interno, com busca local de +-2 px (desalinhamento residual)."""
    R = int(np.ceil(_R_INT)) + 3
    x0, y0 = int(round(cx)) - R, int(round(cy)) - R
    jan = esc[y0:y0 + 2 * R + 1, x0:x0 + 2 * R + 1]
    if jan.shape != (2 * R + 1, 2 * R + 1):
        return 0.0
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    fx, fy = cx - round(cx), cy - round(cy)
    melhor = 0.0
    for dy in (-2, 0, 2):
        for dx in (-2, 0, 2):
            m = (xx - fx - dx) ** 2 + (yy - fy - dy) ** 2 <= _R_INT ** 2
            melhor = max(melhor, float(jan[m].mean()))
    return melhor


def _decidir(r):
    """r: preenchimento relativo das alternativas. Devolve (resposta, confianca, flags)."""
    ordem = np.argsort(r)[::-1]
    r1, r2 = float(r[ordem[0]]), float(r[ordem[1]])
    marcadas = [i for i in range(len(r)) if r[i] > T_MARCADA]
    duvida = [i for i in range(len(r)) if T_VAZIA < r[i] <= T_MARCADA]
    letra = L.ALTERNATIVAS[ordem[0]]

    if len(marcadas) >= 2:
        return "MULT", 0.0, ["dupla_marcacao"]
    if not marcadas and not duvida:
        return "BRANCO", round(0.5 + 0.5 * float(np.clip((T_VAZIA - r1) / T_VAZIA, 0, 1)), 3), ["em_branco"]
    if not marcadas:  # so ha bolhas na zona de duvida
        return letra, 0.25, ["marcacao_incerta"]
    # exatamente uma marcada: confianca cresce com a folga acima do limiar e abaixo da zona vazia
    folga = min((r1 - T_MARCADA) / T_MARCADA, (T_VAZIA - r2) / T_VAZIA)
    conf = round(0.5 + 0.5 * float(np.clip(folga, 0, 1)), 3)
    if duvida and r2 >= RAZAO_SEGUNDA * r1:
        return letra, 0.3, ["ambigua"]
    if duvida:  # ex.: residuo de rasura em outra bolha
        return letra, min(conf, 0.45), ["segunda_bolha_suja"]
    return letra, conf, []


# ---------------------------------------------------------------- identificacao
_QR = cv2.QRCodeDetector()
_QR_ARUCO = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else None


def _ler_qr(gray_ret, gray_orig, H):
    x, y = L.QR_POS_MM
    m = 6.0
    x0, y0 = int(_p(x - m)), int(_p(y - m))
    x1, y1 = int(_p(x + L.QR_LADO_MM + m)), int(_p(y + L.QR_LADO_MM + m))
    # recorte a 300 dpi direto da foto original (evita reamostrar duas vezes)
    S = np.diag([2.0, 2.0, 1.0])
    T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=np.float64)
    alto = cv2.warpPerspective(gray_orig, S @ T @ H, (2 * (x1 - x0), 2 * (y1 - y0)), flags=cv2.INTER_CUBIC)
    recorte = gray_ret[y0:y1, x0:x1]

    def candidatos():  # do mais barato ao mais caro; para no primeiro que decodifica
        yield recorte, "plano"
        yield alto, "plano"
        for base in (alto, cv2.resize(recorte, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)):
            nitida = cv2.addWeighted(base, 3.0, cv2.GaussianBlur(base, (0, 0), 3), -2.0, 0)
            yield nitida, "plano"
            yield cv2.threshold(nitida, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1], "plano"
            yield cv2.adaptiveThreshold(base, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 61, 5), "plano"
        yield alto, "curvo"  # papel curvado/amassado

    for img, modo in candidatos():
        dets = (_QR, _QR_ARUCO) if modo == "plano" else (_QR,)
        for det in dets:
            if det is None:
                continue
            try:
                txt = (det.detectAndDecode(img) if modo == "plano" else det.detectAndDecodeCurved(img))[0]
            except cv2.error:
                continue
            if txt and "|" in txt:
                return txt
    return None


def _ler_codigo(esc, base):
    """Codigo do aluno nas bolhas pre-preenchidas: 1 digito por linha, exige margem clara."""
    digitos, centros_id = [], []
    for k in range(L.ID_DIGITOS):
        centros, _, _ = _centros_linha(esc, L.linha_id(k))
        centros_id.append(centros)
        r = np.array([_preenchimento(esc, *c) for c in centros]) - base
        ordem = np.argsort(r)[::-1]
        if r[ordem[0]] > T_MARCADA and r[ordem[1]] < T_VAZIA:
            digitos.append(str(ordem[0]))
        else:
            return None, centros_id
    return "".join(digitos), centros_id


# ---------------------------------------------------------------- pipeline
def corrigir_imagem(img, nome="", debug_path=None, devolver_debug=False):
    # [demo_app] devolver_debug=True -> devolve (res, imagem_debug_BGR | None), sem tocar o disco.
    t0 = time.perf_counter()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    res = {"arquivo": nome, "prova": None, "aluno": None, "aluno_codigo": None, "id_fonte": None,
           "status": "ok", "flags_folha": [], "questoes": []}

    achados = _detectar_aruco(gray)
    if len(achados) < 3:
        res["status"] = "falha"
        res["flags_folha"].append(f"fiduciais_insuficientes({len(achados)}/4)")
        res["tempo_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return (res, None) if devolver_debug else res
    if len(achados) == 3:
        res["flags_folha"].append("fiducial_ausente(3/4)")
    H = _homografia(achados)
    interp = cv2.INTER_AREA if max(gray.shape) > 2 * H_RET else cv2.INTER_LINEAR
    ret = cv2.warpPerspective(gray, H, (W_RET, H_RET), flags=interp)
    esc = _escuridao(ret)

    # respostas
    centros, faltando, desvio_max = {}, 0, 0.0
    for q in range(1, L.N_QUESTOES + 1):
        c, f, dv = _centros_linha(esc, L.linha_questao(q))
        centros[q] = c
        faltando, desvio_max = faltando + f, max(desvio_max, dv)
    if faltando > 4 or desvio_max > _p(4.0):
        res["flags_folha"].append(f"alinhamento_suspeito(ancoras_faltando={faltando})")
    brutos = np.array([[_preenchimento(esc, *c) for c in centros[q]] for q in range(1, L.N_QUESTOES + 1)])
    base = float(np.percentile(brutos, 40))  # a maioria das bolhas esta vazia
    rel = brutos - base
    for q in range(1, L.N_QUESTOES + 1):
        resp, conf, flags = _decidir(rel[q - 1])
        res["questoes"].append({"q": q, "resposta": resp, "confianca": conf, "flags": flags,
                                "preenchimento": [round(float(v), 3) for v in rel[q - 1]]})

    # identificacao: QR e fonte primaria; codigo em bolhas e redundancia
    codigo, centros_id = _ler_codigo(esc, base)
    qr = _ler_qr(ret, gray, H)
    if qr:
        res["prova"], res["aluno"] = qr.split("|", 1)
        res["aluno_codigo"], res["id_fonte"] = L.codigo_aluno(res["aluno"]), "qr"
        if codigo and codigo != res["aluno_codigo"]:
            res["flags_folha"].append("id_divergente(qr!=bolhas)")
    elif codigo:
        res["aluno_codigo"], res["id_fonte"] = codigo, "bolhas"
        res["aluno"] = f"#{codigo}"  # prova = a do lote em processamento
    else:
        res["flags_folha"].append("id_ilegivel(qr_e_bolhas)")

    if res["flags_folha"] or any(x["flags"] for x in res["questoes"]):
        res["status"] = "revisar"
    res["tempo_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if debug_path:
        cv2.imwrite(str(debug_path), desenhar_debug(ret, centros, centros_id, res))
    if devolver_debug:
        return res, desenhar_debug(ret, centros, centros_id, res)
    return res


def desenhar_debug(ret, centros, centros_id, res):
    out = cv2.cvtColor(ret, cv2.COLOR_GRAY2BGR)
    r = int(_p(L.BOLHA_DIAM_MM / 2)) + 3
    for item in res["questoes"]:
        cs = centros[item["q"]]
        for (cx, cy), v in zip(cs, item["preenchimento"]):
            if v > T_MARCADA:
                cor, esp = (40, 170, 40), 3
            elif v > T_VAZIA:
                cor, esp = (0, 140, 255), 3
            else:
                cor, esp = (180, 180, 180), 1
            cv2.circle(out, (int(round(cx)), int(round(cy))), r, cor, esp)
        (x0, y0), (x1, y1) = cs[0], cs[-1]
        if item["flags"]:
            cv2.rectangle(out, (int(x0) - r - 4, int(y0) - r - 4), (int(x1) + r + 4, int(y1) + r + 4), (31, 67, 168), 2)
        cv2.putText(out, item["resposta"], (int(x1) + r + 30, int(y1) + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (31, 67, 168) if item["flags"] else (85, 58, 15), 1, cv2.LINE_AA)
    for linha in centros_id:
        for cx, cy in linha:
            cv2.circle(out, (int(round(cx)), int(round(cy))), r, (200, 120, 40), 1)
    txt = {"qr": f"ID via QR: {res['prova']}|{res['aluno']}", "bolhas": f"ID via bolhas: {res['aluno_codigo']} (QR ilegivel)",
           None: "ID ILEGIVEL"}[res["id_fonte"]]
    cv2.putText(out, txt, (int(_p(20)), int(_p(64))), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (85, 58, 15), 2, cv2.LINE_AA)
    cv2.putText(out, f"status: {res['status']}", (int(_p(30)), int(_p(284))), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (85, 58, 15), 2, cv2.LINE_AA)
    return out


def corrigir_arquivo(caminho, debug_path=None):
    img = cv2.imdecode(np.fromfile(str(caminho), dtype=np.uint8), cv2.IMREAD_COLOR)  # aceita acentos no caminho
    if img is None:
        return {"arquivo": str(caminho), "status": "falha", "flags_folha": ["imagem_ilegivel"], "questoes": []}
    return corrigir_imagem(img, Path(caminho).name, debug_path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("imagem", nargs="?")
    ap.add_argument("--pasta")
    ap.add_argument("--saida", help="JSONL (modo pasta)")
    ap.add_argument("--debug", help="PNG com a folha retificada e as bolhas sobrepostas (modo imagem)")
    ap.add_argument("--debug-dir", help="pasta para os PNGs de debug (modo pasta)")
    args = ap.parse_args()

    if args.pasta:
        exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
        arquivos = sorted(p for p in Path(args.pasta).rglob("*") if p.suffix.lower() in exts)
        if args.debug_dir:
            Path(args.debug_dir).mkdir(parents=True, exist_ok=True)
        saida = open(args.saida, "w", encoding="utf-8") if args.saida else None
        cont = {"ok": 0, "revisar": 0, "falha": 0}
        for p in arquivos:
            dbg = Path(args.debug_dir) / f"{p.stem}_debug.png" if args.debug_dir else None
            r = corrigir_arquivo(p, dbg)
            r["arquivo"] = str(p.relative_to(args.pasta))
            cont[r["status"]] += 1
            print(f"{r['arquivo']}: {r['status']} aluno={r.get('aluno')} {r.get('tempo_ms', '')} ms", file=sys.stderr)
            linha = json.dumps(r, ensure_ascii=False)
            if saida:
                saida.write(linha + "\n")
            else:
                print(linha)
        if saida:
            saida.close()
        print(f"{len(arquivos)} imagens: {cont}", file=sys.stderr)
    elif args.imagem:
        print(json.dumps(corrigir_arquivo(args.imagem, args.debug), ensure_ascii=False, indent=1))
    else:
        ap.error("informe uma imagem ou --pasta")


if __name__ == "__main__":
    main()

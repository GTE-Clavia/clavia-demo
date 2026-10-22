"""Gera a folha de respostas Clavia (A4, PNG 300 dpi + PDF).

Uso:
  python gerar_folha.py --prova P2026-MAT6-01 --aluno A001
  python gerar_folha.py --prova P2026-MAT6-01 --lote 30 --com-guia --saida teste_real/
"""
import argparse
import csv
import os
import random
from pathlib import Path

import cv2
import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFont

import layout as L

DPI = 300
AZUL_PROFUNDO = (15, 58, 85)
TERRACOTA = (168, 67, 31)
AZUL_CLARO = (108, 156, 186)
TINTA = (11, 22, 34)
MUTED = (85, 96, 107)

_FONTES_DIR = [
    Path(__file__).resolve().parents[1] / "static" / "fonts",  # [demo_app] fontes OFL empacotadas (container Linux)
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts",
    Path("C:/Windows/Fonts"),
]


def _fonte(nome_preferido, tamanho_mm, fallback="arial.ttf"):
    tamanho_px = int(round(L.mm2px(tamanho_mm, DPI)))
    for nome in (nome_preferido, fallback):
        for d in _FONTES_DIR:
            p = d / nome
            if p.exists():
                return ImageFont.truetype(str(p), tamanho_px)
    return ImageFont.load_default()


def _px(v):
    return int(round(L.mm2px(v, DPI)))


def _aruco(marker_id, lado_px):
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = cv2.aruco.generateImageMarker(dic, marker_id, lado_px)
    return Image.fromarray(img).convert("RGB")


def _qr(payload, lado_px):
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q, border=0, box_size=10)
    q.add_data(payload)
    q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((lado_px, lado_px), Image.NEAREST)


def render_folha(prova_id, aluno_id, titulo="Matemática · 6º ano"):
    """Devolve a folha como PIL.Image RGB a 300 dpi."""
    w, h = _px(L.PAGINA_MM[0]), _px(L.PAGINA_MM[1])
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    f_marca = _fonte("bricolage-grotesque-v9-latin_latin-ext-700.ttf", 8.5, "arialbd.ttf")
    f_tit = _fonte("lexend-v26-latin_latin-ext-600.ttf", 4.2, "arialbd.ttf")
    f_txt = _fonte("lexend-v26-latin_latin-ext-regular.ttf", 3.2)
    f_peq = _fonte("lexend-v26-latin_latin-ext-regular.ttf", 2.5)
    f_num = _fonte("lexend-v26-latin_latin-ext-600.ttf", 3.6, "arialbd.ttf")
    f_letra = _fonte("lexend-v26-latin_latin-ext-500.ttf", 3.0)

    # Fiduciais
    lado = _px(L.ARUCO_LADO_MM)
    for mid in L.ARUCO_IDS:
        x, y = L.aruco_cantos_mm(mid)[0]
        img.paste(_aruco(mid, lado), (_px(x), _px(y)))

    # Cabecalho: marca-QR provisoria + wordmark
    x0, y0 = 28, 9.5
    d.rounded_rectangle([_px(x0), _px(y0), _px(x0 + 9), _px(y0 + 9)], radius=_px(1.6),
                        outline=AZUL_PROFUNDO, width=_px(0.8))
    d.rectangle([_px(x0 + 3), _px(y0 + 3), _px(x0 + 6), _px(y0 + 6)], fill=TERRACOTA)
    d.text((_px(x0 + 12), _px(y0 - 1.2)), "Clavia", font=f_marca, fill=AZUL_PROFUNDO)
    d.text((_px(x0 + 12), _px(y0 + 9.2)), "Folha de respostas · " + titulo, font=f_txt, fill=TINTA)

    # Identificacao
    y = 34
    for rotulo in ("Turma:", "Data:"):  # sem nome: a folha leva so o codigo do aluno (LGPD)
        d.text((_px(20), _px(y)), rotulo, font=f_txt, fill=TINTA)
        d.line([_px(36), _px(y + 4), _px(144), _px(y + 4)], fill=MUTED, width=_px(0.25))
        y += 9
    d.text((_px(20), _px(y + 1)), f"Prova {prova_id}  ·  Aluno {aluno_id}", font=f_peq, fill=MUTED)

    # QR (com zona de silencio branca garantida pelo layout)
    qx, qy = L.QR_POS_MM
    img.paste(_qr(f"{prova_id}|{aluno_id}", _px(L.QR_LADO_MM)), (_px(qx), _px(qy)))

    # Instrucao
    d.rounded_rectangle([_px(20), _px(72), _px(190), _px(80.5)], radius=_px(1.5), fill=(234, 240, 244))
    d.text((_px(23), _px(73.9)),
           "Pinte toda a bolha da alternativa escolhida. Uma só por questão.",
           font=f_txt, fill=AZUL_PROFUNDO)

    # Grade
    r = L.BOLHA_DIAM_MM / 2
    a = L.ANCORA_LADO_MM / 2

    def ancoras(esq, dir_):
        for ax, ay in (esq, dir_):
            d.rectangle([_px(ax - a), _px(ay - a), _px(ax + a), _px(ay + a)], fill="black")

    for q in range(1, L.N_QUESTOES + 1):
        esq, dir_, bolhas = L.linha_questao(q)
        ancoras(esq, dir_)
        bx0, by = bolhas[0]
        d.text((_px(bx0 - 6.5), _px(by)), f"{q:02d}", font=f_num, fill=TINTA, anchor="rm")
        for (cx, cy), letra in zip(bolhas, L.ALTERNATIVAS):
            d.ellipse([_px(cx - r), _px(cy - r), _px(cx + r), _px(cy + r)],
                      outline=AZUL_PROFUNDO, width=_px(0.3))
            d.text((_px(cx), _px(cy)), letra, font=f_letra, fill=AZUL_CLARO, anchor="mm")

    # Codigo do aluno em bolhas pre-preenchidas (redundancia ao QR; o aluno nao mexe)
    codigo = L.codigo_aluno(aluno_id)
    d.text((_px(20), _px(L.ID_Y0_MM - 8.5)), "Código do aluno (já preenchido pela Clavia, não altere)",
           font=f_peq, fill=MUTED)
    for k in range(L.ID_DIGITOS):
        esq, dir_, bolhas = L.linha_id(k)
        ancoras(esq, dir_)
        for dig, (cx, cy) in enumerate(bolhas):
            caixa = [_px(cx - r), _px(cy - r), _px(cx + r), _px(cy + r)]
            if dig == int(codigo[k]):
                d.ellipse(caixa, fill=TINTA)
            else:
                d.ellipse(caixa, outline=AZUL_PROFUNDO, width=_px(0.3))
                d.text((_px(cx), _px(cy)), str(dig), font=f_letra, fill=AZUL_CLARO, anchor="mm")
    d.text((_px(150), _px(L.ID_Y0_MM + L.ID_PASSO_Y_MM)), codigo, font=f_marca, fill=TINTA, anchor="lm")

    # Rodape
    d.text((_px(105), _px(266)),
           "Para fotografar: folha inteira no quadro, com os 4 quadrados dos cantos visíveis.",
           font=f_peq, fill=MUTED, anchor="mm")
    d.text((_px(105), _px(271)), "Clavia · GTE Soluções · prova de conceito de correção por foto",
           font=f_peq, fill=MUTED, anchor="mm")
    return img


def salvar(img, caminho_base):
    img.save(f"{caminho_base}.png", dpi=(DPI, DPI))
    img.save(f"{caminho_base}.pdf", resolution=DPI)


def guia_preenchimento(alunos, seed=2026):
    """Respostas que a pessoa deve marcar no teste com fotos reais (verdade de campo).

    ~88% marcacao simples, 5% em branco, 3% dupla, 4% rasura (marcar, apagar, marcar outra).
    """
    rng = random.Random(seed)
    linhas = []
    for aluno in alunos:
        for q in range(1, L.N_QUESTOES + 1):
            u = rng.random()
            if u < 0.05:
                linhas.append((aluno, q, "BRANCO", ""))
            elif u < 0.08:
                a, b = rng.sample(L.ALTERNATIVAS, 2)
                linhas.append((aluno, q, "MULT", f"marcar {a} e {b}"))
            elif u < 0.12:
                a, b = rng.sample(L.ALTERNATIVAS, 2)
                linhas.append((aluno, q, b, f"marcar {a}, apagar {a}, marcar {b}"))
            else:
                linhas.append((aluno, q, rng.choice(L.ALTERNATIVAS), ""))
    return linhas


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prova", default="P2026-MAT6-01")
    ap.add_argument("--aluno", default="A001")
    ap.add_argument("--lote", type=int, default=0, help="gera N folhas A001..A00N e um PDF unico")
    ap.add_argument("--com-guia", action="store_true", help="grava respostas_a_marcar.csv (teste real)")
    ap.add_argument("--saida", default="folhas")
    args = ap.parse_args()

    out = Path(args.saida)
    out.mkdir(parents=True, exist_ok=True)
    if args.lote <= 0:
        base = out / f"folha_{args.prova}_{args.aluno}"
        salvar(render_folha(args.prova, args.aluno), base)
        print(f"ok: {base}.png / .pdf")
        return

    alunos = [f"A{i:03d}" for i in range(1, args.lote + 1)]
    paginas = []
    for aluno in alunos:
        img = render_folha(args.prova, aluno)
        img.save(out / f"folha_{args.prova}_{aluno}.png", dpi=(DPI, DPI))
        paginas.append(img)
    pdf = out / f"folhas_{args.prova}_lote{args.lote}.pdf"
    paginas[0].save(pdf, resolution=DPI, save_all=True, append_images=paginas[1:])
    print(f"ok: {len(alunos)} folhas + {pdf}")
    if args.com_guia:
        guia = out / "respostas_a_marcar.csv"
        with open(guia, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["aluno", "questao", "verdade", "instrucao"])
            w.writerows(guia_preenchimento(alunos))
        print(f"ok: {guia}")


if __name__ == "__main__":
    main()

"""Geometria da folha de respostas Clavia (fonte unica para gerar e corrigir).

Tudo em milimetros no sistema de coordenadas da pagina A4 (origem no canto
superior esquerdo, x para a direita, y para baixo).
"""

PAGINA_MM = (210.0, 297.0)
N_QUESTOES = 20
ALTERNATIVAS = "ABCDE"

# Marcadores fiduciais ArUco (DICT_4X4_50), ids 0..3 = TL, TR, BR, BL.
ARUCO_LADO_MM = 14.0
ARUCO_MARGEM_MM = 8.0  # distancia da borda da pagina ate a borda do marcador
ARUCO_IDS = (0, 1, 2, 3)


def aruco_cantos_mm(marker_id):
    """4 cantos (TL, TR, BR, BL) do marcador, na mesma ordem que o OpenCV devolve."""
    w, h = PAGINA_MM
    s, m = ARUCO_LADO_MM, ARUCO_MARGEM_MM
    origem = {0: (m, m), 1: (w - m - s, m), 2: (w - m - s, h - m - s), 3: (m, h - m - s)}[marker_id]
    x, y = origem
    return [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]


# QR Code (payload "prova_id|aluno_id")
QR_POS_MM = (150.0, 28.0)  # canto superior esquerdo
QR_LADO_MM = 40.0

BOLHA_DIAM_MM = 7.0
ANCORA_LADO_MM = 4.0  # quadradinhos pretos no inicio e no fim de cada linha de bolhas

# Grade de respostas: 2 blocos de 10 questoes, 5 alternativas.
PASSO_X_MM = 11.0
PASSO_Y_MM = 15.0
Y_PRIMEIRA_LINHA_MM = 88.0
QUESTOES_POR_BLOCO = 10
BLOCOS_X_MM = (
    {"ancora_esq": 20.0, "bolha0": 40.0, "ancora_dir": 96.0},
    {"ancora_esq": 114.0, "bolha0": 134.0, "ancora_dir": 190.0},
)

# Codigo numerico do aluno em bolhas pre-impressas (redundancia ao QR).
# 3 linhas (centena, dezena, unidade) x 10 digitos. aluno_id termina em 3 digitos.
ID_DIGITOS = 3
ID_Y0_MM = 239.0
ID_PASSO_Y_MM = 9.5
ID_X = {"ancora_esq": 20.0, "bolha0": 40.0, "passo": 10.0, "ancora_dir": 142.0}


def questao_bloco_linha(q):
    """q comeca em 1."""
    return (q - 1) // QUESTOES_POR_BLOCO, (q - 1) % QUESTOES_POR_BLOCO


def linha_questao(q):
    """(ancora_esq, ancora_dir, [centros das 5 bolhas]) em mm."""
    bloco, linha = questao_bloco_linha(q)
    b = BLOCOS_X_MM[bloco]
    y = Y_PRIMEIRA_LINHA_MM + linha * PASSO_Y_MM
    bolhas = [(b["bolha0"] + i * PASSO_X_MM, y) for i in range(len(ALTERNATIVAS))]
    return (b["ancora_esq"], y), (b["ancora_dir"], y), bolhas


def linha_id(k):
    """Linha k (0=centena) do codigo do aluno: (ancora_esq, ancora_dir, [10 centros])."""
    y = ID_Y0_MM + k * ID_PASSO_Y_MM
    bolhas = [(ID_X["bolha0"] + d * ID_X["passo"], y) for d in range(10)]
    return (ID_X["ancora_esq"], y), (ID_X["ancora_dir"], y), bolhas


def bolha_centro_mm(q, alt_idx):
    return linha_questao(q)[2][alt_idx]


def codigo_aluno(aluno_id):
    """Ultimos 3 digitos do aluno_id ("A017" -> "017")."""
    digitos = "".join(c for c in aluno_id if c.isdigit())[-ID_DIGITOS:]
    return digitos.rjust(ID_DIGITOS, "0")


def mm2px(v, dpi):
    return v * dpi / 25.4

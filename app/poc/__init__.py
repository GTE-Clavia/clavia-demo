"""Copia dos modulos da POC (`../poc_correcao/`), versao de 22/10/2026.

Os modulos importam uns aos outros como scripts (`import layout as L`); para
reaproveita-los sem reescrever, esta pasta entra no sys.path.

Diferencas em relacao ao original (marcadas com `[demo_app]` no codigo):
- corrigir.py: `corrigir_imagem(..., devolver_debug=True)` devolve a imagem de
  depuracao em memoria, para a demo nunca gravar nada em disco.
- gerar_folha.py: procura as fontes tambem em `app/static/fonts/` (container Linux).
layout.py e diagnosticar.py estao identicos ao original.
"""
import sys
from pathlib import Path

_AQUI = str(Path(__file__).resolve().parent)
if _AQUI not in sys.path:
    sys.path.insert(0, _AQUI)

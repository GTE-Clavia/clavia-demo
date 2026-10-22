"""Microdiagnostico por habilidade BNCC, por aluno e por turma.

Entrada: gabarito (questao -> resposta certa + codigo de habilidade) e o JSONL do corrigir.py.
Regras:
  - "em branco" conta como erro (o aluno nao respondeu);
  - questao sinalizada por outro motivo (dupla, duvida, rasura) fica PENDENTE de revisao
    e sai do denominador ate o professor decidir (use --revisoes para aplicar as decisoes);
  - nivel por habilidade: consolidada >= 75% | em desenvolvimento 50-74% | lacuna < 50%;
    com menos de 3 itens validos na habilidade: "insuficiente".
  Com 5 itens por habilidade o diagnostico e indicativo (sinal para o professor), nao medida psicometrica.

Uso:
  python diagnosticar.py --gabarito dados/gabarito_P2026-MAT6-01.json --resultados demo_turma/resultados.jsonl
  [--revisoes revisoes.csv (aluno,questao,resposta)] [--verdade demo_turma/verdade_turma.csv] [--saida diagnostico/]
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import layout as L

FOLHA_INTEIRA = ("fiduciais", "alinhamento", "fiducial_ausente")


def nivel(acertos, validas):
    if validas < 3:
        return "insuficiente"
    p = acertos / validas
    return "consolidada" if p >= 0.75 else ("em desenvolvimento" if p >= 0.5 else "lacuna")


def respostas_de_resultados(resultados, revisoes):
    """{aluno: {q: resposta | None(pendente)}}"""
    out = {}
    for r in resultados:
        aluno = r.get("aluno") or f"#{r.get('aluno_codigo') or r.get('arquivo')}"
        folha_inteira = r.get("status") == "falha" or any(f.startswith(FOLHA_INTEIRA) for f in r.get("flags_folha", []))
        resp = {}
        for item in r.get("questoes", []):
            pendente = folha_inteira or any(f != "em_branco" for f in item["flags"])
            resp[item["q"]] = None if pendente else item["resposta"]
        for q in range(1, L.N_QUESTOES + 1):
            resp.setdefault(q, None)
        for q, v in revisoes.get(aluno, {}).items():
            resp[q] = v
        out[aluno] = resp
    return out


def diagnosticar(gab, respostas):
    questoes = {it["q"]: it for it in gab["questoes"]}
    codigos = list(gab["habilidades"])
    alunos = {}
    for aluno, resp in sorted(respostas.items()):
        por_hab = {}
        for c in codigos:
            qs = [q for q, it in questoes.items() if it["habilidade"] == c]
            validas = [q for q in qs if resp.get(q) is not None]
            acertos = sum(resp[q] == questoes[q]["gabarito"] for q in validas)
            por_hab[c] = {"acertos": acertos, "validas": len(validas), "pendentes": len(qs) - len(validas),
                          "percentual": round(acertos / len(validas), 3) if validas else None,
                          "nivel": nivel(acertos, len(validas))}
        total_val = sum(h["validas"] for h in por_hab.values())
        alunos[aluno] = {"habilidades": por_hab,
                         "nota_0a10": round(10 * sum(h["acertos"] for h in por_hab.values()) / total_val, 1) if total_val else None,
                         "lacunas": [c for c, h in por_hab.items() if h["nivel"] == "lacuna"],
                         "pendentes_revisao": sum(h["pendentes"] for h in por_hab.values())}
    turma = {}
    for c in codigos:
        hs = [a["habilidades"][c] for a in alunos.values()]
        ac, va = sum(h["acertos"] for h in hs), sum(h["validas"] for h in hs)
        turma[c] = {"percentual_acerto": round(ac / va, 3) if va else None,
                    "alunos_com_lacuna": sorted(a for a, d in alunos.items() if d["habilidades"][c]["nivel"] == "lacuna"),
                    "rotulo": gab["habilidades"][c]["rotulo_curto"]}
    # agrupamento por perfil de lacuna (base do modulo "Agir")
    grupos = defaultdict(list)
    for a, d in alunos.items():
        grupos[" + ".join(d["lacunas"]) or "sem lacuna"].append(a)
    return {"prova": gab["prova"], "alunos": alunos, "turma": turma, "grupos_por_lacuna": dict(grupos)}


def relatorio_md(diag, gab):
    cod = list(gab["habilidades"])
    lin = [f"# Microdiagnostico -- prova {diag['prova']}", "",
           "Niveis: consolidada >= 75% | em desenvolvimento 50-74% | lacuna < 50% | insuficiente = menos de 3 itens validos.", "",
           "## Turma", "", "| Habilidade | Descricao curta | Acerto da turma | Alunos com lacuna |", "|---|---|---|---|"]
    for c in cod:
        t = diag["turma"][c]
        p = f"{100 * t['percentual_acerto']:.0f}%" if t["percentual_acerto"] is not None else "-"
        lin.append(f"| {c} | {gab['habilidades'][c]['rotulo_curto']} | {p} | {len(t['alunos_com_lacuna'])} |")
    lin += ["", "## Agrupamento por perfil de lacuna", ""]
    for g, al in sorted(diag["grupos_por_lacuna"].items(), key=lambda x: -len(x[1])):
        lin.append(f"- **{g}** ({len(al)}): {', '.join(al)}")
    lin += ["", "## Por aluno", "", "| Aluno | Nota (0-10) | " + " | ".join(cod) + " | Pendentes |",
            "|---|---|" + "---|" * len(cod) + "---|"]
    for a, d in diag["alunos"].items():
        cel = []
        for c in cod:
            h = d["habilidades"][c]
            cel.append(f"{h['acertos']}/{h['validas']} {h['nivel']}")
        lin.append(f"| {a} | {d['nota_0a10']} | " + " | ".join(cel) + f" | {d['pendentes_revisao']} |")
    return "\n".join(lin) + "\n"


def heatmap(diag, gab, caminho):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    cores = {"consolidada": 0, "em desenvolvimento": 1, "lacuna": 2, "insuficiente": 3}
    cmap = ListedColormap(["#C79A3A", "#6C9CBA", "#A8431F", "#DCE0DA"])
    cod = list(gab["habilidades"])
    alunos = list(diag["alunos"])
    M = [[cores[diag["alunos"][a]["habilidades"][c]["nivel"]] for c in cod] for a in alunos]
    fig, ax = plt.subplots(figsize=(6.5, 0.28 * len(alunos) + 1.6))
    ax.imshow(M, cmap=cmap, vmin=0, vmax=3, aspect="auto")
    for i, a in enumerate(alunos):
        for j, c in enumerate(cod):
            h = diag["alunos"][a]["habilidades"][c]
            ax.text(j, i, f"{h['acertos']}/{h['validas']}", ha="center", va="center", fontsize=7,
                    color="white" if M[i][j] in (0, 2) else "#0B1622")
    ax.set_xticks(range(len(cod)), cod, fontsize=8)
    ax.set_yticks(range(len(alunos)), alunos, fontsize=7)
    ax.xaxis.tick_top()
    ax.set_title("Ocre: consolidada | azul: em desenvolvimento | terracota: lacuna | cinza: insuficiente", fontsize=8, pad=24)
    fig.tight_layout()
    fig.savefig(caminho, dpi=160)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gabarito", required=True)
    ap.add_argument("--resultados", required=True, help="JSONL do corrigir.py --pasta")
    ap.add_argument("--revisoes", help="CSV aluno,questao,resposta com as decisoes do professor")
    ap.add_argument("--verdade", help="CSV aluno,questao,verdade (demo/teste): mede a concordancia do diagnostico")
    ap.add_argument("--saida", default="diagnostico")
    args = ap.parse_args()

    gab = json.loads(Path(args.gabarito).read_text(encoding="utf-8"))
    resultados = [json.loads(l) for l in Path(args.resultados).read_text(encoding="utf-8").splitlines() if l.strip()]
    outras = [r for r in resultados if r.get("prova") not in (None, gab["prova"])]
    if outras:
        print(f"aviso: {len(outras)} folhas de outra prova ignoradas")
        resultados = [r for r in resultados if r not in outras]
    revisoes = defaultdict(dict)
    if args.revisoes:
        for row in csv.DictReader(open(args.revisoes, encoding="utf-8")):
            revisoes[row["aluno"]][int(row["questao"])] = row["resposta"]

    diag = diagnosticar(gab, respostas_de_resultados(resultados, revisoes))
    out = Path(args.saida)
    out.mkdir(parents=True, exist_ok=True)
    (out / "diagnostico.json").write_text(json.dumps(diag, indent=1, ensure_ascii=False), encoding="utf-8")
    md = relatorio_md(diag, gab)

    if args.verdade:  # concordancia: diagnostico pelas fotos x diagnostico pelas respostas verdadeiras
        verdade = defaultdict(dict)
        for row in csv.DictReader(open(args.verdade, encoding="utf-8")):
            v = row["verdade"]
            verdade[row["aluno"]][int(row["questao"])] = None if v == "MULT" else v
        diag_v = diagnosticar(gab, verdade)
        por_cod = {L.codigo_aluno(a): a for a in diag_v["alunos"]}
        iguais = total = 0
        for a, d in diag["alunos"].items():
            av = por_cod.get(L.codigo_aluno(a))
            if av is None:
                continue
            for c, h in d["habilidades"].items():
                total += 1
                iguais += h["nivel"] == diag_v["alunos"][av]["habilidades"][c]["nivel"]
        frase = (f"Concordancia do nivel por (aluno, habilidade) entre o diagnostico feito a partir das fotos "
                 f"({'com as revisoes aplicadas' if args.revisoes else 'sem revisao humana; pendentes fora do denominador'}) e o feito a partir das respostas verdadeiras: "
                 f"{iguais}/{total} = {100 * iguais / max(total, 1):.1f}%.")
        md += "\n## Concordancia com a verdade (demo sintetica)\n\n" + frase + "\n"
        print(frase)
    (out / "diagnostico.md").write_text(md, encoding="utf-8")
    heatmap(diag, gab, out / "fig_microdiagnostico_turma.png")
    print(f"ok: {out}/diagnostico.json, diagnostico.md, fig_microdiagnostico_turma.png ({len(diag['alunos'])} alunos)")
    for c, t in diag["turma"].items():
        p = f"{100 * t['percentual_acerto']:.0f}%" if t["percentual_acerto"] is not None else "-"
        print(f"  {c} ({t['rotulo']}): acerto da turma {p}, {len(t['alunos_com_lacuna'])} alunos com lacuna")


if __name__ == "__main__":
    main()

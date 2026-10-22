// Clavia demo: envia a foto para /api/corrigir e mostra leitura, revisao e microdiagnostico.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const estado = $("estado");
  const entrada = $("foto");
  const botoes = Array.from(document.querySelectorAll("[data-exemplo]"));
  const CLASSE = { "consolidada": "consolidada", "em desenvolvimento": "desenvolvimento", "lacuna": "lacuna", "insuficiente": "insuficiente" };
  const ROTULO = { "consolidada": "✓ domina", "em desenvolvimento": "parcial", "lacuna": "reforço", "insuficiente": "poucos itens" };
  let atual = null; // ultimo resultado (com as conferencias do professor aplicadas)

  function el(tag, attrs, ...filhos) {
    const e = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => (k === "class" ? (e.className = v) : e.setAttribute(k, v)));
    filhos.flat().forEach((f) => e.append(f instanceof Node ? f : document.createTextNode(String(f))));
    return e;
  }
  const virgula = (x, casas) => Number(x).toFixed(casas).replace(".", ",");

  // Radar do aluno: mesmo desenho da macro radar() de _macros.html (o resultado da foto chega por fetch).
  const NIVEL = { "consolidada": "domina", "em desenvolvimento": "parcial", "lacuna": "reforço", "insuficiente": "poucos itens" };
  function s(tag, attrs, ...filhos) {
    const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
    filhos.forEach((f) => e.append(f instanceof Node ? f : document.createTextNode(String(f))));
    return e;
  }
  function radar(habs, tam) {
    const cods = Object.keys(habs);
    if (cods.length !== 4) return null;
    const DIR = [[0, -1], [1, 0], [0, 1], [-1, 0]];
    const r = tam * 0.36, cy = tam / 2, cx = r + 136, larg = (2 * cx).toFixed(0);
    const f1 = (x) => x.toFixed(1);
    const xy = (i, v) => [cx + DIR[i][0] * r * v, cy + DIR[i][1] * r * v];
    const pontos = (vs) => vs.map((v, i) => xy(i, v).map(f1).join(",")).join(" ");
    // recalculado de acertos/validas: a conferencia do professor muda esses numeros no navegador
    const pct = cods.map((c) => (habs[c].validas ? Math.round((100 * habs[c].acertos) / habs[c].validas) : null));
    const vals = pct.map((p) => (p === null ? 0 : p / 100));
    const desc = cods.map((c, i) => c + " (" + habs[c].rotulo + ") " +
      (pct[i] === null ? "pendente" : pct[i] + "%, " + NIVEL[habs[c].nivel]));
    const svg = s("svg", { class: "radar-svg", role: "img", viewBox: "0 0 " + larg + " " + tam, width: larg, height: tam,
      "aria-label": "Perfil por habilidade, em percentual de acerto: " + desc.join("; ") + "." });
    [1, 0.75, 0.5].forEach((v) => svg.append(s("polygon", { class: "radar-anel" + (v < 1 ? " radar-anel-" + 100 * v : ""),
      points: pontos([v, v, v, v]) })));
    DIR.forEach((_, i) => { const [x, y] = xy(i, 1); svg.append(s("line", { class: "radar-eixo", x1: f1(cx), y1: f1(cy), x2: f1(x), y2: f1(y) })); });
    [75, 50].forEach((v, i) => {
      svg.append(s("line", { class: "radar-anel radar-anel-" + v, x1: "4", y1: 12 + 14 * i, x2: "22", y2: 12 + 14 * i }));
      svg.append(s("text", { class: "radar-escala", x: "26", y: 15 + 14 * i }, v + "%"));
    });
    svg.append(s("polygon", { class: "radar-area", points: pontos(vals) }));
    cods.forEach((c, i) => {
      const h = habs[c], [x, y] = xy(i, vals[i]), [dx, dy] = DIR[i];
      svg.append(s("circle", { class: "radar-ponto " + (pct[i] === null ? "radar-pendente" : "n-" + CLASSE[h.nivel]), cx: f1(x), cy: f1(y), r: "4.5" },
        s("title", {}, c + " · " + h.rotulo + ": " + h.acertos + "/" + h.validas + " (" + (pct[i] === null ? "pendente" : NIVEL[h.nivel]) + ")")));
      svg.append(s("text", { class: "radar-rotulo", x: f1(cx + dx * (r + 8)), y: f1(cy + dy * (r + 8) + (dy === 0 ? 4 : dy > 0 ? 12 : 0)),
        "text-anchor": dx > 0 ? "start" : dx < 0 ? "end" : "middle" }, c + " · " + (pct[i] === null ? "pendente" : pct[i] + "%")));
    });
    return svg;
  }

  function avisar(texto, erro) {
    estado.textContent = texto;
    estado.className = erro ? "erro" : "";
  }
  function ocupar(sim) {
    botoes.forEach((b) => (b.disabled = sim));
    entrada.disabled = sim;
  }

  async function enviar(arquivo) {
    const limite = Number(entrada.dataset.limite);
    if (arquivo.size > limite) return avisar("Arquivo acima do limite de " + Math.round(limite / 1048576) + " MB.", true);
    const dados = new FormData();
    dados.append("foto", arquivo);
    ocupar(true);
    avisar("Corrigindo a folha...");
    try {
      const r = await fetch("/api/corrigir?imagem=1", { method: "POST", body: dados });
      const j = await r.json().catch(() => ({ erro: "Resposta inválida do servidor." }));
      if (!r.ok) return avisar(j.erro || "Erro " + r.status, true);
      atual = j;
      mostrar();
      avisar(j.status === "falha" ? "Não foi possível ler esta folha." : "Folha corrigida.");
      $("resultado").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      avisar("Falha de conexão com o servidor.", true);
    } finally {
      ocupar(false);
      entrada.value = "";
    }
  }

  // Mesmas regras do diagnosticar.py: pendente fica fora do denominador; em branco conta como erro.
  function nivel(acertos, validas) {
    if (validas < 3) return "insuficiente";
    const p = acertos / validas;
    return p >= 0.75 ? "consolidada" : p >= 0.5 ? "em desenvolvimento" : "lacuna";
  }
  function recalcular() {
    const d = atual.diagnostico;
    if (!d) return;
    let ac = 0, va = 0;
    Object.keys(d.habilidades).forEach((c) => {
      const qs = atual.questoes.filter((q) => q.habilidade === c);
      const validas = qs.filter((q) => !q.pendente);
      const acertos = validas.filter((q) => q.resposta === q.gabarito).length;
      Object.assign(d.habilidades[c], { acertos, validas: validas.length, pendentes: qs.length - validas.length,
        nivel: nivel(acertos, validas.length) });
      ac += acertos; va += validas.length;
    });
    d.nota_0a10 = va ? Math.round((100 * ac) / va) / 10 : null;
    d.pendentes_revisao = atual.questoes.filter((q) => q.pendente).length;
  }

  function mostrar() {
    const j = atual;
    $("resultado").classList.add("visivel");
    const img = $("img-leitura");
    if (j.imagem_leitura) { img.src = j.imagem_leitura; img.hidden = false; } else { img.hidden = true; }
    const d = j.diagnostico;
    $("r-nota").textContent = d && d.nota_0a10 !== null ? virgula(d.nota_0a10, 1) : "-";
    const restantes = j.questoes.filter((q) => q.sinalizada && !q.conferida).length;
    $("r-revisao").textContent = j.status === "falha" ? "-" : restantes + " de 20";
    $("r-tempo").textContent = virgula(j.tempo_total_ms / 1000, 2) + " s";

    const fonte = { qr: "pelo QR Code", bolhas: "pelo código em bolhas (QR ilegível)" }[j.id_fonte];
    $("r-id").textContent = j.aluno
      ? "Prova " + j.prova + " · aluno " + j.aluno + ", identificado " + fonte + ". Leitura do protótipo: " +
        virgula(j.tempo_ms / 1000, 2) + " s; o restante é abrir a imagem e desenhar o resultado."
      : "Aluno não identificado.";
    $("r-folha").textContent = (j.motivos_folha || []).join(" ") +
      (j.prova_reconhecida ? "" : " Esta folha é de outra prova: o diagnóstico usa só a prova P2026-MAT6-01.");

    const diag = $("r-diag");
    diag.replaceChildren();
    if (d) {
      Object.entries(d.habilidades).forEach(([c, h]) => {
        diag.append(el("div", {},
          el("div", { class: "cel n-" + CLASSE[h.nivel], title: c + ": " + h.nivel + " · " + h.rotulo },
            el("b", {}, h.acertos + "/" + h.validas), el("span", { class: "r" }, ROTULO[h.nivel])),
          el("div", { class: "cel-codigo" }, c)));
      });
    } else {
      diag.append(el("p", { class: "pequeno" }, "Sem diagnóstico para esta foto."));
    }
    const caixa = $("r-radar");
    caixa.replaceChildren();
    try {
      const g = d && radar(d.habilidades, 180);
      if (g) caixa.append(g);
    } catch (e) { /* sem radar: o restante do resultado continua na tela */ }
    $("r-radar-fig").hidden = !caixa.firstChild;

    const lista = $("r-revisao-lista");
    lista.replaceChildren();
    j.questoes.filter((q) => q.sinalizada).forEach((q) => lista.append(itemRevisao(q)));
    if (!lista.children.length && j.status !== "falha") lista.append(el("li", {}, "Nenhuma questão sinalizada."));

    const corpo = $("r-tabela");
    corpo.replaceChildren();
    j.questoes.forEach((q) => {
      const sit = q.conferida ? el("span", { class: "tag tag-conf" }, "conferida pelo professor")
        : q.sinalizada ? el("span", { class: "tag tag-rev" }, q.motivos.join("; ") || "folha inteira em revisão")
        : el("span", { class: "tag tag-ok" }, q.acertou ? "acerto" : "erro");
      const barra = el("span", { class: "confianca" }, el("i", {}));
      barra.firstChild.style.width = Math.round(100 * q.confianca) + "%";
      corpo.append(el("tr", {},
        el("td", { class: "c num" }, q.q), el("td", { class: "c" }, q.resposta), el("td", { class: "c" }, q.gabarito),
        el("td", {}, el("span", { class: "code" }, q.habilidade)),
        el("td", { class: "num" }, barra, virgula(q.confianca, 2)), el("td", {}, sit)));
    });
  }

  function itemRevisao(q) {
    const li = el("li", { class: q.conferida ? "feito" : "" },
      el("strong", {}, "Questão " + q.q), el("span", { class: "pequeno" }, "lida: " + q.resposta + " · " +
        (q.motivos.join("; ") || "folha inteira em revisão")));
    if (q.conferida) {
      li.append(el("span", { class: "tag tag-conf" }, "conferida: " + q.resposta));
      return li;
    }
    const id = "conf-" + q.q;
    const sel = el("select", { id: id, "aria-label": "Resposta conferida da questão " + q.q });
    ["A", "B", "C", "D", "E", "BRANCO", "MULT"].forEach((v) => {
      const o = el("option", { value: v }, v === "BRANCO" ? "em branco" : v === "MULT" ? "anular (dupla)" : v);
      if (v === q.resposta) o.selected = true;
      sel.append(o);
    });
    const btn = el("button", { class: "btn btn-peq", type: "button" }, "O professor confirma");
    btn.addEventListener("click", () => {
      q.resposta = sel.value;
      q.conferida = true;
      q.pendente = false;
      q.acertou = q.resposta === q.gabarito;
      recalcular();
      mostrar();
      avisar("Questão " + q.q + " conferida. Nota e diagnóstico recalculados no navegador.");
    });
    li.append(sel, btn);
    return li;
  }

  entrada.addEventListener("change", () => entrada.files[0] && enviar(entrada.files[0]));
  botoes.forEach((b) => b.addEventListener("click", async () => {
    const nome = "exemplo_" + b.dataset.exemplo + "_foto.jpg";
    ocupar(true);
    try {
      const r = await fetch("/static/exemplos/" + nome);
      const blob = await r.blob();
      await enviar(new File([blob], nome, { type: "image/jpeg" }));
    } catch (e) {
      avisar("Não foi possível carregar a foto de exemplo.", true);
      ocupar(false);
    }
  }));
})();

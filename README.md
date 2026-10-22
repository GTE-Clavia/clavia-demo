# Clavia — demonstração pública

Demonstração da prova de conceito da **Clavia** (GTE Soluções, Rio de Janeiro): a prova em papel fotografada pelo professor vira leitura por visão computacional e microdiagnóstico por habilidade da BNCC. Pré-correção por IA com grau de certeza por questão; a nota e a decisão são sempre do professor.

- App FastAPI com a POC real de correção (OpenCV), páginas `/` (institucional), `/demo` (folha → foto → correção → microdiagnóstico → turma → Agir) e `/evidencia` (bancada sintética).
- Nenhuma foto é gravada: processamento em memória, sem cadastro, cookies ou analytics. Não envie dados pessoais nem fotos de rosto.
- Como rodar, publicar (Render) e o que o código garante: [`README_DEPLOY.md`](README_DEPLOY.md).

Projeto submetido ao Programa Centelha 3 RJ (Edital FAPERJ 12/2026), Fase 2. Todos os direitos reservados © 2026 GTE Projetos e Soluções Ltda.

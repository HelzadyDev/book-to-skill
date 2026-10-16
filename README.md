# book-to-skill

Uma skill do Claude que transforma um **livro em PDF** em outra skill do Claude, guardando o **texto integral, sem resumir**, dividido por capítulos e com índice. A skill gerada serve para **consultar** o que o livro diz e **aplicar** o método do livro em tarefas.

## Como funciona

O texto nunca passa por reescrita do modelo. Scripts extraem e copiam o conteúdo página a página; o Claude só escolhe nome, descrição e capítulos. No fim, um verificador confere se cada página está presente e idêntica ao texto extraído.

```
PDF ──extract_book.py──▶ texto por página (OCR nas páginas escaneadas)
    ──build_skill.py───▶ skill do livro: SKILL.md + references/ por capítulo + índice
    ──verify_coverage.py▶ confirma: nenhuma página perdida ou alterada
```

A skill gerada tem um `SKILL.md` curto (modo consulta e modo aplicação), `references/00-indice.md` e um arquivo de texto por capítulo, com marcadores `[p. N]`. Capítulos muito grandes são divididos em partes. Páginas com figuras podem ser salvas como imagem em `assets/figures/`.

## Requisitos

- Python 3.9+ e `pypdf` (`pip install pypdf`)
- poppler-utils (`pdftotext`, `pdfinfo`, `pdftoppm`, `pdfimages`)
- Para PDFs escaneados: `tesseract-ocr` e o idioma do livro

```bash
# Debian/Ubuntu
sudo apt install poppler-utils tesseract-ocr tesseract-ocr-por
```

## Instalação

**Claude Code:** copie a pasta da skill.
```bash
cp -r book-to-skill ~/.claude/skills/
```

**Claude.ai:** compacte a pasta `book-to-skill/` e envie o arquivo na área de Skills das configurações (ou use o `.skill` da página de Releases).

## Uso

Com a skill instalada, envie o PDF e peça, por exemplo: "transforme este livro em uma skill, sem resumir". O Claude segue o fluxo do `SKILL.md`.

Também dá para rodar os scripts direto:

```bash
python book-to-skill/scripts/extract_book.py livro.pdf --workdir work/livro --lang por+eng
python book-to-skill/scripts/build_skill.py --workdir work/livro --pdf livro.pdf --out skills \
  --name nome-do-livro --title "Título do Livro" --author "Autor" \
  --description "Do que o livro trata e quando usar a skill"
python book-to-skill/scripts/verify_coverage.py --workdir work/livro --skill-dir skills/nome-do-livro
```

## Limitações

- **OCR e acentos:** se o idioma do livro não estiver instalado no tesseract, o OCR perde acentos. Por isso a extração **bloqueia** livros escaneados nesse caso (código de saída 3). Use `--allow-fallback-lang` só se aceitar o risco.
- Tabelas, colunas e fórmulas podem perder a formatação na extração de texto.
- Livros muito grandes geram `.skill` pesados e podem passar do limite de upload da sua conta.

## Segurança

Os scripts não usam shell, rede nem `eval`, rodam subprocessos com timeout e tratam o PDF como entrada não confiável. Nomes de arquivo e de skill são sanitizados.

## Direitos autorais

Livros costumam ser protegidos por direitos autorais. Gere skills apenas de obras que você tenha direito de usar e **não publique** a skill gerada nem o PDF. O `.gitignore` deste repositório já ignora `*.pdf`, `skills/` e `work/`.

## Licença

MIT. Veja [LICENSE](LICENSE).

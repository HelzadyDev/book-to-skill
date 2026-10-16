#!/usr/bin/env python3
"""Monta a skill de um livro a partir das páginas extraídas por extract_book.py.

O texto das páginas é copiado SEM alteração para references/*.md (dividido por
capítulo e, se muito grande, em partes nas fronteiras de página). Cada página
recebe um marcador `[p. N]`. Nenhuma etapa resume ou reescreve o conteúdo.

Uso:
    python build_skill.py --workdir WORK --pdf livro.pdf --out /home/claude/skills \
        --name nome-da-skill --title "Título do Livro" --author "Autor" \
        --description "Quando usar esta skill ..."
"""
import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

MARKER_RE = re.compile(r"^\[p\. (\d+)\](?: \[figura: [^\]]+\])?$")
LEADER_RE = re.compile(r"(\.{2,}|…|_{2,})\s*\d+\s*$")
HEAD_RE = re.compile(r"^\s*(cap[ií]tulo|chapter|parte|part)\s+([0-9]{1,3}|[IVXLC]{1,7})\b.*$", re.I)


def slugify(text: str, limit=40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (text[:limit].strip("-")) or "secao"


def clean_title(t: str) -> str:
    t = re.sub(r"[\x00-\x1f]", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:120] or "Sem título"


def parse_pages_arg(s: str, n: int) -> list:
    out = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise SystemExit(f"Lista de páginas inválida: {part!r}")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        out.update(p for p in range(a, b + 1) if 1 <= p <= n)
    return sorted(out)


# ---------- detecção de capítulos ----------
def outline_chapters(pdf: Path, n: int):
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf))
        outline = reader.outline
    except Exception:
        return None
    acc = []

    def walk(items, depth):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
            else:
                try:
                    pg = reader.get_destination_page_number(it) + 1
                    acc.append((depth, clean_title(str(it.title)), pg))
                except Exception:
                    continue

    walk(outline, 0)
    depths = sorted({d for d, _, _ in acc})
    good = [d for d in depths if 4 <= sum(1 for e in acc if e[0] == d) <= 150]
    if not good:
        return None
    d = good[0]
    if sum(1 for e in acc if e[0] == d) < 12:
        deeper = [x for x in good if x > d]
        if deeper:
            d = deeper[0]
    entries = sorted((pg, t) for dd, t, pg in acc if dd == d and 1 <= pg <= n)
    seen, starts = set(), []
    for pg, t in entries:
        if pg not in seen:
            seen.add(pg)
            starts.append((t, pg))
    return starts if len(starts) >= 3 else None


def regex_chapters(pages: list):
    starts = []
    for i, text in enumerate(pages, 1):
        lines = [l for l in text.split("\n") if l.strip()]
        if sum(1 for l in lines if HEAD_RE.match(l) and len(l) <= 80) >= 3:
            continue  # página de sumário
        lines = [l for l in lines if not LEADER_RE.search(l)]  # linhas de sumário com pontilhado
        for l in lines[:6]:
            if HEAD_RE.match(l) and len(l.strip()) <= 80:
                starts.append((clean_title(l), i))
                break
    return starts if len(starts) >= 3 else None


def window_chapters(n: int, w: int):
    return [(f"Páginas {s}–{min(s + w - 1, n)}", s) for s in range(1, n + 1, w)]


def to_ranges(starts: list, n: int) -> list:
    starts = sorted(starts, key=lambda x: x[1])
    if starts[0][1] > 1:
        starts.insert(0, ("Pré-textual (capa, sumário, prefácio)", 1))
    out = []
    for i, (t, s) in enumerate(starts):
        e = (starts[i + 1][1] - 1) if i + 1 < len(starts) else n
        out.append((t, s, e))
    return out


# ---------- cabeçalhos para o índice (trechos copiados, não resumos) ----------
def detect_headings(page_texts: list, limit=25) -> list:
    counts = Counter()
    cand = []
    for t in page_texts:
        for line in t.split("\n"):
            s = line.strip()
            if not (3 <= len(s) <= 90) or s[-1] in ".,;:" or s.isdigit():
                continue
            letters = [c for c in s if c.isalpha()]
            numbered = re.match(r"^\d+(\.\d+)*\.?\s+[A-ZÀ-Ú]", s)
            upper = len(letters) >= 3 and s.upper() == s
            if numbered or upper:
                counts[s] += 1
                cand.append(s)
    min_run = max(3, int(len(page_texts) * 0.25))
    out, seen = [], set()
    for s in cand:
        if s in seen or (counts[s] >= 3 and counts[s] >= min_run):
            continue
        seen.add(s)
        out.append(s)
    return out[:limit]


# ---------- figuras ----------
def rasterize(pdf: Path, pages: list, out_dir: Path, dpi=110) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    made = {}
    for p in pages:
        prefix = out_dir / f"tmp-{p}"
        r = subprocess.run(["pdftoppm", "-jpeg", "-r", str(dpi), "-f", str(p), "-l", str(p), str(pdf), str(prefix)],
                           capture_output=True, text=True, timeout=120)
        src = sorted(out_dir.glob(f"tmp-{p}-*.jpg"))
        if r.returncode == 0 and src:
            dest = out_dir / f"p-{p:04d}.jpg"
            src[0].replace(dest)
            made[p] = f"assets/figures/{dest.name}"
        for extra in out_dir.glob(f"tmp-{p}-*.jpg"):
            extra.unlink()
    return made


# ---------- geração ----------
SKILL_TEMPLATE = """---
name: {name}
description: {description}
---

# {title}{author_part}

Esta skill contém o **texto integral** do livro «{title}» ({n_pages} páginas), dividido por capítulo em `references/`. Nada foi resumido: os arquivos reproduzem o texto extraído do PDF página a página, com marcadores `[p. N]` indicando a página do PDF. Use-a para **consultar** o que o livro diz e para **aplicar** o método do livro em tarefas.

> Conteúdo protegido por direitos autorais: mantenha esta skill para uso pessoal ou com autorização do titular.

## Como usar

Primeiro identifique o modo do pedido (muitos pedidos misturam os dois).

### Modo consulta: "o que o livro diz sobre X?"
1. Abra `references/00-indice.md` e escolha a seção pelo mapa e pelos cabeçalhos detectados. O índice só localiza; ele não substitui a leitura.
2. Leia **por inteiro** o(s) arquivo(s) do capítulo escolhido. Responder de memória ou só pelo índice é o caminho para erros e omissões.
3. Responda com base no texto lido, citando o título do capítulo e a página (ex.: "Capítulo 3, p. 41").
4. Se o livro não trata do assunto, diga isso. Não complete com conhecimento externo sem avisar que ele não vem do livro.
5. Cite trechos curtos entre aspas, com página. Para o restante, explique com precisão e fidelidade em vez de copiar passagens longas.

### Modo aplicação: "use o método do livro para fazer Y"
1. Leia os capítulos que definem o método e também os que trazem exceções, avisos e exemplos.
2. Antes de adaptar, liste o que o livro prescreve: passos, critérios, definições, condições e ordem, como aparecem no texto.
3. Aplique à tarefa do usuário. Separe sempre **"o que o livro prescreve"** (com página) de **"minha adaptação"**.
4. Confira se alguma exceção ou ressalva do autor muda a aplicação.
5. Se a tarefa sair do escopo do livro, diga onde o livro termina e onde começa a sua extrapolação.

### Pedidos que cruzam capítulos
Leia todos os capítulos envolvidos antes de responder. Se o ponto estiver espalhado, use o índice para listar os candidatos e confirme lendo.

## Regras de fidelidade
- O texto em `references/` é a fonte de verdade. Em conflito com sua memória sobre o livro, prevalece o arquivo.
- Não atribua ao autor o que não está no texto lido.
- Se uma página parecer incompleta (tabela, figura, equação), veja "Limitações" abaixo antes de concluir.

## Limitações do texto extraído
{limitations}

## Mapa de capítulos
{chapter_map}
"""


def write_reference_files(ranges, pages, refs_dir, max_chars, figure_map, title):
    refs_dir.mkdir(parents=True, exist_ok=True)
    width = max(2, len(str(len(ranges))))
    chapters = []
    for idx, (ctitle, s, e) in enumerate(ranges, 1):
        slug = slugify(ctitle)
        parts, cur, size = [], [], 0
        for p in range(s, e + 1):
            block_len = len(pages[p - 1]) + 20
            if cur and size + block_len > max_chars:
                parts.append(cur)
                cur, size = [], 0
            cur.append(p)
            size += block_len
        if cur:
            parts.append(cur)
        files = []
        for j, plist in enumerate(parts, 1):
            fname = f"{idx:0{width}d}-{slug}" + (f"-parte{j}" if len(parts) > 1 else "") + ".md"
            head = [f"# {ctitle}", f"> {title} · páginas {plist[0]}–{plist[-1]}"
                    + (f" · parte {j}/{len(parts)} do capítulo (p. {s}–{e})" if len(parts) > 1 else ""), ""]
            body = []
            for p in plist:
                marker = f"[p. {p}]" + (f" [figura: {figure_map[p]}]" if p in figure_map else "")
                body += [marker, pages[p - 1], ""]
            (refs_dir / fname).write_text("\n".join(head + body), encoding="utf-8")
            files.append(fname)
        chapters.append({"n": idx, "title": ctitle, "start": s, "end": e, "files": files,
                         "headings": detect_headings(pages[s - 1:e])})
    return chapters


def write_index(chapters, refs_dir, title):
    lines = [f"# Índice do livro «{title}»", "",
             "Gerado automaticamente. Os cabeçalhos listados são linhas copiadas do texto (não são resumos). "
             "Use este índice só para localizar; leia o capítulo para responder.", "",
             "| Seção | Título (como no livro) | Páginas | Arquivo(s) |", "|---|---|---|---|"]
    for c in chapters:
        files = ", ".join(f"`{f}`" for f in c["files"])
        lines.append(f"| {c['n']} | {c['title']} | {c['start']}–{c['end']} | {files} |")
    lines.append("")
    for c in chapters:
        lines += [f"## {c['n']}. {c['title']} (p. {c['start']}–{c['end']})", ""]
        if c["headings"]:
            lines += ["Cabeçalhos detectados:"] + [f"- {h}" for h in c["headings"]]
        else:
            lines.append("(nenhum cabeçalho detectado automaticamente)")
        lines.append("")
    (refs_dir / "00-indice.md").write_text("\n".join(lines), encoding="utf-8")


def build_limitations(report, figure_map, total):
    items = []
    if report["ocr_pages"]:
        items.append(f"- {len(report['ocr_pages'])} página(s) vieram de OCR (idioma `{report['ocr_lang']}`) e podem ter "
                     "erros de caracteres. Em números, fórmulas e nomes próprios, desconfie e avise o usuário.")
    if report.get("review_pages"):
        rp = report["review_pages"]
        shown = ", ".join(map(str, rp[:30])) + ("…" if len(rp) > 30 else "")
        items.append(f"- Páginas para revisão (texto ausente ou OCR incerto): {shown}.")
    elif report.get("empty_pages"):
        items.append(f"- {len(report['empty_pages'])} página(s) sem texto (provavelmente em branco ou só figura).")
    if figure_map:
        items.append(f"- {len(figure_map)} página(s) com figuras foram salvas como imagem em `assets/figures/`; o marcador "
                     "`[figura: ...]` aponta o arquivo. Abra a imagem quando a resposta depender da figura.")
    items.append("- Tabelas, colunas, fórmulas e figuras podem perder a formatação na extração de texto. "
                 "Se um trecho parecer truncado ou fora de ordem, diga isso ao usuário em vez de adivinhar.")
    return "\n".join(items)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True, help="pasta onde será criada <name>/")
    ap.add_argument("--name", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", default="")
    ap.add_argument("--description", required=True)
    ap.add_argument("--chapters", default="auto", choices=["auto", "outline", "regex", "window"])
    ap.add_argument("--window", type=int, default=20)
    ap.add_argument("--max-chars", type=int, default=40000)
    ap.add_argument("--figure-pages", default="auto", help="auto | none | lista (ex.: 12,45-47)")
    a = ap.parse_args()

    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", a.name) or len(a.name) > 64:
        raise SystemExit("--name deve ter só minúsculas, números e hífens (máx. 64).")
    if len(a.description) > 1024:
        raise SystemExit("A descrição passa de 1024 caracteres.")

    work = Path(a.workdir)
    report = json.loads((work / "report.json").read_text(encoding="utf-8"))
    if report.get("status") != "ok":
        raise SystemExit("A extração não terminou com status ok.")
    n = report["total_pages"]
    width = report["page_file_width"]
    pages = [(work / "pages" / f"{i:0{width}d}.txt").read_text(encoding="utf-8") for i in range(1, n + 1)]

    pdf = Path(a.pdf)
    starts, method = None, None
    if a.chapters in ("auto", "outline"):
        starts, method = outline_chapters(pdf, n), "marcadores do PDF"
    if not starts and a.chapters in ("auto", "regex"):
        starts, method = regex_chapters(pages), "títulos 'Capítulo N' no texto"
    if not starts:
        starts, method = window_chapters(n, a.window), f"janelas de {a.window} páginas (sem capítulos detectados)"
    ranges = to_ranges(starts, n)

    skill_dir = Path(a.out) / a.name
    refs_dir = skill_dir / "references"
    if refs_dir.exists():
        for old in refs_dir.glob("*.md"):
            old.unlink()

    if a.figure_pages == "none":
        fig_list = []
    elif a.figure_pages == "auto":
        fig_list = report.get("figure_pages", [])[:150]
    else:
        fig_list = parse_pages_arg(a.figure_pages, n)
    figure_map = rasterize(pdf, fig_list, skill_dir / "assets" / "figures") if fig_list else {}

    chapters = write_reference_files(ranges, pages, refs_dir, a.max_chars, figure_map, a.title)
    write_index(chapters, refs_dir, a.title)

    map_lines = [f"- Seção {c['n']} — {c['title']} (p. {c['start']}–{c['end']}) → "
                 + ", ".join(f"`references/{f}`" for f in c["files"]) for c in chapters]
    if len(map_lines) > 100:
        map_lines = [f"O livro tem {len(chapters)} seções. Consulte `references/00-indice.md` para o mapa completo."]
    skill_md = SKILL_TEMPLATE.format(
        name=a.name, description=json.dumps(a.description.replace("\n", " "), ensure_ascii=False), title=a.title,
        author_part=f" — {a.author}" if a.author else "", n_pages=n,
        limitations=build_limitations(report, figure_map, n), chapter_map="\n".join(map_lines))
    skill_dir.joinpath("SKILL.md").write_text(skill_md, encoding="utf-8")

    print(json.dumps({"skill_dir": str(skill_dir), "chapter_method": method, "chapters": len(chapters),
                      "reference_files": sum(len(c["files"]) for c in chapters),
                      "figures_saved": len(figure_map)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())

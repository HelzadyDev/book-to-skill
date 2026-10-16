#!/usr/bin/env python3
"""Extrai o texto INTEGRAL de um livro em PDF, página por página.

Nada é resumido nem reescrito: o texto de cada página é gravado em
<workdir>/pages/NNNN.txt. Páginas sem camada de texto (escaneadas) passam por OCR.

Uso:
    python extract_book.py livro.pdf --workdir /home/claude/work/livro [--lang por+eng]

Saída: <workdir>/pages/*.txt e <workdir>/report.json
Códigos de saída: 0 ok | 2 erro de entrada | 3 OCR bloqueado (idioma ausente)

Segurança: o PDF é tratado como entrada não confiável. Todos os subprocessos usam
lista de argumentos (sem shell), com timeout, e nenhum conteúdo do PDF é executado.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CMD_TIMEOUT = 300
PAGE_TIMEOUT = 180


def run(cmd, timeout=CMD_TIMEOUT):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def pdf_info(pdf: Path) -> dict:
    r = run(["pdfinfo", str(pdf)])
    if r.returncode != 0:
        raise SystemExit(f"pdfinfo falhou (PDF protegido por senha ou corrompido?): {r.stderr.strip()[:300]}")
    info = {}
    for line in r.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    if "Pages" not in info:
        raise SystemExit("Não consegui ler o número de páginas do PDF.")
    info["Pages"] = int(info["Pages"])
    return info


def normalize(text: str) -> str:
    """Normalização mínima e sem perda de conteúdo."""
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def text_layer(pdf: Path, n_pages: int, layout: bool) -> list:
    cmd = ["pdftotext", "-enc", "UTF-8"]
    if layout:
        cmd.append("-layout")
    cmd += [str(pdf), "-"]
    r = run(cmd, timeout=CMD_TIMEOUT * 4)
    if r.returncode != 0:
        raise SystemExit(f"pdftotext falhou: {r.stderr.strip()[:300]}")
    pages = r.stdout.split("\f")
    pages = pages[:n_pages] + [""] * max(0, n_pages - len(pages))
    return pages


def looks_garbled(text: str) -> bool:
    if "(cid:" in text:
        return True
    return bool(text) and text.count("\ufffd") / len(text) > 0.02


def tesseract_langs() -> set:
    if not shutil.which("tesseract"):
        return set()
    r = run(["tesseract", "--list-langs"])
    return {l.strip() for l in r.stdout.splitlines()[1:] if l.strip()}


def ocr_page(pdf: Path, n: int, lang: str, dpi: int) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "p"
        try:
            r = run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(n), "-l", str(n), str(pdf), str(prefix)],
                    timeout=PAGE_TIMEOUT)
            imgs = sorted(Path(tmp).glob("p*.png"))
            if r.returncode != 0 or not imgs:
                return ""
            t = run(["tesseract", str(imgs[0]), "stdout", "-l", lang], timeout=PAGE_TIMEOUT)
            return t.stdout if t.returncode == 0 else ""
        except subprocess.TimeoutExpired:
            return ""


def raster_figure_pages(pdf: Path) -> list:
    """Páginas com imagens raster grandes (fotos, gráficos). Usado para preservar figuras."""
    r = run(["pdfimages", "-list", str(pdf)])
    pages = set()
    for line in r.stdout.splitlines()[2:]:
        p = line.split()
        if len(p) > 4 and p[2] == "image" and p[0].isdigit():
            try:
                if int(p[3]) >= 150 and int(p[4]) >= 150:
                    pages.add(int(p[0]))
            except ValueError:
                continue
    return sorted(pages)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--lang", default="por+eng", help="idiomas do OCR (ex.: por+eng)")
    ap.add_argument("--allow-fallback-lang", action="store_true",
                    help="usar os idiomas de OCR disponíveis mesmo sem o pedido (pode perder acentos)")
    ap.add_argument("--layout", action="store_true", help="preservar layout (útil p/ tabelas)")
    ap.add_argument("--min-chars", type=int, default=25, help="abaixo disso a página é candidata a OCR")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()

    pdf = Path(a.pdf)
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        print("Arquivo de entrada inválido (esperado um .pdf).", file=sys.stderr)
        return 2
    work = Path(a.workdir)
    pages_dir = work / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for old in pages_dir.glob("*.txt"):
        old.unlink()

    info = pdf_info(pdf)
    n = info["Pages"]
    width = max(4, len(str(n)))
    raw = [normalize(t) for t in text_layer(pdf, n, a.layout)]

    need_ocr = [i for i, t in enumerate(raw, 1) if len(t) < a.min_chars or looks_garbled(t)]
    with_text = n - len(need_ocr)
    scanned_mode = with_text < n * 0.5
    warnings, ocr_pages, review_pages = [], [], []
    lang_used = None

    if need_ocr:
        if not shutil.which("tesseract"):
            warnings.append("tesseract não está instalado: páginas sem texto ficaram vazias.")
            review_pages = list(need_ocr)
        else:
            avail = tesseract_langs()
            wanted = a.lang.split("+")
            missing = [l for l in wanted if l not in avail]
            usable = [l for l in wanted if l in avail] or (["eng"] if "eng" in avail else [])
            if missing and scanned_mode and not a.allow_fallback_lang:
                report = {"status": "ocr_blocked", "missing_langs": missing, "available_langs": sorted(avail),
                          "total_pages": n, "pages_needing_ocr": len(need_ocr)}
                work.joinpath("report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(f"BLOQUEADO: o livro parece escaneado ({len(need_ocr)}/{n} páginas sem texto) e o "
                      f"idioma de OCR {missing} não está instalado (disponíveis: {sorted(avail)}).\n"
                      "Opções: instalar o pacote de idioma do tesseract; reexecutar com "
                      "--allow-fallback-lang (acentos podem se perder); ou transcrever as páginas "
                      "visualmente.", file=sys.stderr)
                return 3
            lang_used = "+".join(usable)
            if missing:
                warnings.append(f"Idiomas de OCR ausentes {missing}; usado '{lang_used}'. "
                                "Páginas OCR podem ter acentos/caracteres errados.")
            print(f"OCR em {len(need_ocr)} páginas (idioma: {lang_used})...", file=sys.stderr)

            def job(p):
                return p, normalize(ocr_page(pdf, p, lang_used, a.dpi))

            with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
                for done, (p, t) in enumerate(ex.map(job, need_ocr), 1):
                    if t:
                        raw[p - 1] = t
                        ocr_pages.append(p)
                    else:
                        review_pages.append(p)
                    if done % 25 == 0:
                        print(f"  OCR {done}/{len(need_ocr)}", file=sys.stderr)
            if missing and not scanned_mode:
                review_pages = sorted(set(review_pages) | set(ocr_pages))

    for i, t in enumerate(raw, 1):
        (pages_dir / f"{i:0{width}d}.txt").write_text(t, encoding="utf-8")

    empty = [i for i, t in enumerate(raw, 1) if not t]
    figures = [] if scanned_mode else [p for p in raster_figure_pages(pdf) if p not in ocr_pages]
    report = {
        "status": "ok", "pdf": pdf.name, "total_pages": n, "page_file_width": width,
        "title": info.get("Title", ""), "author": info.get("Author", ""),
        "scanned_mode": scanned_mode, "pages_with_text_layer": with_text,
        "ocr_pages": sorted(ocr_pages), "ocr_lang": lang_used, "empty_pages": empty,
        "review_pages": sorted(set(review_pages)), "figure_pages": figures,
        "total_chars": sum(len(t) for t in raw), "warnings": warnings,
    }
    work.joinpath("report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: (v if not isinstance(v, list) or len(v) <= 12 else f"{len(v)} páginas")
                      for k, v in report.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

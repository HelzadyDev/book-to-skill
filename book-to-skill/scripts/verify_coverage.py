#!/usr/bin/env python3
"""Confere que a skill gerada contém TODAS as páginas extraídas, sem alteração.

Uso:
    python verify_coverage.py --workdir WORK --skill-dir /home/claude/skills/nome-da-skill

Verifica: (1) cada página 1..N aparece exatamente uma vez nos arquivos de references/;
(2) o texto de cada página é idêntico ao extraído; (3) o SKILL.md tem frontmatter e
menos de 500 linhas. Código de saída 0 = íntegro, 1 = problemas encontrados.
"""
import argparse
import json
import re
import sys
from pathlib import Path

MARKER_RE = re.compile(r"^\[p\. (\d+)\](?: \[figura: [^\]]+\])?$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--skill-dir", required=True)
    a = ap.parse_args()

    work, skill = Path(a.workdir), Path(a.skill_dir)
    report = json.loads((work / "report.json").read_text(encoding="utf-8"))
    n, width = report["total_pages"], report["page_file_width"]
    problems = []

    found = {}
    for f in sorted((skill / "references").glob("*.md")):
        if f.name == "00-indice.md":
            continue
        current, buf = None, []

        def flush():
            if current is not None:
                if current in found:
                    problems.append(f"Página {current} duplicada ({f.name}).")
                found[current] = "\n".join(buf).strip()

        for line in f.read_text(encoding="utf-8").split("\n"):
            m = MARKER_RE.match(line)
            if m:
                flush()
                current, buf = int(m.group(1)), []
            elif current is not None:
                buf.append(line)
        flush()

    missing = [p for p in range(1, n + 1) if p not in found]
    if missing:
        problems.append(f"Páginas ausentes: {missing[:30]}{'…' if len(missing) > 30 else ''}")
    altered = []
    chars_ref = chars_src = 0
    for p in range(1, n + 1):
        src = (work / "pages" / f"{p:0{width}d}.txt").read_text(encoding="utf-8").strip()
        chars_src += len(src)
        if p in found:
            chars_ref += len(found[p])
            if found[p] != src:
                altered.append(p)
    if altered:
        problems.append(f"Texto diferente do extraído nas páginas: {altered[:30]}")

    sk = skill / "SKILL.md"
    if not sk.exists():
        problems.append("SKILL.md ausente.")
    else:
        txt = sk.read_text(encoding="utf-8")
        if not txt.startswith("---\n"):
            problems.append("SKILL.md sem frontmatter.")
        if txt.count("\n") + 1 >= 500:
            problems.append("SKILL.md com 500 linhas ou mais.")
    if not (skill / "references" / "00-indice.md").exists():
        problems.append("references/00-indice.md ausente.")

    print(f"Páginas no PDF: {n} | encontradas nas references: {len(found)}")
    print(f"Caracteres extraídos: {chars_src} | nas references: {chars_ref}")
    if report.get("empty_pages"):
        print(f"Aviso: {len(report['empty_pages'])} página(s) sem texto (em branco/figura): "
              f"{report['empty_pages'][:20]}")
    if problems:
        print("PROBLEMAS:")
        for p in problems:
            print(" -", p)
        return 1
    print("OK: todas as páginas presentes e idênticas ao texto extraído.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

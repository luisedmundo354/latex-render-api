import io
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path


def _run(cmd: list[str], cwd: Path, timeout_s: int) -> str:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_s,
    )
    return p.stdout


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except Exception:
        return ""


def _extract_magic_root(text: str) -> str | None:
    # Examples (common across editors):
    #   % !TEX root = main.tex
    #   % !TeX root=../thesis.tex
    m = re.search(r"(?im)^\s*%+\s*!\s*tex\s+root\s*=\s*(.+?)\s*$", text)
    if not m:
        return None
    root = m.group(1).strip().strip('"').strip("'")
    return root or None


def _pick_magic_root(workdir: Path) -> Path | None:
    workdir_resolved = workdir.resolve()
    hits: dict[Path, int] = {}

    for tex_path in workdir.rglob("*.tex"):
        text = _read_text(tex_path)
        if not text:
            continue
        root_rel = _extract_magic_root(text)
        if not root_rel:
            continue

        candidate = (tex_path.parent / root_rel).resolve()
        try:
            candidate.relative_to(workdir_resolved)
        except ValueError:
            continue

        if candidate.exists() and candidate.is_file():
            hits[candidate] = hits.get(candidate, 0) + 1

    if not hits:
        return None

    # Prefer the most-referenced root; break ties by shorter path.
    return max(hits.items(), key=lambda kv: (kv[1], -len(str(kv[0]))))[0]


def _score_tex_candidate(tex_path: Path, text: str) -> tuple[int, int]:
    # Higher score is better. Tie-breaker prefers shorter paths.
    score = 0
    name = tex_path.name.lower()

    if name == "main.tex":
        score += 50
    if "\\documentclass" in text:
        score += 40
    if "\\begin{document}" in text:
        score += 20
    if "\\end{document}" in text:
        score += 5

    return score, -len(str(tex_path))


def _pick_main_tex(workdir: Path) -> Path:
    # 1) If the project explicitly declares a root via magic comment, honor it.
    magic_root = _pick_magic_root(workdir)
    if magic_root is not None:
        return magic_root

    # 2) Common convention: main.tex at root
    main = workdir / "main.tex"
    if main.exists():
        return main

    # 3) Heuristics: pick the best-scoring .tex file
    tex_files = list(workdir.rglob("*.tex"))
    if not tex_files:
        raise RuntimeError("No .tex file found in the zip.")

    scored: list[tuple[int, int, Path]] = []
    for tex_path in tex_files:
        text = _read_text(tex_path)
        score, tie = _score_tex_candidate(tex_path, text)
        scored.append((score, tie, tex_path))

    scored.sort(reverse=True)
    return scored[0][2]


def compile_zip_bytes_to_pdf(zip_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "proj"
        workdir.mkdir(parents=True, exist_ok=True)

        # unzip
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
                z.extractall(workdir)
        except zipfile.BadZipFile:
            raise RuntimeError("Invalid zip file.")

        main_tex = _pick_main_tex(workdir)
        tex_dir = main_tex.parent
        stem = main_tex.stem
        tex_name = main_tex.name

        logs = ""
        try:
            # latexmk handles multi-pass compilation + bibliography tools more reliably than
            # hand-rolling pdflatex/biber/bibtex steps.
            logs += _run(
                [
                    "latexmk",
                    "-pdf",
                    f"-pdflatex=pdflatex -interaction=nonstopmode -halt-on-error -no-shell-escape -file-line-error %O %S",
                    tex_name,
                ],
                cwd=tex_dir,
                timeout_s=300,
            )
        except FileNotFoundError:
            # Fallback for environments that don't have latexmk installed.
            base_cmd = [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-no-shell-escape",
                "-file-line-error",
                tex_name,
            ]

            # pass 1
            logs += _run(base_cmd, cwd=tex_dir, timeout_s=90)

            # bibliography (biber if .bcf exists; else bibtex if .aux exists)
            bcf = tex_dir / f"{stem}.bcf"
            aux = tex_dir / f"{stem}.aux"
            if bcf.exists():
                logs += _run(["biber", stem], cwd=tex_dir, timeout_s=60)
            elif aux.exists():
                logs += _run(["bibtex", stem], cwd=tex_dir, timeout_s=60)

            # passes 2-3
            logs += _run(base_cmd, cwd=tex_dir, timeout_s=90)
            logs += _run(base_cmd, cwd=tex_dir, timeout_s=90)

        pdf_path = tex_dir / f"{stem}.pdf"
        if not pdf_path.exists():
            # Return tail of logs to help your agent fix errors quickly
            raise RuntimeError("PDF not produced. Log tail:\n" + logs[-8000:])

        return pdf_path.read_bytes()

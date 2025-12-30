import io
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


def _has_documentclass(path: Path) -> bool:
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return False
    return "\\documentclass" in text


def _pick_main_tex(workdir: Path) -> Path:
    # Convention: main.tex at root
    main = workdir / "main.tex"
    if main.exists():
        return main

    # Prefer files that declare \documentclass (likely entrypoints)
    docclass_tex = [p for p in workdir.rglob("*.tex") if _has_documentclass(p)]
    if len(docclass_tex) == 1:
        return docclass_tex[0]
    if len(docclass_tex) > 1:
        docclass_tex.sort(key=lambda p: len(str(p)))
        return docclass_tex[0]

    # If exactly one .tex at root, use it
    root_tex = list(workdir.glob("*.tex"))
    if len(root_tex) == 1:
        return root_tex[0]

    # Otherwise, find any .tex (prefer shortest path)
    all_tex = list(workdir.rglob("*.tex"))
    if not all_tex:
        raise RuntimeError("No .tex file found in the zip.")
    all_tex.sort(key=lambda p: len(str(p)))
    return all_tex[0]


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

        base_cmd = [
            "pdflatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-no-shell-escape",
            tex_name,
        ]

        logs = ""
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

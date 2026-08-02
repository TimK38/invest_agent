"""把 文章/原始/ 底下的 PDF / DOCX 抽成 .txt 放到 文章/

用法:
  python fetch/extract_articles.py          # 只處理尚未抽過的
  python fetch/extract_articles.py --force  # 全部重抽

新文章直接丟進 文章/原始/ 再跑一次即可。Claude 讀 .txt（比解 PDF 快很多）。
"""
import sys, re, html, zipfile, argparse, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import ARTICLES

SRC = ARTICLES / "原始"


def from_docx(p):
    z = zipfile.ZipFile(p)
    xml = z.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    txt = html.unescape(re.sub(r"<[^>]+>", "", xml))
    return "\n".join(l.strip() for l in txt.split("\n") if l.strip())


def from_pdf(p):
    from pypdf import PdfReader
    r = PdfReader(p)
    return "\n".join(t for t in (pg.extract_text() or "" for pg in r.pages) if t.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    SRC.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in SRC.iterdir() if p.suffix.lower() in (".pdf", ".docx"))
    if not files:
        sys.exit(f"{SRC} 沒有 PDF/DOCX")

    for p in files:
        out = ARTICLES / (p.stem + ".txt")
        if out.exists() and not a.force:
            print(f"  skip  {p.name}")
            continue
        try:
            txt = from_docx(p) if p.suffix.lower() == ".docx" else from_pdf(p)
        except Exception as e:
            print(f"  FAIL  {p.name}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        out.write_text(txt)
        print(f"  ok    {p.name}  ->  {out.name}  ({len(txt):,} 字)")


if __name__ == "__main__":
    main()

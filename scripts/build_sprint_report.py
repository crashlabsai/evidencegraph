# /// script
# requires-python = ">=3.13"
# dependencies = ["python-docx==1.2.0", "markdown-it-py==4.0.0"]
# ///
"""Fill the official sprint DOCX template from the marked Markdown research draft.

Template is supplied explicitly; no upload, publication or network request occurs.
Render the resulting DOCX to PDF with LibreOffice. Author and affiliation come from
the Markdown metadata. The template's title footnote and cover layout are kept.
"""

import argparse
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from markdown_it import MarkdownIt


def fill_inline(paragraph, children) -> None:
    bold = italic = False
    href = None
    for token in children or []:
        if token.type == "strong_open":
            bold = True
        elif token.type == "strong_close":
            bold = False
        elif token.type == "em_open":
            italic = True
        elif token.type == "em_close":
            italic = False
        elif token.type == "link_open":
            href = token.attrGet("href")
        elif token.type == "link_close":
            href = None
        elif token.type in {"text", "code_inline", "softbreak"}:
            text = " " if token.type == "softbreak" else token.content
            if href and href.startswith("https://"):
                relationship = paragraph.part.relate_to(
                    href,
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                    is_external=True,
                )
                hyperlink = OxmlElement("w:hyperlink")
                hyperlink.set(qn("r:id"), relationship)
                run = OxmlElement("w:r")
                props = OxmlElement("w:rPr")
                color = OxmlElement("w:color")
                color.set(qn("w:val"), "225C99")
                props.append(color)
                run.append(props)
                node = OxmlElement("w:t")
                node.text = text
                run.append(node)
                hyperlink.append(run)
                paragraph._p.append(hyperlink)
            else:
                run = paragraph.add_run(text)
                run.bold, run.italic = bold, italic
                if token.type == "code_inline":
                    run.font.name = "Liberation Mono"
                    run.font.size = Pt(9)


def all_table_paragraphs(table):
    seen = set()
    for row in table.rows:
        for cell in row.cells:
            if cell._tc in seen:
                continue
            seen.add(cell._tc)
            yield from cell.paragraphs
            for nested in cell.tables:
                yield from all_table_paragraphs(nested)


def collapse_author_placeholders(table, author_details: str) -> None:
    if table.cell(0, 0).text.startswith("Author name 1\n"):
        cell = table.cell(0, 0).merge(table.cell(len(table.rows) - 1, len(table.columns) - 1))
        cell.text = author_details
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        return
    seen = set()
    for row in table.rows:
        for cell in row.cells:
            if cell._tc in seen:
                continue
            seen.add(cell._tc)
            for nested in cell.tables:
                collapse_author_placeholders(nested, author_details)


def build(markdown: Path, template: Path, out: Path) -> None:
    text = markdown.read_text(encoding="utf-8")
    title = text.splitlines()[0].removeprefix("# ")
    metadata = dict(re.findall(r"^\*\*(Author|Affiliation):\*\* ([^\n]+)$", text, re.MULTILINE))
    if not all(metadata.get(field, "").strip() for field in ("Author", "Affiliation")):
        raise ValueError("Markdown must specify Author and Affiliation metadata")
    author_details = f"{metadata['Author']}\n{metadata['Affiliation']}"
    abstract = text.split("## Abstract\n", 1)[1].split("\n## ", 1)[0].strip()
    if len(abstract.split()) > 150:
        raise ValueError("abstract exceeds the sprint's 150-word limit")
    document = Document(template)
    if len(document.tables) != 2 or not any(
        p.text == "PROJECT TITLE" for p in all_table_paragraphs(document.tables[0])
    ):
        raise ValueError("unexpected official template structure; inspect before editing")
    cover = document.tables[0]
    collapse_author_placeholders(cover, author_details)
    for p in all_table_paragraphs(cover):
        if p.text == "PROJECT TITLE":
            for run in p.runs:  # leave the template's footnote reference run untouched
                if run.text == "PROJECT TITLE":
                    run.text = title
        elif p.text.startswith("Author name"):
            p.text = author_details if p.text.startswith("Author name 1\n") else ""
        elif p.text.startswith("Summarize your project"):
            p.text = abstract
            for run in p.runs:
                run.italic = False
    body = document.element.body
    for element in list(body):
        if element is not cover._tbl and element.tag != qn("w:sectPr"):
            body.remove(element)
    body_text = "## 1. Introduction" + text.split("## 1. Introduction", 1)[1]
    tokens = MarkdownIt().parse(body_text)
    heading = None
    list_number = None
    bulleted = False
    references = False
    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            heading = "Heading 2" if token.tag == "h2" else "Heading 3"
        elif token.type == "ordered_list_open":
            list_number = int(token.attrGet("start") or "1")
        elif token.type == "ordered_list_close":
            list_number = None
        elif token.type in {"bullet_list_open", "bullet_list_close"}:
            bulleted = token.type == "bullet_list_open"
        elif token.type == "fence":
            p = document.add_paragraph(style="normal")
            p.paragraph_format.left_indent = Inches(0.3)
            p.paragraph_format.space_after = Pt(7)
            run = p.add_run(token.content.rstrip("\n"))
            run.font.name = "Liberation Mono"
            run.font.size = Pt(9)
        elif token.type == "inline":
            previous = tokens[i - 1].type if i else ""
            if previous == "heading_open":
                p = document.add_paragraph(style=heading or "Heading 2")
                if token.content.startswith("Appendix A."):
                    p.paragraph_format.page_break_before = True
                fill_inline(p, token.children)
                references = token.content == "References"
                heading = None
            elif previous == "paragraph_open":
                images = [child for child in token.children or [] if child.type == "image"]
                if images:
                    for img in images:
                        path = markdown.parent / str(img.attrGet("src"))
                        p = document.add_paragraph()
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        p.paragraph_format.keep_with_next = True
                        p.add_run().add_picture(str(path), width=Inches(6.0))
                else:
                    p = document.add_paragraph(style="normal")
                    p.paragraph_format.space_after = Pt(7)
                    if list_number is not None:
                        p.add_run(f"{list_number}. ")
                        list_number += 1
                    elif bulleted:
                        p.paragraph_format.left_indent = Inches(0.3)
                        p.paragraph_format.first_line_indent = Inches(-0.2)
                        p.paragraph_format.space_after = Pt(3)
                        p.add_run("\u2022  ")
                    fill_inline(p, token.children)
                    if token.content.startswith("*Figure"):
                        for run in p.runs:
                            run.font.size = Pt(9)
                    if references and list_number is not None:
                        p.paragraph_format.space_after = Pt(3)
                        for run in p.runs:
                            run.font.size = Pt(10)
    out.parent.mkdir(parents=True, exist_ok=True)
    document.core_properties.title = title
    document.core_properties.author = metadata["Author"]
    document.core_properties.subject = "AI Incident Response Sprint 2026, Track 1 report"
    document.save(out)
    main = body_text.split("## References", 1)[0]
    print(
        f"Abstract: {len(abstract.split())} words; main body: {len(re.findall(r'\S+', main))} words"
    )
    print(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("template", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    build(args.markdown, args.template, args.out)

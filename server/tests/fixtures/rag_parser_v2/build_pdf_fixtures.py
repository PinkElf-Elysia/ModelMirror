"""Author-owned geometric fixtures; build-only reportlab, not a production dependency.

Run explicitly with --output-dir; tests read the frozen bytes. The page text,
coordinates and ruled grid are the independent oracle.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas


def canvas(path: Path) -> Canvas:
    return Canvas(str(path), pagesize=(612, 792), invariant=1, pageCompression=0)


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    pdf = canvas(output / "page_edges.pdf")
    for number in range(1, 4):
        pdf.setFont("Helvetica", 11)
        pdf.drawString(72, 764, "INTERNAL HEADER 42")
        pdf.drawString(72, 690, f"Page {number} approved body HARBOR-{number}.")
        pdf.drawString(72, 664, f"Page {number} distinct second paragraph.")
        pdf.drawString(72, 25, "PRIVATE FOOTER 42")
        pdf.showPage()
    pdf.save()

    for ambiguous in (False, True):
        pdf = canvas(output / ("ambiguous_columns.pdf" if ambiguous else "two_columns.pdf"))
        pdf.setFont("Helvetica", 11)
        for index in range(4):
            pdf.drawString(60, 710 - index * 24, f"Left {index + 1} evidence alpha.")
            pdf.drawString(340, 710 - index * 24, f"Right {index + 1} evidence beta.")
        if ambiguous:
            pdf.drawString(190, 675, "CROSS COLUMN OBJECT overlaps the gutter")
        pdf.showPage()
        pdf.save()

    pdf = canvas(output / "ruled_table.pdf")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(60, 735, "Inventory table")
    for x in (60, 260, 460):
        pdf.line(x, 580, x, 700)
    for y in (580, 620, 660, 700):
        pdf.line(60, y, 460, y)
    for row, values in enumerate((("Item", "Count"), ("Harbor", "42"), ("Spare", "7"))):
        for column, value in enumerate(values):
            pdf.drawString(70 + column * 200, 675 - row * 40, value)
    pdf.showPage()
    pdf.save()

    scan = Image.new("RGB", (600, 240), "white")
    ImageDraw.Draw(scan).text((35, 80), "IMAGE ONLY - HARBOR SCANNED RECORD", fill="black")
    pdf = canvas(output / "scanned.pdf")
    pdf.drawImage(ImageReader(scan), 60, 420, width=480, height=192)
    pdf.showPage()
    pdf.save()
    (output / "corrupt.pdf").write_bytes(b"%PDF-1.4\ninvalid non-document\n%%EOF\n")
    (output / "empty.txt").write_bytes(b"")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    build(parser.parse_args().output_dir)

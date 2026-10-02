"""Read a challenge PDF from the sanitized VM workspace, no answer files."""
import re
import sys
from pathlib import Path
from pypdf import PdfReader

root = Path(sys.argv[1]).resolve()
pattern = re.compile(r"[A-Za-z0-9_]{2,24}\{[^{}\n]{1,120}\}")
for path in root.glob("*.pdf"):
    reader = PdfReader(path)
    print(path.name, "pages", len(reader.pages))
    for index, page in enumerate(reader.pages):
        content = page.extract_text() or ""
        matches = pattern.findall(content)
        if matches:
            print("page", index + 1, "flags", matches)
        elif index < 3:
            print("page", index + 1, "preview", content[:250].replace("\n", " "))

from pathlib import Path
import re

FILES = [
    Path("/home/deploy/voos/main.py"),
    Path("/home/deploy/voos/maxmilhas.py"),
    Path("/home/deploy/voos/skyscanner.py"),
    Path("/home/deploy/voos/skyscanner_monitor.py"),
    Path("/home/deploy/voos/update_scraper.py"),
]

typing_import = "from typing import List, Dict, Tuple, Optional\n"

def add_typing_import(text: str) -> str:
    if "from typing import " in text:
        return text
    lines = text.splitlines(keepends=True)
    insert_at = 0

    if lines and lines[0].startswith("from __future__ import"):
        insert_at = 1
        while insert_at < len(lines) and lines[insert_at].startswith("from __future__ import"):
            insert_at += 1

    lines.insert(insert_at, typing_import)
    return "".join(lines)

def transform(text: str) -> str:
    text = re.sub(r'\blist\[(.*?)\]', r'List[\1]', text)
    text = re.sub(r'\bdict\[(.*?)\]', r'Dict[\1]', text)
    text = re.sub(r'\btuple\[(.*?)\]', r'Tuple[\1]', text)

    text = re.sub(r'\bstr \| None\b', 'Optional[str]', text)
    text = re.sub(r'\bfloat \| None\b', 'Optional[float]', text)
    text = re.sub(r'\bint \| None\b', 'Optional[int]', text)
    text = re.sub(r'\bbool \| None\b', 'Optional[bool]', text)

    text = re.sub(r'\bList\[(.*?)\] \| None\b', r'Optional[List[\1]]', text)
    text = re.sub(r'\bDict\[(.*?)\] \| None\b', r'Optional[Dict[\1]]', text)
    text = re.sub(r'\bTuple\[(.*?)\] \| None\b', r'Optional[Tuple[\1]]', text)

    return text

for path in FILES:
    text = path.read_text()
    text = add_typing_import(text)
    text = transform(text)
    path.write_text(text)

print("OK")

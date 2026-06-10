"""Message classification logic — port of the n8n Classify Message Code-node.

Same regex patterns and same rules:
  - parse UK postcodes
  - detect priority/depart/cancel/ETA keywords
  - decide what action to take
"""
import re
from dataclasses import dataclass


POSTCODE_RE = re.compile(
    r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b",
    re.IGNORECASE,
)
PRIORITY_RE = re.compile(
    r"(asap|срочн|приорит|пр[іi]орит|urgent|priority|важн|терміново|терминово)",
    re.IGNORECASE,
)
DEPART_RE = re.compile(
    r"(вы[еї]хал|вы[еї]хала|вы[еї]зжаю|виїхав|виїхала|виїжджаю|стартую|"
    r"поехал|поехала|поїхав|поїхала|в\s*дорозі|майже\s*на\s*доставці|"
    r"en\s*route|on\s*the\s*way|departed|leaving)",
    re.IGNORECASE,
)
CANCEL_RE = re.compile(
    r"(отменил[иa]?|скасован[оi]?|скасували|убрали|cancel(?:led)?|removed?)",
    re.IGNORECASE,
)
ETA_QUERY_RE = re.compile(r"\bет[aа]\b|\beta\b", re.IGNORECASE)
NOTE_RE = re.compile(r"\(([^)]+)\)")


@dataclass
class Stop:
    """One delivery stop."""
    code: str
    priority: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "priority": self.priority, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "Stop":
        return cls(
            code=d["code"],
            priority=bool(d.get("priority", False)),
            note=d.get("note", ""),
        )


@dataclass
class FoundPostcode:
    code: str
    line: str


def find_postcodes(text: str) -> list[FoundPostcode]:
    """Return all postcodes in text, each with the line it appeared on
    (for per-line priority detection)."""
    out: list[FoundPostcode] = []
    for m in POSTCODE_RE.finditer(text):
        idx = m.start()
        line_start = text.rfind("\n", 0, idx) + 1
        line_end_idx = text.find("\n", idx)
        line = text[line_start:] if line_end_idx == -1 else text[line_start:line_end_idx]
        code = f"{m.group(1)} {m.group(2)}".upper()
        code = re.sub(r"\s+", " ", code)
        out.append(FoundPostcode(code=code, line=line))
    return out


def extract_note(line: str) -> str:
    """Get content of first (...) in line, or empty string."""
    m = NOTE_RE.search(line)
    return m.group(1).strip() if m else ""


def is_short_message(text: str, max_chars: int = 120, max_lines: int = 2) -> bool:
    """Short = ≤max_chars and ≤max_lines non-empty lines."""
    non_empty = sum(1 for ln in text.split("\n") if ln.strip())
    return len(text) <= max_chars and non_empty <= max_lines


def looks_like_list(found: list[FoundPostcode], text: str) -> bool:
    """A 'list' is ≥2 postcodes OR 1 postcode + at least one newline (column layout)."""
    return len(found) >= 2 or (len(found) >= 1 and "\n" in text)

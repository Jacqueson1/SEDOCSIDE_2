"""
template_parser.py

Reads a template file (like SRS_Template_v2.md) and turns it into a list of
Section objects. No AI is used here, it only reads text.

To test it on its own (from the project folder, with the .venv active):
    python engine/template_parser.py
    python engine/template_parser.py templates/SRS_Template_v2.md
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Small patterns used to read the file
# ---------------------------------------------------------------------------

COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)      # <!-- a comment -->
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")     # "## 1.1 Purpose"
NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")    # "1.1 Purpose"
TAG_RE = re.compile(r"^@(\w+)\s*:\s*(.*)$")            # "@table: A | B"

KNOWN_TAGS = {"type", "source", "write", "after", "table"}


# ---------------------------------------------------------------------------
# The data we produce
# ---------------------------------------------------------------------------

class TemplateError(Exception):
    """Raised when the template file has a mistake we cannot ignore."""


@dataclass
class Section:
    number: str                  # "2.3.1"
    title: str                   # "Users Definition"
    level: int                   # 1, 2 or 3 (how deep the heading is)
    instruction: str = ""        # the text for the AI agent
    kind: str = "normal"         # "normal", "owner" or "heading_only"
    source: str = ""             # "scanner" or ""
    write_last: bool = False     # True when the template says "@write: last"
    after: str = ""              # number of a section that must be written first
    table_columns: list[str] = field(default_factory=list)
    parent: str = ""             # number of the parent section ("" if none)
    children: list[str] = field(default_factory=list)
    line: int = 0                # line number in the file (for error messages)


@dataclass
class Template:
    name: str                    # file name, for example "SRS_Template_v2.md"
    global_rules: str            # the text of the GLOBAL RULES block
    sections: list[Section]
    warnings: list[str]

    def find(self, number):
        """Return the section with this number (like "3.3"), or None."""
        for section in self.sections:
            if section.number == number:
                return section
        return None


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

def parse_template(path):
    """Read a template file and return a Template object."""
    path = Path(path)
    if not path.exists():
        raise TemplateError(f"Template file not found: {path}")

    raw_text = path.read_text(encoding="utf-8-sig")
    text = _remove_comments(raw_text)
    return _parse_text(text, name=path.name)


def _remove_comments(text):
    """Remove <!-- comments --> but keep the line count the same,
    so the line numbers in error messages stay correct."""
    return COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _split_blocks(text, warnings):
    """Cut the text into blocks. One block = one heading + the lines under it."""
    blocks = []
    current = None

    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("#"):
            match = HEADING_RE.match(line)
            if not match:
                raise TemplateError(
                    f"Line {line_no}: a heading needs a space after the # sign. "
                    f"Found: {line!r}"
                )
            current = {
                "line": line_no,
                "hashes": len(match.group(1)),
                "title": match.group(2),
                "body": [],
            }
            blocks.append(current)
        elif current is None:
            if line.strip():
                warnings.append(
                    f"Line {line_no}: text before the first heading is ignored"
                )
        else:
            current["body"].append((line_no, line))

    return blocks


def _read_body(body_lines, warnings):
    """Split the lines under a heading into:
    - tags: the @ settings, like {"table": ("A | B", 12)}
    - instruction: everything else (the text for the AI)."""
    tags = {}
    text_lines = []

    for line_no, line in body_lines:
        match = TAG_RE.match(line.strip())
        if match:
            key = match.group(1).lower()
            value = match.group(2).strip()
            if key in KNOWN_TAGS:
                tags[key] = (value, line_no)
            else:
                warnings.append(f"Line {line_no}: unknown setting '@{key}' (ignored)")
        else:
            text_lines.append(line)

    instruction = "\n".join(text_lines).strip()
    instruction = re.sub(r"\n{3,}", "\n\n", instruction)   # no big empty gaps
    return instruction, tags


def _expected_level(number):
    """'1.0' -> 1, '1.1' -> 2, '2.3.1' -> 3 (used to double-check the # signs)."""
    if re.fullmatch(r"\d+(\.0)?", number):
        return 1
    return number.count(".") + 1


def _parse_text(text, name):
    warnings = []
    blocks = _split_blocks(text, warnings)

    global_rules = ""
    sections = []
    by_number = {}
    stack = []      # remembers the parents of the section we are reading

    for block in blocks:
        line_no = block["line"]
        instruction, tags = _read_body(block["body"], warnings)
        type_value = tags.get("type", ("", 0))[0].lower()

        # The special GLOBAL RULES block (it has no section number)
        if type_value == "rules":
            global_rules = instruction
            continue

        # Every other heading must start with a section number
        match = NUMBER_RE.match(block["title"])
        if not match:
            raise TemplateError(
                f"Line {line_no}: the heading '{block['title']}' has no section "
                f"number. Example: '1.1 Purpose'. "
                f"(If this is the global rules block, add the line '@type: rules'.)"
            )

        number = match.group(1)
        title = match.group(2).strip()
        level = block["hashes"]

        if number in by_number:
            raise TemplateError(f"Line {line_no}: section {number} appears twice")

        section = Section(
            number=number,
            title=title,
            level=level,
            instruction=instruction,
            line=line_no,
        )

        # Apply the @ settings
        if "type" in tags:
            if type_value == "owner":
                section.kind = "owner"
            else:
                warnings.append(
                    f"Line {tags['type'][1]}: unknown @type '{type_value}' (ignored)"
                )

        if "source" in tags:
            source_value = tags["source"][0].lower()
            if source_value == "scanner":
                section.source = source_value
            else:
                warnings.append(
                    f"Line {tags['source'][1]}: unknown @source '{source_value}' (ignored)"
                )

        if "write" in tags:
            write_value = tags["write"][0].lower()
            if write_value == "last":
                section.write_last = True
            else:
                warnings.append(
                    f"Line {tags['write'][1]}: unknown @write '{write_value}' (ignored)"
                )

        if "after" in tags:
            section.after = tags["after"][0].strip()

        if "table" in tags:
            columns = tags["table"][0].split("|")
            section.table_columns = [c.strip() for c in columns if c.strip()]

        # Double-check the heading depth against the number
        expected = _expected_level(number)
        if expected != level:
            warnings.append(
                f"Line {line_no}: section {number} uses {level} '#' sign(s), "
                f"but its number suggests {expected}"
            )

        # Find the parent (the closest section above with a smaller level)
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            section.parent = stack[-1].number
            stack[-1].children.append(number)
        stack.append(section)

        by_number[number] = section
        sections.append(section)

    # Final checks, after we know all the sections
    for section in sections:
        if section.kind == "normal" and not section.instruction:
            if section.children:
                section.kind = "heading_only"
            else:
                warnings.append(
                    f"Section {section.number} ({section.title}) has no instruction"
                )
        if section.after and section.after not in by_number:
            warnings.append(
                f"Section {section.number}: '@after: {section.after}' "
                f"points to a section that does not exist"
            )

    if not global_rules:
        warnings.append("No GLOBAL RULES block found")

    return Template(
        name=name,
        global_rules=global_rules,
        sections=sections,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Showing the result (for testing in the terminal)
# ---------------------------------------------------------------------------

def print_outline(template):
    rules_lines = [l for l in template.global_rules.splitlines() if l.strip()]

    print(f"Template: {template.name}")
    print(f"Global rules: {len(rules_lines)} lines")
    print(f"Sections found: {len(template.sections)}")
    print()

    for s in template.sections:
        flags = []
        if s.kind == "owner":
            flags.append("owner")
        if s.kind == "heading_only":
            flags.append("heading only")
        if s.write_last:
            flags.append("write last")
        if s.after:
            flags.append(f"after {s.after}")
        if s.source:
            flags.append(s.source)
        if s.table_columns:
            flags.append("table: " + " | ".join(s.table_columns))

        indent = "  " * (s.level - 1)
        flag_text = f"   [{', '.join(flags)}]" if flags else ""
        print(f"{indent}{s.number} {s.title}{flag_text}")

    sections = template.sections
    print()
    print("Summary")
    print(f"  owner sections : {sum(1 for s in sections if s.kind == 'owner')}")
    print(f"  write last     : {sum(1 for s in sections if s.write_last)}")
    print(f"  after another  : {sum(1 for s in sections if s.after)}")
    print(f"  scanner        : {sum(1 for s in sections if s.source == 'scanner')}")
    print(f"  tables         : {sum(1 for s in sections if s.table_columns)}")
    print(f"  heading only   : {sum(1 for s in sections if s.kind == 'heading_only')}")

    print()
    if template.warnings:
        print("Warnings:")
        for warning in template.warnings:
            print(f"  - {warning}")
    else:
        print("No warnings.")


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    default_path = project_root / "templates" / "SRS_Template_v2.md"
    template_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_path

    try:
        result = parse_template(template_path)
    except TemplateError as error:
        print(f"ERROR: {error}")
        sys.exit(1)

    print_outline(result)
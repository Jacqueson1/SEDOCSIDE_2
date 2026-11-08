"""
scanner.py  (Round 1: the generic scanner)

Reads a software project folder and collects simple facts about it.
No AI is used here. It only READS files, it never changes anything
inside the project it scans.

What it collects:
  - files, main languages, and a short folder tree
  - dependencies (composer.json, package.json, requirements.txt)
  - frameworks and test tools it recognises
  - the start of the README
  - the key NAMES from .env.example (never the values)
  - how many test files exist

Safety rules:
  - heavy folders (vendor, node_modules, .git ...) are skipped
  - secret files (.env, keys, credentials ...) are counted but NEVER read

How to run (from the SEDOCSIDE_2 folder, with the .venv active):
    python engine/scanner.py "C:\\laragon\\www\\expense-tracker"
"""

import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Settings (easy to change)
# ---------------------------------------------------------------------------

# Folders we never go into
IGNORED_DIRS = {
    ".git", ".svn", ".hg", ".idea", ".vscode",
    "node_modules", "vendor", "storage",
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".cache",
    "dist", "build", "target", ".next", ".nuxt", "coverage",
}

# Files that are safe to read even though they start with ".env"
SAFE_ENV_EXAMPLES = {".env.example", ".env.sample", ".env.dist", ".env.template"}

SECRET_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
SECRET_NAMES = {"id_rsa", "id_ed25519", "auth.json"}
# "secret" or "credential" in a file name only counts for data-like files
DATA_EXTENSIONS = {".json", ".yml", ".yaml", ".txt", ".ini", ".xml", ".cfg", ".conf", ""}

MAX_FILES = 20000          # stop after this many files (very big projects)
MAX_README_CHARS = 3000    # how much of the README we keep
MAX_TREE_DEPTH = 2         # folder levels shown in the tree
MAX_TREE_LINES = 60

# File type -> language name (only the ones we want to report)
LANGUAGES = {
    ".php": "PHP", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".vue": "Vue",
    ".py": "Python", ".html": "HTML", ".css": "CSS", ".scss": "CSS",
    ".sass": "CSS", ".sql": "SQL", ".java": "Java", ".cs": "C#",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".kt": "Kotlin",
    ".dart": "Dart", ".c": "C", ".cpp": "C++",
}

# Package name -> friendly name
KNOWN_FRAMEWORKS = {
    "laravel/framework": "Laravel",
    "livewire/livewire": "Livewire",
    "inertiajs/inertia-laravel": "Inertia.js",
    "@inertiajs/vue3": "Inertia.js",
    "laravel/breeze": "Laravel Breeze",
    "laravel/sanctum": "Laravel Sanctum",
    "vue": "Vue",
    "react": "React",
    "next": "Next.js",
    "express": "Express",
    "tailwindcss": "Tailwind CSS",
    "bootstrap": "Bootstrap",
    "vite": "Vite",
    "laravel-mix": "Laravel Mix",
    "alpinejs": "Alpine.js",
    "chart.js": "Chart.js",
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
}

KNOWN_TEST_TOOLS = {
    "phpunit/phpunit": "PHPUnit",
    "pestphp/pest": "Pest",
    "jest": "Jest",
    "vitest": "Vitest",
    "mocha": "Mocha",
    "cypress": "Cypress",
    "@playwright/test": "Playwright",
    "pytest": "pytest",
}


# ---------------------------------------------------------------------------
# The data we produce
# ---------------------------------------------------------------------------

class ScanError(Exception):
    """Raised when the project cannot be scanned (for example, wrong path)."""


@dataclass
class FileInfo:
    path: Path        # path relative to the project folder
    size: int         # size in bytes


@dataclass
class DependencyInfo:
    source: str                                   # "composer.json"
    runtime: dict = field(default_factory=dict)   # {"php": "^8.2"}
    packages: dict = field(default_factory=dict)  # {"laravel/framework": "^11.0"}
    dev_packages: dict = field(default_factory=dict)


@dataclass
class ProjectSnapshot:
    name: str
    root: str
    file_count: int
    total_size_kb: int
    languages: dict
    skipped_folders: list
    skipped_secret_files: int
    top_level_files: list
    tree: list
    dependencies: list
    frameworks: list
    test_tools: list
    test_file_count: int
    readme_excerpt: str
    env_keys: list
    warnings: list


# ---------------------------------------------------------------------------
# Step 1: collect the files
# ---------------------------------------------------------------------------

def _is_secret_file(name):
    """True if a file might hold secrets. Such files are never read."""
    lower = name.lower()
    if lower in SAFE_ENV_EXAMPLES:
        return False
    if lower == ".env" or lower.startswith(".env."):
        return True
    if lower in SECRET_NAMES:
        return True
    extension = os.path.splitext(lower)[1]
    if extension in SECRET_EXTENSIONS:
        return True
    if ("secret" in lower or "credential" in lower) and extension in DATA_EXTENSIONS:
        return True
    return False


def collect_files(root):
    """Walk through the project and list its files.
    Returns: files, skipped folder names, number of secret files, truncated?"""
    files = []
    skipped_folders = set()
    secret_count = 0
    truncated = False

    for current_dir, dir_names, file_names in os.walk(root):
        # Remove ignored folders from the walk
        kept = []
        for name in dir_names:
            if name.lower() in IGNORED_DIRS:
                skipped_folders.add(name)
            else:
                kept.append(name)
        dir_names[:] = sorted(kept)

        for name in sorted(file_names):
            if _is_secret_file(name):
                secret_count += 1      # counted, but never opened
                continue

            full_path = Path(current_dir) / name
            try:
                size = full_path.stat().st_size
            except OSError:
                continue
            files.append(FileInfo(path=full_path.relative_to(root), size=size))

        if len(files) >= MAX_FILES:
            truncated = True
            break

    return files, sorted(skipped_folders), secret_count, truncated


# ---------------------------------------------------------------------------
# Step 2: simple facts from the file list
# ---------------------------------------------------------------------------

def _language_of(file_name):
    lower = file_name.lower()
    if lower.endswith(".blade.php"):
        return "Blade"
    extension = os.path.splitext(lower)[1]
    return LANGUAGES.get(extension)


def count_languages(files):
    counter = Counter()
    for f in files:
        language = _language_of(f.path.name)
        if language:
            counter[language] += 1
    return dict(counter.most_common())


def build_tree(files, root_name):
    """A short folder tree. Returns (tree lines, top-level file names)."""
    folder_counts = {}
    top_level_files = []

    for f in files:
        parts = f.path.parts
        if len(parts) == 1:
            top_level_files.append(parts[0])
            continue
        deepest = min(len(parts) - 1, MAX_TREE_DEPTH)
        for depth in range(1, deepest + 1):
            key = parts[:depth]
            folder_counts[key] = folder_counts.get(key, 0) + 1

    ordered = sorted(folder_counts, key=lambda k: tuple(p.lower() for p in k))

    lines = [f"{root_name}/"]
    for key in ordered:
        indent = "  " * len(key)
        lines.append(f"{indent}{key[-1]}/  ({folder_counts[key]} files)")
        if len(lines) >= MAX_TREE_LINES:
            lines.append("  ...")
            break

    return lines, sorted(top_level_files)[:40]


def count_test_files(files):
    count = 0
    test_folders = {"tests", "test", "__tests__", "spec"}
    test_endings = ("test.php", ".test.js", ".test.ts", ".spec.js", ".spec.ts", "_test.py")

    for f in files:
        if _language_of(f.path.name) is None:
            continue
        folders = [p.lower() for p in f.path.parts[:-1]]
        name = f.path.name.lower()
        in_test_folder = any(p in test_folders for p in folders)
        looks_like_test = name.startswith("test_") or name.endswith(test_endings)
        if in_test_folder or looks_like_test:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Step 3: read a few known files (dependencies, README, .env.example)
# ---------------------------------------------------------------------------

def _read_text(path):
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def read_composer(path, warnings):
    try:
        data = json.loads(_read_text(path))
    except (OSError, ValueError):
        warnings.append("composer.json could not be read (invalid JSON?)")
        return None

    require = _as_dict(data.get("require"))
    runtime = {}
    packages = {}
    for name, version in require.items():
        if name == "php" or name.startswith("ext-"):
            runtime[name] = version
        else:
            packages[name] = version

    return DependencyInfo(
        source="composer.json",
        runtime=runtime,
        packages=packages,
        dev_packages=_as_dict(data.get("require-dev")),
    )


def read_package_json(path, warnings):
    try:
        data = json.loads(_read_text(path))
    except (OSError, ValueError):
        warnings.append("package.json could not be read (invalid JSON?)")
        return None

    return DependencyInfo(
        source="package.json",
        runtime=_as_dict(data.get("engines")),
        packages=_as_dict(data.get("dependencies")),
        dev_packages=_as_dict(data.get("devDependencies")),
    )


REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(.*)$")


def read_requirements(path, warnings):
    try:
        text = _read_text(path)
    except OSError:
        warnings.append("requirements.txt could not be read")
        return None

    packages = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-") or "://" in line:
            continue
        match = REQUIREMENT_RE.match(line)
        if match:
            packages[match.group(1)] = match.group(2).split(";")[0].strip()

    return DependencyInfo(source="requirements.txt", packages=packages)


def read_readme(root, root_files):
    for name in ("readme.md", "readme.markdown", "readme.txt", "readme"):
        if name in root_files:
            try:
                with open(root / root_files[name], "r", encoding="utf-8", errors="replace") as f:
                    return f.read(MAX_README_CHARS).strip()
            except OSError:
                return ""
    return ""


ENV_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def read_env_key_names(root, root_files):
    """Only the NAMES of the settings. Values are never kept."""
    for name in (".env.example", ".env.sample", ".env.dist", ".env.template"):
        if name in root_files:
            try:
                text = _read_text(root / root_files[name])
            except OSError:
                return []
            keys = []
            for line in text.splitlines():
                if line.strip().startswith("#"):
                    continue
                match = ENV_KEY_RE.match(line)
                if match:
                    keys.append(match.group(1))
            return keys
    return []


def detect_names(dependencies, table):
    """Find known packages (frameworks or test tools) in the dependencies."""
    found = {}
    for dep in dependencies:
        for packages in (dep.packages, dep.dev_packages):
            for name, version in packages.items():
                label = table.get(name.lower())
                if label and label not in found:
                    found[label] = version

    result = []
    for label, version in found.items():
        if version and version != "*":
            result.append(f"{label} {version}")
        else:
            result.append(label)
    return result


# ---------------------------------------------------------------------------
# Put it all together
# ---------------------------------------------------------------------------

def scan_project(project_path):
    root = Path(project_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ScanError(f"Folder not found: {root}")

    warnings = []
    root_name = root.name or str(root)

    files, skipped_folders, secret_count, truncated = collect_files(root)
    if truncated:
        warnings.append(f"Very big project: stopped after {MAX_FILES} files")
    if not files:
        warnings.append("No readable files were found in this folder")

    # Files in the top folder, by lowercase name (to find composer.json etc.)
    root_files = {f.path.name.lower(): f.path for f in files if len(f.path.parts) == 1}

    # Dependencies
    dependencies = []
    readers = (
        ("composer.json", read_composer),
        ("package.json", read_package_json),
        ("requirements.txt", read_requirements),
    )
    for file_name, reader in readers:
        if file_name in root_files:
            info = reader(root / root_files[file_name], warnings)
            if info:
                dependencies.append(info)
    if not dependencies:
        warnings.append("No composer.json, package.json or requirements.txt found in the top folder")

    readme_excerpt = read_readme(root, root_files)
    if not readme_excerpt:
        warnings.append("No README found")

    tree, top_level_files = build_tree(files, root_name)

    return ProjectSnapshot(
        name=root_name,
        root=str(root),
        file_count=len(files),
        total_size_kb=sum(f.size for f in files) // 1024,
        languages=count_languages(files),
        skipped_folders=skipped_folders,
        skipped_secret_files=secret_count,
        top_level_files=top_level_files,
        tree=tree,
        dependencies=dependencies,
        frameworks=detect_names(dependencies, KNOWN_FRAMEWORKS),
        test_tools=detect_names(dependencies, KNOWN_TEST_TOOLS),
        test_file_count=count_test_files(files),
        readme_excerpt=readme_excerpt,
        env_keys=read_env_key_names(root, root_files),
        warnings=warnings,
    )


def save_snapshot(snapshot):
    """Save the result as a JSON file in SEDOCSIDE_2/scan_output
    (never inside the project that was scanned)."""
    output_folder = Path(__file__).resolve().parent.parent / "scan_output"
    output_folder.mkdir(exist_ok=True)

    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", snapshot.name)
    output_path = output_folder / f"{safe_name}_scan.json"
    output_path.write_text(
        json.dumps(asdict(snapshot), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


# ---------------------------------------------------------------------------
# Showing the result in the terminal
# ---------------------------------------------------------------------------

def _print_packages(title, packages, limit=15):
    if not packages:
        return
    print(f"    {title}: {len(packages)}")
    for index, (name, version) in enumerate(packages.items()):
        if index >= limit:
            print(f"      ... and {len(packages) - limit} more")
            break
        print(f"      {name} {version}".rstrip())


def print_summary(snapshot, saved_path):
    print(f"Project : {snapshot.name}")
    print(f"Location: {snapshot.root}")
    print(f"Files   : {snapshot.file_count:,} ({snapshot.total_size_kb:,} KB)")
    print(f"Skipped folders: {', '.join(snapshot.skipped_folders) or 'none'}")
    print(f"Secret files skipped (never read): {snapshot.skipped_secret_files}")

    print()
    print("Languages")
    if snapshot.languages:
        for language, count in snapshot.languages.items():
            print(f"  {language:<12} {count} files")
    else:
        print("  none found")

    print()
    print("Frameworks / main tools")
    for item in snapshot.frameworks or ["none recognised"]:
        print(f"  {item}")

    print()
    print("Dependencies")
    if not snapshot.dependencies:
        print("  none found")
    for dep in snapshot.dependencies:
        print(f"  {dep.source}")
        if dep.runtime:
            runtime_text = ", ".join(f"{k} {v}" for k, v in dep.runtime.items())
            print(f"    runtime: {runtime_text}")
        _print_packages("packages", dep.packages)
        _print_packages("dev packages", dep.dev_packages)

    print()
    tools = ", ".join(snapshot.test_tools) or "no known test tool"
    print(f"Tests: {snapshot.test_file_count} test files ({tools})")

    print()
    print(f"Settings in .env.example: {len(snapshot.env_keys)}")
    if snapshot.env_keys:
        print("  " + ", ".join(snapshot.env_keys[:20]))
        if len(snapshot.env_keys) > 20:
            print(f"  ... and {len(snapshot.env_keys) - 20} more")

    print()
    print("Top-level files")
    print("  " + (", ".join(snapshot.top_level_files) or "none"))

    print()
    print("Folder tree")
    for line in snapshot.tree:
        print(f"  {line}")

    print()
    print("README (first 300 characters)")
    excerpt = " ".join(snapshot.readme_excerpt[:300].split())
    print(f"  {excerpt or 'none'}")

    print()
    if snapshot.warnings:
        print("Warnings:")
        for warning in snapshot.warnings:
            print(f"  - {warning}")
    else:
        print("No warnings.")

    print()
    print(f"Saved the full result to: {saved_path}")


def main():
    # Do not crash if the console cannot show a special character
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    if len(sys.argv) < 2:
        print("Usage: python engine/scanner.py <path to a software project>")
        print('Example: python engine/scanner.py "C:\\laragon\\www\\expense-tracker"')
        sys.exit(1)

    try:
        snapshot = scan_project(sys.argv[1])
    except ScanError as error:
        print(f"ERROR: {error}")
        sys.exit(1)

    saved_path = save_snapshot(snapshot)
    print_summary(snapshot, saved_path)


if __name__ == "__main__":
    main()
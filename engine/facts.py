"""
facts.py

Turns the big scan result (made by scanner.py) into one short, clean
"project brief". No AI is used here, only simple rules.

The AI agents will read this brief instead of the raw scan, so they get
only clean facts: fast, and with less chance to make things up.

What the rules do:
  - build a first function list from the routes (IDs like FR_01 are made here)
  - hide Laravel's own default tables (sessions, cache, jobs ...)
  - choose the database: .env.example first, config/database.php as backup
  - say clearly if the app has no login
  - collect what a user needs to run the app
  - keep a separate list of "notes for you" (not sent to the AI)

To test it on its own (from the project folder, with the .venv active):
    python engine/facts.py                       (uses the newest scan)
    python engine/facts.py expense-tracker       (uses a named scan)
    python engine/facts.py --description "Write 1-2 sentences about your app here"
"""

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Settings (easy to change)
# ---------------------------------------------------------------------------

# Tables that Laravel itself creates. They are not features of the app.
FRAMEWORK_TABLES = {
    "password_reset_tokens", "password_resets", "sessions", "cache",
    "cache_locks", "jobs", "job_batches", "failed_jobs", "migrations",
    "personal_access_tokens",
}

DEFAULT_USER_COLUMNS = {
    "id", "name", "email", "email_verified_at", "password",
    "remember_token", "created_at", "updated_at",
}

HIDDEN_COLUMNS = {"id", "created_at", "updated_at", "remember_token"}

ROLE_COLUMNS = {"role", "roles", "is_admin", "admin", "user_type", "type", "permissions", "is_superadmin"}

AUTH_PACKAGES = {
    "laravel/breeze": "Laravel Breeze (login and registration)",
    "laravel/sanctum": "Laravel Sanctum (API tokens / SPA login)",
    "laravel/fortify": "Laravel Fortify (authentication)",
    "laravel/jetstream": "Laravel Jetstream (authentication and teams)",
    "laravel/passport": "Laravel Passport (OAuth2)",
    "laravel/socialite": "Laravel Socialite (social login)",
    "laravel/ui": "Laravel UI (login screens)",
    "spatie/laravel-permission": "spatie/laravel-permission (roles and permissions)",
}

# Well-known login pages, so they get a nice name
WELL_KNOWN_URIS = {
    "/login": "Log in",
    "/register": "Register an account",
    "/logout": "Log out",
    "/forgot-password": "Reset a forgotten password",
    "/reset-password": "Reset password",
    "/verify-email": "Verify email address",
    "/confirm-password": "Confirm password",
    "/dashboard": "Dashboard",
    "/profile": "Edit profile",
}

DB_NAMES = {
    "mysql": "MySQL", "mariadb": "MariaDB", "pgsql": "PostgreSQL",
    "sqlite": "SQLite", "sqlsrv": "SQL Server",
}

NOT_PAGES = ("layouts.", "components.", "partials.", "emails.", "vendor.")

README_BRIEF_CHARS = 600


class FactsError(Exception):
    """Raised when the brief cannot be built (for example, no scan file)."""


# ---------------------------------------------------------------------------
# The brief
# ---------------------------------------------------------------------------

@dataclass
class ProjectBrief:
    name: str
    description: str = ""                 # written by the project owner
    project_type: str = ""
    technology: list = field(default_factory=list)
    languages: list = field(default_factory=list)
    database: str = "TBD"
    users: str = "TBD"
    functions: list = field(default_factory=list)     # {"id", "name", "routes", "requires_login"}
    data_tables: list = field(default_factory=list)   # {"name", "columns"}
    classes: list = field(default_factory=list)       # {"name", "kind", "info"}
    pages: list = field(default_factory=list)
    needs: list = field(default_factory=list)         # what a user must have to run the app
    packages: list = field(default_factory=list)      # main packages with versions
    tests: str = ""
    readme_excerpt: str = ""
    notes: list = field(default_factory=list)         # for the person, NOT sent to the AI

    def to_text(self):
        """The text the AI agents will read."""
        lines = ["PROJECT BRIEF", f"Name: {self.name}"]

        if self.description:
            lines.append(f"Description (written by the project owner): {self.description}")
        elif self.readme_excerpt:
            lines.append(f"README (start): {self.readme_excerpt}")

        lines.append(f"Type: {self.project_type}")
        if self.technology:
            lines.append("Technology: " + ", ".join(self.technology))
        if self.languages:
            lines.append("Languages: " + ", ".join(self.languages))
        lines.append(f"Database: {self.database}")
        lines.append(f"Users and login: {self.users}")

        if self.functions:
            lines.append("Functions:")
            for item in self.functions:
                routes = ", ".join(item["routes"][:3])
                login = " [login required]" if item["requires_login"] else ""
                lines.append(f"  {item['id']} {item['name']} ({routes}){login}")

        if self.data_tables:
            lines.append("Data tables:")
            for table in self.data_tables:
                lines.append(f"  {table['name']}: " + ", ".join(table["columns"]))

        if self.classes:
            lines.append("Classes:")
            for item in self.classes:
                info = f" - {item['info']}" if item["info"] else ""
                lines.append(f"  {item['name']} ({item['kind']}){info}")

        if self.pages:
            lines.append("Pages (views): " + ", ".join(self.pages))
        if self.needs:
            lines.append("Needed to run the app: " + "; ".join(self.needs))
        if self.packages:
            lines.append("Main packages: " + ", ".join(self.packages))
        if self.tests:
            lines.append(f"Tests: {self.tests}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------

def _humanize(text):
    """ExpenseCategory -> Expense Category, monthly_report -> Monthly Report"""
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return text.strip().title()


def _plural_word(word):
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def _plural(text):
    words = text.split()
    if not words:
        return text
    words[-1] = _plural_word(words[-1])
    return " ".join(words)


def _major(version_text):
    match = re.search(r"\d+", version_text or "")
    return match.group(0) if match else ""


def _clean_readme(text):
    """Make a README readable: remove HTML tags, images and badges."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#*`>|]", " ", text)
    return " ".join(text.split())


def _looks_like_default_readme(text):
    lower = text.lower()
    return "laravel.com" in lower and "logo" in lower


def _package_version(snapshot, package_name):
    for dep in snapshot.get("dependencies", []):
        for packages in (dep.get("packages") or {}, dep.get("dev_packages") or {}):
            if package_name in packages:
                return packages[package_name]
    return ""


# ---------------------------------------------------------------------------
# The rules, one small function for each part of the brief
# ---------------------------------------------------------------------------

def _project_type(snapshot, laravel):
    runtime = ""
    for dep in snapshot.get("dependencies", []):
        if "php" in (dep.get("runtime") or {}):
            runtime = f" (PHP {dep['runtime']['php']})"

    if laravel:
        major = _major(_package_version(snapshot, "laravel/framework"))
        label = f"Laravel {major}".strip()
        return f"{label} web application{runtime}"

    frameworks = snapshot.get("frameworks", [])
    for name in ("Django", "Flask", "FastAPI"):
        if any(f.startswith(name) for f in frameworks):
            return f"{name} web application"
    for name in ("Next.js", "React", "Vue", "Express"):
        if any(f.startswith(name) for f in frameworks):
            return f"{name} application"

    languages = list((snapshot.get("languages") or {}).keys())
    if languages:
        return f"{languages[0]} project"
    return "software project"


def _database(laravel, notes):
    config = laravel.get("config", {})
    env_db = (config.get("env_example") or {}).get("DB_CONNECTION", "")
    default_db = config.get("database_default", "")

    if env_db:
        name = DB_NAMES.get(env_db.lower(), env_db)
        if default_db and default_db.lower() != env_db.lower():
            notes.append(
                f"config/database.php falls back to '{default_db}', but .env.example says "
                f"'{env_db}'. The brief uses '{env_db}'. Please check which one your app really uses."
            )
        return f"{name} (from .env.example)"

    if default_db:
        name = DB_NAMES.get(default_db.lower(), default_db)
        return f"{name} (default in config/database.php)"

    return "TBD"


def _hide_default_users(laravel):
    """True if the users table is just Laravel's default and nothing uses login."""
    routes = laravel.get("routes", [])
    if any(_needs_login(r) for r in routes):
        return False
    for table in laravel.get("tables", []):
        if table["name"] == "users":
            names = {c.split(":")[0] for c in table["columns"]}
            return names <= DEFAULT_USER_COLUMNS
    return False


def _needs_login(route):
    return any(m == "auth" or m.startswith("auth:") for m in route.get("middleware", []))


def _describe_users(snapshot, laravel):
    routes = laravel.get("routes", [])
    if not routes:
        return "TBD (no routes were found)"

    protected = sum(1 for r in routes if _needs_login(r))
    if protected == 0:
        text = "No login. Every page is open to anyone who can open the app (one type of user)."
    elif protected == len(routes):
        text = "Login is required for all pages."
    else:
        text = f"Login is required for {protected} of {len(routes)} routes. The other routes are public."

    for table in laravel.get("tables", []):
        if table["name"] == "users":
            names = [c.split(":")[0] for c in table["columns"]]
            role_columns = [n for n in names if n in ROLE_COLUMNS]
            if role_columns:
                text += f" The users table has a role-like column ({', '.join(role_columns)}), so there may be user roles (TBD)."

    for package, label in AUTH_PACKAGES.items():
        if _package_version(snapshot, package):
            text += f" Uses {label}."

    return text


def _function_name(route):
    """Make a human function name from one route. None = skip this route."""
    uri_key = route["uri"].split("/{")[0] or "/"
    if uri_key in WELL_KNOWN_URIS:
        return WELL_KNOWN_URIS[uri_key]

    action = route.get("action", "")

    if "@" in action:
        controller, method = action.split("@", 1)
        base = controller[:-len("Controller")] if controller.endswith("Controller") else controller
        singular = _humanize(base)
        plural = _plural(singular)
        crud = {
            "index": f"List {plural}",
            "create": f"Add {singular}",
            "store": f"Add {singular}",
            "show": f"View {singular} details",
            "edit": f"Edit {singular}",
            "update": f"Edit {singular}",
            "destroy": f"Delete {singular}",
        }
        if method in crud:
            return crud[method]
        if method == "__invoke":
            return singular or route["uri"]
        return f"{_humanize(method)} ({singular})"

    if action == "closure":
        return "Home page" if route["uri"] == "/" else f"Page {route['uri']}"

    if action.startswith("view:"):
        view = action[len("view:"):].strip()
        return "Home page" if route["uri"] == "/" else f"Page {view}"

    if action.startswith("redirect"):
        return None

    return f"{route['method']} {route['uri']}"


def _build_functions(routes):
    """Group the routes into functions. Create + store become one function,
    edit + update become one, and so on. IDs (FR_01 ...) are made here."""
    functions = []
    by_name = {}

    for route in routes:
        name = _function_name(route)
        if name is None:
            continue
        entry = by_name.get(name)
        if entry is None:
            entry = {"name": name, "routes": [], "requires_login": False}
            by_name[name] = entry
            functions.append(entry)
        entry["routes"].append(f"{route['method']} {route['uri']}")
        if _needs_login(route):
            entry["requires_login"] = True

    for number, entry in enumerate(functions, start=1):
        entry["id"] = f"FR_{number:02d}"
    return functions


COLUMN_RE = re.compile(r"^(\w+): (\w+)(?: -> (\w+))?(?: \((.*)\))?$")


def _short_column(text):
    match = COLUMN_RE.match(text)
    if not match:
        return text
    name, column_type, foreign, flags = match.groups()
    parts = [column_type]
    if foreign:
        parts.append(f"links to {foreign}")
    if flags and "nullable" in flags:
        parts.append("optional")
    if flags and "unique" in flags:
        parts.append("unique")
    return f"{name} ({', '.join(parts)})"


def _app_tables(laravel, hide_users, notes):
    tables = []
    hidden = []
    for table in laravel.get("tables", []):
        name = table["name"]
        if name in FRAMEWORK_TABLES or (name == "users" and hide_users):
            hidden.append(name)
            continue
        columns = [
            _short_column(c) for c in table["columns"]
            if c.split(":")[0] not in HIDDEN_COLUMNS
        ]
        tables.append({"name": name, "columns": columns})

    if hidden:
        notes.append(
            "Hidden Laravel default tables (not features of your app): " + ", ".join(hidden) + "."
        )
    if hide_users:
        notes.append(
            "The users table is Laravel's default and no page uses login, so the brief says "
            "'no login' and does not describe users."
        )
    return tables


def _build_classes(laravel, hide_users):
    classes = []
    for model in laravel.get("models", []):
        if model["name"] == "User" and hide_users:
            continue
        info = []
        if model["fillable"]:
            info.append("fields: " + ", ".join(model["fillable"]))
        if model["relationships"]:
            info.append("relations: " + "; ".join(model["relationships"]))
        classes.append({"name": model["name"], "kind": "Model", "info": "; ".join(info)})

    for controller in laravel.get("controllers", []):
        if not controller["methods"]:
            continue    # for example the empty base Controller
        classes.append({
            "name": controller["name"],
            "kind": "Controller",
            "info": "actions: " + ", ".join(controller["methods"]),
        })
    return classes


def _build_pages(laravel):
    return [v for v in laravel.get("views", []) if not v.startswith(NOT_PAGES) and v != "layout"]


def _find_gaps(laravel, notes):
    views = set(laravel.get("views", []))
    if not views:
        return
    for route in laravel.get("routes", []):
        name = route.get("name", "")
        if route["method"] != "GET" or "." not in name:
            continue
        if name.rsplit(".", 1)[-1] in ("index", "create", "edit", "show") and name not in views:
            notes.append(
                f"Possible gap: route '{name}' ({route['uri']}) has no view named '{name}'. "
                f"The page may use another name or may not be finished."
            )


def _needs_to_run(snapshot, database):
    dependencies = snapshot.get("dependencies", [])
    sources = {d["source"] for d in dependencies}
    runtime = {}
    for dep in dependencies:
        runtime.update(dep.get("runtime") or {})

    needs = []
    if "php" in runtime:
        needs.append(f"PHP {runtime['php']}")
    extensions = sorted(k[4:] for k in runtime if k.startswith("ext-"))
    if extensions:
        needs.append("PHP extensions: " + ", ".join(extensions))
    if "composer.json" in sources:
        needs.append("Composer (installs the PHP packages)")
    if "package.json" in sources:
        node = runtime.get("node")
        if node:
            needs.append(f"Node.js {node} and npm (build the front-end files)")
        else:
            needs.append("Node.js and npm (build the front-end files)")
    if "requirements.txt" in sources:
        needs.append("Python and pip (installs the Python packages)")
    if database and not database.startswith("TBD"):
        needs.append(f"{database.split(' (')[0]} database")
    return needs


def _main_packages(snapshot):
    packages = []
    for dep in snapshot.get("dependencies", []):
        for name, version in (dep.get("packages") or {}).items():
            packages.append(f"{name} {version}".strip())
    return packages


def _tests_text(snapshot):
    count = snapshot.get("test_file_count", 0)
    if not count:
        return "no tests found"
    tools = ", ".join(snapshot.get("test_tools", [])) or "tool not recognised"
    return f"{count} test files ({tools})"


# ---------------------------------------------------------------------------
# Put it all together
# ---------------------------------------------------------------------------

def build_brief(snapshot, description=""):
    """snapshot: the dict saved by scanner.py. description: optional text
    written by the project owner."""
    laravel = snapshot.get("laravel") or {}
    notes = []

    brief = ProjectBrief(name=snapshot.get("name", "unknown project"))
    brief.description = description.strip()
    brief.project_type = _project_type(snapshot, laravel)
    brief.technology = list(snapshot.get("frameworks", []))
    brief.languages = [
        f"{language} ({count} {'file' if count == 1 else 'files'})"
        for language, count in list((snapshot.get("languages") or {}).items())[:4]
    ]
    brief.database = _database(laravel, notes)
    brief.users = _describe_users(snapshot, laravel)

    hide_users = _hide_default_users(laravel) if laravel else False

    if laravel:
        brief.functions = _build_functions(laravel.get("routes", []))
        brief.data_tables = _app_tables(laravel, hide_users, notes)
        brief.classes = _build_classes(laravel, hide_users)
        brief.pages = _build_pages(laravel)
        _find_gaps(laravel, notes)
    else:
        notes.append(
            "No function list could be built from routes (this is not a Laravel project, or no "
            "routes were found). A later step will need to find the functions another way."
        )

    brief.needs = _needs_to_run(snapshot, brief.database)
    brief.packages = _main_packages(snapshot)
    brief.tests = _tests_text(snapshot)

    # README: useful only if it is a real one
    readme = _clean_readme(snapshot.get("readme_excerpt", ""))
    if snapshot.get("readme_excerpt") and _looks_like_default_readme(snapshot["readme_excerpt"]):
        readme = ""
    brief.readme_excerpt = readme[:README_BRIEF_CHARS]

    if not brief.description and not brief.readme_excerpt:
        notes.append(
            "No project description: the README is missing or is the default Laravel one. "
            "Sections like Purpose will be weak. Add a short description (1-3 sentences)."
        )

    brief.notes = notes
    return brief


# ---------------------------------------------------------------------------
# Test from the terminal
# ---------------------------------------------------------------------------

def _scan_folder():
    return Path(__file__).resolve().parent.parent / "scan_output"


def find_scan_file(argument):
    if argument:
        path = Path(argument)
        if path.is_file():
            return path
        candidate = _scan_folder() / f"{argument}_scan.json"
        if candidate.is_file():
            return candidate
        raise FactsError(f"Scan file not found: {argument}")

    files = sorted(_scan_folder().glob("*_scan.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FactsError("No scan result found. Run engine/scanner.py first.")
    return files[0]


def save_brief(brief):
    folder = _scan_folder()
    folder.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", brief.name)

    json_path = folder / f"{safe_name}_brief.json"
    json_path.write_text(json.dumps(asdict(brief), indent=2, ensure_ascii=False), encoding="utf-8")

    text_path = folder / f"{safe_name}_brief.txt"
    text_path.write_text(brief.to_text(), encoding="utf-8")
    return text_path, json_path


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    description = ""
    positional = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--description" and i + 1 < len(args):
            description = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    try:
        scan_path = find_scan_file(positional[0] if positional else "")
        snapshot = json.loads(scan_path.read_text(encoding="utf-8"))
    except (FactsError, OSError, ValueError) as error:
        print(f"ERROR: {error}")
        sys.exit(1)

    brief = build_brief(snapshot, description)
    text = brief.to_text()

    print(f"Scan used: {scan_path}")
    print()
    print("=== What the AI will read ===")
    print(text)
    print("=============================")
    print(f"Size: {len(text)} characters (about {len(text) // 4} tokens)")

    print()
    print("Notes for you (NOT sent to the AI):")
    for note in brief.notes or ["none"]:
        print(f"  - {note}")

    text_path, json_path = save_brief(brief)
    print()
    print(f"Saved: {text_path}")
    print(f"Saved: {json_path}")


if __name__ == "__main__":
    main()
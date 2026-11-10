"""
laravel_scanner.py  (Round 2: Laravel details)

Called by scanner.py when the project is a Laravel app.
It READS the project files with simple text matching (it never runs Laravel
or any project code) and collects:

  - routes       (from routes/*.php)
  - tables       (from database/migrations)
  - models       (from app/Models)
  - controllers  (from app/Http/Controllers)
  - views        (from resources/views)
  - config       (database default + a few harmless .env.example values)
  - auth hints   (login packages, role columns, middleware)

Limits: it understands normal Laravel code. Very unusual code may be missed.
"""

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 300_000     # bigger files are skipped
MAX_VIEWS = 100
MAX_PRINT_ROUTES = 40

# Only these .env.example values are ever read (they are not secrets)
ENV_SAFE_KEYS = {
    "APP_ENV", "DB_CONNECTION", "CACHE_STORE", "CACHE_DRIVER",
    "SESSION_DRIVER", "QUEUE_CONNECTION", "MAIL_MAILER",
    "FILESYSTEM_DISK", "BROADCAST_CONNECTION", "BROADCAST_DRIVER",
    "APP_LOCALE", "LOG_CHANNEL",
}

ROUTE_VERBS = {
    "get", "post", "put", "patch", "delete", "options", "any",
    "match", "view", "redirect", "resource", "apiResource",
}

# Table calls that are not columns
IGNORED_COLUMN_CALLS = {
    "index", "unique", "primary", "fullText", "spatialIndex",
    "dropIndex", "dropUnique", "dropPrimary", "dropForeign",
    "dropTimestamps", "dropSoftDeletes", "dropRememberToken",
    "dropMorphs", "dropConstrainedForeignId", "renameIndex",
    "engine", "charset", "collation", "comment",
}

MORPH_CALLS = {
    "morphs", "nullableMorphs", "uuidMorphs", "nullableUuidMorphs",
    "ulidMorphs", "nullableUlidMorphs",
}

PACKAGE_HINTS = {
    "laravel/breeze": "Laravel Breeze (login and registration screens)",
    "laravel/sanctum": "Laravel Sanctum (API tokens / SPA login)",
    "laravel/fortify": "Laravel Fortify (authentication backend)",
    "laravel/jetstream": "Laravel Jetstream (authentication and teams)",
    "laravel/passport": "Laravel Passport (OAuth2)",
    "laravel/socialite": "Laravel Socialite (social login)",
    "laravel/ui": "Laravel UI (login scaffolding)",
    "spatie/laravel-permission": "spatie/laravel-permission (roles and permissions)",
}

ROLE_COLUMNS = {"role", "roles", "is_admin", "admin", "user_type", "type", "permissions", "is_superadmin"}


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

QUOTED_RE = re.compile(r"""['"]([^'"]+)['"]""")
CLASS_REF_RE = re.compile(r"([\w\\]+)::class")
STRING_ONLY_RE = re.compile(r"""\s*(['"])(.*)\1\s*""", re.DOTALL)
ROUTE_START_RE = re.compile(r"Route::\s*(\w+)\s*\(")
CALL_RE = re.compile(r"\s*(?:Route::|->)\s*(\w+)\s*\(")
UP_RE = re.compile(r"function\s+up\s*\([^)]*\)\s*(?::\s*[\w\\?]+)?\s*\{")
SCHEMA_RE = re.compile(
    r"""Schema::(create|table)\s*\(\s*['"]([\w.]+)['"]\s*,\s*(?:static\s+)?function\s*\(\s*(?:\\?[\w\\]*Blueprint)?\s*\$(\w+)\s*\)\s*(?::\s*\w+)?\s*\{"""
)
RELATION_RE = re.compile(
    r"""function\s+(\w+)\s*\([^)]*\)\s*(?::\s*[\w\\?]+)?\s*\{\s*return\s+\$this->(hasOne|hasMany|belongsTo|belongsToMany|hasOneThrough|hasManyThrough|morphOne|morphMany|morphTo|morphToMany|morphedByMany)\(\s*([\w\\]+)?"""
)
ENV_LINE_RE = re.compile(r"\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")


# ---------------------------------------------------------------------------
# The data we produce
# ---------------------------------------------------------------------------

@dataclass
class LaravelFacts:
    routes: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    models: list = field(default_factory=list)
    controllers: list = field(default_factory=list)
    views: list = field(default_factory=list)
    config: dict = field(default_factory=dict)
    auth_hints: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _strip_php_comments(text):
    """Remove /* block */ comments and // or # whole-line comments."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)(?<![:'\"\w/])//.*$", "", text)
    text = re.sub(r"(?m)^\s*#(?!\[).*$", "", text)
    return text


def _read_php(root, rel_path, warnings):
    path = root / rel_path
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            warnings.append(f"{rel_path} is too big, skipped")
            return ""
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    return _strip_php_comments(text)


def _find_closing(text, open_index, open_char, close_char):
    """Find the bracket that closes the one at open_index. -1 if not found."""
    depth = 0
    quote = None
    i = open_index
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\":
                i += 1
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top(text, separator):
    """Split text on a separator, but only outside brackets and quotes."""
    parts = []
    current = []
    depth = 0
    quote = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            current.append(char)
            if char == "\\" and i + 1 < len(text):
                current.append(text[i + 1])
                i += 1
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char in "([{":
            depth += 1
            current.append(char)
        elif char in ")]}":
            depth -= 1
            current.append(char)
        elif char == separator and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        i += 1
    last = "".join(current).strip()
    if last:
        parts.append(last)
    return [p for p in parts if p]


def _string_value(arg):
    """'abc' -> abc. Returns None if arg is not a plain string."""
    match = STRING_ONLY_RE.fullmatch(arg)
    return match.group(2) if match else None


def _uri_value(arg):
    value = _string_value(arg)
    return value if value is not None else "(dynamic)"


def _short(class_name):
    """App\\Http\\Controllers\\ExpenseController -> ExpenseController"""
    return class_name.strip("\\").split("\\")[-1]


def _singular(word):
    word = word.replace("-", "_")
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _plural(word):
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def _snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _parse_chain(text, start):
    """Read a chain like Route::middleware('auth')->get('/x', ...)->name('y').
    Returns a list of calls (name + the text between the brackets)."""
    calls = []
    position = start
    while True:
        match = CALL_RE.match(text, position)
        if not match:
            break
        open_index = match.end() - 1
        close_index = _find_closing(text, open_index, "(", ")")
        if close_index == -1:
            break
        calls.append({
            "name": match.group(1),
            "args": text[open_index + 1:close_index],
            "open": open_index,
            "close": close_index,
        })
        position = close_index + 1
    return calls


def _middleware_names(args):
    names = QUOTED_RE.findall(args)
    names += [_short(c) for c in CLASS_REF_RE.findall(args)]
    return names


def _apply_modifiers(target, calls):
    """Apply middleware(), prefix() and controller() calls to a context."""
    for call in calls:
        if call["name"] == "middleware":
            target["middleware"] += _middleware_names(call["args"])
        elif call["name"] == "prefix":
            target["prefix"] = _string_value(call["args"]) or ""
        elif call["name"] == "controller":
            match = CLASS_REF_RE.search(call["args"])
            if match:
                target["controller"] = _short(match.group(1))


def _find_groups(text, chains):
    """Find Route::...->group(function () { ... }) blocks."""
    groups = []
    for _start, calls in chains:
        group_index = next((i for i, c in enumerate(calls) if c["name"] == "group"), None)
        if group_index is None:
            continue

        group = {"middleware": [], "prefix": "", "controller": ""}
        _apply_modifiers(group, calls[:group_index])

        group_call = calls[group_index]

        # Old style: Route::group(['middleware' => 'auth', 'prefix' => 'x'], function () {
        if group_call["args"].lstrip().startswith("["):
            array_text = _split_top(group_call["args"], ",")[0]
            found = re.search(
                r"""['"]middleware['"]\s*=>\s*(\[[^\]]*\]|['"][^'"]*['"])""", array_text
            )
            if found:
                group["middleware"] += _middleware_names(found.group(1))
            found = re.search(r"""['"]prefix['"]\s*=>\s*['"]([^'"]*)['"]""", array_text)
            if found:
                group["prefix"] = found.group(1)

        brace = text.find("{", group_call["open"], group_call["close"])
        if brace == -1:
            continue
        end = _find_closing(text, brace, "{", "}")
        if end == -1:
            continue

        group["start"] = brace
        group["end"] = end
        groups.append(group)

    groups.sort(key=lambda g: g["start"])
    return groups


def _describe_action(arg, controller):
    arg = arg.strip()
    if not arg:
        return ""
    if re.match(r"(static\s+)?(function|fn)\b", arg):
        return "closure"

    match = re.fullmatch(r"""\[\s*\\?([\w\\]+)::class\s*,\s*['"](\w+)['"]\s*\]""", arg)
    if match:
        return f"{_short(match.group(1))}@{match.group(2)}"

    match = re.fullmatch(r"\\?([\w\\]+)::class", arg)
    if match:
        return f"{_short(match.group(1))}@__invoke"

    text = _string_value(arg)
    if text is not None:
        if "@" in text:
            class_name, method = text.split("@", 1)
            return f"{_short(class_name)}@{method}"
        if controller:
            return f"{controller}@{text}"
        return text

    return " ".join(arg.split())[:60]


def _make_url(prefixes, uri):
    parts = [p.strip("/") for p in list(prefixes) + [uri]]
    return "/" + "/".join(p for p in parts if p)


def _resource_entries(verb, args, only, except_):
    name = _string_value(args[0]) if args else None
    if not name:
        return []

    controller = ""
    if len(args) > 1:
        match = CLASS_REF_RE.search(args[1])
        if match:
            controller = _short(match.group(1))

    parts = name.split(".")
    base = "/".join([f"{p}/{{{_singular(p)}}}" for p in parts[:-1]] + [parts[-1]])
    param = "{" + _singular(parts[-1]) + "}"

    actions = [
        ("index", "GET", base),
        ("create", "GET", base + "/create"),
        ("store", "POST", base),
        ("show", "GET", f"{base}/{param}"),
        ("edit", "GET", f"{base}/{param}/edit"),
        ("update", "PUT/PATCH", f"{base}/{param}"),
        ("destroy", "DELETE", f"{base}/{param}"),
    ]
    if verb == "apiResource":
        actions = [a for a in actions if a[0] not in ("create", "edit")]
    if only is not None:
        actions = [a for a in actions if a[0] in only]
    if except_ is not None:
        actions = [a for a in actions if a[0] not in except_]

    entries = []
    for action, method, uri in actions:
        target = f"{controller}@{action}" if controller else action
        entries.append((method, uri, target, f"{name}.{action}"))
    return entries


def _entries_for_verb(verb, args, controller, only, except_):
    """Returns a list of (method, uri, action, default name)."""
    if verb in ("get", "post", "put", "patch", "delete", "options", "any"):
        uri = _uri_value(args[0]) if args else ""
        action = _describe_action(args[1], controller) if len(args) > 1 else ""
        return [(verb.upper(), uri, action, "")]

    if verb == "match":
        methods = re.findall(r"""['"](\w+)['"]""", args[0]) if args else []
        uri = _uri_value(args[1]) if len(args) > 1 else ""
        action = _describe_action(args[2], controller) if len(args) > 2 else ""
        method = "/".join(m.upper() for m in methods) or "MATCH"
        return [(method, uri, action, "")]

    if verb == "view":
        uri = _uri_value(args[0]) if args else ""
        view = _string_value(args[1]) if len(args) > 1 else ""
        return [("GET", uri, f"view: {view}", "")]

    if verb == "redirect":
        uri = _uri_value(args[0]) if args else ""
        target = _string_value(args[1]) if len(args) > 1 else ""
        return [("GET", uri, f"redirect to {target}", "")]

    if verb in ("resource", "apiResource"):
        return _resource_entries(verb, args, only, except_)

    return []


def _routes_from_text(text, rel_path):
    # Pass 1: read every Route:: chain in the file
    chains = []
    for match in ROUTE_START_RE.finditer(text):
        calls = _parse_chain(text, match.start())
        if calls:
            chains.append((match.start(), calls))

    # Pass 2: find the group blocks, then build the routes
    groups = _find_groups(text, chains)
    routes = []

    for start, calls in chains:
        verb_index = next((i for i, c in enumerate(calls) if c["name"] in ROUTE_VERBS), None)
        if verb_index is None:
            continue

        # What the surrounding groups give to this route
        middleware = []
        prefixes = []
        controller = ""
        for group in groups:
            if group["start"] < start < group["end"]:
                middleware += group["middleware"]
                if group["prefix"]:
                    prefixes.append(group["prefix"])
                if group["controller"]:
                    controller = group["controller"]

        # What is written right before the verb: Route::middleware('auth')->get(...)
        own = {"middleware": [], "prefix": "", "controller": ""}
        _apply_modifiers(own, calls[:verb_index])
        middleware += own["middleware"]
        if own["prefix"]:
            prefixes.append(own["prefix"])
        if own["controller"]:
            controller = own["controller"]

        # What is written after the verb: ->name('x')->middleware('auth')
        route_name = ""
        only = None
        except_ = None
        for call in calls[verb_index + 1:]:
            if call["name"] == "middleware":
                middleware += _middleware_names(call["args"])
            elif call["name"] == "name":
                route_name = _string_value(call["args"]) or ""
            elif call["name"] == "only":
                only = QUOTED_RE.findall(call["args"])
            elif call["name"] == "except":
                except_ = QUOTED_RE.findall(call["args"])

        verb_call = calls[verb_index]
        args = _split_top(verb_call["args"], ",")
        entries = _entries_for_verb(verb_call["name"], args, controller, only, except_)

        for method, uri, action, default_name in entries:
            routes.append({
                "method": method,
                "uri": _make_url(prefixes, uri),
                "action": action,
                "name": route_name or default_name,
                "middleware": list(middleware),
                "file": rel_path,
            })

    return routes


def _scan_routes(root, rel_paths, warnings):
    routes = []
    for rel in rel_paths:
        if not (rel.startswith("routes/") and rel.endswith(".php") and rel.count("/") == 1):
            continue
        if rel in ("routes/console.php", "routes/channels.php"):
            continue
        text = _read_php(root, rel, warnings)
        if text:
            routes.extend(_routes_from_text(text, rel))
    return routes


# ---------------------------------------------------------------------------
# Database tables (from migrations)
# ---------------------------------------------------------------------------

def _up_body(text):
    """The inside of the up() method (we ignore down())."""
    match = UP_RE.search(text)
    if not match:
        return text
    open_index = match.end() - 1
    close_index = _find_closing(text, open_index, "{", "}")
    if close_index == -1:
        return text[open_index + 1:]
    return text[open_index + 1:close_index]


def _make_column(name, column_type, chain):
    flags = []
    if "->nullable(" in chain:
        flags.append("nullable")
    if "->unique(" in chain:
        flags.append("unique")
    if "->primary(" in chain:
        flags.append("primary")
    if "->default(" in chain:
        flags.append("has default")

    foreign = ""
    if column_type in ("foreignId", "foreignUuid", "foreignUlid", "foreignIdFor") and "->constrained(" in chain:
        match = re.search(r"""->constrained\(\s*(?:table:\s*)?['"](\w+)['"]""", chain)
        if match:
            foreign = match.group(1)
        else:
            base = name[:-3] if name.endswith("_id") else name
            foreign = _plural(base)
    if not foreign:
        match = re.search(r"""->on\(\s*['"](\w+)['"]""", chain)
        if match:
            foreign = match.group(1)

    return {"name": name, "type": column_type, "flags": flags, "fk": foreign}


def _put_column(columns, column):
    """Add a column, or replace it if a column with that name already exists."""
    for index, existing in enumerate(columns):
        if existing["name"] == column["name"]:
            columns[index] = column
            return
    columns.append(column)


def _apply_statement(table, variable, statement):
    """Read one line like  $table->string('email')->unique()  """
    match = re.match(r"\$" + re.escape(variable) + r"\s*->\s*(\w+)\s*\(", statement)
    if not match:
        return

    column_type = match.group(1)
    open_index = match.end() - 1
    close_index = _find_closing(statement, open_index, "(", ")")
    if close_index == -1:
        return

    args_text = statement[open_index + 1:close_index]
    args = _split_top(args_text, ",")
    chain = statement[close_index + 1:]
    first = _string_value(args[0]) if args else None
    columns = table["columns"]

    if column_type in IGNORED_COLUMN_CALLS:
        return

    if column_type == "dropColumn":
        names = QUOTED_RE.findall(args_text)
        table["columns"] = [c for c in columns if c["name"] not in names]
        return

    if column_type == "renameColumn":
        names = QUOTED_RE.findall(args_text)
        if len(names) >= 2:
            for column in columns:
                if column["name"] == names[0]:
                    column["name"] = names[1]
        return

    if column_type == "foreign":
        found = re.search(r"""->on\(\s*['"](\w+)['"]""", chain)
        if first and found:
            for column in columns:
                if column["name"] == first:
                    column["fk"] = found.group(1)
        return

    if column_type == "id":
        _put_column(columns, _make_column(first or "id", "id", chain))
    elif column_type in ("timestamps", "timestampsTz", "nullableTimestamps"):
        _put_column(columns, {"name": "created_at", "type": "timestamp", "flags": ["nullable"], "fk": ""})
        _put_column(columns, {"name": "updated_at", "type": "timestamp", "flags": ["nullable"], "fk": ""})
    elif column_type in ("softDeletes", "softDeletesTz"):
        _put_column(columns, {"name": first or "deleted_at", "type": "timestamp", "flags": ["nullable"], "fk": ""})
    elif column_type == "rememberToken":
        _put_column(columns, {"name": "remember_token", "type": "string", "flags": ["nullable"], "fk": ""})
    elif column_type in MORPH_CALLS:
        if first:
            _put_column(columns, _make_column(first + "_id", column_type, chain))
            _put_column(columns, _make_column(first + "_type", "string", chain))
    elif column_type == "foreignIdFor":
        found = CLASS_REF_RE.search(args_text)
        if found:
            name = _snake(_short(found.group(1))) + "_id"
            _put_column(columns, _make_column(name, column_type, chain))
    elif first:
        _put_column(columns, _make_column(first, column_type, chain))


def _apply_schema_calls(body, tables, file_name):
    for match in SCHEMA_RE.finditer(body):
        action, table_name, variable = match.group(1), match.group(2), match.group(3)
        open_index = match.end() - 1
        close_index = _find_closing(body, open_index, "{", "}")
        if close_index == -1:
            continue

        inner = body[open_index + 1:close_index]
        table = tables.setdefault(table_name, {"columns": [], "migrations": []})
        if action == "create":
            table["columns"] = []
        if file_name not in table["migrations"]:
            table["migrations"].append(file_name)

        for statement in _split_top(inner, ";"):
            _apply_statement(table, variable, statement)

    for match in re.finditer(r"""Schema::(?:dropIfExists|drop)\s*\(\s*['"]([\w.]+)['"]""", body):
        tables.pop(match.group(1), None)

    for match in re.finditer(r"""Schema::rename\s*\(\s*['"](\w+)['"]\s*,\s*['"](\w+)['"]""", body):
        old_name, new_name = match.group(1), match.group(2)
        if old_name in tables:
            tables[new_name] = tables.pop(old_name)


def _format_column(column):
    text = f"{column['name']}: {column['type']}"
    if column["fk"]:
        text += f" -> {column['fk']}"
    if column["flags"]:
        text += f" ({', '.join(column['flags'])})"
    return text


def _scan_migrations(root, rel_paths, warnings):
    tables = {}
    for rel in rel_paths:     # sorted, so older migrations come first
        if not (rel.startswith("database/migrations/") and rel.endswith(".php")):
            continue
        text = _read_php(root, rel, warnings)
        if text:
            _apply_schema_calls(_up_body(text), tables, rel.split("/")[-1])

    result = []
    for name, info in tables.items():
        result.append({
            "name": name,
            "columns": [_format_column(c) for c in info["columns"]],
            "migrations": info["migrations"],
        })
    return result


# ---------------------------------------------------------------------------
# Models, controllers, views
# ---------------------------------------------------------------------------

def _scan_models(root, rel_paths, warnings):
    models = []
    for rel in rel_paths:
        if not (rel.startswith("app/Models/") and rel.endswith(".php")):
            continue
        text = _read_php(root, rel, warnings)
        class_match = re.search(r"class\s+(\w+)\s+extends\s+(\w+)", text)
        if not class_match:
            continue

        fillable_match = re.search(r"\$fillable\s*=\s*\[(.*?)\]", text, re.DOTALL)
        fillable = re.findall(r"""['"](\w+)['"]""", fillable_match.group(1)) if fillable_match else []

        table_match = re.search(r"""\$table\s*=\s*['"](\w+)['"]""", text)

        relationships = []
        for found in RELATION_RE.finditer(text):
            related = _short(found.group(3)) if found.group(3) else ""
            relationships.append(f"{found.group(1)}: {found.group(2)} {related}".strip())

        models.append({
            "name": class_match.group(1),
            "file": rel,
            "table": table_match.group(1) if table_match else "",
            "fillable": fillable,
            "relationships": relationships,
        })
    return models


def _scan_controllers(root, rel_paths, warnings):
    controllers = []
    for rel in rel_paths:
        if not (rel.startswith("app/Http/Controllers/") and rel.endswith(".php")):
            continue
        text = _read_php(root, rel, warnings)
        class_match = re.search(r"class\s+(\w+)", text)
        if not class_match:
            continue

        methods = re.findall(r"public\s+(?:static\s+)?function\s+(\w+)\s*\(", text)
        methods = [m for m in methods if m == "__invoke" or not m.startswith("__")]

        controllers.append({
            "name": class_match.group(1),
            "file": rel,
            "methods": methods,
        })
    return controllers


def _scan_views(rel_paths):
    prefix = "resources/views/"
    suffix = ".blade.php"
    views = []
    for rel in rel_paths:
        if rel.startswith(prefix) and rel.endswith(suffix):
            views.append(rel[len(prefix):-len(suffix)].replace("/", "."))
    return views[:MAX_VIEWS]


# ---------------------------------------------------------------------------
# Config and hints
# ---------------------------------------------------------------------------

def _scan_config(root, rel_paths, warnings):
    config = {}

    if "config/database.php" in rel_paths:
        text = _read_php(root, "config/database.php", warnings)
        match = re.search(
            r"""['"]default['"]\s*=>\s*env\(\s*['"]DB_CONNECTION['"]\s*,\s*['"](\w+)['"]""", text
        )
        if match:
            config["database_default"] = match.group(1)

    if ".env.example" in rel_paths:
        try:
            text = (root / ".env.example").read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            text = ""
        safe_values = {}
        for line in text.splitlines():
            match = ENV_LINE_RE.match(line)
            if match and match.group(1) in ENV_SAFE_KEYS:
                value = match.group(2).split("#", 1)[0].strip().strip("'\"")
                safe_values[match.group(1)] = value
        if safe_values:
            config["env_example"] = safe_values

    return config


def _auth_hints(facts, package_names):
    hints = []

    for package, text in PACKAGE_HINTS.items():
        if package in package_names:
            hints.append(text)

    for table in facts.tables:
        if table["name"] == "users":
            names = [c.split(":")[0] for c in table["columns"]]
            role_columns = [n for n in names if n in ROLE_COLUMNS]
            if role_columns:
                hints.append("users table has role-like column(s): " + ", ".join(role_columns))

    middleware = sorted({m for r in facts.routes for m in r["middleware"]})
    if middleware:
        hints.append("route middleware used: " + ", ".join(middleware))

    if facts.routes:
        protected = sum(
            1 for r in facts.routes
            if any(m == "auth" or m.startswith("auth:") for m in r["middleware"])
        )
        hints.append(f"{protected} of {len(facts.routes)} routes require login (auth)")

    return hints


# ---------------------------------------------------------------------------
# Main function (called by scanner.py)
# ---------------------------------------------------------------------------

def scan_laravel(root, files, package_names, warnings):
    """root: project folder (Path). files: list of FileInfo from scanner.py.
    package_names: set of lowercase composer package names."""
    rel_paths = sorted(f.path.as_posix() for f in files)
    facts = LaravelFacts()

    try:
        facts.routes = _scan_routes(root, rel_paths, warnings)
    except Exception as error:      # never crash on strange code
        warnings.append(f"Laravel routes could not be read: {error}")

    try:
        facts.tables = _scan_migrations(root, rel_paths, warnings)
    except Exception as error:
        warnings.append(f"Laravel migrations could not be read: {error}")

    try:
        facts.models = _scan_models(root, rel_paths, warnings)
    except Exception as error:
        warnings.append(f"Laravel models could not be read: {error}")

    try:
        facts.controllers = _scan_controllers(root, rel_paths, warnings)
    except Exception as error:
        warnings.append(f"Laravel controllers could not be read: {error}")

    facts.views = _scan_views(rel_paths)

    try:
        facts.config = _scan_config(root, rel_paths, warnings)
    except Exception as error:
        warnings.append(f"Laravel config could not be read: {error}")

    try:
        facts.auth_hints = _auth_hints(facts, package_names)
    except Exception as error:
        warnings.append(f"Laravel auth hints could not be built: {error}")

    return facts


# ---------------------------------------------------------------------------
# Showing the result in the terminal (gets the dict saved in the snapshot)
# ---------------------------------------------------------------------------

def print_laravel(laravel):
    print("Laravel details")

    routes = laravel.get("routes", [])
    print(f"  Routes: {len(routes)}")
    for route in routes[:MAX_PRINT_ROUTES]:
        extra = ""
        if route["middleware"]:
            extra += f"  [{', '.join(route['middleware'])}]"
        if route["name"]:
            extra += f"  ({route['name']})"
        print(f"    {route['method']:<9} {route['uri']:<28} {route['action']}{extra}")
    if len(routes) > MAX_PRINT_ROUTES:
        print(f"    ... and {len(routes) - MAX_PRINT_ROUTES} more")

    tables = laravel.get("tables", [])
    print(f"  Database tables: {len(tables)}")
    for table in tables:
        columns = table["columns"]
        shown = ", ".join(columns[:8])
        if len(columns) > 8:
            shown += f", ... +{len(columns) - 8} more"
        print(f"    {table['name']} ({len(columns)} columns): {shown}")

    models = laravel.get("models", [])
    print(f"  Models: {len(models)}")
    for model in models:
        print(f"    {model['name']}")
        if model["fillable"]:
            print(f"      fillable: {', '.join(model['fillable'])}")
        if model["relationships"]:
            print(f"      relations: {'; '.join(model['relationships'])}")

    controllers = laravel.get("controllers", [])
    print(f"  Controllers: {len(controllers)}")
    for controller in controllers:
        methods = ", ".join(controller["methods"]) or "no public methods"
        print(f"    {controller['name']}: {methods}")

    views = laravel.get("views", [])
    print(f"  Views: {len(views)}")
    if views:
        print("    " + ", ".join(views[:20]))
        if len(views) > 20:
            print(f"    ... and {len(views) - 20} more")

    config = laravel.get("config", {})
    print("  Config")
    if config.get("database_default"):
        print(f"    database default (config/database.php): {config['database_default']}")
    for key, value in config.get("env_example", {}).items():
        print(f"    {key} = {value}")
    if not config:
        print("    nothing found")

    print("  Hints about users and login")
    for hint in laravel.get("auth_hints", []) or ["none"]:
        print(f"    {hint}")
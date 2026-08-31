"""The architecture rules and the Definition of Done metrics, in one scanner.

`tests/test_architecture.py` asserts on the violations; running this module directly prints
the report a batch attaches to its summary.  Rules whose target directories do not exist yet
return nothing and start biting by themselves the moment a phase creates those directories.

    uv run python scripts/architecture_metrics.py
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from safwa.bootstrap.modules import MODULES

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SAFWA = SRC / "safwa"
FEATURES = SAFWA / "features"

# The registry is the source of these names, which is the point of Rule H: a feature that
# is not in `bootstrap/modules.py` does not exist, and no other module may spell it out.
ENTITIES = tuple(
    sorted(
        {contribution.handler.entity for module in MODULES for contribution in module.proposals}
        | {tool.name for module in MODULES for tool in module.mutation_tools}
    )
)

# Rule A: what a business rule may never know about.
DELIVERY_PACKAGES = ("aiogram", "openai", "telethon", "telegram_llm", "llm_gateway")
BUSINESS_FILES = ("rules.py", "model.py", "use_cases.py", "data.py")

# Rule F: packages that must stay usable without Safwa.
REUSABLE_PACKAGES = ("llm_gateway", "agent_runtime", "telegram_llm")

# Rules C and D: processes that live beside the features rather than inside one.
PROCESS_PACKAGES = ("safwa/turn/", "safwa/cues/")

# Rule E: how deep into a feature a door reaches, and how deep a module is allowed to reach.
# Anything not named here is assembly, which is the top and may open every door.
DOOR_LAYERS = {"api": 1, "use_cases": 2, "telegram": 3}
MODULE_LAYERS = {"model.py": 1, "api.py": 1, "use_cases.py": 2}

HTML_TAG = re.compile(r"</?(?:b|i|u|s|a|code|pre|blockquote|tg-spoiler)\b")
EMOJI = re.compile("[\U0001f000-\U0001faff←-⇿☀-➿]")


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    path: str
    line: int
    detail: str

    def key(self) -> str:
        return f"{self.rule}|{self.path}|{self.detail}"

    def __str__(self) -> str:
        return f"{self.rule}  {self.path}:{self.line}  {self.detail}"


@dataclass(frozen=True, slots=True)
class Module:
    path: Path
    tree: ast.Module

    @property
    def rel(self) -> str:
        return self.path.relative_to(SRC).as_posix()

    @property
    def feature(self) -> str | None:
        """The feature that owns this module, or None if it lives outside `features/`."""
        try:
            parts = self.path.relative_to(FEATURES).parts
        except ValueError:
            return None
        return parts[0] if len(parts) > 1 else None

    def imported_roots(self) -> Iterator[tuple[str, int]]:
        """Every imported top-level package name, absolute or relative, with its line."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield alias.name.split(".")[0], node.lineno
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    yield self._relative_root(node), node.lineno
                elif node.module:
                    yield node.module.split(".")[0], node.lineno

    def imported_paths(self) -> Iterator[tuple[str, int]]:
        """Every import target as a dotted path, relative ones resolved against the package."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield alias.name, node.lineno
            elif isinstance(node, ast.ImportFrom):
                base = self._resolve(node)
                for alias in node.names:
                    yield f"{base}.{alias.name}" if base else alias.name, node.lineno

    def _resolve(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        package = self.path.relative_to(SRC).parts[:-1]
        anchor = package[: len(package) - node.level + 1]
        return ".".join((*anchor, node.module)) if node.module else ".".join(anchor)

    def _relative_root(self, node: ast.ImportFrom) -> str:
        resolved = self._resolve(node)
        return resolved.split(".")[0] if resolved else ""


def modules() -> list[Module]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        found.append(Module(path, ast.parse(path.read_text(encoding="utf-8-sig"), str(path))))
    return found


def process_modules(*names: str) -> Iterator[Module]:
    """Modules of a feature or of a process that lives beside them, such as the turn."""
    for module in modules():
        owned = module.feature is not None or module.rel.startswith(PROCESS_PACKAGES)
        if owned and module.path.name in names:
            yield module


def module_layer(module: Module) -> int:
    """How deep a module sits in its own feature, which is how deep it may reach into others."""
    if "telegram" in module.path.parts:
        return DOOR_LAYERS["telegram"]
    return MODULE_LAYERS.get(module.path.name, DOOR_LAYERS["telegram"])


def _calls_named(tree: ast.AST, *names: str) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in names:
            yield node
        elif isinstance(function, ast.Name) and function.id in names:
            yield node


def _string_constants(tree: ast.AST) -> Iterator[ast.Constant]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


# --------------------------------------------------------------------------- rules


def rule_a() -> list[Violation]:
    """Business files know nothing about delivery, the provider SDK or settings."""
    out = []
    for module in process_modules(*BUSINESS_FILES):
        for root, line in module.imported_roots():
            if root in DELIVERY_PACKAGES:
                out.append(Violation("Rule A", module.rel, line, f"imports {root}"))
        for path, line in module.imported_paths():
            if "telegram" in path.split(".") or path.endswith("bootstrap.settings"):
                out.append(Violation("Rule A", module.rel, line, f"imports {path}"))
    return out


def _is_frozen_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if "dataclass" not in ast.unparse(decorator.func):
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "frozen" and getattr(keyword.value, "value", False) is True:
                return True
    return False


def _is_orm_entity(node: ast.ClassDef) -> bool:
    return any(ast.unparse(base) == "Base" for base in node.bases)


def _is_enum(node: ast.ClassDef) -> bool:
    return any(ast.unparse(base).endswith("Enum") for base in node.bases)


def _union_members(tree: ast.Module) -> set[str]:
    """The variants of every `XState = A | B` or `type XState = A | B` in a module.

    A union names its variants whatever reads best — `Idle` and `Answering` say more
    than `IdleState` — so the alias is what says they are process state, not the suffix
    on each one.
    """
    members: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.TypeAlias):
            name, value = node.name.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            name, value = target.id, node.value
        else:
            continue
        if not name.endswith(("State", "Action", "Effect")):
            continue
        while isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
            if isinstance(value.right, ast.Name):
                members.add(value.right.id)
            value = value.left
        if isinstance(value, ast.Name):
            members.add(value.id)
    return members


def rule_c() -> list[Violation]:
    """Process state is frozen: every variant of a State, Action or Effect union."""
    out = []
    for module in process_modules("manager.py", "model.py"):
        named = _union_members(module.tree)
        for node in ast.walk(module.tree):
            if isinstance(node, ast.ClassDef) and (
                node.name.endswith(("State", "Action", "Effect")) or node.name in named
            ):
                # A persisted row is durable state, not the process state this rule means:
                # it is mutable by definition, and its truth is the table, not a union.
                # An enum is a closed vocabulary, and its members are already immutable.
                if _is_orm_entity(node) or _is_enum(node):
                    continue
                if not _is_frozen_dataclass(node):
                    out.append(
                        Violation("Rule C", module.rel, node.lineno, f"{node.name} is not frozen")
                    )
    return out


def rule_d() -> list[Violation]:
    """Reducers are pure: a transition may not await, read a file or touch a session."""
    out = []
    for module in process_modules("reducer.py", "manager.py"):
        # Every function in a `reducer.py` is a transition, whatever it is called: the
        # entry point is usually a `match` that hands the work to private helpers, and
        # checking the name alone would leave those helpers free to open a session.
        whole_file = module.path.name == "reducer.py"
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not whole_file and not node.name.startswith("reduce"):
                continue
            if isinstance(node, ast.AsyncFunctionDef) or any(
                isinstance(inner, ast.Await) for inner in ast.walk(node)
            ):
                out.append(Violation("Rule D", module.rel, node.lineno, f"{node.name} awaits"))
            for call in _calls_named(node, "open", "execute", "scalar", "commit"):
                out.append(
                    Violation("Rule D", module.rel, call.lineno, f"{node.name} does input/output")
                )
    return out


def rule_e() -> list[Violation]:
    """A module opens a door no higher than its own layer, in any feature including its own.

    A feature is three layers deep.  `model.py` and `api.py` are what a thing is called;
    `use_cases.py` is what may be done to it; everything else — the adapter, the agent
    contract, the proposal handler, the wiring — is assembly.  Each may name a door at its
    own layer or below, and never one above: a door that imported what is built on top of it
    would be a door with the whole feature behind it, and two such doors facing each other
    is how an import cycle starts.

    So `api` may be opened by anyone, `use_cases` by the operations layer and above, and
    `telegram` by assembly alone.  What that decides is what a door may contain: the
    vocabulary and the reads that need no operation, never an operation itself.
    """
    out = []
    for module in modules():
        if module.feature is None:
            continue
        layer = module_layer(module)
        for path, line in module.imported_paths():
            parts = path.split(".")
            if "features" not in parts:
                continue
            index = parts.index("features")
            if not parts[index + 1 : index + 2]:
                continue
            door = parts[index + 2 : index + 3]
            depth = DOOR_LAYERS.get(door[0], 0) if door else 0
            if depth > layer:
                out.append(
                    Violation("Rule E", module.rel, line, f"layer {layer} opens {door[0]}: {path}")
                )
    return out


def rule_f() -> list[Violation]:
    """The reusable packages work without Safwa."""
    out = []
    for module in modules():
        package = module.path.relative_to(SRC).parts[0]
        if package not in REUSABLE_PACKAGES:
            continue
        for root, line in module.imported_roots():
            if root == "safwa":
                out.append(Violation("Rule F", module.rel, line, "imports safwa"))
    return out


def rule_g() -> list[Violation]:
    """Whoever starts a background task also cancels one.

    A task nothing can cancel outlives the shutdown of whatever started it, and nothing
    knows it is still running.  Owning the lifetime is the permission, so the module that
    cancels is the module allowed to start.
    """
    out = []
    for module in modules():
        if any(_calls_named(module.tree, "cancel")):
            continue
        for call in _calls_named(module.tree, "create_task"):
            out.append(Violation("Rule G", module.rel, call.lineno, "starts a background task"))
    return out


def _declares_registry(module: Module) -> bool:
    """The module `ENTITIES` is read from, found by what it declares rather than by path."""
    return any(
        isinstance(node, ast.AnnAssign | ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "MODULES"
            for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        )
        for node in module.tree.body
    )


def _entity_names(nodes: Iterator[ast.expr]) -> set[str]:
    return {node.value for node in nodes if isinstance(node, ast.Constant) and node.value in ENTITIES}


def _join(names: set[str]) -> str:
    return ", ".join(sorted(names))


def entity_dispatch_points() -> list[Violation]:
    """Rule H: places outside a feature that fan out over entity names.

    A dict, set, tuple or list keyed by two or more entity names, or a comparison against
    one, is a central registry: adding an entity means editing it.  The one place allowed to
    name them all is the registry they are read from — the module that declares `MODULES` —
    because that is where a feature is declared to exist at all.
    """
    out = []
    for module in modules():
        if module.feature or _declares_registry(module):
            continue
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Dict):
                names = _entity_names(key for key in node.keys if key is not None)
                if len(names) >= 2:
                    out.append(
                        Violation("Rule H", module.rel, node.lineno, f"dict over {_join(names)}")
                    )
            elif isinstance(node, ast.Set | ast.Tuple | ast.List):
                names = _entity_names(iter(node.elts))
                if len(names) >= 2:
                    out.append(
                        Violation(
                            "Rule H", module.rel, node.lineno, f"collection over {_join(names)}"
                        )
                    )
            elif isinstance(node, ast.Compare):
                names = _entity_names(iter(node.comparators))
                if names:
                    out.append(
                        Violation("Rule H", module.rel, node.lineno, f"branches on {_join(names)}")
                    )
    return out


def rule_h() -> list[Violation]:
    return entity_dispatch_points()


def rule_k() -> list[Violation]:
    """A proposal handler carries no wording, and a tool adapter carries no commit."""
    out = []
    for module in process_modules("proposal.py"):
        for root, line in module.imported_roots():
            if root in ("aiogram", "telegram_llm"):
                out.append(Violation("Rule K", module.rel, line, f"imports {root}"))
        for constant in _string_constants(module.tree):
            if HTML_TAG.search(constant.value) or EMOJI.search(constant.value):
                out.append(
                    Violation("Rule K", module.rel, constant.lineno, "carries a user-facing string")
                )
    for module in process_modules("agent.py"):
        for call in _calls_named(module.tree, "commit"):
            out.append(Violation("Rule K", module.rel, call.lineno, "commits"))
        for path, line in module.imported_paths():
            if path.endswith(".use_cases"):
                out.append(Violation("Rule K", module.rel, line, f"calls the domain via {path}"))
    return out


RULES = {
    "Rule A": rule_a,
    "Rule C": rule_c,
    "Rule D": rule_d,
    "Rule E": rule_e,
    "Rule F": rule_f,
    "Rule G": rule_g,
    "Rule H": rule_h,
    "Rule K": rule_k,
}
# Rules I and J are snapshots of built artefacts rather than of the source tree, so they
# live with their baselines in `tests/test_architecture.py`.


def violations() -> list[Violation]:
    return [item for check in RULES.values() for item in check()]


# ------------------------------------------------------------------ import graph


def import_graph() -> dict[str, set[str]]:
    """Module-to-module edges inside `src/`, packages collapsed onto their `__init__`."""
    names = {module.rel.removesuffix(".py").replace("/", "."): module for module in modules()}
    graph: dict[str, set[str]] = {name: set() for name in names}
    for name, module in names.items():
        for target, _ in module.imported_paths():
            parts = target.split(".")
            while parts:
                candidate = ".".join(parts)
                if candidate in names and candidate != name:
                    graph[name].add(candidate)
                    break
                if f"{candidate}.__init__" in names and f"{candidate}.__init__" != name:
                    graph[name].add(f"{candidate}.__init__")
                    break
                parts.pop()
    return graph


def cycles() -> list[list[str]]:
    """Every import cycle, each reported once from its lowest-sorting member."""
    graph = import_graph()
    found: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    colour: dict[str, int] = {}
    stack: list[str] = []

    def walk(node: str) -> None:
        colour[node] = 1
        stack.append(node)
        for target in sorted(graph[node]):
            if colour.get(target, 0) == 0:
                walk(target)
            elif colour[target] == 1:
                loop = stack[stack.index(target) :]
                rotation = loop[loop.index(min(loop)) :] + loop[: loop.index(min(loop))]
                if tuple(rotation) not in seen:
                    seen.add(tuple(rotation))
                    found.append(rotation)
        stack.pop()
        colour[node] = 2

    for node in sorted(graph):
        if colour.get(node, 0) == 0:
            walk(node)
    return found


def edge_count() -> int:
    return sum(len(targets) for targets in import_graph().values())


# ------------------------------------------------------------------- done metrics


def module_sizes() -> list[tuple[str, int]]:
    sizes = [
        (module.rel, len(module.path.read_text(encoding="utf-8-sig").splitlines()))
        for module in modules()
    ]
    return sorted(sizes, key=lambda item: -item[1])


def use_case_base_hits() -> list[Violation]:
    """Definition of Done #2: no base abstraction ever grows under the use cases."""
    out = []
    for module in modules():
        for node in ast.walk(module.tree):
            if isinstance(node, ast.ClassDef) and node.name in ("UseCase", "UseCaseBase"):
                out.append(Violation("DoD 2", module.rel, node.lineno, f"class {node.name}"))
            elif (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == "execute"
                and node.args.args
                and node.args.args[0].arg == "self"
            ):
                out.append(Violation("DoD 2", module.rel, node.lineno, "def execute(self"))
    return out


def facade_modules() -> list[str]:
    """Definition of Done #13: a module that only re-exports is an old path kept alive.

    A facade imports names from elsewhere and defines nothing of its own beyond `__all__`.
    A module full of its own assignments is a constants module, not a facade.
    """
    out = []
    for module in modules():
        if module.path.name == "__init__.py":
            continue
        borrowed = False
        own = False
        for node in module.tree.body:
            if isinstance(node, ast.Expr):
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue
            if isinstance(node, ast.Import | ast.ImportFrom):
                borrowed = True
            elif isinstance(node, ast.Assign) and _assigns_only(node, "__all__"):
                continue
            else:
                own = True
        if borrowed and not own:
            out.append(module.rel)
    return out


def _assigns_only(node: ast.Assign, name: str) -> bool:
    return all(isinstance(target, ast.Name) and target.id == name for target in node.targets)


def report() -> str:
    found = violations()
    lines = ["# Architecture metrics", ""]
    lines.append(f"Modules scanned: {len(modules())}")
    lines.append(f"Rule violations: {len(found)}")
    for rule in RULES:
        lines.append(f"  {rule}: {sum(1 for item in found if item.rule == rule)}")
    lines += ["", "## Largest modules"]
    for name, size in module_sizes()[:10]:
        lines.append(f"  {size:>5}  {name}")
    lines += ["", "## Definition of Done"]
    lines.append(f"  #1 entity dispatch points outside features: {len(entity_dispatch_points())}")
    lines.append(f"  #2 use case base abstractions: {len(use_case_base_hits())}")
    lines.append(f"  #3 modules over 600 lines: {sum(1 for _, n in module_sizes() if n > 600)}")
    lines.append(f"  #13 re-export only modules: {len(facade_modules())}")
    lines += ["", "## Import graph"]
    lines.append(f"  edges: {edge_count()}")
    lines.append(f"  cycles: {len(cycles())}")
    for loop in cycles():
        lines.append(f"    {' -> '.join([*loop, loop[0]])}")
    lines += ["", "## Violations"]
    lines += [f"  {item}" for item in found] or ["  none"]
    return "\n".join(lines)


def allowlist() -> dict[str, dict[str, int]]:
    """The current violations as counts per key, which is the shape the allowlist stores.

    Counting rather than listing is what makes the file a ratchet: a phase may lower any
    number and remove any key, and the test fails on a key that grew or appeared.
    """
    grouped: dict[str, dict[str, int]] = {}
    for item in violations():
        counts = grouped.setdefault(item.rule, {})
        counts[item.key()] = counts.get(item.key(), 0) + 1
    return {rule: dict(sorted(counts.items())) for rule, counts in sorted(grouped.items())}


def main() -> None:
    print(report())
    print()
    print(json.dumps(allowlist(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

"""The architecture rules and the Definition of Done metrics, in one scanner.

`tests/test_architecture.py` asserts on the violations; running this module directly prints
the report a batch attaches to its summary.  Rules whose target directories do not exist yet
return nothing and start biting by themselves the moment a phase creates those directories.

    uv run python scripts/architecture_metrics.py

Naming a feature prints its map instead: where its code is, which scenarios it is held to
and what cites them, which of its views each reader may query, and what it plugs into.

    uv run python scripts/architecture_metrics.py cards
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SAFWA = SRC / "safwa"
FEATURES = SAFWA / "features"

# The map reads the `.feature` files and the test docstrings, and `tests/brd_ids.py` is
# what already reads both. Under pytest that directory is on the path; run as a script,
# only `scripts/` is, so the map would otherwise need a second copy of those parsers.
sys.path.insert(0, str(REPO / "tests"))

from brd_ids import BRD, cited_tests, titled_scenarios  # noqa: E402

from safwa.bootstrap.modules import AGENTS, AI_VIEWS, HELPERS, MODULES, REGISTRY  # noqa: E402
from safwa.features.advisor.agent import ADVISOR_VIEWS  # noqa: E402
from tg_agent_shell.telegram.contributions import ScreenCommand  # noqa: E402

# The registry is the source of these names, which is the point of Rule H: a feature that
# is not in `bootstrap/modules.py` does not exist, and no other module may spell it out.
ENTITIES = tuple(
    sorted(
        {contribution.handler.entity for module in MODULES for contribution in module.proposals}
        | {tool.name for module in MODULES for tool in module.mutation_tools}
    )
)

# Rule A: what a business rule may never know about. The settings module is named because
# a business file that reads it takes its decisions from the environment instead of from
# its arguments; `safwa/config.py` is where this application keeps it.
DELIVERY_PACKAGES = ("aiogram", "openai", "telethon", "telegram_llm", "llm_gateway")
BUSINESS_FILES = ("rules.py", "model.py", "use_cases.py", "hierarchy.py", "data.py")
SETTINGS_MODULE = "safwa.config"

# Rule F: packages that import no Safwa. That is what the rule proves — not that the
# packages are portable, which is what running a second application on them would show.
REUSABLE_PACKAGES = ("llm_gateway", "agent_runtime", "telegram_llm", "tg_agent_shell")

# Rule R: the feature packages `MODULES` registers, and the one Safwa package that is not
# a feature. The advisor is the persona and the root prompt, which the composition root
# assembles itself rather than plugging in. `MODULES` also carries modules from outside
# `features/` — the shell's own review flow is one — and those are not this rule's.
REGISTERED_FEATURES = frozenset(module.name for module in MODULES)
UNREGISTERED_PACKAGES = frozenset({"advisor"})

# Rules A, C and D: processes that live beside the features rather than inside one. The
# review flow is one of them: it left `features/` with the package, and everything those
# rules say about a model, a reducer and a use case is still said about it.
PROCESS_PACKAGES = ("tg_agent_shell/turn/", "tg_agent_shell/cues/", "tg_agent_shell/proposals/")

# Rule E: how deep into a feature a door reaches, and how deep a module is allowed to reach.
# Anything not named here is assembly, which is the top and may open every door.
DOOR_LAYERS = {"api": 1, "use_cases": 2, "hierarchy": 2, "telegram": 3}
MODULE_LAYERS = {"model.py": 1, "api.py": 1, "use_cases.py": 2, "hierarchy.py": 2, "hooks.py": 3}

# Rule K: the doors at the operations layer, which is what an `agent.py` may not reach
# for.  Read from `DOOR_LAYERS` rather than spelled again, so a feature that splits its
# operations into a second module — Cards keeps its derived-value walk in `hierarchy.py` —
# declares that name once and every rule about the layer follows.
OPERATION_DOORS = frozenset(door for door, depth in DOOR_LAYERS.items() if depth == 2)

HTML_TAG = re.compile(r"</?(?:b|i|u|s|a|code|pre|blockquote|tg-spoiler)\b")
EMOJI = re.compile("[\U0001f000-\U0001faff←-⇿☀-➿]")


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    path: str
    line: int
    detail: str

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


def business_imports(module: Module) -> list[Violation]:
    """Rule A on one module, so an example can be checked without a file on disk."""
    out = []
    for root, line in module.imported_roots():
        if root in DELIVERY_PACKAGES:
            out.append(Violation("Rule A", module.rel, line, f"imports {root}"))
    for path, line in module.imported_paths():
        settings = path == SETTINGS_MODULE or path.startswith(f"{SETTINGS_MODULE}.")
        if "telegram" in path.split(".") or path.startswith("tg_agent_shell.hooks.") or settings:
            out.append(Violation("Rule A", module.rel, line, f"imports {path}"))
    return out


def rule_a() -> list[Violation]:
    """Business files know nothing about delivery, the provider SDK or settings.

    Scope: the `BUSINESS_FILES` names, inside a feature or inside one of
    `PROCESS_PACKAGES`. Nothing else in the repository is under this rule.
    """
    return [item for module in process_modules(*BUSINESS_FILES) for item in business_imports(module)]


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
    """Process state is frozen: every variant of a State, Action or Effect union.

    Scope: `manager.py` and `model.py` inside a feature or inside one of
    `PROCESS_PACKAGES`. `agent_runtime/` is not under it — a session accumulator is a
    running total, not a transition, and copying it at every step would say nothing.
    """
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
    """Reducers are pure: a transition may not await, read a file or touch a session.

    Scope is Rule C's: `reducer.py` and `manager.py` inside a feature or inside one of
    `PROCESS_PACKAGES`, not the whole of `agent_runtime/`.
    """
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
    """The reusable packages import no Safwa.

    That is the whole of what an import graph can show. Whether a second application can
    actually be built on them is answered by building one, not here.
    """
    out = []
    for module in modules():
        package = module.path.relative_to(SRC).parts[0]
        if package not in REUSABLE_PACKAGES:
            continue
        for root, line in module.imported_roots():
            if root == "safwa":
                out.append(Violation("Rule F", module.rel, line, "imports safwa"))
    return out


# Rule M: what the engine may reach, which is the layer every layer may reach.
ENGINE = "tg_agent_shell/ai/"
ENGINE_MAY_IMPORT = ("tg_agent_shell.ai.", "tg_agent_shell.foundation.", "tg_agent_shell.hooks.")


def rule_m() -> list[Violation]:
    """The agent engine is the bottom of the package and imports none of it.

    `ai/` is what a session is and what a tool call costs.  The review flow, the aiogram
    surface, the turn lease and the cues are all built on it, so an import back up is an
    engine that only this shell can run — and the direction is what proves the claim
    rather than the intention.
    """
    out = []
    for module in modules():
        if module.rel.startswith("tg_agent_shell/hooks/"):
            # The hook contract is below the engine too: neither can import its adapters.
            allowed = ("tg_agent_shell.hooks.", "tg_agent_shell.foundation.")
        elif module.rel.startswith(ENGINE):
            allowed = ENGINE_MAY_IMPORT
        else:
            continue
        for path, line in module.imported_paths():
            if path.startswith("tg_agent_shell.") and not path.startswith(allowed):
                out.append(Violation("Rule M", module.rel, line, f"imports {path}"))
    return out


def rule_g() -> list[Violation]:
    """Whoever starts a background task also cancels one.

    A task nothing can cancel outlives the shutdown of whatever started it, and nothing
    knows it is still running.  Owning the lifetime is the permission, so the module that
    cancels is the module allowed to start.

    A `cancel` anywhere in the module satisfies this. That the right task is cancelled at
    the right moment is a test's to show, never this rule's.
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
    """Nothing outside a feature fans out over entity names."""
    return entity_dispatch_points()


def proposal_wording(module: Module) -> list[Violation]:
    """Rule K on one `proposal.py`, so an example can be checked without a file on disk."""
    out = []
    for root, line in module.imported_roots():
        if root in ("aiogram", "telegram_llm"):
            out.append(Violation("Rule K", module.rel, line, f"imports {root}"))
    for constant in _string_constants(module.tree):
        if HTML_TAG.search(constant.value) or EMOJI.search(constant.value):
            out.append(
                Violation("Rule K", module.rel, constant.lineno, "carries a user-facing string")
            )
    return out


def agent_domain_calls(module: Module) -> list[Violation]:
    """Rule K on one `agent.py`, so an example can be checked without a file on disk."""
    out = []
    for call in _calls_named(module.tree, "commit"):
        out.append(Violation("Rule K", module.rel, call.lineno, "commits"))
    for path, line in module.imported_paths():
        # `from .use_cases import create_diary_entry` resolves to a path that ends in the
        # function, and `import ... .use_cases as writes` to one that ends in the module,
        # so the segment is what says the door was opened rather than how the path ends.
        if OPERATION_DOORS & set(path.split(".")):
            out.append(Violation("Rule K", module.rel, line, f"calls the domain via {path}"))
    return out


def rule_k() -> list[Violation]:
    """A proposal handler carries no wording, and a tool adapter carries no commit."""
    return [
        *(item for module in process_modules("proposal.py") for item in proposal_wording(module)),
        *(item for module in process_modules("agent.py") for item in agent_domain_calls(module)),
    ]


def unregistered_packages(names: Iterable[str]) -> list[str]:
    """Which of these Safwa packages `MODULES` does not register, advisor aside."""
    return sorted(set(names) - REGISTERED_FEATURES - UNREGISTERED_PACKAGES)


def rule_r() -> list[Violation]:
    """Every Safwa feature package is registered in `MODULES`.

    A package `MODULES` does not name has no view, no tool, no screen and no route: it is
    code that runs nowhere, and nothing else in the repository notices.
    """
    found = {
        path.name: path
        for path in sorted(FEATURES.iterdir())
        if path.is_dir() and (path / "__init__.py").exists()
    }
    return [
        Violation(
            "Rule R",
            (found[name] / "__init__.py").relative_to(SRC).as_posix(),
            1,
            "is not registered in MODULES",
        )
        for name in unregistered_packages(found)
    ]


# Rule P: the aiogram calls that put a message in the chat.  `answer` is one of them, but a
# tap acknowledgement carries the same name and writes nothing, so its receivers are named.
TELEGRAM_SENDS = (
    "answer",
    "delete_message",
    "edit_message_text",
    "edit_text",
    "reply",
    "send_message",
    "send_photo",
)
TAP_ACKNOWLEDGERS = frozenset({"callback", "event"})


def rule_p() -> list[Violation]:
    """Every bot message goes through the one place that marks it.

    A message's kind is written into its own text by `telegram_llm`, and that kind is the
    only thing deciding whether the model ever reads the message back.  A raw aiogram send
    is therefore a message with no kind — invisible to the reader, and unfixable later
    because Telegram is the store rather than a cache of one.
    """
    out = []
    for module in modules():
        if module.rel.startswith("telegram_llm/"):
            continue
        if all(root != "aiogram" for root, _ in module.imported_roots()):
            continue
        for call in _calls_named(module.tree, *TELEGRAM_SENDS):
            function = call.func
            if not isinstance(function, ast.Attribute):
                continue
            acknowledges = (
                function.attr == "answer"
                and isinstance(function.value, ast.Name)
                and function.value.id in TAP_ACKNOWLEDGERS
            )
            if acknowledges:
                continue
            out.append(Violation("Rule P", module.rel, call.lineno, f"sends via {function.attr}"))
    return out


@dataclass(frozen=True, slots=True)
class Rule:
    """One rule as the report shows it: what it is called, and what answers it.

    The letter it is keyed by is the stable reference — it is what a `Violation` carries
    and what a document cites — so a rule is renamed freely and never re-lettered. Letters
    A to Q have all been used at some point; a new rule takes the next unused one.
    """

    name: str
    check: Callable[[], list[Violation]]


RULES = {
    "Rule A": Rule("a business file knows no delivery, provider or settings", rule_a),
    "Rule C": Rule("process state is frozen", rule_c),
    "Rule D": Rule("a reducer is pure", rule_d),
    "Rule E": Rule("a module opens no door above its own layer", rule_e),
    "Rule F": Rule("the reusable packages import no Safwa", rule_f),
    "Rule G": Rule("whoever starts a background task also cancels one", rule_g),
    "Rule H": Rule("nothing outside a feature fans out over entity names", rule_h),
    "Rule K": Rule("a proposal handler carries no wording, an agent no domain call", rule_k),
    "Rule M": Rule("the agent engine imports none of the shell above it", rule_m),
    "Rule P": Rule("every bot message goes through the one place that marks it", rule_p),
    "Rule R": Rule("every Safwa feature package is registered in MODULES", rule_r),
}
# Rules I, J and O are snapshots of built artefacts rather than of the source tree, so they
# live with their baselines in `tests/test_architecture.py`.


def violations() -> list[Violation]:
    return [item for rule in RULES.values() for item in rule.check()]


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


# ------------------------------------------------------------------- feature map


def readers() -> dict[str, tuple[str, ...]]:
    """Every published view, and the readers whose own list names it.

    Publishing a view grants nobody anything: a reader reaches the views its declaration
    spells out, so these lists are the whole of who may query each one. A view no reader
    names is not a violation — it may be read outside the model — but it is worth asking.

    A reader naming a view no feature publishes is refused where it is assembled, by
    `view_catalogue` and by `ReadOnlyQueryRunner.scoped`, so it never reaches this.
    """
    declared = {
        "advisor": ADVISOR_VIEWS,
        **{agent.name: agent.views for agent in AGENTS},
        **{helper.name: helper.views for helper in HELPERS.values()},
    }
    found: dict[str, list[str]] = {view.name: [] for view in AI_VIEWS}
    for reader, views in declared.items():
        for view in views:
            found[view].append(reader)
    return {name: tuple(who) for name, who in found.items()}


def reader_scopes(name: str) -> list[tuple[str, tuple[str, ...]]]:
    """The readers this feature owns, and the views each one's own list names.

    The advisor's list is a constant of its own package rather than a manifest field: it
    is the root session, which the composition root assembles instead of plugging in.
    """
    if name == "advisor":
        return [("advisor", ADVISOR_VIEWS)]
    module = next((item for item in MODULES if item.name == name), None)
    if module is None:
        return []
    return [
        *((agent.name, agent.views) for agent in module.agents),
        *((helper.name, helper.views) for helper in module.helpers),
    ]


def feature_names() -> list[str]:
    """What the map answers about: the Safwa feature packages and whatever `MODULES` adds."""
    packages = {path.name for path in FEATURES.iterdir() if (path / "__init__.py").exists()}
    return sorted(packages | {module.name for module in MODULES})


def feature_package(name: str) -> Path | None:
    """Where the code is: under `features/`, or the shell package `MODULES` registers."""
    if (FEATURES / name / "__init__.py").exists():
        return FEATURES / name
    found = (path for path in sorted(SRC.rglob(name)) if (path / "__init__.py").exists())
    return next(found, None)


def wiring(name: str) -> list[str]:
    """What this feature's `FeatureModule` contributes, by field, in manifest order."""
    module = next((item for item in MODULES if item.name == name), None)
    if module is None:
        return []
    declared = [
        ("agents", [agent.name for agent in module.agents]),
        ("helpers", [helper.name for helper in module.helpers]),
        ("proposals", [item.handler.entity for item in module.proposals]),
        ("mutation_tools", [tool.name for tool in module.mutation_tools]),
        ("before_tool", [f"{len(module.before_tool)} watchers"] if module.before_tool else []),
        ("after_tool", [f"{len(module.after_tool)} watchers"] if module.after_tool else []),
        ("views", [view.name for view in module.views]),
        ("screens", [spec.item_type for spec in module.screens]),
        ("commands", [_command_name(command) for command in module.commands]),
        # The buttons are named in the adapter's `handlers.py`; here their number is the
        # connection point, and 46 of them would bury every other line of the map.
        (
            "callback_actions",
            [f"{len(module.callback_actions)} actions"] if module.callback_actions else [],
        ),
        ("text_inputs", [flow.name for flow in module.text_inputs]),
        ("start_links", [f"{len(module.start_links)} payloads"] if module.start_links else []),
        ("hooks", [
            f"{item.spec.name} ({'on' if item.enabled else 'off'})"
            for item in REGISTRY.hooks.registrations if item.spec.owner == module.name
        ]),
        ("recover", ["recover_startup"] if module.recover else []),
        ("background", [task.name for task in module.background]),
    ]
    return [f"  {field:<17}{', '.join(values)}" for field, values in declared if values]


def _command_name(command: ScreenCommand) -> str:
    return f"/{command.command}" if command.command else f"nav:{command.nav}"


def opened_from_outside(package: Path) -> list[str]:
    """Who imports this feature today. Not who would break: a caller may reach it by name
    the graph cannot see, and a screen the composition root hands on has no import at all.
    """
    inside = f"{package.relative_to(SRC).as_posix().replace('/', '.')}."
    return [
        f"  {name} -> {target}"
        for name, targets in sorted(import_graph().items())
        if not name.startswith(inside)
        for target in sorted(targets)
        if target.startswith(inside)
    ]


# What the map is worth, printed with it, because a map is read as a promise otherwise.
DECLARED_ONLY = (
    "Declared links only. A citation is a test's claim on a scenario, not proof the\n"
    "scenario is checked through; the imports are who opens this feature today, not\n"
    "everything that would break if it went."
)


def feature_map(name: str) -> str:
    """One feature's declared links: its sources, its scenarios, its views and its wiring."""
    if name not in feature_names():
        raise SystemExit(f"No such feature: {name}. Known: {', '.join(feature_names())}")
    package = feature_package(name)
    lines = [f"# {name}", "", DECLARED_ONLY, ""]

    lines.append(f"## Sources — {package.relative_to(REPO).as_posix()}")
    lines += [
        f"  {path.relative_to(package).as_posix()}"
        for path in sorted(package.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]

    approved = next(iter(sorted(BRD.rglob(f"{name}.feature"))), None)
    lines += ["", f"## Scenarios — {approved.relative_to(REPO).as_posix() if approved else 'none'}"]
    cited = cited_tests()
    for identifier, wording in titled_scenarios(approved) if approved else []:
        lines.append(f"  {identifier} — {wording}")
        lines += [f"      {test}" for test in cited.get(identifier, ["(no test cites it)"])]

    published = readers()
    owned = {view.name for module in MODULES if module.name == name for view in module.views}
    lines += ["", "## Views published"]
    lines += [
        f"  {view:<26}read by {', '.join(published[view]) or '(no reader)'}"
        for view in sorted(owned)
    ] or ["  none"]

    lines += ["", "## Views its own readers may query"]
    lines += [
        f"  {reader:<26}{', '.join(views)}" for reader, views in reader_scopes(name)
    ] or ["  none"]

    lines += ["", "## Registered in MODULES"]
    lines += wiring(name) or ["  nothing: this package is assembled by the composition root"]

    lines += ["", "## Opened from outside"]
    lines += opened_from_outside(package) or ["  none"]
    return "\n".join(lines)


def report() -> str:
    found = violations()
    lines = ["# Architecture metrics", ""]
    lines.append(f"Modules scanned: {len(modules())}")
    # The rules fail a run; everything under `Metrics` is read at review and fails nothing.
    lines += ["", f"## Rules (each reads zero; {len(found)} broken)"]
    for letter, rule in RULES.items():
        count = sum(1 for item in found if item.rule == letter)
        lines.append(f"  {letter}: {count:<3} {rule.name}")
    lines += ["", "## Largest modules"]
    for name, size in module_sizes()[:10]:
        lines.append(f"  {size:>5}  {name}")
    lines += ["", "## Metrics (counted for review, not enforced)"]
    lines.append(f"  #1 entity dispatch points outside features: {len(entity_dispatch_points())}")
    lines.append(f"  #2 use case base abstractions: {len(use_case_base_hits())}")
    lines.append(
        "  #3 modules over 600 lines, a size worth a look at what one owns: "
        f"{sum(1 for _, n in module_sizes() if n > 600)}"
    )
    lines.append(f"  #13 re-export only modules: {len(facade_modules())}")
    orphans = sorted(name for name, who in readers().items() if not who)
    lines.append(
        f"  published views no reader names, which may still be read outside the model: "
        f"{', '.join(orphans) or 'none'}"
    )
    lines += ["", "## Import graph"]
    lines.append(f"  edges: {edge_count()}")
    lines.append(f"  cycles: {len(cycles())}")
    for loop in cycles():
        lines.append(f"    {' -> '.join([*loop, loop[0]])}")
    lines += ["", "## Violations"]
    lines += [f"  {item}" for item in found] or ["  none"]
    return "\n".join(lines)


def main() -> None:
    """The whole repository by default; one feature's map when it is named."""
    print(feature_map(sys.argv[1]) if len(sys.argv) > 1 else report())


if __name__ == "__main__":
    main()

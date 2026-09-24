"""The hub manifest validator — pure rules over one `recipe.json`, no I/O.

A hub tool is a tool a maker publishes on treg, made of other tools (docs/HUB-DECISIONS.md). Its
manifest is the reviewer's whole view of it: what it takes, which tools it may reach, what it
returns, what it costs. Every refusal here names the FIELD and the RULE, because the maker is
usually an agent that must fix the file without a person reading a stack trace.

Rules that came from Crawl4AI's recipes (the same idea, live for months; `LESSONS.md` there):
an input is required by having NO default (never a `required` key); every non-secret input carries
an `example` or a `default`, so the first run and the gallery both work; an `int` input names a
`max`, or the runner cannot clamp it; exactly one of `steps` | `script`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The ceilings of version one (HUB-DECISIONS round 2 q6). A manifest may declare smaller limits,
# never larger.
MAX_STEPS = 20
MAX_WALL_S = 120
MAX_INPUTS = 20
MAX_USES = 50
MAX_OUTPUT_FIELDS = 50
MAX_SUMMARY = 200
MAX_README = 4000
MAX_PRICE_USD = 100.0
MAX_COST_USD = 100.0
MAX_MARKUP_PERCENT = 100000.0     # percent: a sane cap; the hold is bounded by the caller's ceiling

# The maker's words (docs/hub-pricing-decisions.md round 3, 2026-09-18): per call, per result, or a
# percent of the provider fees. The 2026-09-14 names stay accepted and are stored canonically.
PRICING_MODES = ("per_call", "per_result", "percent")
PRICING_MODE_ALIASES = {"flat": "per_call", "per_unit": "per_result", "cost_plus": "percent"}
PRICING_KEY_ALIASES = {"per_unit_usd": "per_result_usd", "markup_percent": "percent"}
PRICING_KEYS = frozenset({"mode", "price_usd", "per_result_usd", "results_from", "percent", "max_price_usd",
                          "max_charge_usd"})
# A script's own-key steps cost the MAKER at vendors treg cannot see. `ctx.charge(usd, label)` lets the
# script bill the caller for one such step, and `pricing.max_charge_usd` is the most all charges may
# total in one run: the caller sees it before the run (owner + Jason, 2026-09-24). Without it,
# ctx.charge is refused.
MAX_CHARGES_PER_RUN = 20

INPUT_TYPES = ("string", "int", "float", "bool", "list", "object")
INPUT_KEYS = frozenset({"type", "default", "max", "min", "secret", "example", "note"})
STEP_KEYS = frozenset({"name", "call", "method", "input", "for_each", "as", "skip_if_empty", "writes", "allow_fail"})
LIMIT_KEYS = frozenset({"steps", "wall_s", "cost_usd"})
MANIFEST_KEYS = frozenset({
    "name", "version", "summary", "writes", "inputs", "uses", "limits", "price_usd", "pricing",
    "steps", "script", "output",
})

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")        # the tool's name: `leads-db`
_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")        # input names, step names, output fields
_CATALOG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)+$")
SCRIPT_FILE = "run.js"


class ManifestError(ValueError):
    """One refused manifest: `field` (a dotted path into the file) and `rule` (what it broke)."""

    def __init__(self, field: str, rule: str) -> None:
        super().__init__(f"{field}: {rule}")
        self.field = field
        self.rule = rule


@dataclass(frozen=True)
class Validated:
    """A manifest that passed: normalized values the store and the runner rely on."""

    name: str
    kind: str                 # "steps" | "script"
    summary: str
    writes: bool
    inputs: dict[str, dict[str, Any]]
    uses: list[str]
    limits: dict[str, Any]    # steps, wall_s, cost_usd (cost_usd may be None)
    price_micro: int          # the flat reserve price; 0 for per_unit and cost_plus
    pricing: dict[str, Any]   # normalized money as micro ints: mode + per_result/percent/max_price/results_max
    steps: list[dict[str, Any]] | None
    script: str | None
    output: dict[str, Any]
    manifest: dict[str, Any]  # the normalized file, what gets stored


def _fail(field: str, rule: str) -> ManifestError:
    return ManifestError(field, rule)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool))


def validate(
    raw: Any,
    *,
    catalog_ids: set[str],
    own_tools: set[str],
    hub_ids: frozenset[str] | set[str] = frozenset(),
) -> Validated:
    """Validate one manifest. `catalog_ids` and `own_tools` are the two universes `uses` may
    name; `hub_ids` are refused by name (a hub tool may not use a hub tool, depth one)."""
    if not isinstance(raw, dict):
        raise _fail("manifest", "must be a JSON object")
    unknown = sorted(set(raw) - MANIFEST_KEYS)
    if unknown:
        raise _fail(unknown[0], "unknown field (allowed: " + ", ".join(sorted(MANIFEST_KEYS)) + ")")

    name = raw.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise _fail("name", "2-40 characters, lowercase letters, digits and dashes, starting with a letter")

    if "version" in raw and not (_is_int(raw["version"]) and raw["version"] >= 1):
        raise _fail("version", "a positive integer; treg assigns it on publish, you may omit it")

    summary = raw.get("summary")
    if not isinstance(summary, str) or not (1 <= len(summary.strip()) <= MAX_SUMMARY):
        raise _fail("summary", f"required, 1-{MAX_SUMMARY} characters; an agent reads this first")
    summary = summary.strip()

    writes = raw.get("writes", False)
    if not isinstance(writes, bool):
        raise _fail("writes", "true or false")

    inputs = _validate_inputs(raw.get("inputs", {}))
    has_steps, has_script = "steps" in raw, "script" in raw
    if has_steps == has_script:
        raise _fail("steps", "exactly one of `steps` or `script` (found "
                    + ("both" if has_steps else "neither") + ")")
    # A script may use nothing: a tool that serves its uploaded CSV (ctx.data) makes no call.
    uses = _validate_uses(raw.get("uses", []), catalog_ids, own_tools, hub_ids, allow_empty=has_script)
    limits = _validate_limits(raw.get("limits", {}))

    if has_script:
        kind = "script"
        script = raw["script"]
        if script != SCRIPT_FILE:
            raise _fail("script", f"must be {SCRIPT_FILE!r}, the file beside the manifest")
        steps = None
        output = _validate_output_script(raw.get("output"))
        output_fields = set(output["fields"])
    else:
        kind = "steps"
        script = None
        steps = _validate_steps(raw["steps"], uses, limits["steps"])
        output = _validate_output_steps(raw.get("output"))
        output_fields = set(output)

    pricing_public, pricing_micro = _validate_pricing(raw, uses, output_fields)
    price_micro = pricing_micro["price_micro"]

    manifest = {
        "name": name, "summary": summary, "writes": writes, "inputs": inputs, "uses": uses,
        "limits": limits, "price_usd": price_micro / 1_000_000, "pricing": pricing_public,
        **({"steps": steps} if steps is not None else {"script": script}),
        "output": output,
    }
    return Validated(name=name, kind=kind, summary=summary, writes=writes, inputs=inputs,
                     uses=uses, limits=limits, price_micro=price_micro, pricing=pricing_micro,
                     steps=steps, script=script, output=output, manifest=manifest)


def _validate_inputs(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise _fail("inputs", "an object of input name → spec")
    if len(raw) > MAX_INPUTS:
        raise _fail("inputs", f"at most {MAX_INPUTS} inputs")
    out: dict[str, dict[str, Any]] = {}
    for key, spec in raw.items():
        path = f"inputs.{key}"
        if not isinstance(key, str) or not _IDENT_RE.match(key):
            raise _fail(path, "input names are 1-32 characters, lowercase letters, digits and underscores")
        if not isinstance(spec, dict):
            raise _fail(path, "an object with at least `type`")
        if "required" in spec:
            raise _fail(f"{path}.required", "not a key: an input is required by having no `default`")
        extra = sorted(set(spec) - INPUT_KEYS)
        if extra:
            raise _fail(f"{path}.{extra[0]}", "unknown key (allowed: " + ", ".join(sorted(INPUT_KEYS)) + ")")
        typ = spec.get("type")
        if typ not in INPUT_TYPES:
            raise _fail(f"{path}.type", "one of " + ", ".join(INPUT_TYPES))
        secret = spec.get("secret", False)
        if not isinstance(secret, bool):
            raise _fail(f"{path}.secret", "true or false")
        if secret and typ != "string":
            raise _fail(f"{path}.secret", "a secret input must be a string")
        if secret and "default" in spec:
            raise _fail(f"{path}.default", "a secret input has no default")
        if not secret and "example" not in spec and "default" not in spec:
            raise _fail(path, "needs an `example` or a `default`, so the first run works")
        if typ == "int":
            if "max" not in spec:
                raise _fail(f"{path}.max", "an int input needs a `max`, or the runner cannot clamp it")
            if not _is_int(spec["max"]) or spec["max"] < 1:
                raise _fail(f"{path}.max", "a positive integer")
            if "min" in spec and (not _is_int(spec["min"]) or spec["min"] > spec["max"]):
                raise _fail(f"{path}.min", "an integer no larger than `max`")
            if "default" in spec and (not _is_int(spec["default"])
                                      or not spec.get("min", 0) <= spec["default"] <= spec["max"]):
                raise _fail(f"{path}.default", "an integer within min..max")
        elif "max" in spec or "min" in spec:
            raise _fail(f"{path}.max", "`min`/`max` apply to int inputs only")
        if "note" in spec and not isinstance(spec["note"], str):
            raise _fail(f"{path}.note", "a string")
        out[key] = {k: spec[k] for k in INPUT_KEYS if k in spec}
        out[key].setdefault("secret", False)
    return out


def _validate_uses(raw: Any, catalog_ids: set[str], own_tools: set[str],
                   hub_ids: frozenset[str] | set[str], allow_empty: bool = False) -> list[str]:
    if not isinstance(raw, list) or (not raw and not allow_empty):
        raise _fail("uses", "a non-empty list of the tools this recipe may call")
    # The runner tells an own tool from a catalog id by the dot. An own tool NAMED like a catalog
    # id would run as the caller and could later resolve to a hub tool of that name: a nested
    # run with an undisclosed price (8.1 review). Refused here, by name.
    for i, u in enumerate(raw):
        if isinstance(u, str) and u in own_tools and "." in u:
            raise _fail(f"uses[{i}]", f"own tool {u!r} is named like a catalog id; rename it without a dot")
    if len(raw) > MAX_USES:
        raise _fail("uses", f"at most {MAX_USES} entries")
    seen: list[str] = []
    for i, entry in enumerate(raw):
        path = f"uses[{i}]"
        if not isinstance(entry, str) or not entry:
            raise _fail(path, "a catalog id or one of your own tool names")
        if entry in hub_ids:
            raise _fail(path, f"{entry!r} is a hub tool; a hub tool may not use a hub tool")
        if entry in catalog_ids or entry in own_tools:
            if entry not in seen:
                seen.append(entry)
            continue
        if _CATALOG_ID_RE.match(entry):
            raise _fail(path, f"{entry!r} is not a catalog id (catalog_search finds valid ids)")
        raise _fail(path, f"{entry!r} is not one of your team's tools (register it first: treg tool add)")
    return seen


def _validate_limits(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _fail("limits", "an object with steps, wall_s, cost_usd")
    extra = sorted(set(raw) - LIMIT_KEYS)
    if extra:
        raise _fail(f"limits.{extra[0]}", "unknown key (allowed: steps, wall_s, cost_usd)")
    steps = raw.get("steps", MAX_STEPS)
    if not _is_int(steps) or not 1 <= steps <= MAX_STEPS:
        raise _fail("limits.steps", f"an integer 1-{MAX_STEPS}")
    wall = raw.get("wall_s", MAX_WALL_S)
    if not _is_int(wall) or not 1 <= wall <= MAX_WALL_S:
        raise _fail("limits.wall_s", f"an integer 1-{MAX_WALL_S} seconds")
    cost = raw.get("cost_usd")
    if cost is not None and (not _is_number(cost) or not 0 < cost <= MAX_COST_USD):
        raise _fail("limits.cost_usd", f"a number above 0 and at most {MAX_COST_USD}")
    return {"steps": steps, "wall_s": wall, "cost_usd": cost}


def _validate_price(raw: Any) -> int:
    import math
    if _is_number(raw) and not math.isfinite(float(raw)):
        raise _fail("price_usd", "a finite number")
    if not _is_number(raw) or raw < 0 or raw > MAX_PRICE_USD:
        raise _fail("price_usd", f"a number from 0 to {MAX_PRICE_USD} (dollars per successful run; 0 = free)")
    micro = round(float(raw) * 1_000_000)
    if abs(micro - float(raw) * 1_000_000) > 1e-6:
        raise _fail("price_usd", "at most 6 decimal places (treg prices in micro-dollars)")
    return int(micro)


def _money_micro(field: str, raw: Any, *, allow_zero: bool) -> int:
    """A dollar amount in the range 0..MAX_PRICE_USD, at most 6 decimals, as a micro-dollar int."""
    import math
    if not _is_number(raw) or not math.isfinite(float(raw)):
        raise _fail(field, "a finite number")
    if raw < 0 or raw > MAX_PRICE_USD:
        raise _fail(field, f"a number from 0 to {MAX_PRICE_USD} (dollars)")
    micro = round(float(raw) * 1_000_000)
    if abs(micro - float(raw) * 1_000_000) > 1e-6:
        raise _fail(field, "at most 6 decimal places (treg prices in micro-dollars)")
    if not allow_zero and micro <= 0:
        raise _fail(field, "a number above 0")
    return int(micro)


def _only(block: dict[str, Any], allowed: set[str], path: str) -> None:
    """Refuse a field that this pricing mode does not use, by name."""
    bad = sorted(set(block) - allowed)
    if bad:
        raise _fail(f"{path}.{bad[0]}", "not used by this mode (allowed: " + ", ".join(sorted(allowed)) + ")")


def _validate_pricing(raw: dict[str, Any], uses: list[str],
                      output_fields: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (public, micro): the normalized `pricing` block for the stored manifest, and the same
    numbers as micro-dollar integers for the runner. Three modes, one per tool version, decided in
    docs/hub-pricing-decisions.md (2026-09-14). A manifest with a top-level `price_usd` and no
    `pricing` block stays flat, exactly as before."""
    import math
    if "pricing" not in raw:
        micro = _validate_price(raw.get("price_usd", 0))          # legacy: a per-call price, or free
        return ({"mode": "per_call", "price_usd": micro / 1_000_000}, _micro_block("per_call", price_micro=micro))
    if "price_usd" in raw:
        raise _fail("price_usd", "not a top-level field when `pricing` is present; put it inside `pricing`")
    block = raw["pricing"]
    if not isinstance(block, dict):
        raise _fail("pricing", "an object with `mode` and the numbers that mode needs")
    block = canon_pricing(block)
    extra = sorted(set(block) - PRICING_KEYS)
    if extra:
        raise _fail(f"pricing.{extra[0]}", "unknown key (allowed: " + ", ".join(sorted(PRICING_KEYS)) + ")")
    mode = block.get("mode")
    if mode not in PRICING_MODES:
        raise _fail("pricing.mode", "one of " + ", ".join(PRICING_MODES) + " (per_call: a fixed price per "
                    "successful run; per_result: a price per result returned; percent: a percent of the run's "
                    "provider fees)")
    charge_micro = (_money_micro("pricing.max_charge_usd", block.get("max_charge_usd"), allow_zero=False)
                    if block.get("max_charge_usd") is not None else 0)
    if charge_micro and not raw.get("script"):
        raise _fail("pricing.max_charge_usd", "only a script tool can call ctx.charge; a JSON recipe has no own-key step to bill")
    public_charge = {"max_charge_usd": charge_micro / 1_000_000} if charge_micro else {}

    if mode == "per_call":
        _only(block, {"mode", "price_usd", "max_charge_usd"}, "pricing")
        micro = _money_micro("pricing.price_usd", block.get("price_usd", 0), allow_zero=True)
        return ({"mode": "per_call", "price_usd": micro / 1_000_000, **public_charge},
                _micro_block("per_call", price_micro=micro, max_charge_micro=charge_micro))

    # An optional ceiling on the maker's part. Never required: the hold is bounded by the caller's run
    # ceiling (percent) or by the results input (per_result); a maker who wants a lower cap may say so.
    max_micro = (_money_micro("pricing.max_price_usd", block.get("max_price_usd"), allow_zero=False)
                 if block.get("max_price_usd") is not None else 0)
    public_cap = {"max_price_usd": max_micro / 1_000_000} if max_micro else {}

    if mode == "per_result":
        _only(block, {"mode", "per_result_usd", "results_from", "max_price_usd", "max_charge_usd"}, "pricing")
        unit_micro = _money_micro("pricing.per_result_usd", block.get("per_result_usd"), allow_zero=False)
        if max_micro and max_micro < unit_micro:
            raise _fail("pricing.max_price_usd", "at least `per_result_usd`")
        if not ({"results", "units"} & output_fields):
            raise _fail("output", "a per_result tool must return an integer `results` field (the count to bill)")
        results_from = block.get("results_from")
        results_max = 0
        if results_from is not None:
            spec = (raw.get("inputs") or {}).get(results_from) if isinstance(raw.get("inputs"), dict) else None
            if (not isinstance(results_from, str) or not isinstance(spec, dict) or spec.get("type") != "int"
                    or not isinstance(spec.get("max"), int) or spec["max"] < 1):
                raise _fail("pricing.results_from", "the name of an `int` input with a `max` (the most results one run returns)")
            results_max = int(spec["max"])
        elif not max_micro:
            raise _fail("pricing.results_from", "name the `int` input whose `max` bounds the results (or give `max_price_usd`)")
        public = {"mode": "per_result", "per_result_usd": unit_micro / 1_000_000, **public_cap, **public_charge}
        if results_from is not None:
            public["results_from"] = results_from
        return (public, _micro_block("per_result", per_result_micro=unit_micro, max_price_micro=max_micro,
                                     results_from=results_from, results_max=results_max,
                                     max_charge_micro=charge_micro))

    # percent: the maker earns that percent of the run's provider fees (the catalog steps).
    _only(block, {"mode", "percent", "max_price_usd", "max_charge_usd"}, "pricing")
    mp: Any = block.get("percent")
    if not _is_number(mp) or not math.isfinite(float(mp)) or mp <= 0 or mp > MAX_MARKUP_PERCENT:
        raise _fail("pricing.percent", f"a number above 0 and at most {MAX_MARKUP_PERCENT}")
    percent_micro = round(float(mp) / 100 * 1_000_000)             # 30 percent -> 300000
    if abs(percent_micro - float(mp) / 100 * 1_000_000) > 1e-6:
        raise _fail("pricing.percent", "at most 4 decimal places")
    if not any("." in u for u in uses):
        raise _fail("pricing.mode",
                    "percent needs at least one catalog tool in `uses` (an own-tool run has no provider fees)")
    return ({"mode": "percent", "percent": float(mp), **public_cap, **public_charge},
            _micro_block("percent", percent_micro=percent_micro, max_price_micro=max_micro,
                         max_charge_micro=charge_micro))


def _micro_block(mode: str, *, price_micro: int = 0, per_result_micro: int = 0, percent_micro: int = 0,
                 max_price_micro: int = 0, results_from: str | None = None, results_max: int = 0,
                 max_charge_micro: int = 0) -> dict[str, Any]:
    """The runner's view of a pricing block: every key present, micro-dollar ints, 0 = none."""
    return {"mode": mode, "price_micro": price_micro, "per_result_micro": per_result_micro,
            "percent_micro": percent_micro, "max_price_micro": max_price_micro,
            "results_from": results_from, "results_max": results_max, "max_charge_micro": max_charge_micro}


def canon_pricing(block: dict[str, Any]) -> dict[str, Any]:
    """A pricing block with the 2026-09-18 names: the old mode names and keys map onto them, so a
    stored manifest from before the rename reads the same."""
    out = {PRICING_KEY_ALIASES.get(k, k): v for k, v in block.items()}
    if "mode" in out:
        out["mode"] = PRICING_MODE_ALIASES.get(out["mode"], out["mode"])
    return out


def _validate_steps(raw: Any, uses: list[str], max_steps: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise _fail("steps", "a non-empty list of steps")
    if len(raw) > max_steps:
        raise _fail("steps", f"at most {max_steps} steps (limits.steps)")
    names: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, step in enumerate(raw):
        path = f"steps[{i}]"
        if not isinstance(step, dict):
            raise _fail(path, "an object with name, call, input")
        extra = sorted(set(step) - STEP_KEYS)
        if extra:
            raise _fail(f"{path}.{extra[0]}", "unknown key (allowed: " + ", ".join(sorted(STEP_KEYS)) + ")")
        name = step.get("name")
        if not isinstance(name, str) or not _IDENT_RE.match(name):
            raise _fail(f"{path}.name", "1-32 characters, lowercase letters, digits and underscores")
        if name in names:
            raise _fail(f"{path}.name", f"duplicate step name {name!r}")
        if name == "input":
            raise _fail(f"{path}.name", "`input` is reserved for the caller's inputs")
        names.add(name)
        call = step.get("call")
        # A catalog id as is, or one of the maker's own tools as `<tool>/<path>` (the tool name
        # must be in `uses`; the path is the upstream path under its base url).
        target = call.split("/", 1)[0] if isinstance(call, str) else None
        if not isinstance(call, str) or target not in uses:
            raise _fail(f"{path}.call", f"{call!r} is not in `uses`; every tool a step calls must be declared there")
        if "/" in call and "." in target:
            raise _fail(f"{path}.call", f"{target!r} is a catalog id; it takes no path (a path belongs to one of your own tools)")
        method = step.get("method", None)
        if method is not None and (not isinstance(method, str)
                                   or method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE")):
            raise _fail(f"{path}.method", "one of GET, POST, PUT, PATCH, DELETE")
        inp = step.get("input", {})
        if not isinstance(inp, dict):
            raise _fail(f"{path}.input", "an object of parameter → value or reference")
        for_each, as_name = step.get("for_each"), step.get("as")
        if (for_each is None) != (as_name is None):
            raise _fail(f"{path}.for_each", "`for_each` and `as` go together")
        if for_each is not None:
            if not isinstance(for_each, str) or not for_each.startswith("$"):
                raise _fail(f"{path}.for_each", "a reference to a list, like $people.contacts")
            if not isinstance(as_name, str) or not _IDENT_RE.match(as_name) or as_name in names or as_name == "input":
                raise _fail(f"{path}.as", "a fresh identifier for the current item")
        skip = step.get("skip_if_empty")
        if skip is not None and (not isinstance(skip, str) or not skip.startswith("$")):
            raise _fail(f"{path}.skip_if_empty", "a reference; the step is skipped when it is empty")
        if "writes" in step and not isinstance(step["writes"], bool):
            raise _fail(f"{path}.writes", "true or false")
        if "allow_fail" in step and not isinstance(step["allow_fail"], bool):
            raise _fail(f"{path}.allow_fail", "true or false")
        for where, value in (("input", inp), ("for_each", for_each), ("skip_if_empty", skip)):
            bad = _bad_ref(value)
            if bad is not None:
                raise _fail(f"{path}.{where}", f"{bad!r} is not a valid reference (see the reference language)")
        out.append({k: step[k] for k in STEP_KEYS if k in step} | {"input": inp})
    from . import graph as _graph   # local: graph imports ManifestError from here
    _graph.build(out)               # unknown-step references and cycles are refused at publish
    return out


def own_names(uses: list[str]) -> set[str]:
    """The entries of `uses` that are a team's own tool names (no dot) rather than catalog ids."""
    return {u for u in uses if "." not in u}


def pricing_micro(manifest: dict[str, Any]) -> dict[str, Any]:
    """The stored `pricing` block as micro-dollar integers, for the runner. A manifest from before
    the pricing block (only `price_usd`) reads as flat. The keys mirror Validated.pricing:
    mode, price_micro, per_unit_micro, markup_micro, max_price_micro."""
    p = _stored_pricing(manifest)

    def m(x: Any) -> int:
        return int(round(float(x or 0) * 1_000_000))

    mode = p.get("mode", "per_call")
    cap = m(p.get("max_price_usd")) if p.get("max_price_usd") else 0
    charge = m(p.get("max_charge_usd")) if p.get("max_charge_usd") else 0
    if mode == "per_result":
        rf = p.get("results_from")
        spec = (manifest.get("inputs") or {}).get(rf) if rf else None
        results_max = int(spec.get("max", 0)) if isinstance(spec, dict) else 0
        return _micro_block("per_result", per_result_micro=m(p["per_result_usd"]), max_price_micro=cap,
                            results_from=rf, results_max=results_max, max_charge_micro=charge)
    if mode == "percent":
        return _micro_block("percent", percent_micro=int(round(float(p["percent"]) / 100 * 1_000_000)),
                            max_price_micro=cap)
    return _micro_block("per_call", price_micro=m(p.get("price_usd", 0)), max_charge_micro=charge)


def _stored_pricing(manifest: dict[str, Any]) -> dict[str, Any]:
    """The pricing block of a stored manifest in canonical names; a manifest from before the block
    (only `price_usd`) reads as per_call."""
    return canon_pricing(manifest.get("pricing") or {"mode": "per_call", "price_usd": manifest.get("price_usd", 0)})


def price_label(manifest: dict[str, Any]) -> str:
    """The maker's price in the maker's words: "$0.15 per call", "$0.02 per result", "5% of provider
    fees". What the maker earns on a successful run; the provider fees (the metered steps) are billed
    to the caller on top. A caller reads `range_label` instead."""
    p = _stored_pricing(manifest)
    mode = p.get("mode", "per_call")
    if mode == "per_result":
        return f"${float(p['per_result_usd']):.6g} per result" + _charge_suffix(p)
    if mode == "percent":
        return f"{float(p['percent']):.6g}% of provider fees" + _charge_suffix(p)
    price = float(p.get("price_usd", 0) or 0)
    base = f"${price:.6g} per call" if price else "free"
    return base + _charge_suffix(p)


def _charge_suffix(p: dict[str, Any]) -> str:
    """The run-level cap on ctx.charge lines, when the tool declares one: the caller reads it as
    part of the price, since those lines are billed to them."""
    cap = p.get("max_charge_usd")
    return f" + own-key steps up to ${float(cap):.6g} per run" if cap else ""


def results_of(output: Any) -> int | None:
    """The integer count a per_result run returns (`results`, or the older `units`), else None."""
    if not isinstance(output, dict):
        return None
    u = output.get("results", output.get("units"))
    return u if isinstance(u, int) and not isinstance(u, bool) and u >= 0 else None


def seller_part_micro(manifest: dict[str, Any], steps_micro: int, observed_price_micro: int | None,
                      output: Any = None) -> int:
    """What the seller earns on one run, for the price range: the observed price when a caller
    paid one (`observed_price_micro`, a run by another team), else derived from the pricing block
    and the run itself (the maker's own runs and the checks pay no seller price, but a caller
    would have): per_call, the price; percent, that percent of `steps_micro`; per_result, the
    count the run returned times the per-result price. A declared cap applies when present."""
    if observed_price_micro is not None:
        return int(observed_price_micro)
    p = pricing_micro(manifest)
    cap = p["max_price_micro"] or None
    if p["mode"] == "percent":
        part = (int(steps_micro) * p["percent_micro"] + 500_000) // 1_000_000
    elif p["mode"] == "per_result":
        part = (results_of(output) or 0) * p["per_result_micro"]
    else:
        part = p["price_micro"]
    return min(part, cap) if cap else part


def range_label(manifest: dict[str, Any], low_micro: int | None, high_micro: int | None) -> str:
    """The headline price a caller reads: what one successful run has actually cost, steps and
    seller price together, over recent runs (docs/hub-pricing-decisions.md, 2026-09-18). One
    number when every run cost the same, a range otherwise, and before any run the declared
    worst case with the steps unknown."""
    p = _stored_pricing(manifest)
    mode = p.get("mode", "per_call")
    fees = " + provider fees" if any("." in u for u in manifest.get("uses", [])) else ""
    lead = f"${float(p['per_result_usd']):.6g}/result" if mode == "per_result" else ""
    if low_micro is None or high_micro is None:
        if mode == "per_result":
            return lead + fees
        if mode == "percent":
            return f"provider fees + {float(p['percent']):.6g}%"
        price = float(p.get("price_usd", 0) or 0)
        return (f"${price:.6g}/run" if price else "free") + fees
    lo, hi = low_micro / 1_000_000, high_micro / 1_000_000
    span = (f"${lo:.6g}/run" if low_micro else "free") if low_micro == high_micro else f"${lo:.6g}–${hi:.6g}/run"
    return f"{lead} · {span}" if lead else span


def _bad_ref(value: Any) -> str | None:
    """The first reference-looking token that does not parse, or None."""
    from . import refs
    if isinstance(value, str):
        for m in re.finditer(r"\$[A-Za-z0-9_.\[\]]+", value):
            token = m.group(0)
            try:
                refs.parse(token)
            except refs.RefError:
                # a template may end a reference at punctuation; accept if a prefix parses
                if not any(refs.find(token)):
                    return token
        return None
    if isinstance(value, dict):
        for v in value.values():
            b = _bad_ref(v)
            if b is not None:
                return b
    if isinstance(value, list):
        for v in value:
            b = _bad_ref(v)
            if b is not None:
                return b
    return None


def _validate_output_steps(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        raise _fail("output", "an object mapping each output field to a reference, like {\"leads\": \"$verify[]\"}")
    if len(raw) > MAX_OUTPUT_FIELDS:
        raise _fail("output", f"at most {MAX_OUTPUT_FIELDS} fields")
    for key, ref in raw.items():
        if not isinstance(key, str) or not _IDENT_RE.match(key):
            raise _fail(f"output.{key}", "field names are 1-32 characters, lowercase letters, digits and underscores")
        if not isinstance(ref, str) or not ref.startswith("$"):
            raise _fail(f"output.{key}", "a reference starting with $ (a literal value is not an output)")
        from . import refs
        try:
            refs.parse(ref)
        except refs.RefError:
            raise _fail(f"output.{key}", f"{ref!r} is not a valid reference") from None
    return dict(raw)


def _validate_output_script(raw: Any) -> dict[str, Any]:
    fields = raw.get("fields") if isinstance(raw, dict) else None
    if not isinstance(fields, list) or not fields:
        raise _fail("output.fields", "a non-empty list of the top-level field names the script returns")
    if len(fields) > MAX_OUTPUT_FIELDS:
        raise _fail("output.fields", f"at most {MAX_OUTPUT_FIELDS} fields")
    for f in fields:
        if not isinstance(f, str) or not _IDENT_RE.match(f):
            raise _fail("output.fields", f"{f!r}: field names are 1-32 characters, lowercase letters, digits and underscores")
    if set(raw) - {"fields"}:
        raise _fail("output", "for a script, only `fields` (the runner checks the returned object has them)")
    return {"fields": list(dict.fromkeys(fields))}


def validate_check(raw: Any, inputs: dict[str, dict[str, Any]], output_fields: list[str]) -> dict[str, Any]:
    """`check.json`: sample inputs and the least the answer must contain. Validated against the
    manifest so a check that can never pass is refused before anyone pays for it."""
    if not isinstance(raw, dict):
        raise _fail("check", "must be a JSON object with `inputs` and `fields`")
    extra = sorted(set(raw) - {"inputs", "fields", "min_rows"})
    if extra:
        raise _fail(f"check.{extra[0]}", "unknown key (allowed: inputs, fields, min_rows)")
    sample = raw.get("inputs")
    if not isinstance(sample, dict):
        raise _fail("check.inputs", "an object of input name → sample value")
    for key in sample:
        if key not in inputs:
            raise _fail(f"check.inputs.{key}", "not an input of this recipe")
    for key, spec in inputs.items():
        if "default" not in spec and key not in sample:
            raise _fail(f"check.inputs.{key}", "required input (no default) is missing from the sample")
    fields = raw.get("fields")
    if not isinstance(fields, list) or not fields:
        raise _fail("check.fields", "a non-empty list of output fields the check must find")
    for f in fields:
        if f not in output_fields:
            raise _fail("check.fields", f"{f!r} is not an output field of this recipe")
    min_rows = raw.get("min_rows", 0)
    if not _is_int(min_rows) or min_rows < 0:
        raise _fail("check.min_rows", "an integer ≥ 0")
    return {"inputs": sample, "fields": list(fields), "min_rows": min_rows}


def validate_readme(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise _fail("readme", "required: what the tool does, for a human")
    if len(raw) > MAX_README:
        raise _fail("readme", f"at most {MAX_README} characters")
    return raw

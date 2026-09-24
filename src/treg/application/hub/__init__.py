"""The tool hub use cases: publish a tool, find the version that serves an id.

Phase 1 of the hub (docs/HUB-DECISIONS.md, `.arcterm` plan): a tool is validated and STORED here,
nothing runs yet. The check run that turns `unchecked` into `live` arrives with the runner, so
every row this phase writes stays `unchecked` and the call road answers 501 for it — the honest
shape until the runner exists (CLAUDE.md: never document, or serve, what is not built).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...domain.catalog import store as catalog_store
from ...domain.hub import ManifestError, price_label, stored_pricing, validate, validate_check, validate_readme
from ...models import HubTool, Org, Tool

HUB_ID_MIN_PARTS = 2


def enabled() -> bool:
    """The plain flag: the hub exists on this registry. Pages with no caller (the share page, the
    agent-facing files) use this one."""
    return bool(get_settings().hub_enabled)


def enabled_for(org_slug: str | None) -> bool:
    """The hub exists AND this team may use it: the flag, then `TREG_HUB_TEAMS` when it is set
    (empty = every team). Every gate that has a caller uses this one, so a team outside the list
    sees exactly what it sees with the flag off: 404 on the routes, no hub rows in search, the
    hub ids unknown on /call/ and in MCP."""
    s = get_settings()
    if not s.hub_enabled:
        return False
    teams = s.hub_team_set
    return not teams or (org_slug or "").lower() in teams


def is_hub_id_shape(rest: str) -> bool:
    """`<team-slug>.<name>[@N]`: dotted, slash-free, not a URL. Whether it IS a hub tool is the
    database's answer (`tool_for`); this only says the call road may ask."""
    if "/" in rest or rest.startswith("http") or "." not in rest:
        return False
    base = rest.split("@", 1)[0]
    return base.count(".") >= HUB_ID_MIN_PARTS - 1


def split_id(rest: str) -> tuple[str, int | None]:
    """`acme.leads-db@3` → (`acme.leads-db`, 3); no pin → (`acme.leads-db`, None)."""
    base, _, pin = rest.partition("@")
    if not pin:
        return base, None
    try:
        return base, int(pin)
    except ValueError:
        return base, None


OLD_VERSION_DAYS = 30   # a pinned old version stays callable this long after a newer live one


async def tool_for(db: AsyncSession, rest: str, *, live_only: bool = True,
                   caller_org_id: int | None = None, caller_slug: str | None = None) -> HubTool | None:
    """The version that serves `rest`: the newest `live` one, or `@N` pinned. A pinned version may
    also be the one UNDER CHECK (the check run pins it: HUB-DECISIONS round 2 q10), and a pinned
    old version stays callable for OLD_VERSION_DAYS after a newer live one exists (round 4 q8)."""
    # The public views (catalog get, the share page) pass no caller and get the plain flag: a
    # contract is readable. A CALL names its caller's team and goes through the allow-list.
    if not (enabled_for(caller_slug) if caller_slug is not None else enabled()) or not is_hub_id_shape(rest):
        return None
    tool_id, pin = split_id(rest)
    if pin is None:
        q = (select(HubTool).where(HubTool.tool_id == tool_id, HubTool.status == "live")
             .order_by(HubTool.version.desc()).limit(1))
        return (await db.execute(q)).scalars().first()
    row = (await db.execute(select(HubTool).where(HubTool.tool_id == tool_id, HubTool.version == pin))).scalars().first()
    if row is None:
        return None
    if row.status == "checking":
        # the check run pins it (round 2 q10); anyone else who guesses @N gets nothing (8.1 review)
        return row if caller_org_id is None or row.org_id == caller_org_id else None
    if live_only and row.status != "live":
        return None
    newer = (await db.execute(
        select(HubTool.created_at).where(HubTool.tool_id == tool_id, HubTool.status == "live",
                                         HubTool.version > pin)
        .order_by(HubTool.version.asc()).limit(1))).scalar_one_or_none()
    if newer is not None and (_utcnow() - newer).days >= OLD_VERSION_DAYS:
        return None
    return row


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Published:
    tool_id: str
    version: int
    status: str
    kind: str


MAX_DATA_BYTES = 5_000_000   # the engine holds 64 MB; the rows travel into it on every run (8.1 review)


def validate_data(data: Any) -> str | None:
    """The uploaded CSV: text, at most 50 MB, a header row and at least one data row, parseable."""
    import csv
    import io
    if data is None or data == "":
        return None
    if not isinstance(data, str):
        raise ManifestError("data", "the CSV as text (the contents of data.csv)")
    if len(data.encode("utf-8")) > MAX_DATA_BYTES:
        raise ManifestError("data", "at most 5 MB")
    try:
        reader = csv.reader(io.StringIO(data))
        header = next(reader, None)
        first = next(reader, None)
    except csv.Error as exc:
        raise ManifestError("data", f"not valid CSV: {exc}") from None
    if not header or not any(h.strip() for h in header):
        raise ManifestError("data", "the first row must be the header (column names)")
    if first is None:
        raise ManifestError("data", "at least one data row under the header")
    return data


async def publish(
    db: AsyncSession, *, org: Org, maker_email: str,
    manifest: Any, script: str | None, check: Any, readme: Any, data: Any = None,
) -> Published:
    """Validate the four files against this team's world and store one version. Raises
    ManifestError with field + rule; the HTTP layer turns it into a 422 the maker's agent can act
    on. Does NOT commit: the caller's transaction owns it."""
    own_tools = {
        name for (name,) in (await db.execute(
            select(Tool.name).where(Tool.org_id == org.id))).all()
    }
    hub_ids = {
        tid for (tid,) in (await db.execute(select(HubTool.tool_id).distinct())).all()
    }
    v = validate(manifest, catalog_ids=set(catalog_store.load().by_id),
                 own_tools=own_tools, hub_ids=hub_ids)
    if v.kind == "script":
        if not isinstance(script, str) or not script.strip():
            raise ManifestError("script", "the manifest names run.js; send its contents as `script`")
        if len(script) > 200_000:
            raise ManifestError("script", "at most 200,000 characters")
    elif script:
        raise ManifestError("script", "a steps recipe carries no script; remove it or switch to \"script\": \"run.js\"")
    output_fields = (v.output["fields"] if v.kind == "script" else list(v.output))
    check_v = validate_check(check, v.inputs, output_fields)
    readme_v = validate_readme(readme)
    data_v = validate_data(data)
    if data_v is not None and v.kind != "script":
        raise ManifestError("data", "an uploaded CSV is read by a script (ctx.data); a steps recipe has no reader for it")

    tool_id = f"{org.slug}.{v.name}"
    newest = (await db.execute(
        select(HubTool.version).where(HubTool.tool_id == tool_id)
        .order_by(HubTool.version.desc()).limit(1))).scalar_one_or_none()
    version = 1 if newest is None else newest + 1
    row = HubTool(
        org_id=org.id, tool_id=tool_id, name=v.name, version=version, kind=v.kind,
        status="checking", summary=v.summary, writes=v.writes, price_micro=v.price_micro,
        manifest={**v.manifest, "version": version}, script=script if v.kind == "script" else None,
        check=check_v, readme=readme_v, created_by=maker_email, data=data_v,
    )
    db.add(row)
    await db.flush()
    return Published(tool_id=tool_id, version=version, status=row.status, kind=v.kind)


async def run_check(db: AsyncSession, row: HubTool, *, maker_headers: dict[str, str], app: Any) -> dict[str, Any]:
    """The check run (HUB-DECISIONS round 2 q10, round 3 q6): `check.json`'s sample inputs, run
    ONCE for real as the maker over the real call road (`POST /call/<id>@<version>` in-process
    with the maker's own identity headers), charged to the maker's balance at the normal step
    prices, seller price not charged. Pass ⇒ `live`; fail ⇒ `failed`, with the reason on the row.
    `app` is the running application (the router passes `request.app`), so this layer never
    imports the HTTP entry point. Returns the verdict dict, also stored as `check_result`. Does not commit."""
    import httpx

    from ...application.hub import runner as hub_runner

    headers = {k: v for k, v in maker_headers.items() if k.lower() in ("x-treg-token", "x-treg-org", "cookie")}
    headers["X-Treg-Client"] = "hub-check"
    headers[hub_runner.RUN_MAX_COST_HEADER] = "5.00"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://treg.internal",
                                     headers=headers, timeout=200.0) as client:
            r = await client.post(f"/call/{row.tool_id}@{row.version}", json=row.check.get("inputs", {}))
        from .health import verdict_from
        try:
            body = r.json()
        except ValueError:
            body = {"text": r.text[:600]}
        verdict = verdict_from(row, r.status_code, body, dict(r.headers))
        row.status = "live" if verdict["status"] == "passed" else "failed"
        row.check_result = verdict
        db.add(row)
        return verdict
    except Exception as exc:  # noqa: BLE001 - a crashed check must not leave the version `checking`
        verdict = {"status": "failed", "checked_at": _utcnow().isoformat(),
                   "error": {"error": "check_crashed", "kind": type(exc).__name__,
                             "message": "the check could not finish; publish again"}}
        row.status = "failed"
        row.check_result = verdict
        db.add(row)
        return verdict


async def price_ranges(db: AsyncSession, tools: dict[str, dict[str, Any]], *, days: int = 30) -> dict[str, dict[str, Any]]:
    """Per tool id: the lowest and highest total a successful run cost in the last `days` (steps plus
    the seller's part, `seller_part_micro`) and the sample count, over EVERY successful run of that
    tool, the maker's own and the scheduled checks included: the steps cost the same whoever calls,
    and a new tool would otherwise show no number until a stranger pays. `tools` maps a tool id to
    the manifest that prices it (the newest). A tool with no run is absent from the answer."""
    from datetime import timedelta
    from ...domain.hub import seller_part_micro
    from ...models import HubRun
    from ...timeutil import utcnow_naive
    if not tools:
        return {}
    since = utcnow_naive() - timedelta(days=days)
    recent = (HubRun.tool_id.in_(list(tools)), HubRun.version > 0, HubRun.status == "ok", HubRun.started_at >= since)
    own = HubRun.caller_org_id == HubRun.maker_org_id
    # The trace is read only where the seller part is derived from it: the maker's own runs of a
    # script, whose ctx.charge lines say what a caller would have paid.
    scripts = [tid for tid, m in tools.items() if stored_pricing(m)["mode"] == "charge"]
    charged: dict[int, int] = {}
    if scripts:
        for rid, trace in (await db.execute(select(HubRun.id, HubRun.trace).where(
                *recent, own, HubRun.tool_id.in_(scripts)))).all():
            charged[rid] = sum(int(e.get("cost_micro") or 0) for e in (trace or [])
                               if isinstance(e, dict) and e.get("outcome") == "charged")
    out: dict[str, dict[str, Any]] = {}
    for rid, tid, cost, price, is_own in (await db.execute(
            select(HubRun.id, HubRun.tool_id, HubRun.cost_micro, HubRun.price_micro, own)
            .where(*recent))).all():
        total = int(cost or 0) + seller_part_micro(tools[tid], None if is_own else int(price or 0),
                                                   charged.get(rid, 0))
        r = out.setdefault(tid, {"low_micro": total, "high_micro": total, "samples": 0})
        r["low_micro"], r["high_micro"] = min(r["low_micro"], total), max(r["high_micro"], total)
        r["samples"] += 1
    return out


def with_range(manifest: dict[str, Any], rng: dict[str, Any] | None) -> dict[str, Any]:
    """The keys every price surface carries: `price_range` (the headline, `range_label`),
    `price_samples` (how many runs it rests on; 0 means the declared worst case) and the observed
    `price_low_micro`/`price_high_micro` (None before any run) for a surface that shows the numbers
    alone."""
    from ...domain.hub import range_label
    rng = rng or {}
    return {"price_range": range_label(manifest, rng.get("low_micro"), rng.get("high_micro")),
            "price_samples": int(rng.get("samples", 0)),
            "price_low_micro": rng.get("low_micro"), "price_high_micro": rng.get("high_micro")}


def worst_usd(manifest: dict[str, Any], price_micro: int, rng: dict[str, Any] | None) -> float | None:
    """`cost.usd` for a hub row: the most a run has cost recently (steps and seller part) when runs
    exist, else the declared price: a recipe's fixed price or a script's cap."""
    if rng and rng.get("samples"):
        return rng["high_micro"] / 1_000_000
    p = stored_pricing({"price_usd": price_micro / 1_000_000, **manifest})
    return p["max_price_usd"] if p["mode"] == "charge" else p["price_usd"]


def view(row: HubTool) -> dict[str, Any]:
    """The maker-facing shape of one version. The script is the maker's own; it is returned to
    the maker here and to nobody else (the public page of phase 7 hides it)."""
    return {
        "tool_id": row.tool_id, "version": row.version, "kind": row.kind, "status": row.status,
        "summary": row.summary, "writes": row.writes, "price_usd": row.price_micro / 1_000_000,
        "pricing": stored_pricing({"price_usd": row.price_micro / 1_000_000, **row.manifest}),
        "listed": bool(row.listed), "public_log": bool(row.public_log),
        "price_label": price_label(row.manifest),
        "uses": row.manifest.get("uses", []), "inputs": row.manifest.get("inputs", {}),
        "output": row.manifest.get("output", {}), "limits": row.manifest.get("limits", {}),
        "created_by": row.created_by, "created_at": row.created_at.isoformat(),
        "check_result": row.check_result,
        "data": ({"rows": max(0, (row.data.count("\n") + (0 if row.data.endswith("\n") else 1)) - 1),
                  "bytes": len(row.data.encode("utf-8"))} if row.data else None),
    }


async def transient(db: AsyncSession, *, org: Org, maker_email: str,
                    manifest: Any, script: str | None, check: Any, readme: Any, data: Any = None) -> HubTool:
    """The same validation as publish(), but the row is NOT added to the session: a dry run of a
    folder from the maker's machine. Version 0 marks it in every trace."""
    own_tools = {name for (name,) in (await db.execute(select(Tool.name).where(Tool.org_id == org.id))).all()}
    hub_ids = {tid for (tid,) in (await db.execute(select(HubTool.tool_id).distinct())).all()}
    v = validate(manifest, catalog_ids=set(catalog_store.load().by_id), own_tools=own_tools, hub_ids=hub_ids)
    if v.kind == "script" and not (isinstance(script, str) and script.strip()):
        raise ManifestError("script", "the manifest names run.js; send its contents as `script`")
    output_fields = (v.output["fields"] if v.kind == "script" else list(v.output))
    validate_check(check, v.inputs, output_fields)
    validate_readme(readme)
    data_v = validate_data(data)
    return HubTool(org_id=org.id, tool_id=f"{org.slug}.{v.name}", name=v.name, version=0, kind=v.kind,
                   status="dry-run", summary=v.summary, writes=v.writes, price_micro=v.price_micro,
                   manifest={**v.manifest, "version": 0}, script=script if v.kind == "script" else None,
                   check=check, readme=readme, created_by=maker_email, data=data_v)


async def retire(db: AsyncSession, *, org_id: int, tool_id: str) -> int:
    """Every version of a team's tool leaves the call road (round 7 of the case study: the owner
    asked for it). Returns how many versions changed. Does not commit."""
    from sqlalchemy import update
    result = await db.execute(update(HubTool).where(
        HubTool.tool_id == tool_id, HubTool.org_id == org_id, HubTool.status != "retired")
        .values(status="retired"))
    return int(result.rowcount or 0)


async def set_price(db: AsyncSession, *, org_id: int, tool_id: str, price_usd: float) -> HubTool | None:
    """The price applies to later runs of the newest live version (round 3 q8): no version bump,
    every trace stamps the price it paid. A JSON recipe's `price_usd`; a script's `max_price_usd`,
    the cap on its ctx.charge lines (above 0: a script's amounts live in run.js). Returns the row,
    or None when the team has no such live tool. Does not commit."""
    from ...domain.hub import manifest as hub_manifest
    row = (await db.execute(select(HubTool).where(
        HubTool.tool_id == tool_id, HubTool.org_id == org_id, HubTool.status == "live")
        .order_by(HubTool.version.desc()).limit(1))).scalars().first()
    if row is None:
        return None
    if row.kind == "script":
        micro = hub_manifest._money_micro("max_price_usd", price_usd, allow_zero=False)
        row.manifest = {**{k: v for k, v in row.manifest.items() if k != "price_usd"},
                        "pricing": {"mode": "charge", "max_price_usd": micro / 1_000_000}}
        db.add(row)
        return row
    micro = hub_manifest._validate_price(price_usd)
    row.price_micro = micro
    row.manifest = {**row.manifest, "price_usd": micro / 1_000_000,
                    "pricing": {"mode": "per_call", "price_usd": micro / 1_000_000}}
    db.add(row)
    return row


async def search_listed(db: AsyncSession, query: str, cat: Any) -> tuple[list[tuple[dict, float]], dict[str, dict]]:
    """The listed live hub tools that match `query` (docs/hub-listing-decisions.md, decision 2):
    the newest live version of every tool with `listed` on, scored by `catalog_store.score_extra`
    (the catalog's own tokens, idf and gate, no boost). Returns `([(row, score)], stats)`; `stats`
    is keyed by id with the 30-day ok rate and sample count of runs by OTHERS, the same shape the
    evidence rerank reads for a catalog row. The row is the public contract: never the script, the
    maker's tools or a key."""
    if not enabled() or not query.strip():
        return [], {}
    from datetime import timedelta
    from ...domain.catalog import store as catalog_store
    from ...domain.hub import price_label
    from ...models import HubRun
    from ...timeutil import utcnow_naive
    rows = (await db.execute(
        select(HubTool).where(HubTool.listed == True, HubTool.status == "live")  # noqa: E712
        .order_by(HubTool.tool_id, HubTool.version.desc()))).scalars().all()
    newest: dict[str, HubTool] = {}
    for r in rows:
        newest.setdefault(r.tool_id, r)
    if not newest:
        return [], {}
    org_ids = {r.org_id for r in newest.values()}
    slugs = {o.id: o.slug for o in (await db.execute(select(Org).where(Org.id.in_(org_ids)))).scalars().all()}
    since = utcnow_naive() - timedelta(days=30)
    agg: dict[str, list[int]] = {}
    for tid, status in (await db.execute(
            select(HubRun.tool_id, HubRun.status)
            .where(HubRun.tool_id.in_(list(newest)), HubRun.version > 0, HubRun.started_at >= since,
                   HubRun.caller_org_id != HubRun.maker_org_id))).all():
        a = agg.setdefault(tid, [0, 0])
        a[0] += 1
        a[1] += 1 if status == "ok" else 0
    ranges = await price_ranges(db, {tid: r.manifest for tid, r in newest.items()})
    extra = []
    for tid, r in newest.items():
        m = r.manifest
        slug = slugs.get(r.org_id, "")
        worst = worst_usd(m, r.price_micro, ranges.get(tid))
        ep = {
            "id": tid, "kind": "hub", "hub": True, "version": r.version,
            "name": r.name, "summary": r.summary, "provider": slug, "provider_display": slug,
            "capability": None, "capability_description": "", "platform": "", "tier": "core", "verified": True,
            "method": "POST", "path": f"/call/{tid}", "writes": r.writes,
            "cost": {"type": "per_success", "usd": worst, "currency": "USD", "unit": "run"},
            "price_line": "seller " + price_label(m) + " + provider fees", "price_label": price_label(m),
            **with_range(m, ranges.get(tid)),
            "made_of": len(m.get("uses", [])),
        }
        fields = [(catalog_store.W_SUMMARY, f"{r.name} {r.summary}".lower()),
                  (catalog_store.W_PATH, f"{tid} {slug}".lower())]
        extra.append((ep, fields))
    scored = catalog_store.score_extra(query, cat, extra)
    stats = {tid: {"ok_rate": (a[1] / a[0]) if a[0] else None, "samples": a[0]} for tid, a in agg.items()}
    return scored, stats


async def set_flags(db: AsyncSession, *, org_id: int, tool_id: str,
                    listed: bool | None = None, public_log: bool | None = None) -> HubTool | None:
    """The two distribution switches (docs/hub-listing-decisions.md, 2026-09-16), on the newest live
    version, no version bump: `listed` (appears in catalog search) and `public_log` (the share page
    shows the recent-runs log). A switch given as None is left alone. Returns the row, or None when
    the team has no such live tool. Does not commit."""
    row = (await db.execute(select(HubTool).where(
        HubTool.tool_id == tool_id, HubTool.org_id == org_id, HubTool.status == "live")
        .order_by(HubTool.version.desc()).limit(1))).scalars().first()
    if row is None:
        return None
    if listed is not None:
        row.listed = bool(listed)
    if public_log is not None:
        row.public_log = bool(public_log)
    db.add(row)
    return row

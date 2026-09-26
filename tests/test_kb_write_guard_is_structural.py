"""`requires_kb_tier` is only ever attached where it can see a KB -- structurally.

`requires_kb_tier("write")` checks the caller's per-KB role on every KB the
*request names*. On a route whose request names none it used to fall back to
the caller's **global** role, silently -- which is how
`DELETE /api/reviews/{review_id}` let any write-tier user delete a review on
a private KB by id: the guard was attached, and checked nothing per-KB.

This file makes that a test failure. It walks the real app's routes and, for
every route whose dependant tree contains a `requires_kb_tier` guard, asserts
one of two things:

- the **named** form (`requires_kb_tier(tier)`): the route declares a
  *required* KB-bearing parameter the resolver reads (`KB_PARAM_NAMES`, in
  path, query or a JSON body model). Optional is not enough: a request that
  leaves it out is back on the global role.
- the **row** form (`requires_kb_tier(tier, resolve_kb=dep)`): the guard's
  own dependant carries a resolver returning `RowKB`, which looks up the row
  and hands the row's KB to the per-KB rule.

The walk is `route.dependant` -- FastAPI's own record of what runs on every
request -- not the handler source.
"""

import inspect

import pytest

from fastapi.routing import APIRoute

from pyrite.server.api import KB_PARAM_NAMES, RowKB, create_app

NAMED = "pyrite.server.api.requires_kb_tier.<locals>._check_kb_tier"
ROW = "pyrite.server.api.requires_kb_tier.<locals>._check_row_kb_tier"


def _api_routes():
    app = create_app()

    def walk(routes):
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                yield from walk(route.original_router.routes)
            elif isinstance(route, APIRoute):
                yield route

    for route in walk(app.routes):
        for method in sorted(route.methods or ()):
            if method not in ("HEAD", "OPTIONS"):
                yield method, route


def _qualified(call) -> str:
    return f"{getattr(call, '__module__', '?')}.{getattr(call, '__qualname__', repr(call))}"


def _kb_tier_guards(dependant):
    """Every requires_kb_tier guard in the tree, as (qualified name, sub-dependant)."""
    for dep in dependant.dependencies:
        name = _qualified(dep.call)
        if name in (NAMED, ROW):
            yield name, dep
        yield from _kb_tier_guards(dep)


def _is_required(field) -> bool:
    info = getattr(field, "field_info", None)
    if info is not None and hasattr(info, "is_required"):
        return info.is_required()
    return bool(getattr(field, "required", False))


def _required_kb_parameters(route: APIRoute) -> set[str]:
    """The KB-bearing parameters, by wire name, a request to this route must carry."""
    d = route.dependant
    names: set[str] = set()
    for p in (*d.path_params, *d.query_params):
        wire = p.alias or p.name
        if wire in KB_PARAM_NAMES and _is_required(p):
            names.add(wire)
    for p in d.body_params:
        if not _is_required(p):
            continue
        model = getattr(p.field_info, "annotation", None)
        for field_name, field in (getattr(model, "model_fields", None) or {}).items():
            if field_name in KB_PARAM_NAMES and field.is_required():
                names.add(field_name)
    return names


def test_the_walk_finds_both_forms():
    """Guards the walk: if it stops seeing guards, the next test proves nothing."""
    kinds = {name for _, route in _api_routes() for name, _ in _kb_tier_guards(route.dependant)}
    assert kinds == {NAMED, ROW}, f"requires_kb_tier forms found: {sorted(kinds)}"


def test_every_requires_kb_tier_route_names_its_kb_or_resolves_the_row():
    offenders = []
    for method, route in _api_routes():
        for name, dep in _kb_tier_guards(route.dependant):
            if name == ROW:
                resolvers = [
                    sub.call
                    for sub in dep.dependencies
                    if inspect.signature(sub.call).return_annotation is RowKB
                ]
                if not resolvers:
                    offenders.append((method, route, "row form without its resolver"))
            elif not _required_kb_parameters(route):
                offenders.append(
                    (method, route, "names no required KB -- falls back to the global role")
                )
    if offenders:
        listing = "\n".join(
            f"  {method:6} {route.path:44} {why} "
            f"({route.endpoint.__module__.rsplit('.', 1)[-1]}.py:{route.endpoint.__name__})"
            for method, route, why in offenders
        )
        raise AssertionError(
            f"{len(offenders)} route(s) attach requires_kb_tier where it cannot see a KB:\n"
            f"{listing}\n"
            "Declare a required `kb`/`kb_name` parameter (path, query or JSON body), or "
            "look the row up: requires_kb_tier(tier, resolve_kb=<dependency returning "
            "RowKB>) -- see review_kb in pyrite/server/endpoints/reviews.py."
        )


@pytest.mark.control(
    reason="pins the FastAPI setting; only its docstring changed with the guard fix"
)
def test_every_route_with_a_body_refuses_to_parse_an_undeclared_content_type():
    """Pins FastAPI's `strict_content_type` on every route with a body.

    One of two independent defences. The guard reads every non-form
    body whatever its Content-Type (`pyrite.server.api._resolve_kb_names`);
    this keeps the handler from binding, as JSON, a body a browser can send
    without a CORS preflight. A route -- or a FastAPI default -- that turned
    it off would lose the second defence, not the first.
    """
    from fastapi.datastructures import DefaultPlaceholder

    lax = []
    for method, route in _api_routes():
        if not route.dependant.body_params:
            continue
        setting = route.strict_content_type
        if isinstance(setting, DefaultPlaceholder):
            setting = setting.value
        if setting is not True:
            lax.append(f"  {method:6} {route.path} strict_content_type={setting!r}")
    assert not lax, "routes that parse a body with no JSON content-type:\n" + "\n".join(lax)

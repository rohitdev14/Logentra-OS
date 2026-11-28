from collections import Counter

from starlette.routing import Mount

from logentra_server.auth import ALL_PERMISSIONS
from logentra_server.authz import OBJECTS
from logentra_server.route_policies import PUBLIC_ROUTES, ROUTE_POLICIES
from scripts.authz_table import DESTINATION, render


def mounted_methods(routes, prefix=""):
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from mounted_methods(included.routes, prefix + route.include_context.prefix)
        elif isinstance(route, Mount):
            for method in ("GET", "HEAD"):
                yield method, prefix + route.path, route
        else:
            for method in route.methods:
                yield method, prefix + route.path, route


def test_all_mounted_routes_have_policies(client):
    mounted = [(method, path) for method, path, _ in mounted_methods(client.app.routes)]
    assert len(mounted) == len(set(mounted)), "Duplicate route declarations"
    assert set(mounted) == ROUTE_POLICIES.keys()
    assert {route for route, policy in ROUTE_POLICIES.items() if policy.kind == "public"} == PUBLIC_ROUTES
    counts = Counter(policy.kind for policy in ROUTE_POLICIES.values())
    assert set(counts) == {"public", "authenticated", "object"}
    for (_, path), policy in ROUTE_POLICIES.items():
        assert set(policy.permissions + policy.any_of) <= ALL_PERMISSIONS
        if policy.kind == "object":
            assert policy.object_kind in OBJECTS and policy.action and "{" + policy.parameter + "}" in path
    print(f"{len(mounted)} route methods have policies: {dict(sorted(counts.items()))}")


def test_generated_permissions_reference_is_current():
    assert DESTINATION.read_text(encoding="utf-8") == render(), "Run python scripts/authz_table.py"

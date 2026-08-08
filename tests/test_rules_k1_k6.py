"""Unit tests for K1–K6 on injected facts — positive, negative, boundary."""
from limen.rules import (consumers, forbidden, layer, ownership, public_api,
                         scoped)

from conftest import ff, make_ctx, make_index, spec_from

SPEC_YAML = """
version: 1
roots:
  host: { path: app, language: php }
units:
  declared: [Auth, Folders, Projects]
artifact_sets:
  models:
    discovery: { root: host, pattern: "app/Models/*.php" }
public_surface:
  php:
    - "{unit}/Application/Contracts/**"
rules:
  - id: k1
    kind: layer-dependency
    severity: error
    forbid: { from_layer: Application, to_layer: Infrastructure }
    scope: { roots: [host] }
    message: "Application must not depend on Infrastructure."
    remedy: "Invert the dependency."
  - id: k2
    kind: ownership
    severity: error
    artifact_set: models
    owners: { "App\\\\Models\\\\User": Auth }
    message: "Every model has exactly one owner."
    remedy: "Add the model to owners:."
  - id: k3
    kind: consumer-allowlist
    severity: error
    artifact_set: models
    allow: { "App\\\\Models\\\\User": [Projects] }
    message: "A model may be imported only by its owner or a declared consumer."
    remedy: "Reach the data through the owning unit's contract."
  - id: k4
    kind: public-api-only
    severity: error
    scope: { roots: [host] }
    exempt: []
    message: "Cross-unit dependencies must resolve to the public surface."
    remedy: "Import the Contracts entry."
  - id: k5
    kind: scoped-allowlist
    severity: error
    scope: { paths: ["app/Modules/Projects/Http/**"] }
    target_prefix: "App\\\\Modules\\\\Audit\\\\"
    allow: ["App\\\\Modules\\\\Audit\\\\Contracts\\\\AuditApi"]
    message: "Only the allowlisted Audit classes."
    remedy: "Depend on AuditApi."
  - id: k6
    kind: forbidden-target
    severity: error
    scope: { paths: ["app/Modules/Projects/**"] }
    forbid: ["App\\\\Models\\\\Secret"]
    message: "Never."
    remedy: "Use the module API."
"""


def _spec():
    return spec_from(SPEC_YAML)


def test_k1_flags_only_forbidden_direction():
    spec = _spec()
    app = ff("app/Modules/A/Application/S.php",
             declares=["App\\Modules\\A\\Application\\S"],
             deps=[("App\\Modules\\A\\Infrastructure\\Repo", 5, "use"),
                   ("App\\Modules\\A\\Domain\\E", 6, "use")],
             unit="A", layer="Application")
    infra = ff("app/Modules/A/Infrastructure/Repo.php",
               declares=["App\\Modules\\A\\Infrastructure\\Repo"],
               unit="A", layer="Infrastructure")
    domain = ff("app/Modules/A/Domain/E.php",
                declares=["App\\Modules\\A\\Domain\\E"], unit="A", layer="Domain")
    ctx = make_ctx(spec, make_index([app, infra, domain]))
    out = layer.check(spec.rule_by_id("k1"), ctx)
    assert [(f.file, f.line, f.target) for f in out] == \
        [("app/Modules/A/Application/S.php", 5,
          "App\\Modules\\A\\Infrastructure\\Repo")]


def test_k1_boundary_unresolved_target_is_not_flagged():
    spec = _spec()
    app = ff("app/Modules/A/Application/S.php",
             deps=[("Vendor\\Lib\\Thing", 3, "use")],
             unit="A", layer="Application")
    ctx = make_ctx(spec, make_index([app]))
    assert layer.check(spec.rule_by_id("k1"), ctx) == []


def test_k2_unowned_artifact_is_flagged_owned_is_not():
    spec = _spec()
    user = ff("app/Models/User.php", declares=["App\\Models\\User"])
    orphan = ff("app/Models/Orphan.php", declares=["App\\Models\\Orphan"])
    ctx = make_ctx(spec, make_index([user, orphan]))
    out = ownership.check(spec.rule_by_id("k2"), ctx)
    assert [f.target for f in out] == ["App\\Models\\Orphan"]
    assert out[0].spec_path.endswith(".owners")


def test_k3_owner_and_declared_consumer_pass_others_fail():
    spec = _spec()
    user = ff("app/Models/User.php", declares=["App\\Models\\User"])
    by_owner = ff("app/Modules/Auth/S.php", unit="Auth",
                  deps=[("App\\Models\\User", 4, "use")])
    by_consumer = ff("app/Modules/Projects/S.php", unit="Projects",
                     deps=[("App\\Models\\User", 4, "use")])
    by_rogue = ff("app/Modules/Folders/S.php", unit="Folders",
                  deps=[("App\\Models\\User", 9, "use")])
    ctx = make_ctx(spec, make_index([user, by_owner, by_consumer, by_rogue]))
    out = consumers.check(spec.rule_by_id("k3"), ctx)
    assert [(f.file, f.unit) for f in out] == \
        [("app/Modules/Folders/S.php", "Folders")]


def test_k4_contract_import_passes_internal_import_fails():
    spec = _spec()
    contract = ff("app/Modules/Folders/Application/Contracts/FolderModuleApi.php",
                  declares=["App\\Modules\\Folders\\Application\\Contracts\\FolderModuleApi"],
                  unit="Folders", unit_dir="app/Modules/Folders")
    internal = ff("app/Modules/Folders/Application/FolderCreationService.php",
                  declares=["App\\Modules\\Folders\\Application\\FolderCreationService"],
                  unit="Folders", unit_dir="app/Modules/Folders")
    good = ff("app/Modules/Projects/Application/A.php", unit="Projects",
              unit_dir="app/Modules/Projects",
              deps=[("App\\Modules\\Folders\\Application\\Contracts\\FolderModuleApi",
                     3, "use")])
    bad = ff("app/Modules/Projects/Application/B.php", unit="Projects",
             unit_dir="app/Modules/Projects",
             deps=[("App\\Modules\\Folders\\Application\\FolderCreationService",
                    18, "use")])
    ctx = make_ctx(spec, make_index([contract, internal, good, bad]))
    out = public_api.check(spec.rule_by_id("k4"), ctx)
    assert [(f.file, f.line) for f in out] == \
        [("app/Modules/Projects/Application/B.php", 18)]
    assert out[0].exemptible is True


def test_k4_same_unit_dependency_is_never_flagged():
    spec = _spec()
    internal = ff("app/Modules/Folders/Application/Impl.php",
                  declares=["App\\Modules\\Folders\\Application\\Impl"],
                  unit="Folders", unit_dir="app/Modules/Folders")
    sibling = ff("app/Modules/Folders/Http/C.php", unit="Folders",
                 unit_dir="app/Modules/Folders",
                 deps=[("App\\Modules\\Folders\\Application\\Impl", 2, "use")])
    ctx = make_ctx(spec, make_index([internal, sibling]))
    assert public_api.check(spec.rule_by_id("k4"), ctx) == []


def test_k5_allowlisted_target_passes_others_in_prefix_fail():
    spec = _spec()
    f = ff("app/Modules/Projects/Http/C.php", unit="Projects",
           deps=[("App\\Modules\\Audit\\Contracts\\AuditApi", 3, "use"),
                 ("App\\Modules\\Audit\\Internal\\Writer", 4, "use"),
                 ("App\\Modules\\Other\\Thing", 5, "use")])
    ctx = make_ctx(spec, make_index([f]))
    out = scoped.check(spec.rule_by_id("k5"), ctx)
    assert [f_.target for f_ in out] == ["App\\Modules\\Audit\\Internal\\Writer"]


def test_k6_forbidden_target_in_scope_only():
    spec = _spec()
    inside = ff("app/Modules/Projects/A.php",
                deps=[("App\\Models\\Secret", 7, "use")])
    outside = ff("app/Modules/Auth/A.php",
                 deps=[("App\\Models\\Secret", 7, "use")])
    ctx = make_ctx(spec, make_index([inside, outside]))
    out = forbidden.check(spec.rule_by_id("k6"), ctx)
    assert [f.file for f in out] == ["app/Modules/Projects/A.php"]

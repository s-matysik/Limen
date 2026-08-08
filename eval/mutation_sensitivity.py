#!/usr/bin/env python3
"""Mutation sensitivity: does each tool still see a violation when one is planted?

    python eval/mutation_sensitivity.py

The frozen snapshot is clean — both the legacy checkers and limen report zero
architecture violations on it. Agreement on an empty set is weak evidence: two
tools that never fire also "agree". This harness closes that gap.

For every legacy rule that has a mechanically injectable counter-example, it

  1. copies the whole workspace to a temporary directory (symlinks preserved,
     so shared/ and core-backend/ still resolve),
  2. applies ONE mutation — a single planted violation of that rule,
  3. runs the legacy oracle (eval/run_legacy_oracle.py) and `limen check` over
     the mutated copy,
  4. records, per rule: did legacy fire, did limen fire, and — for legacy —
     whether the finding count rose by exactly the number injected.

Two independent claims come out of the same run:

  * SENSITIVITY (both tools): a rule whose violation neither tool detects is a
    rule for which "they agree" means nothing. Reported as detected_legacy /
    detected_limen per rule.
  * SHIM VALIDATION (legacy only): the shim is a re-implementation of PHPUnit's
    plumbing, not of the rules — but that claim needs evidence. A shim that
    silently swallowed violations would report zero on the mutants too. The
    delta column (legacy_delta == injected) is that evidence, and it is written
    to results/oracle_validation.json.

Each mutation is described declaratively (target file, edit, expected legacy
rule id, expected limen rule id). Injecting a violation into a COPY never
touches the frozen workspace.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
LIMEN_DIR = EVAL_DIR.parent
LIMEN_SRC = LIMEN_DIR / "src"
DEFAULT_WORKSPACE = LIMEN_DIR.parent / "workspace"

BACKEND = "litrev-backend"
SHARED = "backend-shared"
FRONTEND = "litrev-frontend"

# ---------------------------------------------------------------------------
# mutation primitives
# ---------------------------------------------------------------------------


def insert_use(rel_path: str, statement: str):
    """Insert a `use ...;` line directly after the namespace declaration."""

    def apply(root: Path) -> list[str]:
        path = root / rel_path
        lines = path.read_text().split("\n")
        idx = next(i for i, line in enumerate(lines) if line.startswith("namespace "))
        lines.insert(idx + 2, statement)
        path.write_text("\n".join(lines))
        return [rel_path]

    return apply


def append_line(rel_path: str, text: str):
    def apply(root: Path) -> list[str]:
        path = root / rel_path
        path.write_text(path.read_text().rstrip("\n") + "\n" + text + "\n")
        return [rel_path]

    return apply


def write_file(rel_path: str, content: str):
    def apply(root: Path) -> list[str]:
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return [rel_path]

    return apply


def make_dir(rel_path: str):
    def apply(root: Path) -> list[str]:
        (root / rel_path).mkdir(parents=True, exist_ok=True)
        (root / rel_path / ".gitkeep").write_text("")
        return [rel_path]

    return apply


def delete_file(rel_path: str):
    def apply(root: Path) -> list[str]:
        (root / rel_path).unlink()
        return [rel_path]

    return apply


def rename_file(rel_path: str, new_name: str):
    def apply(root: Path) -> list[str]:
        src = root / rel_path
        src.rename(src.parent / new_name)
        return [rel_path]

    return apply


def pad_lines(rel_path: str, target_lines: int, comment: str = "// padding"):
    def apply(root: Path) -> list[str]:
        path = root / rel_path
        text = path.read_text()
        current = len(text.split("\n"))
        if current < target_lines:
            text = text.rstrip("\n") + "\n" + "\n".join([comment] * (target_lines - current)) + "\n"
            path.write_text(text)
        return [rel_path]

    return apply


def strip_readme_section(rel_path: str, marker: str):
    def apply(root: Path) -> list[str]:
        path = root / rel_path
        text = path.read_text()
        path.write_text(re.sub(re.escape(marker), "REDACTED-BY-MUTATION", text))
        return [rel_path]

    return apply


def compose(*mutations):
    def apply(root: Path) -> list[str]:
        touched: list[str] = []
        for mutation in mutations:
            touched.extend(mutation(root))
        return touched

    return apply


# ---------------------------------------------------------------------------
# mutation catalogue
# ---------------------------------------------------------------------------


@dataclass
class Mutation:
    id: str
    inventory_id: str
    side: str  # 'backend' | 'frontend'
    description: str
    apply: object
    injected: int
    expect_legacy_rule: str | None
    expect_limen_rule: str | None
    frontend_repo: str | None = None
    notes: str = ""


def catalogue() -> list[Mutation]:
    b = lambda p: f"{BACKEND}/{p}"  # noqa: E731
    s = lambda p: f"{SHARED}/{p}"  # noqa: E731
    f = lambda p: f"{FRONTEND}/{p}"  # noqa: E731

    return [
        # ---- backend -----------------------------------------------------
        Mutation(
            id="M-BE-02-admin-import",
            inventory_id="L-BE-TEST-02",
            side="backend",
            description="Business module (Folders) imports an admin module class.",
            apply=insert_use(
                b("app/Modules/Folders/Http/Controllers/FolderController.php"),
                "use App\\Modules\\AdminUsers\\Http\\Controllers\\UserController;",
            ),
            injected=1,
            expect_legacy_rule="AdminBoundaryRulesTest::test_module_does_not_import_admin_modules_directly",
            expect_limen_rule="modules-not-into-admin",
            notes="Also trips HttpBoundaryRulesTest (cross-module Http import) — both are legacy rules, "
            "so the legacy delta is 2 for one injected line.",
        ),
        Mutation(
            id="M-BE-03-foreign-application",
            inventory_id="L-BE-TEST-03",
            side="backend",
            description="Module imports another module's concrete Application class (not Contracts). "
            "The target class is created too, so the import RESOLVES.",
            apply=compose(
                write_file(
                    b("app/Modules/Audit/Application/AuditLogWriterService.php"),
                    "<?php\n\nnamespace App\\Modules\\Audit\\Application;\n\nclass AuditLogWriterService\n{\n}\n",
                ),
                insert_use(
                    b("app/Modules/Projects/Http/Controllers/ProjectController.php"),
                    "use App\\Modules\\Audit\\Application\\AuditLogWriterService;",
                ),
            ),
            injected=1,
            expect_legacy_rule="ApplicationBoundaryRulesTest::test_modules_do_not_import_foreign_application_internals",
            expect_limen_rule="host-cross-module-public-surface",
            notes="The host modules contain no Application classes at all in the frozen snapshot "
            "(every app/Modules/*/Application holds only .gitkeep), so a violation of this rule "
            "cannot be planted with a single import line — the target class has to be created too. "
            "See M-BE-03b for what happens when it is not.",
        ),
        Mutation(
            id="M-BE-03b-foreign-application-dangling",
            inventory_id="L-BE-TEST-03",
            side="backend",
            description="Same import as M-BE-03, but the target class is NOT created: the import dangles.",
            apply=insert_use(
                b("app/Modules/Projects/Http/Controllers/ProjectController.php"),
                "use App\\Modules\\Audit\\Application\\AuditLogWriterService;",
            ),
            injected=1,
            expect_legacy_rule="ApplicationBoundaryRulesTest::test_modules_do_not_import_foreign_application_internals",
            expect_limen_rule=None,
            notes="DIVERGENCE, expected and reported as such: legacy matches the `use` statement as TEXT "
            "and fires; limen resolves dependencies to units first and a PHP import that resolves to no "
            "file is dropped silently (not even unresolved-dependency, which the spec's unresolved_policy "
            "raises only for the JS roots). A dangling import is dead code rather than an architecture "
            "violation, so this is a difference in what the two tools consider a fact, not a difference "
            "in the rule. Recorded in results/oracle_gaps.md.",
        ),
        Mutation(
            id="M-BE-04-no-root-compose",
            inventory_id="L-BE-TEST-04",
            side="backend",
            description="The single root docker-compose.yml is removed.",
            apply=delete_file(b("docker-compose.yml")),
            injected=1,
            expect_legacy_rule="DeploymentBoundaryRulesTest::test_repo_has_single_root_docker_compose_file",
            expect_limen_rule="modules-have-no-local-deployment",
            notes="Legacy splits this across two methods of DeploymentBoundaryRulesTest; limen folds both "
            "into one structure rule (required_files + forbidden_dirs).",
        ),
        Mutation(
            id="M-BE-05-module-docker-dir",
            inventory_id="L-BE-TEST-05",
            side="backend",
            description="A module defines its own docker/ directory.",
            apply=make_dir(b("app/Modules/Folders/docker")),
            injected=1,
            expect_legacy_rule="DeploymentBoundaryRulesTest::test_modules_do_not_define_local_deploy_or_docker_directories",
            expect_limen_rule="modules-have-no-local-deployment",
        ),
        Mutation(
            id="M-BE-06-module-db-connection",
            inventory_id="L-BE-TEST-06",
            side="backend",
            description="config/database.php declares a module-scoped connection.",
            apply=lambda root: _inject_db_connection(root),
            injected=1,
            expect_legacy_rule="DeploymentBoundaryRulesTest::test_database_config_does_not_define_module_scoped_connections",
            expect_limen_rule=None,
            notes="L-BE-TEST-06 is documented as not-encoded in limen (inventory status 'not-encoded'); "
            "limen is expected NOT to fire.",
        ),
        Mutation(
            id="M-BE-07-cross-module-http",
            inventory_id="L-BE-TEST-07",
            side="backend",
            description="Module imports another module's Http layer.",
            apply=insert_use(
                b("app/Modules/Messages/Http/Controllers/TreeMessageController.php"),
                "use App\\Modules\\Projects\\Http\\Controllers\\ProjectController;",
            ),
            injected=1,
            expect_legacy_rule="HttpBoundaryRulesTest::test_module_does_not_import_http_layer_of_other_modules",
            expect_limen_rule="host-cross-module-public-surface",
        ),
        Mutation(
            id="M-BE-08-application-into-infrastructure",
            inventory_id="L-BE-TEST-08",
            side="backend",
            description="An Application-layer file imports a module Infrastructure class.",
            apply=write_file(
                b("app/Modules/Relationships/Application/MutantService.php"),
                "<?php\n\nnamespace App\\Modules\\Relationships\\Application;\n\n"
                "use App\\Modules\\Relationships\\Infrastructure\\Relationships\\RelationshipPersistenceGateway;\n\n"
                "class MutantService\n{\n}\n",
            ),
            injected=1,
            expect_legacy_rule="LayerBoundaryRulesTest::test_application_layer_does_not_import_module_infrastructure",
            expect_limen_rule="application-not-into-infrastructure",
        ),
        Mutation(
            id="M-BE-09-model-into-application",
            inventory_id="L-BE-TEST-09",
            side="backend",
            description="An Eloquent model imports a module Application class.",
            apply=insert_use(
                b("app/Models/Folder.php"),
                "use App\\Modules\\Notifications\\Application\\MailNotificationService;",
            ),
            injected=1,
            expect_legacy_rule="LayerBoundaryRulesTest::test_models_do_not_import_module_application_classes",
            expect_limen_rule="support-and-models-module-import-allowlist",
            notes="Legacy fires twice: the dedicated Models->Application rule (L-BE-TEST-09) and the "
            "allowlist driver over model_module_import_rules (L-BE-TEST-11).",
        ),
        Mutation(
            id="M-BE-10-support-module-import",
            inventory_id="L-BE-TEST-10",
            side="backend",
            description="app/Support imports a module class (allowlist is empty = total ban).",
            apply=insert_use(
                b("app/Support/PolishAlphabet.php"),
                "use App\\Modules\\Folders\\Http\\Controllers\\FolderController;",
            ),
            injected=1,
            expect_legacy_rule="LayerBoundaryRulesTest::test_support_imports_only_allowlisted_module_classes",
            expect_limen_rule="support-and-models-module-import-allowlist",
        ),
        Mutation(
            id="M-BE-12-undeclared-model",
            inventory_id="L-BE-TEST-12",
            side="backend",
            description="A new model in app/Models has no declared owner module.",
            apply=write_file(
                b("app/Models/MutantModel.php"),
                "<?php\n\nnamespace App\\Models;\n\nuse Illuminate\\Database\\Eloquent\\Model;\n\n"
                "class MutantModel extends Model\n{\n}\n",
            ),
            injected=1,
            expect_legacy_rule="ModelOwnershipRulesTest::test_every_model_has_declared_owner_module",
            expect_limen_rule="model-owner-declared",
        ),
        Mutation(
            id="M-BE-13-unknown-owner-module",
            inventory_id="L-BE-TEST-13",
            side="backend",
            description="config declares a model owner that is not a registered module.",
            apply=lambda root: _retarget_model_owner(root),
            injected=1,
            expect_legacy_rule="ModelOwnershipRulesTest::test_model_owner_modules_exist",
            expect_limen_rule=None,
            notes="L-BE-TEST-13 is a config-integrity check (inventory status 'subsumed'); the limen "
            "equivalent is spec validation, not `check`, so `check` is expected NOT to fire.",
        ),
        Mutation(
            id="M-BE-14-model-consumer",
            inventory_id="L-BE-TEST-14",
            side="backend",
            description="A module imports a model it is not a declared consumer of.",
            apply=insert_use(
                b("app/Modules/Messages/Http/Controllers/TreeMessageController.php"),
                "use App\\Models\\AuditLog;",
            ),
            injected=1,
            expect_legacy_rule="ModelOwnershipRulesTest::test_model_imports_are_limited_to_owner_or_declared_consumers",
            expect_limen_rule="model-consumers",
        ),
        Mutation(
            id="M-BE-15-forbidden-model",
            inventory_id="L-BE-TEST-15",
            side="backend",
            description="Folders Infrastructure/PermissionProfiles imports the PermissionProfile model directly.",
            apply=insert_use(
                b("app/Modules/Folders/Infrastructure/PermissionProfiles/PermissionProfileFolderGateway.php"),
                "use App\\Models\\PermissionProfile;",
            ),
            injected=1,
            expect_legacy_rule="ModelOwnershipRulesTest::test_forbidden_model_import_rules_are_enforced",
            expect_limen_rule="no-direct-permissionprofile-model",
        ),
        Mutation(
            id="M-BE-16-notifications-domain-baseline",
            inventory_id="L-BE-TEST-16",
            side="backend",
            description="A non-Notifications module imports Notifications\\Domain (baseline is empty).",
            apply=insert_use(
                b("app/Modules/Messages/Http/Controllers/TreeMessageController.php"),
                "use App\\Modules\\Notifications\\Domain\\NotificationChannel;",
            ),
            injected=1,
            expect_legacy_rule="ModuleBoundaryBaselineRulesTest::test_cross_module_notifications_domain_imports_match_declared_baseline",
            expect_limen_rule="notifications-domain-baseline",
        ),
        Mutation(
            id="M-BE-17-notifications-mail-baseline",
            inventory_id="L-BE-TEST-17",
            side="backend",
            description="A non-Notifications module imports Notifications\\Mail (baseline is empty).",
            apply=insert_use(
                b("app/Modules/Projects/Http/Controllers/ProjectController.php"),
                "use App\\Modules\\Notifications\\Mail\\ProjectSharedMail;",
            ),
            injected=1,
            expect_legacy_rule="ModuleBoundaryBaselineRulesTest::test_cross_module_notifications_mail_imports_match_declared_baseline",
            expect_limen_rule="notifications-mail-baseline",
        ),
        Mutation(
            id="M-BE-18-module-application-allowlist",
            inventory_id="L-BE-TEST-18",
            side="backend",
            description="Profile/Http imports a non-allowlisted Audit\\Application class.",
            apply=insert_use(
                b("app/Modules/Profile/Http/Controllers/ProfileController.php"),
                "use App\\Modules\\Audit\\Application\\AuditLogWriter;",
            ),
            injected=1,
            expect_legacy_rule="ModuleContractRulesTest::test_module_paths_use_only_allowlisted_cross_module_application_imports",
            expect_limen_rule="admin-http-audit-application-allowlist",
        ),
        Mutation(
            id="M-BE-19-shared-http-layer",
            inventory_id="L-BE-TEST-19",
            side="backend",
            description="backend-shared grows an Http/ layer.",
            apply=write_file(
                s("src/Folders/Http/MutantController.php"),
                "<?php\n\nnamespace LitRev\\BackendShared\\Folders\\Http;\n\nclass MutantController\n{\n}\n",
            ),
            injected=1,
            expect_legacy_rule="SharedBoundaryRulesTest::test_shared_has_no_http_or_infrastructure_layers",
            expect_limen_rule="shared-has-no-http-or-infrastructure",
        ),
        Mutation(
            id="M-BE-20-shared-host-import",
            inventory_id="L-BE-TEST-20",
            side="backend",
            description="backend-shared imports a host App\\ class outside the allowlist.",
            apply=insert_use(
                s("src/Auth/Application/AuthAdminUserStateFacade.php"),
                "use App\\Modules\\Folders\\Http\\Controllers\\FolderController;",
            ),
            injected=1,
            expect_legacy_rule="SharedBoundaryRulesTest::test_shared_does_not_import_forbidden_host_classes",
            expect_limen_rule="shared-not-into-host-modules",
        ),
        Mutation(
            id="M-BE-21-shared-cross-module-concrete",
            inventory_id="L-BE-TEST-21",
            side="backend",
            description="A shared module imports another shared module's concrete Application service.",
            apply=insert_use(
                s("src/Auth/Application/AuthAdminUserStateFacade.php"),
                "use LitRev\\BackendShared\\Folders\\Application\\FolderHomeService;",
            ),
            injected=1,
            expect_legacy_rule="SharedBoundaryRulesTest::test_shared_cross_module_imports_use_contracts_events_domain_or_presentation",
            expect_limen_rule="shared-cross-module-public-surface",
        ),
        # ---- frontend ----------------------------------------------------
        Mutation(
            id="M-FE-01-raw-success-data",
            inventory_id="L-FE-01",
            side="frontend",
            frontend_repo=FRONTEND,
            description="Page file uses the raw `response?.data ??` success fallback.",
            apply=append_line(f("src/modules/projects/pages/ProjectsPage.jsx"), "const mutant = response?.data ?? null;"),
            injected=1,
            expect_legacy_rule="raw-success-response-data",
            expect_limen_rule="raw-success-response-data",
        ),
        Mutation(
            id="M-FE-02-local-message-fallback",
            inventory_id="L-FE-02",
            side="frontend",
            frontend_repo=FRONTEND,
            description="Page file uses the local `message || t(...)` error fallback.",
            apply=append_line(f("src/modules/schedule/pages/SchedulePage.jsx"), "const mutantMsg = message || t('x');"),
            injected=1,
            expect_legacy_rule="local-message-fallback",
            expect_limen_rule="local-message-fallback",
        ),
        Mutation(
            id="M-FE-03-console-log",
            inventory_id="L-FE-03",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A runtime source file leaves a console.log(...) behind.",
            apply=append_line(f("src/shared/http/api-client.js"), "console.log('mutant');"),
            injected=1,
            expect_legacy_rule="runtime-console-log",
            expect_limen_rule="runtime-console-log",
        ),
        Mutation(
            id="M-FE-04-shared-into-modules",
            inventory_id="L-FE-04",
            side="frontend",
            frontend_repo=FRONTEND,
            description="src/shared imports from @/modules.",
            apply=append_line(f("src/shared/http/api-projects.js"), 'import { mutant } from "@/modules/projects";'),
            injected=1,
            expect_legacy_rule="shared-must-not-depend-on-app-or-modules",
            expect_limen_rule="frontend-shared-not-into-modules",
        ),
        Mutation(
            id="M-FE-05-cross-module-deep-import",
            inventory_id="L-FE-05",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A module deep-imports another module's internal path via the @/ alias.",
            apply=append_line(
                f("src/modules/schedule/hooks/useProjects.js"),
                'import { mutant } from "@/modules/projects/hooks/useProjectsPageState";',
            ),
            injected=1,
            expect_legacy_rule="cross-module-deep-import",
            expect_limen_rule="frontend-cross-module-public-entry",
            notes="Also trips cross-module-internal-import (L-FE-06) in legacy: one import, two legacy rules.",
        ),
        Mutation(
            id="M-FE-06-cross-module-internal-relative",
            inventory_id="L-FE-06",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A module reaches into another module's internals through a relative path.",
            apply=append_line(
                f("src/modules/schedule/hooks/useScheduleInfo.js"),
                'import { mutant } from "../../projects/hooks/useProjectsPageState";',
            ),
            injected=1,
            expect_legacy_rule="cross-module-internal-import",
            expect_limen_rule="frontend-cross-module-public-entry",
        ),
        Mutation(
            id="M-FE-07-app-deep-module-import",
            inventory_id="L-FE-07",
            side="frontend",
            frontend_repo=FRONTEND,
            description="src/app deep-imports a module path (legacy WARNING).",
            apply=append_line(
                f("src/app/DashboardLayout.jsx"),
                'import { mutant } from "@/modules/projects/hooks/useProjectsPageState";',
            ),
            injected=1,
            expect_legacy_rule="app-deep-module-import",
            expect_limen_rule=None,
            notes="L-FE-07 is documented as not-encoded in limen; limen fires a different rule "
            "(frontend-cross-module-public-entry does not apply to src/app), so it is expected NOT to fire.",
        ),
        Mutation(
            id="M-FE-08-oversized-page",
            inventory_id="L-FE-08",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A page file grows past 600 lines (legacy WARNING).",
            apply=pad_lines(f("src/modules/projects/pages/ProjectsPage.jsx"), 620),
            injected=1,
            expect_legacy_rule="oversized-page-file",
            expect_limen_rule="oversized-page-file",
        ),
        Mutation(
            id="M-FE-09-oversized-entrypoint",
            inventory_id="L-FE-09",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A module entrypoint grows past 80 lines (legacy WARNING).",
            apply=pad_lines(f("src/modules/messages/index.js"), 95),
            injected=1,
            expect_legacy_rule="oversized-module-entrypoint",
            expect_limen_rule="oversized-module-entrypoint",
        ),
        Mutation(
            id="M-FE-10-oversized-hook",
            inventory_id="L-FE-10",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A hook file grows past 350 lines (legacy WARNING).",
            apply=pad_lines(f("src/modules/schedule/hooks/useProjects.js"), 380),
            injected=1,
            expect_legacy_rule="oversized-hook-file",
            expect_limen_rule="oversized-hook-file",
        ),
        Mutation(
            id="M-FE-11-missing-entrypoint",
            inventory_id="L-FE-11",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A module loses its root index.* entrypoint.",
            apply=rename_file(f("src/modules/folders/index.js"), "entry.js"),
            injected=1,
            expect_legacy_rule="missing-module-entrypoint",
            expect_limen_rule="frontend-module-entrypoint",
        ),
        Mutation(
            id="M-FE-12-missing-readme",
            inventory_id="L-FE-12",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A module loses its README.md (legacy WARNING).",
            apply=delete_file(f("src/modules/folders/README.md")),
            injected=1,
            expect_legacy_rule="missing-module-readme",
            expect_limen_rule="frontend-module-readme-contract",
        ),
        Mutation(
            id="M-FE-13-missing-readme-contract",
            inventory_id="L-FE-13",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A module README loses one of the three required contract sections.",
            apply=strip_readme_section(f("src/modules/projects/README.md"), "Odpowiedzialność:"),
            injected=1,
            expect_legacy_rule="missing-module-contract",
            expect_limen_rule="frontend-module-readme-contract",
        ),
        Mutation(
            id="M-FE-15-shared-domain-helper",
            inventory_id="L-FE-15",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A src/shared/lib helper is used by only one module (legacy WARNING).",
            apply=compose(
                write_file(f("src/shared/lib/mutantHelper.js"), "export const mutantHelper = () => 1;\n"),
                append_line(
                    f("src/modules/schedule/hooks/useProjects.js"),
                    'import { mutantHelper } from "@/shared/lib/mutantHelper";',
                ),
            ),
            injected=1,
            expect_legacy_rule="unexpected-shared-domain-helper",
            expect_limen_rule=None,
            notes="L-FE-15 is documented as not-encoded in limen; limen is expected NOT to fire.",
        ),
        Mutation(
            id="M-FE-14-unexpected-subentrypoint",
            inventory_id="L-FE-14",
            side="frontend",
            frontend_repo=FRONTEND,
            description="A module exposes a non-standard root-level public file (legacy WARNING).",
            apply=write_file(f("src/modules/folders/mutant.js"), "export const mutant = 1;\n"),
            injected=1,
            expect_legacy_rule="unexpected-module-subentrypoint",
            expect_limen_rule=None,
            notes="L-FE-14 is documented as not-encoded in limen; limen is expected NOT to fire.",
        ),
    ]


def _inject_db_connection(root: Path) -> list[str]:
    rel = f"{BACKEND}/config/database.php"
    path = root / rel
    text = path.read_text()
    marker = "        'sqlite' => ["
    injected = (
        "        'folders_module' => [\n"
        "            'driver' => 'sqlite',\n"
        "            'database' => ':memory:',\n"
        "        ],\n\n"
    )
    path.write_text(text.replace(marker, injected + marker, 1))
    return [rel]


def _retarget_model_owner(root: Path) -> list[str]:
    rel = f"{BACKEND}/config/architecture.php"
    path = root / rel
    text = path.read_text()
    path.write_text(
        text.replace(
            "App\\Models\\AuditLog::class => 'Audit',",
            "App\\Models\\AuditLog::class => 'NoSuchModule',",
            1,
        )
    )
    return [rel]


# ---------------------------------------------------------------------------
# runners
# ---------------------------------------------------------------------------


def run_legacy(workspace: Path, out: Path) -> dict:
    subprocess.run(
        [sys.executable, str(EVAL_DIR / "run_legacy_oracle.py"), "--workspace", str(workspace), "--out", str(out), "--quiet"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.read_text())


def run_limen(workspace: Path) -> dict:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(LIMEN_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "limen.cli",
            "check",
            "--spec",
            str((workspace / "architecture.yaml").resolve()),
            "--workspace",
            str(workspace),
            "--today",
            "2026-08-04",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if not proc.stdout.strip():
        raise RuntimeError(f"limen check produced no output: {proc.stderr[:500]}")
    return json.loads(proc.stdout)


def rule_counts(findings: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f[key]] = counts.get(f[key], 0) + 1
    return counts


def copy_workspace(source: Path, dest: Path) -> None:
    shutil.copytree(
        source,
        dest,
        symlinks=True,
        ignore=shutil.ignore_patterns(".limen", "node_modules", "vendor", "__pycache__"),
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--out", type=Path, default=EVAL_DIR / "results" / "mutation_sensitivity.json")
    parser.add_argument("--validation-out", type=Path, default=EVAL_DIR / "results" / "oracle_validation.json")
    parser.add_argument("--only", nargs="*", default=None, help="run only these mutation ids")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    mutations = catalogue()
    if args.only:
        mutations = [m for m in mutations if m.id in set(args.only)]

    tmp_root = Path(tempfile.mkdtemp(prefix="limen-mutation-"))

    try:
        # -- baseline ----------------------------------------------------
        baseline_ws = tmp_root / "baseline"
        copy_workspace(workspace, baseline_ws)
        base_legacy = run_legacy(baseline_ws, tmp_root / "baseline_legacy.json")
        base_limen = run_limen(baseline_ws)

        base_legacy_n = len(base_legacy["findings"])
        base_limen_n = len(base_limen["findings"])
        base_limen_rules = rule_counts(base_limen["findings"], "rule_id")
        base_legacy_rules = rule_counts(base_legacy["findings"], "legacy_rule_id")

        results = []

        for mutation in mutations:
            mut_ws = tmp_root / mutation.id
            copy_workspace(workspace, mut_ws)
            touched = mutation.apply(mut_ws)

            legacy = run_legacy(mut_ws, tmp_root / f"{mutation.id}_legacy.json")
            limen = run_limen(mut_ws)

            legacy_rules = rule_counts(legacy["findings"], "legacy_rule_id")
            limen_rules = rule_counts(limen["findings"], "rule_id")

            legacy_delta = len(legacy["findings"]) - base_legacy_n
            limen_delta = len(limen["findings"]) - base_limen_n

            expected_legacy_hits = legacy_rules.get(mutation.expect_legacy_rule, 0) - base_legacy_rules.get(
                mutation.expect_legacy_rule, 0
            )
            expected_limen_hits = (
                limen_rules.get(mutation.expect_limen_rule, 0) - base_limen_rules.get(mutation.expect_limen_rule, 0)
                if mutation.expect_limen_rule
                else 0
            )

            detected_legacy = expected_legacy_hits >= mutation.injected
            detected_limen = (
                expected_limen_hits >= mutation.injected if mutation.expect_limen_rule else limen_delta == 0
            )

            results.append(
                {
                    "mutation_id": mutation.id,
                    "inventory_id": mutation.inventory_id,
                    "side": mutation.side,
                    "description": mutation.description,
                    "touched_files": touched,
                    "injected": mutation.injected,
                    "expect_legacy_rule": mutation.expect_legacy_rule,
                    "expect_limen_rule": mutation.expect_limen_rule,
                    "legacy_hits_on_expected_rule": expected_legacy_hits,
                    "limen_hits_on_expected_rule": expected_limen_hits,
                    "legacy_total_delta": legacy_delta,
                    "limen_total_delta": limen_delta,
                    "legacy_delta_equals_injected": legacy_delta == mutation.injected,
                    "detected_legacy": detected_legacy,
                    "detected_limen": detected_limen,
                    "legacy_rules_fired": {
                        k: v - base_legacy_rules.get(k, 0)
                        for k, v in legacy_rules.items()
                        if v - base_legacy_rules.get(k, 0) > 0
                    },
                    "limen_rules_fired": {
                        k: v - base_limen_rules.get(k, 0)
                        for k, v in limen_rules.items()
                        if v - base_limen_rules.get(k, 0) > 0
                    },
                    "notes": mutation.notes,
                }
            )
            shutil.rmtree(mut_ws, ignore_errors=True)

        summary = {
            "mutations": len(results),
            "detected_legacy": sum(1 for r in results if r["detected_legacy"]),
            "detected_limen": sum(1 for r in results if r["detected_limen"]),
            "detected_both": sum(1 for r in results if r["detected_legacy"] and r["detected_limen"]),
            "legacy_delta_exact": sum(1 for r in results if r["legacy_delta_equals_injected"]),
            "backend_mutations": sum(1 for r in results if r["side"] == "backend"),
            "frontend_mutations": sum(1 for r in results if r["side"] == "frontend"),
        }

        doc = {
            "schema": "mutation-sensitivity/1",
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "workspace": str(workspace),
            "baseline": {
                "legacy_findings": base_legacy_n,
                "limen_findings": base_limen_n,
                "limen_rules": base_limen_rules,
                "legacy_rules": base_legacy_rules,
            },
            "summary": summary,
            "results": results,
        }

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

        # -- shim validation view (backend only) -------------------------
        backend = [r for r in results if r["side"] == "backend"]
        validation = {
            "schema": "oracle-validation/1",
            "generated_at": doc["generated_at"],
            "claim": "The PHPUnit/Laravel shim in eval/legacy/php/ runs the legacy rules faithfully: "
            "on the clean snapshot it reports zero violations, and every planted backend violation "
            "is reported by the unmodified legacy class, with the total finding count rising by "
            "exactly the number of rules the injected line breaks.",
            "shim_mutation_validation": {
                "baseline_legacy_findings": base_legacy_n,
                "backend_mutations": len(backend),
                "detected": sum(1 for r in backend if r["detected_legacy"]),
                "undetected": [r["mutation_id"] for r in backend if not r["detected_legacy"]],
                "delta_exact": sum(1 for r in backend if r["legacy_delta_equals_injected"]),
                "delta_not_exact": [
                    {
                        "mutation_id": r["mutation_id"],
                        "injected": r["injected"],
                        "legacy_total_delta": r["legacy_total_delta"],
                        "legacy_rules_fired": r["legacy_rules_fired"],
                        "notes": r["notes"],
                    }
                    for r in backend
                    if not r["legacy_delta_equals_injected"]
                ],
                "per_mutation": [
                    {
                        "mutation_id": r["mutation_id"],
                        "inventory_id": r["inventory_id"],
                        "expect_legacy_rule": r["expect_legacy_rule"],
                        "legacy_hits_on_expected_rule": r["legacy_hits_on_expected_rule"],
                        "legacy_total_delta": r["legacy_total_delta"],
                        "detected": r["detected_legacy"],
                    }
                    for r in backend
                ],
            },
        }

        existing = {}
        if args.validation_out.is_file():
            existing = json.loads(args.validation_out.read_text())
        existing.update(validation)
        args.validation_out.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")

        print(json.dumps({"out": str(args.out), **summary}, indent=2))
        return 0

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

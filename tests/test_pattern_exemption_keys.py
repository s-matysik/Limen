"""`pattern-ban` exemptions: the validator's `path:` key works, and an
unconstrained exemption never switches a rule off.

Found while extracting deprecation norms from 66 statistics libraries: the spec
validator documents `exempt_files` entries keyed by `path:`, but the rule read
`target:`.  A validator-approved `- path: x.py` entry therefore carried no
constraint, matched every file, and an injected violation went undetected in
5 of 5 libraries -- the rule was off and nothing said so.
"""
from limen.rules import pattern

from conftest import FakeTree, ff, make_ctx, make_index, spec_from

SPEC = """
version: 1
roots:
  repo: { path: ., language: python }
rules:
  - id: no-old
    kind: pattern-ban
    severity: error
    scope: { roots: [repo], paths: ["pkg/**/*.py"] }
    pattern: 'old_fn\\('
    exempt_files: EXEMPT
    message: "old_fn is deprecated."
    remedy: "Use new_fn."
"""

FILES = {"pkg/legacy.py": "old_fn(1)\n", "pkg/new.py": "old_fn(2)\n"}


def _run(exempt_yaml):
    spec = spec_from(SPEC.replace("EXEMPT", exempt_yaml))
    idx = make_index([ff(p, language="python", kind="module") for p in FILES],
                     root="repo", language="python")
    ctx = make_ctx(spec, idx, tree=FakeTree(files=FILES))
    return sorted(f.file for f in pattern.check(spec.rules[0], ctx))


ENTRY = '{{ {key}: pkg/legacy.py, reason: legacy, owner: team, expires: "2099-01-01" }}'


def test_path_key_exempts_only_the_named_file():
    assert _run("[" + ENTRY.format(key="path") + "]") == ["pkg/new.py"]


def test_target_key_still_accepted():
    assert _run("[" + ENTRY.format(key="target") + "]") == ["pkg/new.py"]


def test_unconstrained_exemption_does_not_silence_the_rule():
    assert _run('[{ reason: oops, owner: team, expires: "2099-01-01" }]') == [
        "pkg/legacy.py", "pkg/new.py"]

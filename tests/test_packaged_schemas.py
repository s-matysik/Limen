"""Packaged schema copies must equal the canonical ones in schemas/."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_schemas_match_canonical():
    canon = sorted((ROOT / "schemas").glob("*.json"))
    assert canon, "no canonical schemas found"
    for f in canon:
        packaged = ROOT / "src" / "limen" / "schemas" / f.name
        assert packaged.exists(), f"missing packaged copy: {packaged}"
        assert packaged.read_bytes() == f.read_bytes(), f"packaged copy differs: {f.name}"

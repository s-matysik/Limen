"""Optional tree-sitter extractor (extras: ``pip install limen-arch[treesitter]``).

Same plugin contract, ``engine: treesitter``. Used by
``limen extract --compare regex,treesitter`` to produce the regex↔AST
agreement table for the paper (deps found only by regex = false positives from
comments/strings; only by AST = multiline or grouped constructs regex missed).
"""
from __future__ import annotations


def get_treesitter_extractor(language: str, config):
    try:
        import tree_sitter  # noqa: F401
    except ImportError as exc:
        from .base import ExtractorFailure
        raise ExtractorFailure(
            "tree-sitter extractor requested but the 'treesitter' extra is not "
            "installed; run: pip install limen-arch[treesitter]") from exc
    raise NotImplementedError(
        "tree-sitter grammars are wired per release; regex remains the default engine")

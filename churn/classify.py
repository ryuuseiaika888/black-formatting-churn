"""Classify diff hunks of a Python file by what they change.

A hunk is applied on its own to the old file, and the result is compared with
the old file:

  formatting  same AST and same comments
  comment     same AST, different comments
  logic       different AST (includes docstring text changes)
  unparsed    the old file, or the old file plus this hunk, does not parse

The AST comparison mirrors Black's own safety check: docstrings are compared
with per-line whitespace removed, and string `kind` (the u"" prefix) is ignored.
"""
import ast
import io
import tokenize
import warnings
from dataclasses import dataclass

FORMATTING = "formatting"
COMMENT = "comment"
LOGIC = "logic"
UNPARSED = "unparsed"


@dataclass
class Hunk:
    old_start: int  # as in the @@ header
    old_count: int
    added: list  # new lines, including line endings


def _normalise_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                value = body[0].value.value
                if isinstance(value, str):
                    lines = (line.strip() for line in value.splitlines())
                    body[0].value.value = "\n".join(line for line in lines if line)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            node.kind = None
    return tree


def ast_fingerprint(source):
    """Return a comparable form of the AST, or None if source does not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.dump(_normalise_docstrings(ast.parse(source)))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def comments_of(source):
    """Return comment texts in order; `#x` and `# x` count as the same comment."""
    comments = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comments.append(tok.string.lstrip("#").strip())
    except (tokenize.TokenError, SyntaxError, ValueError):
        return None
    return comments


def apply_hunk(old_lines, hunk):
    start = hunk.old_start - 1 if hunk.old_count > 0 else hunk.old_start
    return old_lines[:start] + hunk.added + old_lines[start + hunk.old_count:]


def classify_hunks(old_source, new_source, hunks):
    """Return one category per hunk."""
    old_ast = ast_fingerprint(old_source)
    if old_ast is None:
        return [UNPARSED] * len(hunks)
    old_comments = comments_of(old_source)

    # Reformatting commits touch hundreds of hunks: skip per-hunk work when possible.
    if new_source is not None and ast_fingerprint(new_source) == old_ast and comments_of(new_source) == old_comments:
        return [FORMATTING] * len(hunks)

    old_lines = old_source.splitlines(keepends=True)
    categories = []
    for hunk in hunks:
        candidate = "".join(apply_hunk(old_lines, hunk))
        candidate_ast = ast_fingerprint(candidate)
        if candidate_ast is None:
            categories.append(UNPARSED)
        elif candidate_ast != old_ast:
            categories.append(LOGIC)
        elif comments_of(candidate) != old_comments:
            categories.append(COMMENT)
        else:
            categories.append(FORMATTING)
    return categories

from __future__ import annotations

import hashlib
import subprocess
import unicodedata
from collections.abc import Mapping, Set
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_TOKEN_DIGESTS = frozenset(
    {
        "6f7ac1823da81d2e52d1a1549ee69c85bbf8bb56d06682849e7c09da2785ce3b",
        "7d3194f79e645c42e4396dda38be04766810ec6a00d00aced3ffc2a0a1f1a9ef",
    }
)
_FORBIDDEN_TOKEN_DIGESTS_BY_LENGTH: dict[int, frozenset[str]] = dict.fromkeys(
    (6, 8), _FORBIDDEN_TOKEN_DIGESTS
)


def _candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        _REPO_ROOT / raw_path.decode("utf-8") for raw_path in result.stdout.split(b"\0") if raw_path
    ]


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _contains_forbidden_digest(
    value: str,
    digests_by_length: Mapping[int, Set[str]],
) -> bool:
    compact_value = _compact(value)
    for length, forbidden_digests in digests_by_length.items():
        for start in range(max(len(compact_value) - length + 1, 0)):
            token = compact_value[start : start + length].encode("utf-8")
            if hashlib.sha256(token).hexdigest() in forbidden_digests:
                return True
    return False


def test_supplier_scan_catches_separator_and_filename_evasion() -> None:
    digest = hashlib.sha256(b"alpha").hexdigest()
    policy = {5: frozenset({digest})}

    assert _contains_forbidden_digest("a-l_p\u200bha", policy)
    assert _contains_forbidden_digest("src/a／l.p h-a_adapter.py", policy)


def test_public_tree_contains_no_supplier_specific_tokens() -> None:
    violations: list[str] = []
    for path in _candidate_files():
        relative_path = path.relative_to(_REPO_ROOT)
        if _contains_forbidden_digest(
            relative_path.as_posix(),
            _FORBIDDEN_TOKEN_DIGESTS_BY_LENGTH,
        ):
            violations.append(str(relative_path))
            continue
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_bytes().decode("utf-8", errors="ignore")
        if _contains_forbidden_digest(content, _FORBIDDEN_TOKEN_DIGESTS_BY_LENGTH):
            violations.append(str(relative_path))

    assert not violations, "supplier-specific tokens found in: " + ", ".join(violations)

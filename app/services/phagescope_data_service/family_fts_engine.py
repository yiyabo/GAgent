"""Read-only protein-family FTS5 search over a validated GAgent-owned snapshot.

No PostgreSQL writes or online index construction. Each request opens a separate
immutable SQLite connection so ThreadingHTTPServer can serve queries safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

try:
    from . import db_queries as dq
except ImportError:  # host data service runs these modules as loose files
    import db_queries as dq


def family_index_root_from_env() -> Path | None:
    raw = os.environ.get("PHAGE_FAMILY_INDEX_ROOT", "").strip()
    return Path(raw) if raw else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())


def _quoted(value: str) -> str:
    # FTS5 syntax is never interpolated into SQL. Each term is a quoted FTS5
    # phrase and the whole MATCH expression is passed through a SQL parameter.
    return '"' + value.replace('"', '""') + '"'


class FamilyFTSEngine:
    def __init__(self, root: Path) -> None:
        self.available = False
        self.last_error = "index_unavailable"
        self.index_path: Path | None = None
        self.manifest: dict[str, Any] = {}
        self.alias_groups: dict[str, tuple[str, ...]] = {}
        try:
            supplied = Path(root)
            resolved = supplied.resolve(strict=True)
            if not resolved.is_dir() or resolved.parent != supplied.parent.resolve(strict=True):
                raise ValueError("current must resolve to a direct child version directory")
            manifest = json.loads((resolved / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
                raise ValueError("unsupported family index manifest")
            if not isinstance(manifest.get("indexed_rows"), int) or manifest["indexed_rows"] <= 0:
                raise ValueError("invalid indexed family count")
            if not isinstance(manifest.get("source_rows"), int) or manifest["source_rows"] < manifest["indexed_rows"]:
                raise ValueError("invalid source family count")
            if manifest.get("version") != resolved.name or manifest.get("source_role") != "phage_ro":
                raise ValueError("family index provenance or version mismatch")
            if not isinstance(manifest.get("built_at"), str) or not manifest["built_at"]:
                raise ValueError("missing family index build timestamp")
            files = manifest["files"]
            for name in ("families.sqlite", "family_synonyms.json"):
                file_path = resolved / name
                info = files[name]
                if file_path.is_symlink() or not file_path.is_file():
                    raise ValueError("missing or linked index file")
                if file_path.stat().st_size != info["bytes"] or _sha256(file_path) != info["sha256"]:
                    raise ValueError("family index file checksum mismatch")
            self.index_path = resolved / "families.sqlite"
            synonyms = json.loads((resolved / "family_synonyms.json").read_text(encoding="utf-8"))
            if synonyms.get("format_version") != 1 or not isinstance(synonyms.get("groups"), list):
                raise ValueError("unsupported family synonym format")
            for group in synonyms["groups"]:
                terms = (group["canonical"], *group["aliases"])
                if not 2 <= len(terms) <= 12 or not all(isinstance(term, str) and term.strip() for term in terms):
                    raise ValueError("invalid family synonym group")
                for term in terms:
                    normalized = _normalize(term)
                    if normalized in self.alias_groups:
                        raise ValueError("duplicate family synonym term")
                    self.alias_groups[normalized] = terms
            with closing(self._connect()) as conn:
                count = conn.execute("SELECT COUNT(*) FROM families").fetchone()[0]
                if count != manifest["indexed_rows"]:
                    raise ValueError("family index row count mismatch")
                conn.execute(
                    "SELECT rowid FROM family_fts WHERE family_fts MATCH ? LIMIT 1",
                    ('"endolysin"',),
                ).fetchone()
            self.manifest = manifest
            self.available = True
            self.last_error = ""
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            self.last_error = str(exc)
            self.index_path = None

    def _connect(self) -> sqlite3.Connection:
        if self.index_path is None:
            raise ValueError("family index file is unavailable")
        conn = sqlite3.connect(self.index_path.as_uri() + "?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def search(
        self,
        keyword: str,
        *,
        mode: str = "fts",
        dominant_class: str | None = None,
        min_members: int | None = None,
        min_datasets: int | None = None,
        non_singleton_only: bool = True,
        page: int = 1,
        pagesize: int = 20,
    ) -> dict[str, Any]:
        if not self.available:
            raise dq.ServiceQueryError("family_index_unavailable", "The family FTS5 index is unavailable.", 503)
        if mode not in ("fts", "synonyms"):
            raise dq.ServiceQueryError("invalid_parameter", "family_search_mode must be fts or synonyms.")
        if not isinstance(keyword, str) or len(keyword) > 120 or not keyword.strip():
            raise dq.ServiceQueryError(
                "invalid_parameter",
                "A family keyword of 1..120 characters is required.",
            )
        term = _normalize(keyword)
        if not any(char.isalnum() for char in term) or any(ord(char) < 32 for char in keyword):
            raise dq.ServiceQueryError("invalid_parameter", "A family keyword must contain letters or digits.")
        terms = self.alias_groups.get(term, (term,)) if mode == "synonyms" else (term,)
        match_query = " OR ".join(_quoted(value) for value in terms)
        try:
            page = max(1, int(page))
            pagesize = max(1, min(int(pagesize), 100))
        except (TypeError, ValueError) as exc:
            raise dq.ServiceQueryError("invalid_parameter", "page and pagesize must be integers.") from exc
        offset = (page - 1) * pagesize
        class_filter = str(dominant_class or "")
        params = (
            term,
            term,
            match_query,
            1 if non_singleton_only else 0,
            min_members,
            min_members,
            min_datasets,
            min_datasets,
            class_filter,
            class_filter,
            pagesize + 1,
            offset,
        )
        try:
            with closing(self._connect()) as conn:
                raw = conn.execute(
                    """SELECT f.family_id, f.member_count, f.db_member_count,
       f.dominant_class, f.consensus_product, f.subfamily_count,
       f.representative_protein_id, f.dataset_count,
       CASE WHEN instr(lower(f.consensus_product), ?) > 0
                  OR instr(lower(f.dominant_class), ?) > 0 THEN 0 ELSE 1 END AS alias_rank
FROM family_fts JOIN families AS f ON f.rowid = family_fts.rowid
WHERE family_fts MATCH ?
  AND (? = 0 OR f.is_singleton = 0)
  AND (? IS NULL OR f.member_count >= ?)
  AND (? IS NULL OR f.dataset_count >= ?)
  AND (? = '' OR instr(lower(f.dominant_class), lower(?)) > 0)
ORDER BY f.member_count DESC, alias_rank, f.family_id DESC
LIMIT ? OFFSET ?""",
                    params,
                ).fetchall()
        except sqlite3.Error as exc:
            raise dq.ServiceQueryError("family_index_unavailable", "The family FTS5 query failed.", 503) from exc
        results: list[dict[str, Any]] = []
        for row in raw[:pagesize]:
            result = {
                name: row[name]
                for name in (
                    "family_id",
                    "member_count",
                    "db_member_count",
                    "dominant_class",
                    "consensus_product",
                    "subfamily_count",
                    "representative_protein_id",
                    "dataset_count",
                )
            }
            result["dominant_class"] = result["dominant_class"] or ""
            result["representative_protein_id"] = result["representative_protein_id"] or ""
            if mode == "synonyms":
                result["match_kind"] = "literal" if row["alias_rank"] == 0 else "alias"
            results.append(result)
        return {
            "page": page,
            "pagesize": pagesize,
            "has_more": len(raw) > pagesize,
            "results": results,
            "source": "family_fts5",
            "search_mode": mode,
            "expanded_terms": list(terms) if mode == "synonyms" else [],
            "index_built_at": self.manifest["built_at"],
        }

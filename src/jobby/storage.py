"""SQLite persistence layer for Jobby."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from jobby.models import JobOpportunity, OpportunityType


class Storage:
    """Handles local SQLite storage and queries for job opportunities."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = os.environ.get("JOBBY_DATABASE_PATH", "_bmad-output/jobby.db")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS opportunities (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    opportunity_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT,
                    location TEXT,
                    remote_policy TEXT,
                    salary_raw TEXT,
                    url TEXT NOT NULL,
                    author_name TEXT,
                    author_profile_url TEXT,
                    author_title TEXT,
                    description TEXT,
                    published_at TEXT,
                    discovered_at TEXT NOT NULL,
                    score REAL,
                    score_reasons TEXT,
                    tags TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    dedup_key TEXT NOT NULL,
                    raw_metadata TEXT,
                    post_intent TEXT,
                    ai_summary TEXT
                )
            """)
            # Auto-migrate table columns if missing
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(opportunities)").fetchall()}
            if "post_intent" not in cols:
                conn.execute("ALTER TABLE opportunities ADD COLUMN post_intent TEXT")
            if "ai_summary" not in cols:
                conn.execute("ALTER TABLE opportunities ADD COLUMN ai_summary TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dedup ON opportunities (dedup_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON opportunities (source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON opportunities (status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON opportunities (score)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered ON opportunities (discovered_at)")
            conn.commit()

    def upsert_opportunity(self, opp: JobOpportunity) -> Tuple[bool, JobOpportunity]:
        """
        Inserts or updates an opportunity.
        Returns: (is_new, updated_or_saved_opp)
        """
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id, status, score FROM opportunities WHERE id = ?", (opp.id,)
            ).fetchone()
            
            is_new = existing is None
            status = existing["status"] if existing else opp.status
            score = opp.score if opp.score is not None else (existing["score"] if existing else None)

            published_str = opp.published_at.isoformat() if opp.published_at else None
            discovered_str = opp.discovered_at.isoformat()
            reasons_json = json.dumps(opp.score_reasons, ensure_ascii=False)
            tags_json = json.dumps(opp.tags, ensure_ascii=False)
            raw_meta_json = json.dumps(opp.raw_metadata, ensure_ascii=False)

            conn.execute("""
                INSERT INTO opportunities (
                    id, source, opportunity_type, title, company, location,
                    remote_policy, salary_raw, url, author_name, author_profile_url,
                    author_title, description, published_at, discovered_at,
                    score, score_reasons, tags, status, dedup_key, raw_metadata,
                    post_intent, ai_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    company = COALESCE(excluded.company, opportunities.company),
                    location = COALESCE(excluded.location, opportunities.location),
                    remote_policy = COALESCE(excluded.remote_policy, opportunities.remote_policy),
                    description = excluded.description,
                    score = COALESCE(excluded.score, opportunities.score),
                    score_reasons = excluded.score_reasons,
                    tags = excluded.tags,
                    post_intent = COALESCE(excluded.post_intent, opportunities.post_intent),
                    ai_summary = COALESCE(excluded.ai_summary, opportunities.ai_summary)
            """, (
                opp.id,
                opp.source,
                opp.opportunity_type.value,
                opp.title,
                opp.company,
                opp.location,
                opp.remote_policy,
                opp.salary_raw,
                opp.url,
                opp.author_name,
                opp.author_profile_url,
                opp.author_title,
                opp.description,
                published_str,
                discovered_str,
                score,
                reasons_json,
                tags_json,
                status,
                opp.dedup_key,
                raw_meta_json,
                opp.post_intent,
                opp.ai_summary
            ))
            conn.commit()

        opp.status = status
        opp.score = score
        return is_new, opp

    def get_opportunity(self, opp_id: str) -> Optional[JobOpportunity]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,)).fetchone()
            if not row:
                return None
            return self._row_to_opportunity(row)

    def list_opportunities(
        self,
        source: Optional[str] = None,
        status: Optional[str] = None,
        min_score: Optional[float] = None,
        opp_type: Optional[OpportunityType] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[JobOpportunity]:
        query = "SELECT * FROM opportunities WHERE 1=1"
        params: List[Any] = []

        if source:
            query += " AND source = ?"
            params.append(source)
        if status:
            query += " AND status = ?"
            params.append(status)
        if min_score is not None:
            query += " AND score >= ?"
            params.append(min_score)
        if opp_type:
            query += " AND opportunity_type = ?"
            params.append(opp_type.value)

        query += " ORDER BY discovered_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_opportunity(r) for r in rows]

    def update_status(self, opp_id: str, new_status: str) -> bool:
        with self._connection() as conn:
            cur = conn.execute(
                "UPDATE opportunities SET status = ? WHERE id = ?",
                (new_status, opp_id)
            )
            conn.commit()
            return cur.rowcount > 0

    def get_stats(self) -> Dict[str, Any]:
        with self._connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
            by_source = dict(conn.execute("SELECT source, COUNT(*) FROM opportunities GROUP BY source").fetchall())
            by_type = dict(conn.execute("SELECT opportunity_type, COUNT(*) FROM opportunities GROUP BY opportunity_type").fetchall())
            by_status = dict(conn.execute("SELECT status, COUNT(*) FROM opportunities GROUP BY status").fetchall())
            return {
                "total": total,
                "by_source": by_source,
                "by_type": by_type,
                "by_status": by_status,
            }

    @staticmethod
    def _row_to_opportunity(row: sqlite3.Row) -> JobOpportunity:
        published_at = datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
        discovered_at = datetime.fromisoformat(row["discovered_at"])
        reasons = json.loads(row["score_reasons"]) if row["score_reasons"] else []
        tags = json.loads(row["tags"]) if row["tags"] else []
        raw_meta = json.loads(row["raw_metadata"]) if row["raw_metadata"] else {}

        post_intent = row["post_intent"] if "post_intent" in row.keys() else None
        ai_summary = row["ai_summary"] if "ai_summary" in row.keys() else None

        return JobOpportunity(
            id=row["id"],
            source=row["source"],
            opportunity_type=OpportunityType(row["opportunity_type"]),
            title=row["title"],
            company=row["company"],
            location=row["location"],
            remote_policy=row["remote_policy"],
            salary_raw=row["salary_raw"],
            url=row["url"],
            author_name=row["author_name"],
            author_profile_url=row["author_profile_url"],
            author_title=row["author_title"],
            description=row["description"] or "",
            published_at=published_at,
            discovered_at=discovered_at,
            score=row["score"],
            score_reasons=reasons,
            tags=tags,
            status=row["status"],
            raw_metadata=raw_meta,
            post_intent=post_intent,
            ai_summary=ai_summary,
        )

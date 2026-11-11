"""SQLite persistence layer for Jobby."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Tuple

from jobby.models import Application, ApplicationStatus, JobOpportunity, MatchEvaluation, OpportunityType, User, UserProfile

KEPT_STATUS_ALIASES = frozenset({"favorite", "saved", "kept"})


def normalize_opportunity_status(raw_status: Optional[str]) -> str:
    """Canonicalizes legacy kept aliases ('favorite', 'saved') to 'kept'."""
    if not raw_status:
        return "new"
    cleaned = str(raw_status).strip().lower()
    if not cleaned:
        return "new"
    return "kept" if cleaned in KEPT_STATUS_ALIASES else cleaned


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
                    is_seen INTEGER NOT NULL DEFAULT 0,
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
            if "is_seen" not in cols:
                conn.execute("BEGIN")
                try:
                    conn.execute("ALTER TABLE opportunities ADD COLUMN is_seen INTEGER NOT NULL DEFAULT 0")
                    conn.execute("UPDATE opportunities SET is_seen = 1 WHERE status != 'new'")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dedup ON opportunities (dedup_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON opportunities (source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON opportunities (status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_is_seen ON opportunities (is_seen)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON opportunities (score)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered ON opportunities (discovered_at)")

            # Users table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    full_name TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # User profiles & CVs table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    cv_filename TEXT,
                    cv_text TEXT,
                    portfolio_url TEXT,
                    github_url TEXT,
                    linkedin_url TEXT,
                    bio TEXT,
                    writing_sample TEXT,
                    writing_tone TEXT,
                    target_roles TEXT,
                    skills TEXT,
                    min_salary INTEGER,
                    remote_preference TEXT,
                    ideal_job_brief TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            profile_cols = {r["name"] for r in conn.execute("PRAGMA table_info(user_profiles)").fetchall()}
            if "ideal_job_brief" not in profile_cols:
                if conn.in_transaction:
                    conn.commit()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    profile_cols = {r["name"] for r in conn.execute("PRAGMA table_info(user_profiles)").fetchall()}
                    if "ideal_job_brief" not in profile_cols:
                        conn.execute("ALTER TABLE user_profiles ADD COLUMN ideal_job_brief TEXT")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            # Application Kanban Pipeline tracking table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'saved',
                    notes TEXT,
                    contact_name TEXT,
                    contact_email TEXT,
                    salary_offered INTEGER,
                    applied_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_app_user ON applications (user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_app_status ON applications (status)")

            # Match evaluations table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS match_evaluations (
                    user_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    score REAL NOT NULL,
                    verdict TEXT NOT NULL,
                    strengths TEXT,
                    weaknesses TEXT,
                    pitch TEXT,
                    cover_letter TEXT,
                    PRIMARY KEY(user_id, opportunity_id)
                )
            """)
            conn.commit()

    def upsert_opportunity(self, opp: JobOpportunity) -> Tuple[bool, JobOpportunity]:
        """
        Inserts or updates an opportunity.
        Returns: (is_new, updated_or_saved_opp)
        """
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT id, status, score, is_seen, raw_metadata, score_reasons FROM opportunities WHERE id = ?",
                    (opp.id,),
                ).fetchone()
                
                is_new = existing is None
                status = normalize_opportunity_status(existing["status"] if existing else opp.status)
                is_seen = bool(existing["is_seen"]) if existing else bool(opp.is_seen)

                existing_meta = self._parse_raw_metadata(existing["raw_metadata"]) if existing else {}
                incoming_meta = opp.raw_metadata if isinstance(opp.raw_metadata, dict) else {}
                merged_meta = {**existing_meta, **incoming_meta}

                preserve_ai_match = (
                    existing is not None
                    and "ai_match" in existing_meta
                    and "ai_match" not in incoming_meta
                )
                if preserve_ai_match:
                    score = existing["score"] if existing["score"] is not None else opp.score
                    try:
                        parsed_reasons = json.loads(existing["score_reasons"]) if existing["score_reasons"] else []
                        score_reasons = parsed_reasons if isinstance(parsed_reasons, list) else []
                    except (ValueError, TypeError):
                        score_reasons = []
                else:
                    score = opp.score if opp.score is not None else (existing["score"] if existing else None)
                    score_reasons = list(opp.score_reasons)

                published_str = opp.published_at.isoformat() if opp.published_at else None
                discovered_str = opp.discovered_at.isoformat()
                reasons_json = json.dumps(score_reasons, ensure_ascii=False)
                tags_json = json.dumps(opp.tags, ensure_ascii=False)
                raw_meta_json = json.dumps(merged_meta, ensure_ascii=False)

                conn.execute("""
                    INSERT INTO opportunities (
                        id, source, opportunity_type, title, company, location,
                        remote_policy, salary_raw, url, author_name, author_profile_url,
                        author_title, description, published_at, discovered_at,
                        score, score_reasons, tags, status, is_seen, dedup_key, raw_metadata,
                        post_intent, ai_summary
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        company = COALESCE(excluded.company, opportunities.company),
                        location = COALESCE(excluded.location, opportunities.location),
                        remote_policy = COALESCE(excluded.remote_policy, opportunities.remote_policy),
                        description = excluded.description,
                        score = COALESCE(excluded.score, opportunities.score),
                        score_reasons = excluded.score_reasons,
                        tags = excluded.tags,
                        raw_metadata = excluded.raw_metadata,
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
                    int(is_seen),
                    opp.dedup_key,
                    raw_meta_json,
                    opp.post_intent,
                    opp.ai_summary
                ))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        opp.status = status
        opp.score = score
        opp.score_reasons = score_reasons
        opp.is_seen = is_seen
        opp.raw_metadata = merged_meta
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
        if status and status.strip():
            norm_filter = normalize_opportunity_status(status)
            if norm_filter == "kept":
                query += " AND LOWER(TRIM(status)) IN ('kept', 'saved', 'favorite')"
            else:
                query += " AND LOWER(TRIM(status)) = ?"
                params.append(norm_filter)
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
        canonical_status = normalize_opportunity_status(new_status)
        with self._connection() as conn:
            if canonical_status != "new":
                cur = conn.execute(
                    "UPDATE opportunities SET status = ?, is_seen = 1 WHERE id = ?",
                    (canonical_status, opp_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE opportunities SET status = ? WHERE id = ?",
                    (canonical_status, opp_id),
                )
            conn.commit()
            return cur.rowcount > 0

    def mark_opportunity_seen(self, opp_id: str, is_seen: bool = True) -> bool:
        """Persist seen state for a single opportunity; returns False if missing."""
        with self._connection() as conn:
            cur = conn.execute(
                "UPDATE opportunities SET is_seen = ? WHERE id = ?",
                (int(bool(is_seen)), opp_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def mark_all_opportunities_seen(self) -> int:
        """Mark all unseen opportunities as seen; returns number of updated rows."""
        with self._connection() as conn:
            cur = conn.execute(
                "UPDATE opportunities SET is_seen = 1 WHERE is_seen = 0"
            )
            conn.commit()
            return cur.rowcount

    def get_stats(self) -> Dict[str, Any]:
        with self._connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
            by_source = dict(conn.execute("SELECT source, COUNT(*) FROM opportunities GROUP BY source").fetchall())
            by_type = dict(conn.execute("SELECT opportunity_type, COUNT(*) FROM opportunities GROUP BY opportunity_type").fetchall())
            raw_by_status = conn.execute("SELECT status, COUNT(*) FROM opportunities GROUP BY status").fetchall()
            by_status: Dict[str, int] = {}
            for raw_st, count in raw_by_status:
                canon_st = normalize_opportunity_status(raw_st)
                by_status[canon_st] = by_status.get(canon_st, 0) + count
            return {
                "total": total,
                "by_source": by_source,
                "by_type": by_type,
                "by_status": by_status,
            }

    @staticmethod
    def _parse_raw_metadata(raw_value: Any) -> Dict[str, Any]:
        """Parses raw_metadata JSON safely, ignoring corrupted or non-dict values."""
        if not raw_value:
            return {}
        if isinstance(raw_value, dict):
            return dict(raw_value)
        try:
            parsed = json.loads(raw_value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _row_to_opportunity(row: sqlite3.Row) -> JobOpportunity:
        published_at = datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
        discovered_at = datetime.fromisoformat(row["discovered_at"])
        reasons = json.loads(row["score_reasons"]) if row["score_reasons"] else []
        tags = json.loads(row["tags"]) if row["tags"] else []
        raw_meta = Storage._parse_raw_metadata(row["raw_metadata"])

        post_intent = row["post_intent"] if "post_intent" in row.keys() else None
        ai_summary = row["ai_summary"] if "ai_summary" in row.keys() else None
        is_seen = bool(row["is_seen"]) if "is_seen" in row.keys() else False

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
            status=normalize_opportunity_status(row["status"]),
            is_seen=is_seen,
            raw_metadata=raw_meta,
            post_intent=post_intent,
            ai_summary=ai_summary,
        )

    # -------------------------------------------------------------
    # User Management
    # -------------------------------------------------------------
    def create_user(self, user: User) -> User:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO users (id, email, full_name, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.email.strip().lower(),
                    user.full_name,
                    user.password_hash,
                    user.created_at.isoformat(),
                ),
            )
            conn.commit()
        return user

    def ensure_user(self, user: User) -> User:
        """Atomically fetches or inserts a user by email without raising IntegrityError on races."""
        normalized_email = user.email.strip().lower()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM users WHERE lower(email) = lower(?)",
                    (normalized_email,),
                ).fetchone()
                if not row:
                    conn.execute(
                        """
                        INSERT INTO users (id, email, full_name, password_hash, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(email) DO NOTHING
                        """,
                        (
                            user.id,
                            normalized_email,
                            user.full_name,
                            user.password_hash,
                            user.created_at.isoformat(),
                        ),
                    )
                    row = conn.execute(
                        "SELECT * FROM users WHERE lower(email) = lower(?)",
                        (normalized_email,),
                    ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return User(
                id=row["id"],
                email=row["email"],
                full_name=row["full_name"],
                password_hash=row["password_hash"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    def get_user_by_email(self, email: str) -> Optional[User]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE lower(email) = lower(?)",
                (email.strip(),),
            ).fetchone()
            if not row:
                return None
            return User(
                id=row["id"],
                email=row["email"],
                full_name=row["full_name"],
                password_hash=row["password_hash"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            return User(
                id=row["id"],
                email=row["email"],
                full_name=row["full_name"],
                password_hash=row["password_hash"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    # -------------------------------------------------------------
    # User Profile, CV & Tone of Voice
    # -------------------------------------------------------------
    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> UserProfile:
        return UserProfile(
            user_id=row["user_id"],
            cv_filename=row["cv_filename"],
            cv_text=row["cv_text"],
            portfolio_url=row["portfolio_url"],
            github_url=row["github_url"],
            linkedin_url=row["linkedin_url"],
            bio=row["bio"],
            writing_sample=row["writing_sample"],
            writing_tone=row["writing_tone"] or "naturel, direct, enthousiaste et percutant",
            target_roles=json.loads(row["target_roles"]) if row["target_roles"] else [],
            skills=json.loads(row["skills"]) if row["skills"] else [],
            min_salary=row["min_salary"],
            remote_preference=row["remote_preference"],
            ideal_job_brief=row["ideal_job_brief"] if "ideal_job_brief" in row.keys() else None,
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_profile(self, user_id: str) -> Optional[UserProfile]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_profile(row)

    def ensure_profile(self, profile: UserProfile) -> UserProfile:
        """Atomically inserts a default profile if missing without overwriting existing fields."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO user_profiles (
                    user_id, cv_filename, cv_text, portfolio_url, github_url,
                    linkedin_url, bio, writing_sample, writing_tone, target_roles,
                    skills, min_salary, remote_preference, ideal_job_brief, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (
                    profile.user_id,
                    profile.cv_filename,
                    profile.cv_text,
                    profile.portfolio_url,
                    profile.github_url,
                    profile.linkedin_url,
                    profile.bio,
                    profile.writing_sample,
                    profile.writing_tone,
                    json.dumps(profile.target_roles),
                    json.dumps(profile.skills),
                    profile.min_salary,
                    profile.remote_preference,
                    profile.ideal_job_brief,
                    now_iso,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?",
                (profile.user_id,),
            ).fetchone()
            return self._row_to_profile(row)

    def save_profile(self, profile: UserProfile) -> None:
        now = datetime.now(timezone.utc)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO user_profiles (
                    user_id, cv_filename, cv_text, portfolio_url, github_url,
                    linkedin_url, bio, writing_sample, writing_tone, target_roles,
                    skills, min_salary, remote_preference, ideal_job_brief, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    cv_filename = excluded.cv_filename,
                    cv_text = excluded.cv_text,
                    portfolio_url = excluded.portfolio_url,
                    github_url = excluded.github_url,
                    linkedin_url = excluded.linkedin_url,
                    bio = excluded.bio,
                    writing_sample = excluded.writing_sample,
                    writing_tone = excluded.writing_tone,
                    target_roles = excluded.target_roles,
                    skills = excluded.skills,
                    min_salary = excluded.min_salary,
                    remote_preference = excluded.remote_preference,
                    ideal_job_brief = excluded.ideal_job_brief,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.user_id,
                    profile.cv_filename,
                    profile.cv_text,
                    profile.portfolio_url,
                    profile.github_url,
                    profile.linkedin_url,
                    profile.bio,
                    profile.writing_sample,
                    profile.writing_tone,
                    json.dumps(profile.target_roles),
                    json.dumps(profile.skills),
                    profile.min_salary,
                    profile.remote_preference,
                    profile.ideal_job_brief,
                    now.isoformat(),
                ),
            )
            conn.commit()
        profile.updated_at = now

    # -------------------------------------------------------------
    # Embedded Application Kanban Pipeline
    # -------------------------------------------------------------
    def add_application(self, app: Application) -> Application:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO applications (
                    id, user_id, opportunity_id, status, notes,
                    contact_name, contact_email, salary_offered, applied_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    notes = excluded.notes,
                    contact_name = excluded.contact_name,
                    contact_email = excluded.contact_email,
                    salary_offered = excluded.salary_offered,
                    applied_at = excluded.applied_at,
                    updated_at = excluded.updated_at
                """,
                (
                    app.id,
                    app.user_id,
                    app.opportunity_id,
                    app.status.value,
                    app.notes,
                    app.contact_name,
                    app.contact_email,
                    app.salary_offered,
                    app.applied_at.isoformat() if app.applied_at else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        return app

    def get_applications(self, user_id: str) -> List[Application]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            # Auto-sync any opportunities marked by the user in hunt view that don't yet have an application entry
            conn.execute(
                """
                INSERT OR IGNORE INTO applications (
                    id, user_id, opportunity_id, status, notes, contact_name, updated_at
                )
                SELECT 
                    'app_' || ? || '_' || o.id,
                    ?,
                    o.id,
                    'saved',
                    '',
                    o.author_name,
                    ?
                FROM opportunities o
                WHERE o.status IN ('kept', 'saved', 'favorite')
                  AND NOT EXISTS (
                      SELECT 1 FROM applications a 
                      WHERE a.user_id = ? AND a.opportunity_id = o.id
                  )
                """,
                (user_id, user_id, now_iso, user_id),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO applications (
                    id, user_id, opportunity_id, status, notes, contact_name, updated_at
                )
                SELECT 
                    'app_' || ? || '_' || o.id,
                    ?,
                    o.id,
                    'rejected',
                    'Archivé depuis le flux',
                    o.author_name,
                    ?
                FROM opportunities o
                WHERE o.status = 'archived'
                  AND NOT EXISTS (
                      SELECT 1 FROM applications a 
                      WHERE a.user_id = ? AND a.opportunity_id = o.id
                  )
                """,
                (user_id, user_id, now_iso, user_id),
            )
            conn.commit()

            rows = conn.execute(
                """
                SELECT 
                    a.id as app_id,
                    a.user_id as app_user_id,
                    a.opportunity_id as app_opp_id,
                    a.status as app_status,
                    a.notes as app_notes,
                    a.contact_name as app_contact_name,
                    a.contact_email as app_contact_email,
                    a.salary_offered as app_salary_offered,
                    a.applied_at as app_applied_at,
                    a.updated_at as app_updated_at,
                    o.*
                FROM applications a
                LEFT JOIN opportunities o ON a.opportunity_id = o.id
                WHERE a.user_id = ?
                ORDER BY a.updated_at DESC
                """,
                (user_id,),
            ).fetchall()

            apps = []
            for row in rows:
                opp = None
                if row["title"]:  # Opportunity was joined
                    opp = self._row_to_opportunity(row)
                
                applied_at = datetime.fromisoformat(row["app_applied_at"]) if row["app_applied_at"] else None
                updated_at = datetime.fromisoformat(row["app_updated_at"]) if row["app_updated_at"] else datetime.now(timezone.utc)
                
                status_val = row["app_status"]
                if status_val not in ApplicationStatus._value2member_map_:
                    status_val = ApplicationStatus.SAVED.value

                apps.append(
                    Application(
                        id=row["app_id"],
                        user_id=row["app_user_id"],
                        opportunity_id=row["app_opp_id"],
                        status=ApplicationStatus(status_val),
                        notes=row["app_notes"] or "",
                        contact_name=row["app_contact_name"],
                        contact_email=row["app_contact_email"],
                        salary_offered=row["app_salary_offered"],
                        applied_at=applied_at,
                        updated_at=updated_at,
                        opportunity=opp,
                    )
                )
            return apps

    def get_application(self, app_id: str, user_id: str) -> Optional[Application]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT 
                    a.id as app_id,
                    a.user_id as app_user_id,
                    a.opportunity_id as app_opp_id,
                    a.status as app_status,
                    a.notes as app_notes,
                    a.contact_name as app_contact_name,
                    a.contact_email as app_contact_email,
                    a.salary_offered as app_salary_offered,
                    a.applied_at as app_applied_at,
                    a.updated_at as app_updated_at,
                    o.*
                FROM applications a
                LEFT JOIN opportunities o ON a.opportunity_id = o.id
                WHERE a.id = ? AND a.user_id = ?
                """,
                (app_id, user_id),
            ).fetchone()
            if not row:
                return None
            opp = self._row_to_opportunity(row) if row["title"] else None
            applied_at = datetime.fromisoformat(row["app_applied_at"]) if row["app_applied_at"] else None
            updated_at = datetime.fromisoformat(row["app_updated_at"]) if row["app_updated_at"] else datetime.now(timezone.utc)
            status_val = row["app_status"]
            if status_val not in ApplicationStatus._value2member_map_:
                status_val = ApplicationStatus.SAVED.value

            return Application(
                id=row["app_id"],
                user_id=row["app_user_id"],
                opportunity_id=row["app_opp_id"],
                status=ApplicationStatus(status_val),
                notes=row["app_notes"] or "",
                contact_name=row["app_contact_name"],
                contact_email=row["app_contact_email"],
                salary_offered=row["app_salary_offered"],
                applied_at=applied_at,
                updated_at=updated_at,
                opportunity=opp,
            )

    def sync_opportunity_to_application(
        self, opportunity_id: str, user_id: str, status: ApplicationStatus | str, notes: Optional[str] = None
    ) -> Optional[Application]:
        status_enum = status if isinstance(status, ApplicationStatus) else ApplicationStatus(str(status).strip().lower())
        now_iso = datetime.now(timezone.utc).isoformat()
        applied_at = now_iso if status_enum == ApplicationStatus.APPLIED else None

        with self._connection() as conn:
            opp_row = conn.execute(
                "SELECT author_name FROM opportunities WHERE id = ?",
                (opportunity_id,),
            ).fetchone()
            if not opp_row:
                return None

            existing_app = conn.execute(
                "SELECT id FROM applications WHERE opportunity_id = ? AND user_id = ?",
                (opportunity_id, user_id),
            ).fetchone()
            app_id = existing_app["id"] if existing_app else f"app_{user_id}_{opportunity_id}"
            contact_name = opp_row["author_name"]

            conn.execute(
                """
                INSERT INTO applications (
                    id, user_id, opportunity_id, status, notes, contact_name, applied_at, updated_at
                )
                VALUES (?, ?, ?, ?, COALESCE(?, ''), ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE applications.notes END,
                    applied_at = CASE WHEN excluded.status = 'applied' THEN COALESCE(applications.applied_at, excluded.applied_at) ELSE applications.applied_at END,
                    updated_at = excluded.updated_at
                """,
                (
                    app_id,
                    user_id,
                    opportunity_id,
                    status_enum.value,
                    notes or "",
                    contact_name,
                    applied_at,
                    now_iso,
                ),
            )
            conn.commit()
            return self.get_application(app_id, user_id)

    def delete_application_for_opportunity(self, opportunity_id: str, user_id: str) -> bool:
        with self._connection() as conn:
            cur = conn.execute(
                "DELETE FROM applications WHERE opportunity_id = ? AND user_id = ?",
                (opportunity_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def update_application_status(self, app_id: str, status: str, user_id: str) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            if status == ApplicationStatus.APPLIED.value:
                cur = conn.execute(
                    """
                    UPDATE applications
                    SET status = ?, updated_at = ?, applied_at = COALESCE(applied_at, ?)
                    WHERE id = ? AND user_id = ?
                    """,
                    (status, now_iso, now_iso, app_id, user_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE applications
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (status, now_iso, app_id, user_id),
                )
            conn.commit()
            return cur.rowcount > 0

    def update_application_notes(self, app_id: str, notes: str, user_id: str) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE applications
                SET notes = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (notes, now_iso, app_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_application(self, app_id: str, user_id: str) -> bool:
        with self._connection() as conn:
            cur = conn.execute(
                "DELETE FROM applications WHERE id = ? AND user_id = ?",
                (app_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    # -------------------------------------------------------------
    # Match & Pitch Evaluations
    # -------------------------------------------------------------
    def save_match_evaluation(self, match: MatchEvaluation) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO match_evaluations (
                    user_id, opportunity_id, score, verdict,
                    strengths, weaknesses, pitch, cover_letter
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, opportunity_id) DO UPDATE SET
                    score = excluded.score,
                    verdict = excluded.verdict,
                    strengths = excluded.strengths,
                    weaknesses = excluded.weaknesses,
                    pitch = excluded.pitch,
                    cover_letter = excluded.cover_letter
                """,
                (
                    match.user_id,
                    match.opportunity_id,
                    match.score,
                    match.verdict,
                    json.dumps(match.strengths),
                    json.dumps(match.weaknesses),
                    match.pitch,
                    match.cover_letter,
                ),
            )
            conn.commit()

    def get_match_evaluation(self, user_id: str, opportunity_id: str) -> Optional[MatchEvaluation]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM match_evaluations WHERE user_id = ? AND opportunity_id = ?",
                (user_id, opportunity_id),
            ).fetchone()
            if not row:
                return None
            return MatchEvaluation(
                user_id=row["user_id"],
                opportunity_id=row["opportunity_id"],
                score=row["score"],
                verdict=row["verdict"],
                strengths=json.loads(row["strengths"]) if row["strengths"] else [],
                weaknesses=json.loads(row["weaknesses"]) if row["weaknesses"] else [],
                pitch=row["pitch"] or "",
                cover_letter=row["cover_letter"] if "cover_letter" in row.keys() and row["cover_letter"] else "",
            )

    def get_user_matches(self, user_id: str) -> Dict[str, MatchEvaluation]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM match_evaluations WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            result = {}
            for r in rows:
                result[r["opportunity_id"]] = MatchEvaluation(
                    user_id=r["user_id"],
                    opportunity_id=r["opportunity_id"],
                    score=r["score"],
                    verdict=r["verdict"],
                    strengths=json.loads(r["strengths"]) if r["strengths"] else [],
                    weaknesses=json.loads(r["weaknesses"]) if r["weaknesses"] else [],
                    pitch=r["pitch"] or "",
                    cover_letter=r["cover_letter"] if "cover_letter" in r.keys() and r["cover_letter"] else "",
                )
            return result

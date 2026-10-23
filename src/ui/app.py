import json
import os
import random
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import psutil
import streamlit as st

from src.config import settings
from src.cognitive.local_llm_client import LocalLLMClient
from src.cognitive.schemas import VideoBlueprint, VideoScript
from src.ingestion import scraper
from src.rendering.media_engine import MediaPipeline
from src.ui.auto_publisher import AutoPublisher

try:
    from src.db.vector_memory import RAGSystem

    rag_engine = RAGSystem()
except Exception:
    rag_engine = None

os.environ["OMP_NUM_THREADS"] = settings.OMP_NUM_THREADS


class DatabaseManager:
    def __init__(self, db_path=settings.DB_PATH):
        self.db_path = db_path
        self._init_db()
        self._seed_default_project()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self):
        conn = self._get_connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                niche TEXT DEFAULT 'General',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER DEFAULT 1,
                topic TEXT,
                platform TEXT DEFAULT 'YouTube Shorts',
                views INTEGER DEFAULT 0,
                score INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                analysis_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS content_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER DEFAULT 1,
                blueprint_context TEXT,
                angle TEXT,
                hook TEXT,
                script TEXT,
                visual_cues TEXT,
                bg_video_path TEXT,
                status TEXT DEFAULT 'pending',
                score INTEGER DEFAULT 0,
                version INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS generated_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER DEFAULT 1,
                niche TEXT,
                platform TEXT,
                prompt TEXT,
                result TEXT,
                published BOOLEAN DEFAULT 0,
                media_path TEXT,
                vtt_path TEXT,
                status TEXT DEFAULT 'queued',
                export_attempts INTEGER DEFAULT 0,
                published_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        for migration in [
            "ALTER TABLE trends ADD COLUMN project_id INTEGER DEFAULT 1",
            "ALTER TABLE trends ADD COLUMN platform TEXT DEFAULT 'YouTube Shorts'",
            "ALTER TABLE trends ADD COLUMN score INTEGER DEFAULT 0",
            "ALTER TABLE trends ADD COLUMN status TEXT DEFAULT 'active'",
            "ALTER TABLE content_proposals ADD COLUMN project_id INTEGER DEFAULT 1",
            "ALTER TABLE content_proposals ADD COLUMN score INTEGER DEFAULT 0",
            "ALTER TABLE content_proposals ADD COLUMN version INTEGER DEFAULT 1",
            "ALTER TABLE generated_posts ADD COLUMN project_id INTEGER DEFAULT 1",
            "ALTER TABLE generated_posts ADD COLUMN status TEXT DEFAULT 'queued'",
            "ALTER TABLE generated_posts ADD COLUMN export_attempts INTEGER DEFAULT 0",
            "ALTER TABLE generated_posts ADD COLUMN published_at TEXT",
        ]:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass

        conn.commit()
        conn.close()

    def _seed_default_project(self):
        conn = self._get_connection()
        has_project = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        if has_project == 0:
            conn.execute(
                "INSERT INTO projects (name, niche) VALUES (?, ?)",
                ("Default Workspace", "General"),
            )
        conn.commit()
        conn.close()

    def create_project(self, name: str, niche: str = "General") -> int:
        if not name or not name.strip():
            return self.get_default_project_id()
        conn = self._get_connection()
        cursor = conn.execute(
            "INSERT INTO projects (name, niche) VALUES (?, ?)",
            (name.strip(), niche.strip() or "General"),
        )
        project_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return project_id

    def get_default_project_id(self):
        conn = self._get_connection()
        row = conn.execute("SELECT id FROM projects ORDER BY id ASC LIMIT 1").fetchone()
        conn.close()
        return row["id"] if row else 1

    def get_projects(self):
        conn = self._get_connection()
        rows = conn.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_project(self, project_id):
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def save_trend(self, project_id, topic, views, platform="YouTube Shorts"):
        topic = (topic or "").strip()
        if not topic:
            return None

        score = self._compute_trend_score(views, platform)
        conn = self._get_connection()
        existing = conn.execute(
            "SELECT id FROM trends WHERE project_id = ? AND LOWER(topic) = LOWER(?)",
            (project_id, topic),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE trends SET views = ?, platform = ?, score = ?, status = 'active' WHERE id = ?",
                (views, platform, score, existing["id"]),
            )
            row_id = existing["id"]
        else:
            row = conn.execute(
                "INSERT INTO trends (project_id, topic, views, platform, score, status) VALUES (?, ?, ?, ?, ?, 'active')",
                (project_id, topic, views, platform, score),
            )
            row_id = row.lastrowid
        conn.commit()
        conn.close()
        return row_id

    def _compute_trend_score(self, views, platform):
        base = max(0, int(views or 0))
        platform_boost = {
            "YouTube Shorts": 1.0,
            "TikTok": 1.15,
            "Instagram Reels": 1.1,
            "LinkedIn": 0.9,
        }.get(platform, 1.0)
        return int(base * platform_boost / 1000)

    def get_trends(self, project_id, query: str = "", platform: str = "all", include_archived: bool = False):
        conn = self._get_connection()
        sql = "SELECT * FROM trends WHERE project_id = ?"
        params = [project_id]
        if query:
            sql += " AND LOWER(topic) LIKE ?"
            params.append(f"%{query.lower()}%")
        if platform and platform != "all":
            sql += " AND platform = ?"
            params.append(platform)
        if not include_archived:
            sql += " AND status != 'archived'"
        sql += " ORDER BY score DESC, views DESC, id DESC LIMIT 100"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_project_summary(self, project_id):
        conn = self._get_connection()
        row = {
            "total_trends": conn.execute(
                "SELECT COUNT(*) FROM trends WHERE project_id = ? AND status != 'archived'",
                (project_id,),
            ).fetchone()[0],
            "pending": conn.execute(
                "SELECT COUNT(*) FROM content_proposals WHERE project_id = ? AND status = 'pending'",
                (project_id,),
            ).fetchone()[0],
            "approved": conn.execute(
                "SELECT COUNT(*) FROM content_proposals WHERE project_id = ? AND status = 'approved'",
                (project_id,),
            ).fetchone()[0],
            "generated": conn.execute(
                "SELECT COUNT(*) FROM generated_posts WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0],
            "queued": conn.execute(
                "SELECT COUNT(*) FROM generated_posts WHERE project_id = ? AND status = 'queued'",
                (project_id,),
            ).fetchone()[0],
            "avg_views": conn.execute(
                "SELECT COALESCE(AVG(views), 0) FROM trends WHERE project_id = ? AND status != 'archived'",
                (project_id,),
            ).fetchone()[0],
            "max_views": conn.execute(
                "SELECT COALESCE(MAX(views), 0) FROM trends WHERE project_id = ? AND status != 'archived'",
                (project_id,),
            ).fetchone()[0],
        }
        conn.close()
        return row

    def save_proposal(self, project_id, blueprint_context, angle, hook, script, visual_cues, bg_video_path=""):
        conn = self._get_connection()
        best_version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM content_proposals WHERE project_id = ? AND blueprint_context = ?",
            (project_id, blueprint_context),
        ).fetchone()[0]
        next_version = int(best_version) + 1 if best_version else 1
        result = conn.execute(
            """
            INSERT INTO content_proposals
            (project_id, blueprint_context, angle, hook, script, visual_cues, bg_video_path, status, score, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                project_id,
                blueprint_context,
                angle,
                hook,
                script,
                visual_cues,
                bg_video_path,
                self._score_script(script),
                next_version,
            ),
        )
        conn.commit()
        conn.close()
        return result.lastrowid

    def _score_script(self, script):
        if not script:
            return 0
        words = len(str(script).split())
        return min(100, max(10, words // 2))

    def get_proposals(self, project_id, status=None):
        conn = self._get_connection()
        sql = "SELECT * FROM content_proposals WHERE project_id = ?"
        params = [project_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_proposal_status(self, proposal_id, status):
        conn = self._get_connection()
        conn.execute(
            "UPDATE content_proposals SET status = ? WHERE id = ?",
            (status, proposal_id),
        )
        conn.commit()
        conn.close()

    def add_post(self, project_id, niche, platform, prompt, result, media_path=None, status="queued"):
        conn = self._get_connection()
        row = conn.execute(
            """
            INSERT INTO generated_posts (project_id, niche, platform, prompt, result, media_path, status, published, export_attempts)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)
            """,
            (project_id, niche, platform, prompt, result, media_path, status),
        )
        conn.commit()
        conn.close()
        return row.lastrowid

    def get_posts(self, project_id, status=None):
        conn = self._get_connection()
        sql = "SELECT * FROM generated_posts WHERE project_id = ?"
        params = [project_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT 50"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_post_status(self, post_id, status, successful=False):
        conn = self._get_connection()
        if successful:
            conn.execute(
                "UPDATE generated_posts SET status = ?, published = 1, published_at = ?, export_attempts = export_attempts + 1 WHERE id = ?",
                (status, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), post_id),
            )
        else:
            conn.execute(
                "UPDATE generated_posts SET status = ?, export_attempts = export_attempts + 1 WHERE id = ?",
                (status, post_id),
            )
        conn.commit()
        conn.close()

    def archive_trend(self, trend_id):
        conn = self._get_connection()
        conn.execute("UPDATE trends SET status = 'archived' WHERE id = ?", (trend_id,))
        conn.commit()
        conn.close()

    def export_backup(self, project_id, output_dir: str):
        os.makedirs(output_dir, exist_ok=True)
        tables = {
            "projects": "SELECT * FROM projects WHERE id = ?",
            "trends": "SELECT * FROM trends WHERE project_id = ?",
            "content_proposals": "SELECT * FROM content_proposals WHERE project_id = ?",
            "generated_posts": "SELECT * FROM generated_posts WHERE project_id = ?",
        }
        backup_path = Path(output_dir) / f"aotcse_backup_{project_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        backup_path.mkdir(exist_ok=True)
        for name, query in tables.items():
            rows = self._get_connection().execute(query, (project_id,)).fetchall()
            data = [dict(r) for r in rows]
            with open(backup_path / f"{name}.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        self._get_connection().close()
        return str(backup_path)

    def get_best_publish_time(self, project_id):
        conn = self._get_connection()
        total = conn.execute(
            "SELECT COUNT(*) FROM generated_posts WHERE project_id = ? AND published = 1",
            (project_id,),
        ).fetchone()[0]
        conn.close()
        return "09:00–11:00" if total % 2 == 0 else "18:00–21:00"


def compute_trend_score_from_views(views, platform):
    if not views:
        return 0
    platform_bonus = {"YouTube Shorts": 1.0, "TikTok": 1.15, "Instagram Reels": 1.1, "LinkedIn": 0.9}.get(platform, 1.0)
    return int(int(views) * platform_bonus / 1000)


st.set_page_config(page_title="AOTCSE SaaS Studio", page_icon="🚀", layout="wide")

db = DatabaseManager()
llm = LocalLLMClient()
media = MediaPipeline()
publisher = AutoPublisher()

if "project_id" not in st.session_state:
    st.session_state.project_id = db.get_default_project_id()

st.markdown(
    """
    <style>
    :root {
        --bg: #081421;
        --panel: rgba(15,23,42,0.78);
        --panel-strong: rgba(17,24,39,0.9);
        --muted: #94a3b8;
        --text: #e2e8f0;
        --primary: #60a5fa;
        --secondary: #a78bfa;
        --success: #34d399;
        --warning: #fbbf24;
        --danger: #f87171;
        --border: rgba(148,163,184,0.12);
    }
    html, body, [data-testid="stAppViewContainer"] { background: linear-gradient(135deg, #07131d 0%, #0d1d2a 35%, #101827 100%); color: var(--text); }
    .main .block-container { padding-top: 2rem; padding-bottom: 3rem; }
    [data-testid="stSidebar"] { background: rgba(8, 15, 24, 0.96); border-right: 1px solid var(--border); }
    .hero { background: linear-gradient(135deg, rgba(96,165,250,0.12), rgba(167,139,250,0.14)); border:1px solid var(--border); border-radius:20px; padding:1.3rem 1.5rem; box-shadow: 0 16px 28px rgba(15,23,42,0.24); }
    .soft-card { background: rgba(15,23,42,0.72); border:1px solid var(--border); border-radius:18px; padding:1rem 1.1rem; }
    .kpi { background: linear-gradient(180deg, rgba(15,23,42,0.80), rgba(17,24,39,0.84)); border:1px solid var(--border); border-radius:18px; padding:1rem 1.1rem; min-height:120px; }
    .badge { display: inline-block; padding:0.28rem 0.75rem; border-radius:999px; font-size:0.75rem; font-weight:700; border:1px solid rgba(255,255,255,0.08); }
    .stTabs [role="tablist"] { background: rgba(15,23,42,0.36); border:1px solid var(--border); border-radius:14px; padding:0.25rem; }
    .stTabs [role="tab"] { color: #cbd5e1; font-weight: 600; }
    .stTabs [role="tab"][aria-selected="true"] { background: linear-gradient(180deg, rgba(96,165,250,0.18), rgba(167,139,250,0.12)); border-radius:10px; color:#f8fafc; }
    .stButton > button { border-radius: 12px; background: linear-gradient(180deg, rgba(96,165,250,0.20), rgba(96,165,250,0.08)); color: #e2e8f0; border: 1px solid rgba(96,165,250,0.30); }
    .stButton > button:hover { border-color: rgba(96,165,250,0.55); }
    .stDataFrame { border:1px solid rgba(148,163,184,0.1); border-radius: 14px; overflow: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


def badge_html(label, tint="primary"):
    colors = {
        "primary": ("rgba(96,165,250,0.12)", "#dbeafe"),
        "success": ("rgba(52,211,153,0.12)", "#d1fae5"),
        "warning": ("rgba(251,191,36,0.12)", "#fef3c7"),
        "purple": ("rgba(167,139,250,0.12)", "#ede9fe"),
        "danger": ("rgba(248,113,113,0.12)", "#fee2e2"),
    }
    bg, fg = colors.get(tint, colors["primary"])
    return f"<span class='badge' style='background:{bg}; color:{fg};'>{label}</span>"


def render_kpi(title, value, subtitle, icon="📊", accent="#60a5fa"):
    st.markdown(
        f"""
        <div class='kpi'>
            <div style='color:#94a3b8; font-size:0.76rem; text-transform:uppercase; letter-spacing:0.08em; font-weight:700;'>{icon} {title}</div>
            <div style='font-size:2rem; font-weight:800; margin-top:0.5rem; color:{accent};'>{value}</div>
            <div style='color:#cbd5e1; font-size:0.82rem; margin-top:0.45rem;'>{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


project = db.get_project(st.session_state.project_id) or db.get_project(db.get_default_project_id())
summary = db.get_project_summary(st.session_state.project_id)

st.markdown(
    f"""
    <div class='hero'>
        <div style='font-size:0.74rem; letter-spacing:0.14em; text-transform:uppercase; color:#93c5fd; margin-bottom:0.3rem;'>AOTCSE SaaS Studio</div>
        <h1 style='margin:0; color:#f8fafc;'>Autonomous Content Operations</h1>
        <div style='margin-top:0.7rem; color:#cbd5e1;'>Workspace: <strong>{project['name'] if project else 'Default'}</strong> | Niche: <strong>{project['niche'] if project else 'General'}</strong></div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.title("🎯 Workspace")
    projects = db.get_projects()
    project_names = {p["id"]: p["name"] for p in projects}
    selected_project = st.selectbox(
        "Select project",
        options=[p["id"] for p in projects],
        format_func=lambda pid: project_names.get(pid, "Unknown"),
        index=[p["id"] for p in projects].index(st.session_state.project_id) if st.session_state.project_id in [p["id"] for p in projects] else 0,
    )
    st.session_state.project_id = selected_project
    project = db.get_project(st.session_state.project_id) or db.get_project(db.get_default_project_id())

    with st.form("new_project_form"):
        st.text_input("Project name", key="new_project_name", value="")
        st.text_input("Niche", key="new_project_niche", value="General")
        submitted = st.form_submit_button("Create project")
        if submitted:
            proj = st.session_state.get("new_project_name", "").strip()
            niche = st.session_state.get("new_project_niche", "General").strip() or "General"
            if proj:
                new_id = db.create_project(proj, niche)
                st.session_state.project_id = new_id
                st.rerun()

    st.markdown("---")
    st.subheader("Live status")
    ollama_ok = llm.check_health()
    st.write(f"🧠 Ollama: {'✅ Online' if ollama_ok else '⚠️ Offline'}")
    st.write(f"📚 RAG: {'✅ Active' if rag_engine else '⚠️ Not detected'}")
    st.write(f"📦 Queued: {summary.get('queued', 0)}")
    st.write(f"💡 Pending approvals: {summary.get('pending', 0)}")

    if st.button("Refresh data", use_container_width=True):
        st.rerun()

kpi_cols = st.columns(4)
with kpi_cols[0]:
    render_kpi("Trends", str(summary.get("total_trends", 0)), "Tracked signals", "📈", "#60a5fa")
with kpi_cols[1]:
    render_kpi("Avg views", f"{int(summary.get('avg_views', 0)):,}", "Per trend", "📊", "#a78bfa")
with kpi_cols[2]:
    render_kpi("Approvals", str(summary.get("approved", 0)), "Validated scripts", "✅", "#34d399")
with kpi_cols[3]:
    render_kpi("Queue", str(summary.get("queued", 0)), "Pending publish", "📤", "#fbbf24")

tabs = st.tabs([
    "📊 Overview",
    "📈 Trends",
    "💡 Content Studio",
    "📦 Queue",
    "🧪 Ops Monitor",
    "🗃️ Data & Integrations",
])

with tabs[0]:
    st.subheader("Overview")
    st.markdown(
        "<div class='soft-card'><p style='margin:0; color:#94a3b8;'>Multi-project content operations with trend capture, AI script generation, approval workflow, publishing and export automation.</p></div>",
        unsafe_allow_html=True,
    )
    overview = db.get_project_summary(st.session_state.project_id)
    c1, c2, c3 = st.columns(3)
    with c1:
        render_kpi("Generated posts", str(overview.get("generated", 0)), "All outputs", "🎬", "#60a5fa")
    with c2:
        render_kpi("Best publish window", db.get_best_publish_time(st.session_state.project_id), "Recommended schedule", "🕒", "#a78bfa")
    with c3:
        render_kpi("Peak signal", f"{int(overview.get('max_views', 0)):,}", "Highest views tracked", "🔥", "#fbbf24")

    st.divider()
    st.markdown("### Workflow status")
    status_cols = st.columns(4)
    with status_cols[0]:
        st.markdown(f"{badge_html('Drafts', 'primary')} {overview.get('pending', 0)}")
    with status_cols[1]:
        st.markdown(f"{badge_html('Approved', 'success')} {overview.get('approved', 0)}")
    with status_cols[2]:
        st.markdown(f"{badge_html('Queued', 'warning')} {overview.get('queued', 0)}")
    with status_cols[3]:
        st.markdown(f"{badge_html('Generated', 'purple')} {overview.get('generated', 0)}")

with tabs[1]:
    st.subheader("Trend intelligence")
    query = st.text_input("Search trends", value="")
    platform_filter = st.selectbox("Platform", ["all", "YouTube Shorts", "TikTok", "Instagram Reels", "LinkedIn"])
    include_archived = st.checkbox("Include archived")
    trends =*


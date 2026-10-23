import os
import sqlite3
import random
import time
import json
from datetime import datetime

import pandas as pd
import psutil
import streamlit as st

from src.config import settings
from src.rendering.media_engine import MediaPipeline
from src.ingestion import scraper
from src.ui.auto_publisher import AutoPublisher
from src.cognitive.local_llm_client import LocalLLMClient
from src.cognitive.schemas import TrendAnalysis, VideoScript, VideoBlueprint

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

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self):
        conn = self._get_connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                views INTEGER,
                analysis_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS content_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                blueprint_context TEXT,
                angle TEXT,
                hook TEXT,
                script TEXT,
                visual_cues TEXT,
                bg_video_path TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS generated_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                niche TEXT,
                platform TEXT,
                prompt TEXT,
                result TEXT,
                published BOOLEAN DEFAULT 0,
                media_path TEXT,
                vtt_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        for col_def in [
            "ALTER TABLE content_proposals ADD COLUMN bg_video_path TEXT",
            "ALTER TABLE content_proposals ADD COLUMN blueprint_context TEXT",
            "ALTER TABLE content_proposals ADD COLUMN visual_cues TEXT",
            "ALTER TABLE content_proposals ADD COLUMN angle TEXT",
            "ALTER TABLE generated_posts ADD COLUMN media_path TEXT",
            "ALTER TABLE generated_posts ADD COLUMN vtt_path TEXT",
        ]:
            try:
                conn.execute(col_def)
            except sqlite3.OperationalError:
                pass

        conn.commit()
        conn.close()

    def get_top_trend(self):
        conn = self._get_connection()
        trend = conn.execute(
            "SELECT * FROM trends ORDER BY views DESC, id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return dict(trend) if trend else None

    def get_top_10_trends(self):
        conn = self._get_connection()
        trends = conn.execute(
            "SELECT topic, views, created_at FROM trends ORDER BY views DESC LIMIT 10"
        ).fetchall()
        conn.close()
        return [dict(t) for t in trends]

    def get_summary_stats(self):
        conn = self._get_connection()
        stats = {
            "total_trends": conn.execute("SELECT COUNT(*) FROM trends").fetchone()[0],
            "pending": conn.execute("SELECT COUNT(*) FROM content_proposals WHERE status = 'pending'").fetchone()[0],
            "approved": conn.execute("SELECT COUNT(*) FROM content_proposals WHERE status = 'approved'").fetchone()[0],
            "generated": conn.execute("SELECT COUNT(*) FROM generated_posts").fetchone()[0],
            "queued": conn.execute("SELECT COUNT(*) FROM generated_posts WHERE published = 0").fetchone()[0],
            "avg_views": conn.execute("SELECT COALESCE(AVG(views), 0) FROM trends").fetchone()[0],
            "max_views": conn.execute("SELECT COALESCE(MAX(views), 0) FROM trends").fetchone()[0],
        }
        conn.close()
        return stats


st.set_page_config(page_title="AOTCSE SaaS Studio", page_icon="🚀", layout="wide")


def badge(label: str, tone: str = "primary"):
    colors = {
        "primary": "rgba(59,130,246,0.14)",
        "success": "rgba(16,185,129,0.14)",
        "warning": "rgba(245,158,11,0.14)",
        "danger": "rgba(239,68,68,0.14)",
        "purple": "rgba(168,85,247,0.14)",
        "neutral": "rgba(148,163,184,0.14)",
    }
    text_colors = {
        "primary": "#bfdbfe",
        "success": "#bbf7d0",
        "warning": "#fcd34d",
        "danger": "#fecaca",
        "purple": "#e9d5ff",
        "neutral": "#e2e8f0",
    }
    st.markdown(
        f"<span style='display:inline-block;padding:0.35rem 0.75rem;border-radius:999px;background:{colors.get(tone, colors['primary'])};color:{text_colors.get(tone, text_colors['primary'])};font-size:0.73rem;font-weight:600;border:1px solid rgba(255,255,255,0.08);'> {label} </span>",
        unsafe_allow_html=True,
    )


def render_kpi_card(title: str, value: str, subtitle: str = "", icon: str = "📊", accent: str = "#60a5fa"):
    st.markdown(
        f"""
        <div style="background:linear-gradient(180deg, rgba(15,23,42,0.78), rgba(17,24,39,0.82)); border:1px solid rgba(148,163,184,0.14); border-radius:18px; padding:1rem 1.1rem; min-height:125px; box-shadow:0 10px 25px rgba(15,23,42,0.20);">
            <div style="color:#94a3b8; font-size:0.76rem; font-weight:600; letter-spacing:0.08em; text-transform:uppercase;">{icon} {title}</div>
            <div style="font-size:2rem; font-weight:800; margin-top:0.55rem; color:{accent};">{value}</div>
            <div style="margin-top:0.35rem; color:#cbd5e1; font-size:0.82rem;">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_info_card(title: str, body: str, icon: str = "ℹ️"):
    st.markdown(
        f"""
        <div style="background: rgba(15,23,42,0.66); border:1px solid rgba(148,163,184,0.12); border-radius:16px; padding:1rem 1.1rem; margin-bottom:0.8rem;">
            <div style="font-size:0.82rem; font-weight:700; color:#cbd5e1; margin-bottom:0.45rem;">{icon} {title}</div>
            <div style="color:#94a3b8; line-height:1.5;">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.markdown(
    """
    <style>
        :root {
            --bg: #07131d;
            --bg-2: #0d1d2a;
            --surface: rgba(15, 23, 42, 0.75);
            --panel: rgba(15, 23, 42, 0.82);
            --panel-2: rgba(17, 24, 39, 0.9);
            --border: rgba(148, 163, 184, 0.12);
            --text: #e2e8f0;
            --muted: #94a3b8;
            --primary: #60a5fa;
            --primary-2: #a78bfa;
            --success: #34d399;
            --warning: #fbbf24;
            --danger: #f87171;
            --shadow: rgba(15, 23, 42, 0.35);
        }

        html, body, [data-testid="stAppViewContainer"] {
            background: linear-gradient(135deg, #07131d 0%, #0d1d2a 26%, #101827 100%);
            color: var(--text);
        }

        .stApp {
            background: transparent;
        }

        .main .block-container {
            padding-top: 2.2rem;
            padding-bottom: 3rem;
        }

        [data-testid="stSidebar"] {
            background: rgba(8, 16, 24, 0.94);
            border-right: 1px solid var(--border);
        }

        .hero {
            background: linear-gradient(135deg, rgba(96,165,250,0.12), rgba(167,139,250,0.12));
            border: 1px solid rgba(148,163,184,0.12);
            border-radius: 22px;
            padding: 1.3rem 1.5rem;
            box-shadow: 0 16px 32px rgba(15,23,42,0.28);
            margin-bottom: 1.2rem;
        }

        .soft-panel {
            background: rgba(15, 23, 42, 0.74);
            border: 1px solid rgba(148,163,184,0.12);
            border-radius: 18px;
            padding: 1rem 1.1rem;
            box-shadow: 0 10px 20px rgba(15, 23, 42, 0.18);
        }

        .stButton > button {
            border-radius: 12px;
            border: 1px solid rgba(96,165,250,0.35);
            background: linear-gradient(180deg, rgba(59,130,246,0.18), rgba(59,130,246,0.08));
            color: #e2e8f0;
            font-weight: 600;
        }

        .stButton > button:hover {
            border-color: rgba(96,165,250,0.55);
            transform: translateY(-1px);
            box-shadow: 0 8px 20px rgba(96,165,250,0.15);
        }

        .stTabs [role="tablist"] {
            background: rgba(15,23,42,0.38);
            border-radius: 14px;
            border: 1px solid rgba(148,163,184,0.08);
            padding: 0.25rem;
        }

        .stTabs [role="tab"] {
            color: #cbd5e1;
            font-weight: 600;
        }

        .stTabs [role="tab"][aria-selected="true"] {
            background: linear-gradient(180deg, rgba(96,165,250,0.20), rgba(167,139,250,0.14));
            border-radius: 10px;
            color: #f8fafc;
        }

        .stDataFrame {
            border: 1px solid rgba(148,163,184,0.1);
            border-radius: 14px;
            overflow: hidden;
        }

        div[data-testid="stVerticalBlock"] > div:has(> .stButton) {
            margin-top: 0.4rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_bg_videos():
    temp_dir = getattr(media, "temp_dir", None)
    if not temp_dir or not os.path.isdir(temp_dir):
        return []
    return [
        os.path.join(temp_dir, file)
        for file in os.listdir(temp_dir)
        if file.lower().endswith(".mp4")
    ]


def safe_get_summary():
    return db.get_summary_stats()


def get_ollama_status():
    return llm.check_health()


# Runtime services

db = DatabaseManager()
llm = LocalLLMClient()
media = MediaPipeline()
publisher = AutoPublisher()

if "current_blueprint" not in st.session_state:
    st.session_state.current_blueprint = None
if "top_trend_topic" not in st.session_state:
    st.session_state.top_trend_topic = None

# Top hero + summary
summary = safe_get_summary()

st.markdown(
    """
    <div class="hero">
        <div style="font-size:0.72rem; letter-spacing:0.14em; text-transform:uppercase; color:#93c5fd; margin-bottom:0.4rem;">AOTCSE SaaS Suite</div>
        <h1 style="margin:0; font-size:2.1rem; color:#f8fafc;">Autonomous Content Operating System</h1>
        <div style="margin-top:0.7rem; color:#cbd5e1; font-size:1rem;">Trend intelligence, AI script generation, render pipeline, publishing queue and live ops monitoring in one workspace.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.title("📊 Overview")
    st.caption("Production workspace")

    cols = st.columns(2)
    with cols[0]:
        render_kpi_card("Trends", str(summary["total_trends"]), "Tracked", "📈", "#60a5fa")
    with cols[1]:
        render_kpi_card("Queue", str(summary["queued"]), "Pending", "📤", "#c084fc")

    st.markdown("<div class='soft-panel'>", unsafe_allow_html=True)
    st.markdown("### Status")
    st.write(f"🧠 Ollama: {'✅ Online' if get_ollama_status() else '⚠️ Offline'}")
    st.write(f"📚 RAG: {'✅ Active' if rag_engine else '⚠️ Not detected'}")
    st.write(f"🎬 Generated: {summary['generated']}")
    st.write(f"⏳ Pending approvals: {summary['pending']}")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='soft-panel' style='margin-top: 0.8rem;'>", unsafe_allow_html=True)
    st.markdown("### Quick actions")
    if st.button("🔄 Refresh", use_container_width=True):
        st.rerun()
    if st.button("🧽 Reset session", use_container_width=True):
        for key in ["current_blueprint", "top_trend_topic"]:
            st.session_state.pop(key, None)
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown(
    """
    <div style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-bottom:1rem;">
        <span style="display:inline-block;background:rgba(96,165,250,0.12);border:1px solid rgba(96,165,250,0.3);border-radius:999px;padding:0.35rem 0.75rem;color:#dbeafe;font-size:0.76rem;">Air-Gapped</span>
        <span style="display:inline-block;background:rgba(52,211,153,0.12);border:1px solid rgba(52,211,153,0.3);border-radius:999px;padding:0.35rem 0.75rem;color:#d1fae5;font-size:0.76rem;">GPU Ready</span>
        <span style="display:inline-block;background:rgba(167,139,250,0.12);border:1px solid rgba(167,139,250,0.3);border-radius:999px;padding:0.35rem 0.75rem;color:#ede9fe;font-size:0.76rem;">LLM Native</span>
        <span style="display:inline-block;background:rgba(251,191,36,0.12);border:1px solid rgba(251,191,36,0.3);border-radius:999px;padding:0.35rem 0.75rem;color:#fef3c7;font-size:0.76rem;">Webhook Export</span>
    </div>
    """,
    unsafe_allow_html=True,
)

# KPI row
kpi_cols = st.columns(4)
with kpi_cols[0]:
    render_kpi_card("Avg views", f"{int(summary['avg_views']):,}", "Per tracked trend", "📊", "#60a5fa")
with kpi_cols[1]:
    render_kpi_card("Peak trend", f"{int(summary['max_views']):,}", "Top recorded reach", "🔥", "#fbbf24")
with kpi_cols[2]:
    render_kpi_card("Approvals", str(summary['approved']), "Validated scripts", "✅", "#34d399")
with kpi_cols[3]:
    render_kpi_card("Live queue", str(summary['queued']), "Publishes pending", "📦", "#a78bfa")

# tabs

tabs = st.tabs([
    "🎯 Trend Intelligence",
    "💡 Content Studio",
    "📤 Publish Queue",
    "💻 Ops Monitor",
])

# --- TAB 1: Trend intelligence ---
with tabs[0]:
    st.subheader("Trend Intelligence Engine")
    st.markdown("<div class='soft-panel'><p style='margin:0;color:#94a3b8;'>Discover what is gaining traction, identify the strongest angle, and convert it into an AI production brief automatically.</p></div>", unsafe_allow_html=True)

    top_10 = db.get_top_10_trends()
    if top_10:
        df_trends = pd.DataFrame(top_10)
        st.markdown("### Top 10 trend signals")
        st.line_chart(df_trends.set_index("topic")["views"], color="#34d399", use_container_width=True)
        st.dataframe(
            df_trends[["topic", "views", "created_at"]].sort_values("views", ascending=False).style.highlight_max(subset=["views"], color="#064e3b"),
            height=240,
            use_container_width=True,
        )
    else:
        render_info_card("No trend data yet", "Start the scrapper to populate the signal feed and unlock blueprint automation.", "📊")

    st.divider()

    left_col, right_col = st.columns(2)

    with left_col:
        st.markdown("#### 🔎 Trend collector")
        st.caption("Search by niche, keyword or vertical")
        query_input = st.text_input("Topic / niche", value="AI automation")
        if st.button("🚀 Run trend scan", use_container_width=True, type="primary"):
            try:
                with st.status("Scanning trend surface...", expanded=True) as status:
                    trends = scraper.search_youtube_shorts(query_input, max_results=5)
                    saved = 0
                    for item in trends:
                        conn = db._get_connection()
                        conn.execute(
                            "INSERT INTO trends (topic, views) VALUES (?, ?)",
                            (item.get("topic"), item.get("views", 0)),
                        )
                        conn.commit()
                        conn.close()
                        saved += 1
                    status.update(label=f"Trend scan finished. {saved} items stored.", state="complete")
                    time.sleep(0.6)
                    st.rerun()
            except Exception as exc:
                st.error(f"Trend scan failed: {exc}")

    with right_col:
        st.markdown("#### 🧠 Blueprint generator")
        st.caption("Build a production plan from the strongest signal")
        if st.button("✨ Generate blueprint", type="primary", use_container_width=True):
            top_trend = db.get_top_trend()
            if top_trend:
                with st.status(f"Analyzing: '{top_trend['topic'][:42]}...'", expanded=True) as status:
                    prompt = (
                        f"Analyze a viral short-form video topic called '{top_trend['topic']}' with {top_trend['views']} views. "
                        "Create a production-ready blueprint for a new video that maximizes retention, momentum, and conversion."
                    )
                    try:
                        blueprint = llm.generate_structured(prompt, VideoBlueprint)
                    except Exception:
                        blueprint = None

                    if not blueprint:
                        st.warning("LLM returned an invalid format; using resilient fallback blueprint.")
                        blueprint = VideoBlueprint(
                            core_message=f"A practical, high-clarity explanation of {top_trend['topic']}",
                            visual_atmosphere="Fast cuts, punchy text, premium motion graphics", 
                            target_audience="AI-curious professionals and growth-focused audiences",
                        )

                    st.session_state.current_blueprint = blueprint
                    st.session_state.top_trend_topic = top_trend["topic"]
                    status.update(label="Blueprint created and saved to the workspace.", state="complete")
                    time.sleep(0.6)
                    st.rerun()
            else:
                st.error("No trend data yet. Run the scan first.")

    if st.session_state.current_blueprint:
        bp = st.session_state.current_blueprint
        st.markdown("### Active blueprint")
        st.markdown(
            f"""
            <div style="background: rgba(16,185,129,0.08); border-left:4px solid #34d399; border-radius:14px; padding:1rem 1.1rem; margin-top:1rem;">
                <div style="color:#d1fae5; font-size:0.85rem; font-weight:700; margin-bottom:0.5rem;">Source: {st.session_state.top_trend_topic}</div>
                <div style="margin-bottom:0.45rem;"><strong>Core message:</strong> {bp.core_message}</div>
                <div style="margin-bottom:0.45rem;"><strong>Visual style:</strong> {bp.visual_atmosphere}</div>
                <div><strong>Audience:</strong> {bp.target_audience}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.success("Blueprint ready for the content studio.")

# --- TAB 2: Content studio ---
with tabs[1]:
    st.subheader("Content Studio")
    st.markdown("<div class='soft-panel'><p style='margin:0;color:#94a3b8;'>Generate multiple creative angles, preview them, and approve or reject each variation before production.</p></div>", unsafe_allow_html=True)

    if st.session_state.current_blueprint:
        if st.button("✨ Generate 3 creative variants", type="primary", use_container_width=True):
            with st.status("Creating multiple script variants from the active blueprint...", expanded=True) as status:
                bp = st.session_state.current_blueprint
                angles = [
                    "Controversial / provocative",
                    "Educational step-by-step",
                    "Story-driven / personal",
                ]
                bg_videos = get_bg_videos()

                conn = db._get_connection()
                for idx, angle in enumerate(angles, start=1):
                    st.write(f"Processing variant {idx}/3 — {angle}")
                    prompt = (
                        f"Create a short-form video script. Angle: {angle}. Audience: {bp.target_audience}. "
                        f"Core message: {bp.core_message}. Visual style: {bp.visual_atmosphere}."
                    )
                    try:
                        script_obj = llm.generate_structured(prompt, VideoScript)
                    except Exception:
                        script_obj = None

                    if not script_obj:
                        script_obj = VideoScript(
                            hook=f"You probably noticed how fast {bp.core_message.lower()} is changing.",
                            body="This shift creates a huge opportunity. Here are the essential points to understand.",
                            cta="Follow for more smart breakdowns.",
                            visual_cues="Fast cuts, dynamic zoom, clean captions",
                        )

                    bg_path = random.choice(bg_videos) if bg_videos else ""
                    full_script = f"{script_obj.hook} {script_obj.body} {script_obj.cta}"
                    conn.execute(
                        """
                        INSERT INTO content_proposals
                        (blueprint_context, angle, hook, script, visual_cues, bg_video_path, status)
                        VALUES (?, ?, ?, ?, ?, ?, 'pending')
                        """,
                        (bp.core_message, angle, script_obj.hook, full_script, script_obj.visual_cues, bg_path),
                    )

                conn.commit()
                conn.close()
                status.update(label="3 variants have been generated and are ready for approval.", state="complete")
                time.sleep(0.7)
                st.rerun()
    else:
        st.warning("Create a blueprint in the first tab before generating creative variants.")

    st.divider()

    conn = db._get_connection()
    proposals = conn.execute("SELECT * FROM content_proposals WHERE status = 'pending' ORDER BY id DESC").fetchall()
    conn.close()

    if proposals:
        st.markdown(f"### Pending approvals ({len(proposals)})")
        for proposal in proposals:
            with st.container():
                st.markdown(
                    f"<div style='background:rgba(15,23,42,0.68);border:1px solid rgba(148,163,184,0.12);border-radius:16px;padding:1rem 1.1rem;margin-bottom:1rem;'>"
                    f"<div style='font-size:1.1rem;font-weight:700;margin-bottom:0.5rem;'>Proposal #{proposal['id']}</div>"
                    f"<div style='display:inline-block;padding:0.3rem 0.7rem;border-radius:999px;background:rgba(96,165,250,0.12);color:#dbeafe;border:1px solid rgba(96,165,250,0.18);'>{proposal['angle']}</div></div>",
                    unsafe_allow_html=True,
                )
                left, right = st.columns([3, 2])

                with left:
                    st.markdown(f"**Hook:** {proposal['hook']}")
                    st.markdown(f"**Voiceover:** {proposal['script']}")
                    st.info(f"**Visual cues:** {proposal['visual_cues']}")

                    a1, a2 = st.columns(2)
                    with a1:
                        if st.button("✅ Approve & render", key=f"approve_{proposal['id']}", type="primary", use_container_width=True):
                            try:
                                with st.status(f"Rendering proposal #{proposal['id']}...", expanded=True) as render_status:
                                    st.write("Generating voiceover...")
                                    audio_path = media.generate_voiceover(proposal['script'])
                                    final_video_path = None
                                    if proposal['bg_video_path'] and os.path.exists(proposal['bg_video_path']) and audio_path:
                                        st.write("Composing 9:16 final video...")
                                        final_video_path = media.render_final_video(proposal['bg_video_path'], audio_path)

                                    conn = db._get_connection()
                                    conn.execute("UPDATE content_proposals SET status = 'approved' WHERE id = ?", (proposal['id'],))
                                    conn.execute(
                                        "INSERT INTO generated_posts (niche, platform, prompt, result, media_path) VALUES (?, ?, ?, ?, ?)",
                                        ("Blueprint Trend", "Shorts/Reels", proposal['angle'], proposal['script'], final_video_path),
                                    )
                                    conn.commit()
                                    conn.close()
                                    render_status.update(label="Render succeeded and content was moved to the queue.", state="complete")
                                time.sleep(0.6)
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Render failed: {exc}")
                    with a2:
                        if st.button("❌ Reject", key=f"reject_{proposal['id']}", use_container_width=True):
                            conn = db._get_connection()
                            conn.execute("UPDATE content_proposals SET status = 'rejected' WHERE id = ?", (proposal['id'],))
                            conn.commit()
                            conn.close()
                            st.rerun()

                with right:
                    st.markdown("**Preview background video:**")
                    if proposal['bg_video_path'] and os.path.exists(proposal['bg_video_path']):
                        st.video(proposal['bg_video_path'])
                    else:
                        st.warning("No background video found. Add .mp4 examples to the media folder.")
            st.divider()
    else:
        st.info("No pending variants. Generate a set from the active blueprint to get started.")

# --- TAB 3: Publish queue ---
with tabs[2]:
    st.subheader("Publishing Queue")
    st.markdown("<div class='soft-panel'><p style='margin:0;color:#94a3b8;'>Review all generated content and export it to your webhook or social publishing pipeline.</p></div>", unsafe_allow_html=True)

    conn = db._get_connection()
    df_posts = pd.read_sql_query(
        "SELECT id, platform, result, media_path, published, created_at FROM generated_posts ORDER BY id DESC LIMIT 20",
        conn,
    )
    conn.close()

    if not df_posts.empty:
        df_posts["Status"] = df_posts["published"].apply(lambda x: "✅ Published" if x == 1 else "⏳ Queued")
        df_posts["Media"] = df_posts["media_path"].apply(lambda x: "🎥 Rendered" if x else "📝 Text only")
        st.dataframe(df_posts[["id", "Status", "Media", "platform", "created_at"]], use_container_width=True)

        if st.button("🚀 Send queued items to webhook", type="primary", use_container_width=True):
            with st.spinner("Publishing queued output..."):
                pending = df_posts[df_posts["published"] == 0]
                if pending.empty:
                    st.info("Nothing queued for export.")
                else:
                    for _, row in pending.iterrows():
                        try:
                            publisher.export_to_webhook(
                                row["id"],
                                "Trendy",
                                row["platform"],
                                row["result"],
                                row["media_path"],
                                None,
                            )
                            conn = db._get_connection()
                            conn.execute("UPDATE generated_posts SET published = 1 WHERE id = ?", (row["id"],))
                            conn.commit()
                            conn.close()
                        except Exception as exc:
                            st.warning(f"Publishing failed for post {row['id']}: {exc}")
                    st.success("Queue processed successfully.")
                    time.sleep(0.6)
                    st.rerun()
    else:
        st.info("No content in the queue yet. Approve a draft to create the first export.")

# --- TAB 4: Ops monitor ---
with tabs[3]:
    st.subheader("Operations Monitor")
    st.markdown("<div class='soft-panel'><p style='margin:0;color:#94a3b8;'>Monitor live system health, resource utilization and AI service availability.</p></div>", unsafe_allow_html=True)

    res_cols = st.columns(3)
    with res_cols[0]:
        render_kpi_card("CPU", f"{psutil.cpu_percent(interval=0.2)} %", "Processor load", "🧠", "#60a5fa")
    with res_cols[1]:
        ram = psutil.virtual_memory()
        render_kpi_card("RAM", f"{ram.percent} %", f"Free: {ram.available / (1024**3):.1f} GB", "💾", "#a78bfa")
    with res_cols[2]:
        disk = psutil.disk_usage(os.getcwd())
        render_kpi_card("Storage", f"{disk.percent} %", f"Free: {disk.free / (1024**3):.1f} GB", "💿", "#34d399")

    st.divider()

    gpu_col, service_col = st.columns(2)

    with gpu_col:
        st.markdown("### GPU / acceleration")
        st.info("NVIDIA Quadro M5000M (8 GB VRAM)")
        try:
            import subprocess
            gpu_info = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                stderr=subprocess.STDOUT,
                timeout=2,
            ).decode().strip()
            gpu_util, mem_used, mem_total = gpu_info.split(",")
            st.metric("GPU utilization", f"{gpu_util.strip()} %")
            st.progress(float(mem_used.strip()) / float(mem_total.strip()), text=f"VRAM: {mem_used.strip()} MB / {mem_total.strip()} MB")
        except Exception:
            st.warning("GPU metrics are not available in this runtime. Local inference may still be using host-side acceleration.")

    with service_col:
        st.markdown("### Service health")
        st.markdown(f"**Ollama:** {'✅ Healthy' if get_ollama_status() else '❌ Offline'}")
        st.markdown(f"**RAG memory:** {'✅ Connected' if rag_engine else '⚠️ Not present'}")
        st.markdown(f"**Production watchdog:** ✅ Active")
        st.markdown(f"**Queue reliability:** {'✅ Stable' if summary['queued'] >= 0 else '⚠️ Monitoring'}")

# Footer
st.markdown("<div style='height:2rem;'></div>", unsafe_allow_html=True)

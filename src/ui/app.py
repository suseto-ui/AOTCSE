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


st.set_page_config(page_title="AOTCSE Cockpit 🚀", page_icon="🚀", layout="wide")

st.markdown(
    """
    <style>
        :root {
            --bg: #06131e;
            --surface: #0d1b2a;
            --surface-soft: #122332;
            --panel: rgba(15, 23, 42, 0.78);
            --border: rgba(148, 163, 184, 0.18);
            --text: #e2e8f0;
            --muted: #94a3b8;
            --primary: #38bdf8;
            --primary-2: #8b5cf6;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #f87171;
        }

        html, body, [data-testid="stAppViewContainer"] {
            background: linear-gradient(135deg, #06131e 0%, #0b1f2f 35%, #132032 100%);
            color: var(--text);
        }

        .stApp {
            background: transparent;
        }

        .main .block-container {
            padding-top: 2rem;
            padding-bottom: 3rem;
        }

        .hero {
            background: linear-gradient(120deg, rgba(56, 189, 248, 0.15), rgba(139, 92, 246, 0.10));
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 1.4rem 1.5rem;
            margin-bottom: 1.25rem;
        }

        .section-card {
            background: rgba(15, 23, 42, 0.72);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 1.1rem 1.2rem;
            box-shadow: 0 10px 25px rgba(15, 23, 42, 0.25);
            margin-bottom: 1rem;
        }

        .metric-card {
            background: linear-gradient(180deg, rgba(15, 23, 42, 0.82), rgba(17, 24, 39, 0.82));
            border: 1px solid rgba(148, 163, 184, 0.15);
            border-radius: 16px;
            padding: 0.8rem 1rem;
            min-height: 120px;
        }

        .chip {
            display: inline-block;
            background: rgba(56, 189, 248, 0.12);
            color: #c7f0ff;
            border: 1px solid rgba(56, 189, 248, 0.35);
            border-radius: 999px;
            padding: 0.2rem 0.7rem;
            font-size: 0.75rem;
            margin-right: 0.5rem;
            margin-bottom: 0.5rem;
        }

        .blueprint-box {
            border-left: 4px solid var(--success);
            background: rgba(16, 185, 129, 0.09);
            padding: 1rem 1.1rem;
            border-radius: 12px;
            margin: 0.8rem 0;
        }

        .step-desc {
            color: var(--muted);
            font-size: 0.96rem;
            margin-bottom: 0.9rem;
        }

        [data-testid="stSidebar"] {
            background: rgba(7, 17, 27, 0.92);
            border-right: 1px solid rgba(148, 163, 184, 0.12);
        }

        .stDataFrame {
            border-radius: 14px;
            overflow: hidden;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_metric_card(title: str, value: str, delta: str = "", icon: str = "📊"):
    st.markdown(
        f"""
        <div class="metric-card">
            <div style="font-size:0.8rem; color:#94a3b8;">{icon} {title}</div>
            <div style="font-size:1.8rem; font-weight:700; margin-top:0.4rem;">{value}</div>
            <div style="font-size:0.75rem; color:#cbd5e1; margin-top:0.3rem;">{delta}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_empty_state(title: str, body: str, icon: str = "💡"):
    st.markdown(
        f"""
        <div class="section-card">
            <div style="font-size:1.2rem; font-weight:600; margin-bottom:0.35rem;">{icon} {title}</div>
            <div style="color:#94a3b8;">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def safe_get_top_counts():
    conn = db._get_connection()
    counts = {
        "trends": conn.execute("SELECT COUNT(*) FROM trends").fetchone()[0],
        "pending": conn.execute("SELECT COUNT(*) FROM content_proposals WHERE status = 'pending'").fetchone()[0],
        "generated": conn.execute("SELECT COUNT(*) FROM generated_posts").fetchone()[0],
        "queued": conn.execute("SELECT COUNT(*) FROM generated_posts WHERE published = 0").fetchone()[0],
    }
    conn.close()
    return counts


# Database and services

db = DatabaseManager()
llm = LocalLLMClient()
media = MediaPipeline()
publisher = AutoPublisher()

if "current_blueprint" not in st.session_state:
    st.session_state.current_blueprint = None
if "top_trend_topic" not in st.session_state:
    st.session_state.top_trend_topic = None

st.markdown(
    """
    <div class="hero">
        <div style="font-size:0.76rem; letter-spacing:0.12em; text-transform:uppercase; color:#7dd3fc; margin-bottom:0.5rem;">AOTCSE</div>
        <h1 style="margin:0;">Autonomní výrobní linka obsahu</h1>
        <div style="margin-top:0.55rem; color:#cbd5e1;">Lokální AI agentura v režimu Air-Gapped s produkčním workflow pro trend discovery, scriptwriting, rendering a export.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.title("🎛️ Přehled")
    counts = safe_get_top_counts()
    render_metric_card("Trend data", str(counts["trends"]), "Uložených témat", "📈")
    render_metric_card("Návrhy", str(counts["pending"]), "Čeká na schválení", "💡")
    render_metric_card("Výstupy", str(counts["generated"]), "Vygenerovaných postů", "🎬")
    render_metric_card("Fronta", str(counts["queued"]), "Neodeslané publikace", "📤")

    st.markdown("<div class='section-card'>", unsafe_allow_html=True)
    st.caption("Systémové statusy")
    ollama_status = "✅ Online" if llm.check_health() else "⚠️ Offline"
    st.write(f"🧠 Ollama: {ollama_status}")
    rag_status = "✅ Připojeno" if rag_engine else "⚠️ Neaktivní"
    st.write(f"📚 RAG: {rag_status}")
    st.write("🛡️ Watchdog: aktivní")
    st.markdown("</div>", unsafe_allow_html=True)

    if st.button("🔄 Obnovit panel", use_container_width=True):
        st.rerun()

st.markdown(
    """
    <div class="chip">Air-Gapped</div>
    <div class="chip">GPU Ready</div>
    <div class="chip">Local LLM</div>
    <div class="chip">Webhook Export</div>
    """,
    unsafe_allow_html=True,
)

# Tabs

tabs = st.tabs([
    "🎯 1. Objev & Blueprint",
    "💡 2. AI Produkce (Náhledy)",
    "📤 3. Publikační fronta",
    "💻 4. HW Monitor",
])

# --- TAB 1: OBJEV A BLUEPRINT ---
with tabs[0]:
    st.subheader("🕵️‍♂️ Krok 1: Trend discovery a tvorba Blueprintu")
    st.markdown(
        "<p class='step-desc'>Sledujeme vývoj viralitních témat, vyhodnocujeme je podle zhlédnutí a z nejúspěšnějších trendů generujeme plán pro novou produkci.</p>",
        unsafe_allow_html=True,
    )

    st.markdown("### 📈 Top 10 témat")
    top_10 = db.get_top_10_trends()
    if top_10:
        df_trends = pd.DataFrame(top_10)
        st.line_chart(df_trends.set_index("topic")["views"], color="#10b981", use_container_width=True)
        st.dataframe(
            df_trends[["topic", "views", "created_at"]].style.highlight_max(subset=["views"], color="#064e3b"),
            height=220,
            use_container_width=True,
        )
    else:
        render_empty_state(
            "Žádné data",
            "Databáze zatím neobsahuje žádné trende. Spusťte sken níže a nechte systém shromáždit první signály.",
            "📊",
        )

    st.divider()

    left_col, right_col = st.columns([1, 1])

    with left_col:
        st.markdown("#### 📡 Sběr čerstvých dat")
        query_input = st.text_input("Zadejte niku / téma:", value="AI automatizace")
        if st.button("📡 Spustit steganí sken", use_container_width=True):
            try:
                with st.status("Skenuji data o trendech...", expanded=True) as status:
                    trends = scraper.search_youtube_shorts(query_input, max_results=5)
                    for t in trends:
                        conn = db._get_connection()
                        conn.execute(
                            "INSERT INTO trends (topic, views) VALUES (?, ?)",
                            (t.get("topic"), t.get("views", 0)),
                        )
                        conn.commit()
                        conn.close()
                    status.update(
                        label=f"Sken dokončen. Uloženo {len(trends) if trends else 0} témat.",
                        state="complete",
                    )
                    time.sleep(1)
                    st.rerun()
            except Exception as exc:
                st.error(f"Sken selhal: {exc}")

    with right_col:
        st.markdown("#### 🧠 Analyzovat TOP téma")
        if st.button("🧠 Vytvořit Blueprint", type="primary", use_container_width=True):
            top_trend = db.get_top_trend()
            if top_trend:
                with st.status(f"Analyzuji vítězné téma: '{top_trend['topic'][:40]}...'", expanded=True) as status:
                    prompt = (
                        f"Zanalyzuj virální video s tématem '{top_trend['topic']}' "
                        f"(Zhlédnutí: {top_trend['views']}). Vytvoř kompletní produkční blueprint "
                        f"pro nové video, které maximalizuje zábavnost a konverzi."
                    )
                    try:
                        blueprint = llm.generate_structured(prompt, VideoBlueprint)
                    except Exception:
                        blueprint = None

                    if not blueprint:
                        st.warning("⚠️ Model vrátil nestandardní formát, aktivuji záložní blueprint...")
                        blueprint = VideoBlueprint(
                            core_message=f"Praktický pohled na téma: {top_trend['topic']}",
                            visual_atmosphere="Dynamické, moderní, s titulky uprostřed",
                            target_audience="Široká veřejnost zajímající se o technologie a inovace",
                        )

                    st.session_state.current_blueprint = blueprint
                    st.session_state.top_trend_topic = top_trend["topic"]
                    status.update(label="Blueprint byl úspěšně vytvořen a uložen do paměti.", state="complete")
                    time.sleep(1)
                    st.rerun()
            else:
                st.error("⚠️ Žádná data k analýze. Nejprve spusťte sken v levém sloupci.")

    if st.session_state.current_blueprint:
        bp = st.session_state.current_blueprint
        st.markdown(f"### 📋 Aktivní Blueprint (odvozen z: {st.session_state.top_trend_topic})")
        st.markdown(
            f"""
            <div class="blueprint-box">
                <p><strong>🎯 Hlavní sdělení:</strong> {bp.core_message}</p>
                <p><strong>👁️ Vizuální styl:</strong> {bp.visual_atmosphere}</p>
                <p><strong>👥 Cílová skupina:</strong> {bp.target_audience}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.success("Blueprint je připraven. Můžete přejít do záložky 2 a začít s generováním variant.")

# --- TAB 2: AI PRODUKCE A NÁHLEDY ---
with tabs[1]:
    st.subheader("💡 Krok 2: Dávková AI produkce a schvalování")
    st.markdown("<p class='step-desc'>Systém vygeneruje několik variant scénářů, přiřadí podkresová videa a umožní vám je schválit nebo zamítnout.</p>", unsafe_allow_html=True)

    if st.session_state.current_blueprint:
        if st.button("✨ Vytvořit 3 varianty", type="primary"):
            with st.status("Generuji kreativní varianty z aktuálního Blueprintu...", expanded=True) as status:
                bp = st.session_state.current_blueprint
                angles = [
                    "Kontroverzní / Provokativní",
                    "Vzdělávací krok-za-krokem",
                    "Příběhový / Osobní",
                ]
                bg_videos = [
                    os.path.join(media.temp_dir, file)
                    for file in os.listdir(media.temp_dir)
                    if file.endswith(".mp4")
                ] if hasattr(media, "temp_dir") and os.path.isdir(media.temp_dir) else []

                conn = db._get_connection()
                for index, angle in enumerate(angles, start=1):
                    st.write(f"Zpracovávám variantu {index}/3 (úhel: {angle})...")
                    prompt = (
                        f"Vytvoř video scénář. Úhel: {angle}. Cílovka: {bp.target_audience}. "
                        f"Sdělení: {bp.core_message}. Vizuál: {bp.visual_atmosphere}."
                    )
                    try:
                        script_obj = llm.generate_structured(prompt, VideoScript)
                    except Exception:
                        script_obj = None

                    if not script_obj:
                        script_obj = VideoScript(
                            hook=f"Všimli jste si, jak rychle se mění {bp.core_message}?",
                            body="Tento směr přináší zásadní změny. Podívejte se na klíčové body, které musíte znát.",
                            cta="Sledujte pro víc tipů!",
                            visual_cues="Rychlé prostřihy, dynamický zoom",
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
                status.update(label="3 varianty byly připraveny ke schválení.", state="complete")
                time.sleep(1)
                st.rerun()
    else:
        st.warning("⚠️ Nejprve musíte vytvořit blueprint v záložce 1.")

    st.divider()

    conn = db._get_connection()
    proposals = conn.execute(
        "SELECT * FROM content_proposals WHERE status = 'pending' ORDER BY id DESC"
    ).fetchall()
    conn.close()

    if proposals:
        st.markdown(f"### Čekající návrhy ke schválení ({len(proposals)})")
        for proposal in proposals:
            with st.container():
                st.markdown(
                    f"#### Návrh #{proposal['id']} — <span class='chip'>{proposal['angle']}</span>",
                    unsafe_allow_html=True,
                )

                text_col, visual_col = st.columns([3, 2])
                with text_col:
                    st.markdown(f"**🎣 Háček:** {proposal['hook']}")
                    st.markdown(f"**🗣️ Text pro dabing:** {proposal['script']}")
                    st.info(f"**🎬 Vizuální pokyny:** {proposal['visual_cues']}")

                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(
                            "✅ Schválit a vyrenderovat",
                            key=f"approve_{proposal['id']}",
                            type="primary",
                            use_container_width=True,
                        ):
                            try:
                                with st.status(f"Spouštím render pro návrh #{proposal['id']}...", expanded=True) as render_status:
                                    st.write("🎙️ Generuji AI dabing...")
                                    audio_path = media.generate_voiceover(proposal['script'])

                                    final_video_path = None
                                    if proposal['bg_video_path'] and os.path.exists(proposal['bg_video_path']) and audio_path:
                                        st.write("🎬 Skládám video a upravuji formát 9:16...")
                                        final_video_path = media.render_final_video(proposal['bg_video_path'], audio_path)

                                    conn = db._get_connection()
                                    conn.execute(
                                        "UPDATE content_proposals SET status = 'approved' WHERE id = ?",
                                        (proposal['id'],),
                                    )
                                    conn.execute(
                                        "INSERT INTO generated_posts (niche, platform, prompt, result, media_path) VALUES (?, ?, ?, ?, ?)",
                                        ("Blueprint Trend", "Shorts/Reels", proposal['angle'], proposal['script'], final_video_path),
                                    )
                                    conn.commit()
                                    conn.close()
                                    render_status.update(
                                        label="✓ Video bylo vyrenderováno a přidáno do fronty.",
                                        state="complete",
                                    )
                                time.sleep(1)
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Render selhal: {exc}")
                    with c2:
                        if st.button("❌ Zamítnout", key=f"reject_{proposal['id']}", use_container_width=True):
                            conn = db._get_connection()
                            conn.execute("UPDATE content_proposals SET status = 'rejected' WHERE id = ?", (proposal['id'],))
                            conn.commit()
                            conn.close()
                            st.rerun()

                with visual_col:
                    st.markdown("**🎥 Náhled podkresového videa:**")
                    if proposal['bg_video_path'] and os.path.exists(proposal['bg_video_path']):
                        st.video(proposal['bg_video_path'])
                    else:
                        st.warning("⚠️ Nebyly nalezeny žádné podkresové klipy. Přidejte ukázková .mp4 videa do složky input_media.")
            st.divider()
    else:
        st.info("💡 Žádné čekající návrhy. Vytvořte nové varianty z Blueprintu.")

# --- TAB 3: PUBLIKAČNÍ FRONTA ---
with tabs[2]:
    st.subheader("📤 Krok 3: Publikační fronta")
    st.markdown("<p class='step-desc'>Zde je přehled všech hotových, čekajících a odeslaných příspěvků pro sociální sítě.</p>", unsafe_allow_html=True)

    conn = db._get_connection()
    df_posts = pd.read_sql_query(
        "SELECT id, platform, result, media_path, published, created_at FROM generated_posts ORDER BY id DESC LIMIT 15",
        conn,
    )
    conn.close()

    if not df_posts.empty:
        df_posts["Stav"] = df_posts["published"].apply(lambda value: "✅ Odesláno" if value == 1 else "⏳ Ve frontě")
        df_posts["Média"] = df_posts["media_path"].apply(lambda value: "🎥 Vyrenderováno (.mp4)" if value else "📄 Pouze text")
        st.dataframe(df_posts[["id", "Stav", "Média", "platform", "created_at"]], use_container_width=True)

        if st.button("🚀 Odeslat čekající příspěvky na webhook", type="primary"):
            with st.spinner("Spouštím export do webhooku..."):
                pending = df_posts[df_posts["published"] == 0]
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
                        st.warning(f"Export příspěvku {row['id']} selhal: {exc}")
                st.success("✓ Fronta byla zpracována.")
                time.sleep(1)
                st.rerun()
    else:
        st.info("💡 Publikační fronta je prázdná. Schválením návrhu se vytvoří první výstup.")

# --- TAB 4: HW MONITOR ---
with tabs[3]:
    st.subheader("💻 Stav hardwaru a systémových služeb")
    st.markdown("<p class='step-desc'>Monitorování zátěže workstationu, paměti, složitých služeb a využití GPU pro lokální inferenci.</p>", unsafe_allow_html=True)

    st.markdown("### ⚙️ Výpočetní zdroje")
    col_cpu, col_ram, col_disk = st.columns(3)
    with col_cpu:
        render_metric_card("CPU", f"{psutil.cpu_percent(interval=0.2)} %", "Vytížení procesoru", "🧠")
    with col_ram:
        ram = psutil.virtual_memory()
        render_metric_card("RAM", f"{ram.percent} %", f"Volno: {ram.available / (1024**3):.1f} GB", "💾")
    with col_disk:
        disk = psutil.disk_usage(os.getcwd())
        render_metric_card("NVMe", f"{disk.percent} %", f"Volno: {disk.free / (1024**3):.1f} GB", "💿")

    st.divider()

    gpu_col, services_col = st.columns(2)

    with gpu_col:
        st.info("NVIDIA Quadro M5000M (8 GB VRAM)")
        try:
            import subprocess
            gpu_info = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                stderr=subprocess.STDOUT,
                timeout=2,
            ).decode().strip()
            gpu_util, mem_used, mem_total = gpu_info.split(",")
            st.metric("Vytížení GPU", f"{gpu_util.strip()} %")
            st.progress(float(mem_used.strip()) / float(mem_total.strip()), text=f"VRAM: {mem_used.strip()} MB / {mem_total.strip()} MB")
        except Exception:
            st.warning("⚠️ Detailní metriky GPU nejsou momentálně dostupné. Ollama ale stále může využívat hostitelské GPU akcelerace.")

    with services_col:
        ollama_status = "✅ Online & stabilní" if llm.check_health() else "❌ Offline (zkontrolujte Ollama host)")
        st.markdown(f"**🧠 Ollama AI Engine:** {ollama_status}")

        rag_status = "✅ Připojeno (sqlite-vec)" if rag_engine else "⚠️ Rozšíření nenalezeno"
        st.markdown(f"**📚 Vektorová paměť (RAG):** {rag_status}")

        st.markdown("**🛡️ Produkční watchdog:** Aktivní na pozadí")
        st.caption("Systém monitoruje provozní zdraví a zajišťuje přehled o aktuálním stavu AI pipeline.")


# Footer
st.markdown("<div style='height: 2rem;'></div>", unsafe_allow_html=True)

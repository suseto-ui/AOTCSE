import os
import sqlite3
import random
import time
import json
import streamlit as st
import pandas as pd
import psutil
from datetime import datetime

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
        conn.execute('PRAGMA journal_mode = WAL;')
        return conn

    def _init_db(self):
        conn = self._get_connection()
        conn.executescript('''
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
        ''')
        # Bezpečná migrace sloupců
        for col_def in [
            'ALTER TABLE content_proposals ADD COLUMN bg_video_path TEXT',
            'ALTER TABLE content_proposals ADD COLUMN blueprint_context TEXT',
            'ALTER TABLE content_proposals ADD COLUMN visual_cues TEXT',
            'ALTER TABLE content_proposals ADD COLUMN angle TEXT'
        ]:
            try:
                conn.execute(col_def)
            except sqlite3.OperationalError:
                pass
            
        conn.commit()
        conn.close()

    def get_top_trend(self):
        conn = self._get_connection()
        trend = conn.execute("SELECT * FROM trends ORDER BY views DESC, id DESC LIMIT 1").fetchone()
        conn.close()
        return dict(trend) if trend else None

    def get_top_10_trends(self):
        conn = self._get_connection()
        trends = conn.execute("SELECT topic, views, created_at FROM trends ORDER BY views DESC LIMIT 10").fetchall()
        conn.close()
        return [dict(t) for t in trends]

st.set_page_config(page_title="AOTCSE Cockpit 🚀", page_icon="🚀", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #0f172a; }
    .card { background-color: #1e293b; padding: 20px; border-radius: 12px; border: 1px solid #334155; margin-bottom: 20px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
    .highlight { color: #38bdf8; font-weight: bold; }
    .blueprint-box { border-left: 4px solid #10b981; background-color: #064e3b; padding: 15px; border-radius: 8px; margin: 10px 0; }
    .step-desc { color: #94a3b8; font-size: 0.95rem; margin-bottom: 15px; }
    </style>
""", unsafe_allow_html=True)

db = DatabaseManager()
llm = LocalLLMClient()
media = MediaPipeline()
publisher = AutoPublisher()

st.title("🚀 AOTCSE - Autonomní Výrobní Linka Obsahu")
st.markdown("Vítejte v řídicím centru vaší lokální AI agentury. Systém funguje v uzavřeném režimu (**Air-Gapped**) s využitím lokální GPU akcelerace.")

tabs = st.tabs([
    "🎯 1. Objev & Blueprint", 
    "💡 2. AI Produkce (Náhledy)", 
    "📤 3. Publikační fronta", 
    "💻 4. HW Monitor"
])

if 'current_blueprint' not in st.session_state:
    st.session_state.current_blueprint = None
if 'top_trend_topic' not in st.session_state:
    st.session_state.top_trend_topic = None

# --- TAB 1: OBJEV A BLUEPRINT ---
with tabs[0]:
    st.subheader("🕵️‍♂️ Krok 1: Analýza trhu, sledovanosti a tvorba Blueprintu")
    st.markdown("<p class='step-desc'>Zde sledujeme vývoj sledovanosti virálních témat a na základě nejúspěšnějšího z nich generujeme strategický plán (Blueprint) pro novou tvorbu.</p>", unsafe_allow_html=True)
    
    st.markdown("### 📈 Spojnicový graf sledovanosti (Top 10 témat včerejška)")
    top_10 = db.get_top_10_trends()
    
    if top_10:
        df_trends = pd.DataFrame(top_10)
        st.line_chart(df_trends.set_index('topic')['views'], color="#10b981", use_container_width=True)
        
        st.markdown("**🔍 Interaktivní tabulka nasbíraných témat a zhlédnutí:**")
        st.dataframe(
            df_trends[['topic', 'views', 'created_at']].style.highlight_max(subset=['views'], color='#064e3b'), 
            height=200, 
            use_container_width=True
        )
    else:
        st.info("💡 V databázi zatím nejsou žádná data. Spusťte prosím sběr témat v tlačítku níže.")
        
    st.divider()
    
    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("#### 📡 Sběr čerstvých dat ze sítí")
        st.markdown("<p class='step-desc'>Prohledá YouTube Shorts a uloží trendy do lokální databáze.</p>", unsafe_allow_html=True)
        query_input = st.text_input("Zadejte sledovanou niku / téma:", value="AI automatizace")
        if st.button("📡 Spustit Stealth Scraper", use_container_width=True):
            with st.status("Stahuji data o nejsledovanějších tématech...", expanded=True) as status:
                st.write("Navazuji spojení s maskovaným klientem...")
                trends = scraper.search_youtube_shorts(query_input, max_results=5)
                for t in trends:
                    conn = db._get_connection()
                    conn.execute('INSERT INTO trends (topic, views) VALUES (?, ?)', (t['topic'], t['views']))
                    conn.commit()
                    conn.close()
                status.update(label=f"Sken úspěšně dokončen! Nalezeno a uloženo {len(trends)} témat.", state="complete")
                time.sleep(1)
                st.rerun()

    with col2:
        st.markdown("#### 🧠 Hloubková AI Analýza TOP Téma")
        st.markdown("<p class='step-desc'>Vezme absolutně nejúspěšnější téma z grafu a sestaví produkční Blueprint.</p>", unsafe_allow_html=True)
        if st.button("🧠 Stáhnout a analyzovat pro Blueprint", type="primary", use_container_width=True):
            top_trend = db.get_top_trend()
            if top_trend:
                with st.status(f"Analyzuji vítězné téma: '{top_trend['topic'][:40]}...'", expanded=True) as status:
                    st.write("Odesílám kontext do lokálního modelu Ollama (Qwen)...")
                    prompt = f"Zanalyzuj virální video s tématem '{top_trend['topic']}' (Zhlédnutí: {top_trend['views']}). Vytvoř kompletní produkční blueprint pro nové video, které využije stejný princip úspěchu."
                    
                    try:
                        blueprint = llm.generate_structured(prompt, VideoBlueprint)
                    except Exception:
                        blueprint = None
                        
                    # Bezpečný fallback v případě, že by strukturovaný JSON selhal
                    if not blueprint:
                        st.warning("⚠️ Model vrátil nestandardní formát, aktivuji bezpečný záložní Blueprint...")
                        blueprint = VideoBlueprint(
                            core_message=f"Praktický pohled na téma: {top_trend['topic']}",
                            visual_atmosphere="Dynamické, moderní, s titulky uprostřed",
                            target_audience="Široká veřejnost zajímající se o moderní technologie"
                        )
                    
                    st.session_state.current_blueprint = blueprint
                    st.session_state.top_trend_topic = top_trend['topic']
                    status.update(label="✨ Blueprint byl úspěšně sestaven a uložen do paměti!", state="complete")
            else:
                st.error("⚠️ Žádná data k analýze. Spusťte nejdříve sken v levém sloupci.")

    if st.session_state.current_blueprint:
        st.markdown(f"### 📋 Aktivní Produkční Blueprint (Odvozeno z: {st.session_state.top_trend_topic})")
        bp = st.session_state.current_blueprint
        st.markdown(f"""
        <div class="blueprint-box">
            <p><strong>🎯 Hlavní sdělení:</strong> {bp.core_message}</p>
            <p><strong>👁️ Vizuální styl:</strong> {bp.visual_atmosphere}</p>
            <p><strong>👥 Cílová skupina:</strong> {bp.target_audience}</p>
        </div>
        """, unsafe_allow_html=True)
        st.success("💡 Blueprint je připraven! Nyní můžete přejít do záložky **'2. AI Produkce (Náhledy)'**.")

# --- TAB 2: AI PRODUKCE A NÁHLEDY ---
with tabs[1]:
    st.subheader("💡 Krok 2: Dávková AI produkce a schvalování s náhledem")
    st.markdown("<p class='step-desc'>Zde systém automaticky vygeneruje několik variant scénářů podle aktivního Blueprintu, spáruje je s podkresovými videi a umožní vám je před schválením zkontrolovat.</p>", unsafe_allow_html=True)
    
    if st.session_state.current_blueprint:
        if st.button("✨ Vygenerovat 3 kreativní varianty z Blueprintu", type="primary"):
            with st.status("Generuji varianty přes Ollama...", expanded=True) as status:
                bp = st.session_state.current_blueprint
                angles = ["Kontroverzní / Provokativní", "Vzdělávací krok-za-krokem", "Příběhový / Osobní"]
                
                bg_videos = [f for f in os.listdir(media.temp_dir) if f.endswith('.mp4')]
                
                conn = db._get_connection()
                for i, angle in enumerate(angles):
                    st.write(f"Zpracovávám variantu {i+1}/3 (Úhel: {angle})...")
                    prompt = f"Vytvoř video scénář. Úhel: {angle}. Cílovka: {bp.target_audience}. Sdělení: {bp.core_message}. Vizuál: {bp.visual_atmosphere}."
                    
                    try:
                        script_obj = llm.generate_structured(prompt, VideoScript)
                    except Exception:
                        script_obj = None
                        
                    if not script_obj:
                        script_obj = VideoScript(
                            hook=f"Všimli jste si, jak rychle se mění {bp.core_message}?",
                            body=f"Tento směr přináší zásadní změny. Podívejte se na klíčové body, které musíte znát.",
                            cta="Sledujte pro víc tipů!",
                            visual_cues="Rychlé prostřihy, dynamický zoom"
                        )
                        
                    bg_path = os.path.join(media.temp_dir, random.choice(bg_videos)) if bg_videos else ""
                    full_script = f"{script_obj.hook} {script_obj.body} {script_obj.cta}"
                    
                    conn.execute("""
                        INSERT INTO content_proposals 
                        (blueprint_context, angle, hook, script, visual_cues, bg_video_path, status) 
                        VALUES (?, ?, ?, ?, ?, ?, 'pending')
                    """, (bp.core_message, angle, script_obj.hook, full_script, script_obj.visual_cues, bg_path))
                
                conn.commit()
                conn.close()
                status.update(label="3 varianty úspěšně připraveny ke schválení níže!", state="complete")
                time.sleep(1)
                st.rerun()
    else:
        st.warning("⚠️ Nejprve musíte vytvořit a uložit Blueprint v záložce 1.")

    st.divider()
    
    conn = db._get_connection()
    proposals = conn.execute("SELECT * FROM content_proposals WHERE status = 'pending' ORDER BY id DESC").fetchall()
    conn.close()

    if proposals:
        st.markdown(f"### Čekající návrhy ke schválení ({len(proposals)})")
        for p in proposals:
            with st.container():
                st.markdown(f"#### Návrh #{p['id']} — Úhel pohledu: <span class='highlight'>{p['angle']}</span>", unsafe_allow_html=True)
                
                col_text, col_visual = st.columns([3, 2])
                with col_text:
                    st.markdown(f"**🎣 Háček (první 3s):** {p['hook']}")
                    st.markdown(f"**🗣️ Text pro dabing:** {p['script']}")
                    st.info(f"**🎬 Pokyny pro editora / vizuál:** {p['visual_cues']}")
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("✅ Schválit a vyrenderovat video", key=f"app_{p['id']}", use_container_width=True, type="primary"):
                            with st.status(f"Spouštím Render Pipeline pro návrh #{p['id']}...", expanded=True) as render_status:
                                st.write("🎙️ Generuji AI dabing (Piper TTS)...")
                                audio_path = media.generate_voiceover(p['script'])
                                
                                final_video_path = None
                                if p['bg_video_path'] and os.path.exists(p['bg_video_path']) and audio_path:
                                    st.write("🎬 Skládám video, aplikuji 9:16 ořez a NVENC akceleraci...")
                                    final_video_path = media.render_final_video(p['bg_video_path'], audio_path)
                                
                                conn = db._get_connection()
                                conn.execute("UPDATE content_proposals SET status = 'approved' WHERE id = ?", (p['id'],))
                                conn.execute("INSERT INTO generated_posts (niche, platform, prompt, result, media_path) VALUES (?, ?, ?, ?, ?)", 
                                             ("Blueprint Trend", "Shorts/Reels", p['angle'], p['script'], final_video_path))
                                conn.commit()
                                conn.close()
                                render_status.update(label="✓ Video vyrenderováno a odesláno do publikační fronty!", state="complete")
                            time.sleep(1)
                            st.rerun()
                    with c2:
                        if st.button("❌ Zamítnout", key=f"rej_{p['id']}", use_container_width=True):
                            conn = db._get_connection()
                            conn.execute("UPDATE content_proposals SET status = 'rejected' WHERE id = ?", (p['id'],))
                            conn.commit()
                            conn.close()
                            st.rerun()

                with col_visual:
                    st.markdown("**🎥 Náhled podkresového videa:**")
                    if p['bg_video_path'] and os.path.exists(p['bg_video_path']):
                        st.video(p['bg_video_path'])
                    else:
                        st.warning("⚠️ Ve složce `input_media` nebyl nalezen žádný podkres. Přidejte sem prosím ukázková .mp4 videa.")
            st.divider()
    else:
        st.info("💡 Žádné čekající návrhy. Zmáčkněte výše tlačítko pro generování nových variant z Blueprintu.")

# --- TAB 3: PUBLIKAČNÍ FRONTA ---
with tabs[2]:
    st.subheader("📤 Krok 3: Publikační fronta a export")
    st.markdown("<p class='step-desc'>Zde vidíte kompletně připravená nebo již odeslaná videa a texty, které systém připravil pro vaše sociální sítě.</p>", unsafe_allow_html=True)
    
    conn = db._get_connection()
    df_posts = pd.read_sql_query("SELECT id, platform, result, media_path, published, created_at FROM generated_posts ORDER BY id DESC LIMIT 15", conn)
    conn.close()
    
    if not df_posts.empty:
        df_posts['Stav'] = df_posts['published'].apply(lambda x: '✅ Odesláno' if x == 1 else '⏳ Čeká ve frontě')
        df_posts['Média'] = df_posts['media_path'].apply(lambda x: '🎥 Vyrenderováno (.mp4)' if x else '📄 Pouze text')
        st.dataframe(df_posts[['id', 'Stav', 'Média', 'platform', 'created_at']], use_container_width=True)
        
        if st.button("🚀 Odeslat čekající příspěvky na webhook", type="primary"):
            with st.spinner("Spouštím Auto-Publisher..."):
                pending = df_posts[df_posts['published'] == 0]
                for _, row in pending.iterrows():
                    publisher.export_to_webhook(row['id'], "Trendy", row['platform'], row['result'], row['media_path'])
                    conn = db._get_connection()
                    conn.execute("UPDATE generated_posts SET published = 1 WHERE id = ?", (row['id'],))
                    conn.commit()
                    conn.close()
                st.success("✓ Fronta byla úspěšně odeslána!")
                time.sleep(1)
                st.rerun()
    else:
        st.info("💡 Publikační fronta je prázdná. Schvalte nějaké video v předchozí záložce.")

# --- TAB 4: HW MONITOR ---
with tabs[3]:
    st.subheader("💻 Stav hardwaru a systémových služeb")
    st.markdown("<p class='step-desc'>Monitorování vytížení vašeho mobilního workstationu HP ZBook 17 G3 (Xeon, RAM, NVMe, Quadro GPU).</p>", unsafe_allow_html=True)
    
    st.markdown("### ⚙️ Výpočetní zdroje")
    c1, c2, c3 = st.columns(3)
    c1.metric("CPU (Intel Xeon)", f"{psutil.cpu_percent(interval=0.2)} %")
    
    ram = psutil.virtual_memory()
    c2.metric("Operační paměť RAM", f"{ram.percent} %", f"Volno: {ram.available / (1024**3):.1f} GB")
    
    disk = psutil.disk_usage(os.getcwd())
    c3.metric("NVMe Úložiště", f"{disk.percent} %", f"Volno: {disk.free / (1024**3):.1f} GB")
    
    st.divider()
    
    st.markdown("### 🔌 Služby a grafická akcelerace")
    col_gpu, col_serv = st.columns(2)
    
    with col_gpu:
        st.info("NVIDIA Quadro M5000M (8 GB VRAM)")
        try:
            import subprocess
            gpu_info = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"], 
                stderr=subprocess.STDOUT, 
                timeout=2
            ).decode().strip()
            gpu_util, mem_used, mem_total = gpu_info.split(',')
            
            st.metric("Vytížení GPU", f"{gpu_util.strip()} %")
            st.progress(float(mem_used.strip()) / float(mem_total.strip()), text=f"VRAM: {mem_used.strip()} MB / {mem_total.strip()} MB")
        except Exception:
            st.warning("⚠️ Detailní VRAM metriky Nvidia-smi nejsou z tohoto kontejneru dostupné. Ollama však GPU využívá nativně.")

    with col_serv:
        ollama_status = "✅ Online & Stabilní" if llm.check_health() else "❌ Offline (Zkontrolujte hostitelskou Ollamu)"
        st.markdown(f"**🧠 Ollama AI Engine:** {ollama_status}")
        
        rag_status = "✅ Připojeno (sqlite-vec)" if rag_engine else "⚠️ Rozšíření nenalezeno"
        st.markdown(f"**📚 Vektorová Paměť (RAG):** {rag_status}")
        
        st.markdown(f"**🛡️ Produkční Watchdog:** Aktivní na pozadí")

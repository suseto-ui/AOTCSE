import os
import sqlite3
import json
import time
import requests
import logging
from src.config import settings

# Nastavení logování pro čistý výstup v Docker konzoli
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [Publisher] %(message)s', datefmt='%H:%M:%S')

class AutoPublisher:
    def __init__(self, db_path=settings.DB_PATH):
        self.db_path = db_path
        self.webhook_url = settings.WEBHOOK_URL
        self._ensure_table_schema()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode = WAL;')
        return conn

    def _ensure_table_schema(self):
        """Zajistí, že databáze obsahuje všechny potřebné sloupce pro multimédia."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('ALTER TABLE generated_posts ADD COLUMN media_path TEXT')
            cursor.execute('ALTER TABLE generated_posts ADD COLUMN vtt_path TEXT')
        except sqlite3.OperationalError:
            pass # Sloupce již existují
        finally:
            conn.commit()
            conn.close()

    def _format_payload_for_platform(self, post_id: int, niche: str, platform: str, content: str, media_path: str, vtt_path: str) -> dict:
        """
        Dynamicky formátuje data (payload) podle požadavků konkrétní sociální sítě.
        """
        # Základní data společná pro všechny platformy
        payload = {
            "aotcse_system_id": post_id,
            "export_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "niche_category": niche,
            "has_media": bool(media_path),
            "media_file_local_path": media_path if media_path else "",
            "subtitles_vtt_local_path": vtt_path if vtt_path else "",
            "raw_content": content
        }

        # Generování hashtagů bez mezer
        safe_niche_tag = niche.replace(" ", "").replace("-", "")

        # Specifická pravidla pro platformy
        if platform == "TikTok":
            payload.update({
                "target_platform": "TikTok",
                "post_type": "vertical_video_9_16",
                "caption": f"{content[:120]}... #{safe_niche_tag} #fyp #viral #trending",
                "comments_disabled": False,
                "duet_disabled": False
            })
        elif platform == "YouTube Shorts":
            payload.update({
                "target_platform": "YouTube",
                "post_type": "shorts_video",
                "title": f"{niche} - Tajemství Odhaleno! 🚀",
                "description": f"{content}\n\n#shorts #{safe_niche_tag}",
                "visibility": "public",
                "notify_subscribers": True
            })
        elif platform == "Instagram Reels":
            payload.update({
                "target_platform": "Instagram",
                "post_type": "reels_video",
                "caption": f"{content}\n\nSledujte pro více tipů! 🔥\n#{safe_niche_tag} #reels #explore",
                "share_to_feed": True
            })
        elif platform == "LinkedIn":
            payload.update({
                "target_platform": "LinkedIn",
                "post_type": "professional_post",
                "text_body": f"💡 Nový poznatek z oblasti: {niche}\n\n{content}\n\nJaký je váš názor? Napište mi do komentářů.\n\n#{safe_niche_tag} #B2B #Innovation #Business",
                "visibility": "PUBLIC"
            })
        else:
            # Fallback pro neznámé platformy
            payload.update({
                "target_platform": platform,
                "caption": content,
                "hashtags": f"#{safe_niche_tag}"
            })

        return payload

    def export_to_webhook(self, post_id: int, niche: str, platform: str, content: str, media_path: str = None, vtt_path: str = None) -> bool:
        """
        Sestaví a odešle kompletní platformně-specifický payload na integrační platformu.
        """
        payload = self._format_payload_for_platform(post_id, niche, platform, content, media_path, vtt_path)
        
        # Pokud webhook není nastaven, provádíme simulovaný export (Dump) do souboru
        if not self.webhook_url or "VASE_WEBHOOK_URL" in self.webhook_url:
            dump_dir = os.path.join(settings.BASE_DIR, "webhook_dumps")
            os.makedirs(dump_dir, exist_ok=True)
            dump_path = os.path.join(dump_dir, f"export_payload_{platform.replace(' ', '_')}_post_{post_id}.json")
            
            with open(dump_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)
            logging.warning(f"Webhook URL není nastavena. Payload pro {platform} byl lokálně dumpnut do: {dump_path}")
            return True
        
        # Reálné odeslání na webhook
        try:
            logging.info(f"Odesílám HTTP POST na webhook pro příspěvek ID: {post_id} (Platforma: {platform})")
            response = requests.post(self.webhook_url, json=payload, timeout=15)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"Chyba sítě při odesílání webhooku (ID {post_id}): {e}")
            return False

    def run_publisher_loop(self):
        """Hlavní smyčka daemona."""
        logging.info(f"Startuji AOTCSE Auto-Publisher Daemon. Webhook cíl: {self.webhook_url}")
        
        while True:
            try:
                conn = self._get_connection()
                pending = conn.execute('''
                    SELECT id, niche, platform, result, media_path, vtt_path 
                    FROM generated_posts 
                    WHERE published = 0 
                    ORDER BY id ASC LIMIT 5
                ''').fetchall()
                
                if not pending:
                    time.sleep(15)
                    conn.close()
                    continue

                for row in pending:
                    post = dict(row)
                    logging.info(f"Zpracovávám čekající příspěvek ID {post['id']} pro platformu {post['platform']}...")
                    
                    success = self.export_to_webhook(
                        post_id=post['id'],
                        niche=post['niche'],
                        platform=post['platform'],
                        content=post['result'],
                        media_path=post.get('media_path'),
                        vtt_path=post.get('vtt_path')
                    )
                    
                    if success:
                        conn.execute('UPDATE generated_posts SET published = 1 WHERE id = ?', (post['id'],))
                        conn.commit()
                        logging.info(f"✓ Příspěvek ID {post['id']} úspěšně označen jako odeslaný.")
                
                conn.close()
                time.sleep(10)
                
            except Exception as e:
                logging.error(f"Kritická chyba v hlavní smyčce publisheru: {e}")
                time.sleep(30)

if __name__ == "__main__":
    publisher = AutoPublisher()
    publisher.run_publisher_loop()
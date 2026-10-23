import sqlite3
import sqlite_vec
import requests
import json
import logging
from src.config import DB_PATH, OLLAMA_URL

logger = logging.getLogger(__name__)

class VectorMemoryManager:
    """
    Spravuje generování embeddings přes Ollamu a jejich ukládání/vyhledávání
    v SQLite databázi pomocí rozšíření sqlite-vec.
    """
    def __init__(self, db_path=DB_PATH, embed_model="nomic-embed-text"):
        self.db_path = db_path
        self.embed_model = embed_model
        # Ollama endpoint pro embeddings (převzato z base URL, např. odřízneme /generate)
        self.embed_url = OLLAMA_URL.replace("/generate", "/embeddings")
        
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.row_factory = sqlite3.Row
        return conn

    def generate_embedding(self, text: str) -> list[float] | None:
        """Vygeneruje vektor z textu pomocí lokální Ollamy."""
        payload = {
            "model": self.embed_model,
            "prompt": text
        }
        try:
            response = requests.post(self.embed_url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get("embedding")
        except Exception as e:
            logger.error(f"Chyba při generování embeddingu přes Ollama: {e}")
            return None

    def add_memory(self, content: str, source: str = "general"):
        """Uloží text a jeho vektorovou reprezentaci do databáze."""
        embedding = self.generate_embedding(content)
        if not embedding:
            logger.error("Paměť nebyla uložena, embedding selhal.")
            return False
            
        conn = self._get_connection()
        cur = conn.cursor()
        
        # 1. Uložení metadat a obsahu
        cur.execute(
            'INSERT INTO memory_chunks (content, source) VALUES (?, ?)',
            (content, source)
        )
        row_id = cur.lastrowid
        
        # 2. Uložení vektoru
        # sqlite-vec vyžaduje vektor jako JSON pole ve string formatu
        embedding_json = json.dumps(embedding)
        cur.execute(
            'INSERT INTO vec_memory (id, embedding) VALUES (?, ?)',
            (row_id, embedding_json)
        )
        
        conn.commit()
        conn.close()
        logger.info(f"Paměť úspěšně uložena s ID {row_id}.")
        return True

    def search_similar(self, query: str, limit: int = 3) -> list[dict]:
        """Najde nejpodobnější záznamy v paměti na základě dotazu."""
        query_embedding = self.generate_embedding(query)
        if not query_embedding:
            return []
            
        query_json = json.dumps(query_embedding)
        conn = self._get_connection()
        
        # Cosine similarity search s využitím K-Nearest Neighbors (KNN) přes sqlite-vec
        sql = '''
            SELECT m.id, m.content, m.source, v.distance
            FROM vec_memory v
            JOIN memory_chunks m ON v.id = m.id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance ASC
        '''
        
        results = conn.execute(sql, (query_json, limit)).fetchall()
        conn.close()
        
        return [dict(r) for r in results]
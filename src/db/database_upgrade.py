import sqlite3
import sqlite_vec
import os
import logging
from src.config import DB_PATH

logger = logging.getLogger(__name__)

def upgrade_database():
    """
    Rozšíří existující SQLite databázi o vektorové tabulky pomocí sqlite-vec.
    Umožňuje ukládat texty a jejich vektorové reprezentace (embeddings) pro RAG.
    """
    logger.info(f"Spouštím upgrade databáze s podporou sqlite-vec na {DB_PATH}...")
    
    # Připojení k databázi
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    
    # Načtení sqlite-vec rozšíření
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    
    # Vytvoření standardní tabulky pro metadata paměti
    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    
    # Vytvoření VIRTUAL tabulky pro vektory. 
    # Rozměr 768 odpovídá běžným modelům jako nomic-embed-text.
    conn.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
            id INTEGER PRIMARY KEY,
            embedding float[768]
        );
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Upgrade databáze dokončen. Vektorové tabulky jsou připraveny.")

if __name__ == "__main__":
    # Testovací spuštění
    upgrade_database()
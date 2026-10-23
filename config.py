import os
from dotenv import load_dotenv

# Načtení proměnných ze souboru .env (pokud existuje a běžíme mimo Docker)
load_dotenv()

# Zabezpečená centrální konfigurace celého ekosystému
class Config:
    # Cesty a databáze
    DB_PATH = os.environ.get("DB_PATH", "aotcse_local.db")
    
    # AI a inference
    OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434/api/generate")
    LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.5:4b")
    
    # Publikace
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://hook.eu1.make.com/VASE_WEBHOOK_URL")
    
    # Výkon (Threads pro lokální modely a média)
    OMP_NUM_THREADS = os.environ.get("OMP_NUM_THREADS", "4")

# Export instance pro snadný import ve zbytku aplikace
settings = Config()
import os
from dotenv import load_dotenv

# Načtení proměnných z .env souboru (pokud existuje)
load_dotenv()

class Config:
    """
    Centrální konfigurace pro AOTCSE. 
    Zajišťuje bezpečné sdílení cest a hesel napříč celou aplikací.
    """
    # Cesty
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "aotcse_local.db"))
    MODELS_DIR = os.path.join(BASE_DIR, "models")
    
    # Lokální AI připojení (Ollama běží nativně na Windows, aplikace v Dockeru)
    # Používáme host.docker.internal k dosažení na GPU hostitele
    OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434/api/generate")
    LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.5:4b")
    
    # Integrace pro auto_publisher
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
    
    # Hardwarová optimalizace pro CPU (Intel Xeon E3-1535M)
    OMP_NUM_THREADS = os.environ.get("OMP_NUM_THREADS", "4")

# Export jediné sdílené instance konfigurace
settings = Config()
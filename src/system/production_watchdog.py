import os
import time
import logging
import psutil
import requests
from datetime import datetime, timedelta
from src.config import settings

# Nastavení logování - zapisuje do souboru i do konzole
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] [Watchdog] %(message)s',
    handlers=[
        logging.FileHandler("production_health.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

class ProductionWatchdog:
    def __init__(self):
        self.temp_media_dir = os.path.join(settings.BASE_DIR, "input_media")
        self.output_media_dir = os.path.join(settings.BASE_DIR, "output_media")
        self.ollama_url = settings.OLLAMA_URL.replace("/api/generate", "/api/tags")
        # Doba v hodinách, po které se dočasné soubory považují za staré
        self.retention_hours = 24 

    def _check_system_resources(self):
        """Monitoruje zátěž procesoru a operační paměti."""
        cpu_usage = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        
        logging.info(f"Metriky - CPU: {cpu_usage}% | RAM: {ram.percent}% (Dostupno: {ram.available / (1024**3):.2f} GB)")
        
        if ram.percent > 90:
            logging.warning("KRITICKÉ: Využití RAM překročilo 90%. Zvažte restart systému nebo vyčištění paměti.")

    def _check_ollama_health(self):
        """Testuje, zda Ollama engine na hostitelském stroji stále odpovídá."""
        try:
            response = requests.get(self.ollama_url, timeout=5)
            if response.status_code == 200:
                logging.info("Ollama API: Připojeno a stabilní.")
            else:
                logging.error(f"Ollama API vrátilo nestandardní kód: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logging.error(f"KRITICKÉ: Ollama API neodpovídá! Detail: {e}")

    def _cleanup_old_files(self, directory: str):
        """
        Prochází složky s médii a maže soubory starší než definovaný limit (24 hodin),
        aby zabránil zaplnění disku během nepřetržitého provozu.
        """
        if not os.path.exists(directory):
            return

        now = time.time()
        cutoff_time = now - (self.retention_hours * 3600)
        deleted_count = 0
        freed_space = 0

        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            
            # Bezpečnostní kontrola, aby se nemazaly složky, jen soubory
            if os.path.isfile(filepath):
                file_mod_time = os.path.getmtime(filepath)
                
                if file_mod_time < cutoff_time:
                    try:
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        deleted_count += 1
                        freed_space += file_size
                    except Exception as e:
                        logging.error(f"Nepodařilo se smazat soubor {filepath}: {e}")

        if deleted_count > 0:
            logging.info(f"Úklid ({directory}): Smazáno {deleted_count} souborů. Uvolněno {freed_space / (1024**2):.2f} MB.")

    def run_stress_test_and_monitor(self):
        """Hlavní smyčka produkčního hlídače."""
        logging.info("==================================================")
        logging.info("Startuji AOTCSE Production Watchdog (Fáze 22)")
        logging.info(f"Ochrana disku: Mažu média starší než {self.retention_hours} hodin.")
        logging.info("==================================================")
        
        while True:
            try:
                logging.info("--- Provádím cyklus kontroly systému ---")
                
                # 1. Kontrola zdrojů (RAM, CPU)
                self._check_system_resources()
                
                # 2. Kontrola spojení s mozkem (Ollama)
                self._check_ollama_health()
                
                # 3. Údržba diskového prostoru
                self._cleanup_old_files(self.temp_media_dir)
                self._cleanup_old_files(self.output_media_dir)
                
                logging.info("--- Cyklus dokončen. Další kontrola za 60 minut. ---")
                
                # Uspání na 1 hodinu (3600 vteřin) pro dlouhodobý běh
                time.sleep(3600)
                
            except KeyboardInterrupt:
                logging.info("Watchdog byl manuálně ukončen.")
                break
            except Exception as e:
                logging.error(f"Neočekávaná chyba ve Watchdog smyčce: {e}")
                time.sleep(300) # Počkat 5 minut před dalším pokusem při selhání

if __name__ == "__main__":
    watchdog = ProductionWatchdog()
    watchdog.run_stress_test_and_monitor()
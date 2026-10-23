import os
import datetime

# Kompletní seznam všech souborů, které tvoří novou AOTCSE architekturu
EXPECTED_FILES = [
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    ".env",
    "start_aotcse.bat",
    "src/config.py",
    "src/db/database_upgrade.py",
    "src/db/vector_memory.py",
    "src/ingestion/scraper.py",
    "src/processing/tts_engine.py",
    "src/processing/whisper_engine.py",
    "src/rendering/media_engine.py",
    "src/ui/app.py",
    "src/ui/auto_publisher.py"
]

def check_system():
    print("=" * 60)
    print("Spouštím kontrolu integrity AOTCSE (Fáze 17)")
    print("=" * 60)
    
    missing_or_empty = []
    
    for file_path in EXPECTED_FILES:
        if not os.path.exists(file_path):
            print(f"[CHYBA] Chybí soubor: {file_path}")
            missing_or_empty.append(f"CHYBÍ: {file_path}")
        else:
            size_bytes = os.path.getsize(file_path)
            
            # Pokud je soubor extrémně malý, pravděpodobně se kód zkopíroval špatně
            if size_bytes < 20:
                print(f"[VAROVÁNÍ] Soubor je podezřele malý (prázdný?): {file_path} ({size_bytes} bajtů)")
                missing_or_empty.append(f"PRÁZDNÝ/NEÚPLNÝ: {file_path} ({size_bytes} bajtů)")
            else:
                # Spočítáme řádky pro hrubou kontrolu
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = sum(1 for _ in f)
                    print(f"[OK] {file_path} (Velikost: {size_bytes} bajtů, Řádků: {lines})")
                except Exception as e:
                    print(f"[CHYBA] Nelze přečíst {file_path}: {e}")
                    missing_or_empty.append(f"NELZE PŘEČÍST: {file_path}")

    print("\n" + "=" * 60)
    
    if not missing_or_empty:
        print("VŠECHNO JE V POŘÁDKU! Všechny soubory jsou na svých místech a nejsou prázdné.")
    else:
        print(f"POZOR! Nalezeno {len(missing_or_empty)} problémů.")
        log_file = "chybejici_soubory.log"
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"--- AOTCSE Audit Log ({datetime.datetime.now()}) ---\n")
            for item in missing_or_empty:
                f.write(f"{item}\n")
        print(f"Detaily byly zapsány do souboru: {log_file}")
        print("Zkopírujte mi prosím obsah tohoto logu a já vám chybějící soubory rovnou vygeneruji.")

if __name__ == "__main__":
    check_system()
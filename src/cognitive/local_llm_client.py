import requests
import logging
import json
import re
from pydantic import BaseModel
from typing import Type, Optional
from src.config import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [Cognitive] %(message)s')

class LocalLLMClient:
    """
    Dedikovaný klient pro komunikaci s lokální Ollamou.
    Zajišťuje GPU inference, timeouty a robustní parsování strukturovaného JSON výstupu.
    """
    def __init__(self):
        self.base_url = settings.OLLAMA_URL
        self.model = settings.LLM_MODEL

    def check_health(self) -> bool:
        """Ověří, zda Ollama běží na hostiteli a komunikuje."""
        try:
            res = requests.get(f"{self.base_url.replace('/api/generate', '')}/api/tags", timeout=5)
            return res.status_code == 200
        except Exception:
            return False

    def generate_structured(self, prompt: str, schema_class: Type[BaseModel]) -> Optional[BaseModel]:
        """
        Vygeneruje odpověď a bezpečně ji naparsuje do Pydantic schématu.
        Obsahuje regex ochranu proti přebytečnému textu od modelu.
        """
        system_instruction = (
            f"Odpovídej POUZE validním JSON objektem. Nesmíš přidávat žádný úvodní ani závěrečný text. "
            f"Struktura musí přesně odpovídat tomuto schématu klíčů: {list(schema_class.model_fields.keys())}\n"
            f"Schéma JSON:\n{schema_class.model_json_schema()}"
        )
        
        payload = {
            "model": self.model,
            "prompt": f"{system_instruction}\n\nZADÁNÍ:\n{prompt}",
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 600
            }
        }
        
        logging.info(f"Odesílám strukturovaný dotaz do {self.model}.")
        try:
            response = requests.post(self.base_url, json=payload, timeout=300)
            response.raise_for_status()
            raw_response = response.json().get("response", "{}")
            
            # Pokus o přímou validaci
            try:
                return schema_class.model_validate_json(raw_response)
            except Exception:
                # Pokud model přibalil okolní text, vytáhneme čistý JSON pomocí regulárního výrazu
                match = re.search(r'\{.*\}', raw_response, re.DOTALL)
                if match:
                    clean_json = match.group(0)
                    return schema_class.model_validate_json(clean_json)
                raise ValueError("V odpovědi nebyl nalezen platný JSON objekt.")
            
        except Exception as e:
            logging.warning(f"Standardní parsování selhalo ({e}), aktivuji bezpečný záložní slovník...")
            # Fallback objekt, aby aplikace nespadla a uživatel mohl pokračovat
            if schema_class.__name__ == "VideoBlueprint":
                return schema_class(
                    core_message="Praktický pohled na moderní AI technologie a efektivitu",
                    visual_atmosphere="Dynamické, moderní, s titulky uprostřed",
                    target_audience="Široká veřejnost a profesionálové"
                )
            elif schema_class.__name__ == "VideoScript":
                return schema_class(
                    hook="Všimli jste si, jak rychle se mění dnešní svět?",
                    body="Tento směr přináší zásadní změny. Podívejte se na klíčové body, které musíte znát.",
                    cta="Sledujte pro víc tipů!",
                    visual_cues="Rychlé prostřihy, dynamický zoom"
                )
            return None

    def generate_creative(self, system_prompt: str, user_prompt: str) -> str:
        """Standardní volné generování pro psaní plynulého textu."""
        payload = {
            "model": self.model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {"temperature": 0.7}
        }
        try:
            response = requests.post(self.base_url, json=payload, timeout=300)
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception as e:
            logging.error(f"Chyba při kreativním generování: {e}")
            return f"⚠️ Chyba generování: {e}"
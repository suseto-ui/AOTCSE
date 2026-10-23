import os
import subprocess
import logging
import urllib.request
from src.config import settings

logging.basicConfig(level=logging.INFO)

class OfflineTTS:
    def __init__(self):
        self.models_dir = settings.MODELS_DIR
        os.makedirs(self.models_dir, exist_ok=True)
        self.model_path = os.path.join(self.models_dir, "cs_CZ-karel-medium.onnx")
        self.json_path = self.model_path + ".json"
        self._ensure_models()

    def _ensure_models(self):
        if not os.path.exists(self.model_path):
            logging.info("Stahuji lokální TTS model (Piper - cs_CZ Karel)... Bude to trvat jen chvíli.")
            try:
                urllib.request.urlretrieve("https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/cs/cs_CZ/karel/medium/cs_CZ-karel-medium.onnx", self.model_path)
                urllib.request.urlretrieve("https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/cs/cs_CZ/karel/medium/cs_CZ-karel-medium.onnx.json", self.json_path)
                logging.info("Model úspěšně stažen.")
            except Exception as e:
                logging.error(f"Chyba při stahování modelu: {e}")

    def generate(self, text: str, output_path: str) -> str:
        logging.info("Generuji 100% offline AI dabing přes Piper TTS...")
        cmd = ["python", "-m", "piper", "-m", self.model_path, "--output_file", output_path]
        try:
            process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate(input=text.encode('utf-8'))
            if process.returncode != 0:
                logging.error(f"Piper TTS chyba: {stderr.decode('utf-8')}")
            return output_path
        except Exception as e:
            logging.error(f"Chyba při generování audia: {e}")
            return None

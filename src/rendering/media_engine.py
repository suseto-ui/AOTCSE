import os
import logging
from moviepy.editor import VideoFileClip, vfx, AudioFileClip, CompositeAudioClip
from src.processing.tts_engine import OfflineTTS

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [Media] %(message)s')

class MediaPipeline:
    def __init__(self, temp_dir="input_media", output_dir="output_media"):
        """
        Inicializuje mediální engine a vytvoří pracovní složky.
        """
        self.temp_dir = temp_dir
        self.output_dir = output_dir
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        self.tts = OfflineTTS()
        logging.info("MediaPipeline inicializována. 100% Lokální režim aktivní (Fáze 20).")

    def generate_voiceover(self, text: str) -> str:
        """
        Veřejná metoda pro spuštění generování lokálního AI dabingu.
        """
        audio_path = os.path.join(self.temp_dir, "voiceover.wav")
        result_path = self.tts.generate(text, audio_path)
        if not result_path:
            raise RuntimeError("Chyba při generování audia.")
        return result_path

    def extract_audio_from_video(self, video_path: str) -> str:
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video nenalezeno: {video_path}")
            
        output_audio_path = os.path.join(self.temp_dir, "extracted_audio.wav")
        logging.info("Extrahuji audio ze zdrojového videa...")
        
        try:
            clip = VideoFileClip(video_path)
            if clip.audio is None:
                clip.close()
                raise ValueError("Video neobsahuje žádnou zvukovou stopu.")
                
            clip.audio.write_audiofile(output_audio_path, logger=None)
            clip.close()
            return output_audio_path
        except Exception as e:
            raise RuntimeError(f"Chyba při extrakci audia: {e}")

    def _crop_to_vertical(self, clip):
        """
        Dynamicky ořízne video na poměr stran 9:16 (Shorts/Reels/TikTok) na střed.
        """
        w, h = clip.size
        target_ratio = 9 / 16.0
        current_ratio = w / float(h)

        if current_ratio > target_ratio:
            # Video je příliš široké, ořízneme boky
            new_w = int(h * target_ratio)
            x_center = w / 2
            clip = clip.crop(x1=x_center - new_w/2, y1=0, x2=x_center + new_w/2, y2=h)
        else:
            # Video je příliš vysoké, ořízneme vršek/spodek
            new_h = int(w / target_ratio)
            y_center = h / 2
            clip = clip.crop(x1=0, y1=y_center - new_h/2, x2=w, y2=y_center + new_h/2)
            
        return clip

    def render_final_video(self, background_video_path: str, audio_path: str) -> str:
        """
        Složí video a audio, ořízne na 9:16, nasmyčkuje B-Roll a aplikuje NVENC.
        """
        output_filename = f"final_{os.path.basename(background_video_path)}"
        output_path = os.path.join(self.output_dir, output_filename)
        
        if not os.path.exists(background_video_path):
            raise FileNotFoundError("Podkresové video nebylo nalezeno.")
            
        try:
            clip = VideoFileClip(background_video_path)
            voice_clip = AudioFileClip(audio_path)
            
            # 1. Ořez na 9:16 Vertikální formát
            stealth_clip = self._crop_to_vertical(clip)
            
            # 2. Stealth úprava: Mírné zrychlení pro zmatení algoritmů
            stealth_clip = stealth_clip.fx(vfx.speedx, 1.03)

            # 3. Looping: Pokud je B-Roll kratší než dabing, video se bude plynule opakovat
            if stealth_clip.duration < voice_clip.duration:
                logging.info("B-Roll je kratší než dabing. Aplikuji smyčku (looping).")
                stealth_clip = stealth_clip.fx(vfx.loop, duration=voice_clip.duration)
            else:
                # Oříznutí videa přesně na délku dabingu, pokud je video delší
                stealth_clip = stealth_clip.subclip(0, voice_clip.duration)
            
            # 4. Ducking efekt: ztlumit původní zvuk videa a přidat dabing
            if stealth_clip.audio:
                bg_audio = stealth_clip.audio.volumex(0.05)
                final_audio = CompositeAudioClip([bg_audio.set_duration(stealth_clip.duration), voice_clip.set_start(0)])
            else:
                final_audio = voice_clip

            stealth_clip = stealth_clip.set_audio(final_audio)

            logging.info("Spouštím hardwarově akcelerované renderování (Quadro M5000M / h264_nvenc)...")
            
            try:
                stealth_clip.write_videofile(
                    output_path,
                    codec="h264_nvenc",
                    audio_codec="aac",
                    fps=30,
                    logger=None
                )
            except Exception as nvenc_error:
                logging.warning(f"NVENC selhal ({nvenc_error}), fallback na CPU (libx264)...")
                stealth_clip.write_videofile(
                    output_path,
                    codec="libx264",
                    audio_codec="aac",
                    fps=30,
                    logger=None
                )
            
            # Explicitní uvolnění paměti - kritické pro stabilitu
            clip.close()
            stealth_clip.close()
            voice_clip.close()
            
            return output_path
            
        except Exception as e:
            raise RuntimeError(f"Kritická chyba při renderu: {str(e)}")
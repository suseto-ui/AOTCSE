from pydantic import BaseModel, Field

class TrendAnalysis(BaseModel):
    """
    Schéma pro reverzní inženýrství virálních trendů.
    nutí model rozebrat video na emoce a spouštěče.
    """
    trigger: str = Field(description="Hlavní psychologický spouštěč (např. FOMO, Status, Zvědavost)")
    emotion: str = Field(description="Cílová emoce diváka (např. Strach, Úžas, Nadšení)")
    hook_idea: str = Field(description="Návrh virálního háčku odvozeného z tohoto trendu (max 1 věta)")

class VideoBlueprint(BaseModel):
    """
    Nové schéma pro kompletní popis a produkční plán nového videa
    na základě analýzy statisticky nejlepšího trendu.
    """
    core_message: str = Field(description="Hlavní sdělení a hodnota pro diváka")
    visual_atmosphere: str = Field(description="Vizuální styl a nálada videa (např. temné, dynamické, edukativní)")
    target_audience: str = Field(description="Cílová skupina diváků")

class VideoScript(BaseModel):
    """
    Schéma pro finální výstup generátoru.
    Zajišťuje oddělení vizuálních instrukcí od textu pro dabing.
    """
    hook: str = Field(description="Zahajovací věta (první 3 vteřiny videa)")
    body: str = Field(description="Hlavní hodnota / obsah videa pro TTS dabing")
    cta: str = Field(description="Výzva k akci na konci (Call to Action)")
    visual_cues: str = Field(description="Pokyny pro editora / podkres (co má být na obrazovce)")
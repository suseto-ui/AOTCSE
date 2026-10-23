@echo off
color 0D
echo ===================================================
echo Startuji AOTCSE Streamlit Cockpit (Lokalni AI)
echo Faze 15: Enterprise Architektura
echo ===================================================
echo.

echo [1/4] Kontrola pripojeni k lokální AI (Ollama)...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Ollama nebezi. Zkousim ji nastartovat na pozadi...
    start "Ollama AI Engine" /MIN ollama serve
    timeout /t 10 /nobreak > NUL
) else (
    echo [OK] Ollama je pripravena.
)
echo.

echo [2/4] Kontrola pripojeni k Dockeru...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Docker Desktop nebezi. Zkousim jej nastartovat...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo Cekam 25 vterin, nez Docker plne nabehne...
    timeout /t 25 /nobreak > NUL
) else (
    echo [OK] Docker je pripraven.
)
echo.

echo [3/4] Cisteni starych instanci a sestaveni kontejneru...
docker-compose down --remove-orphans >nul 2>&1
docker rm -f aotcse_streamlit >nul 2>&1

docker-compose up --build -d
if %errorlevel% neq 0 (
    echo.
    echo ===================================================
    echo KRITICKA CHYBA: Docker stale nebezi nebo selhal build!
    echo ===================================================
    pause
    exit /b
)
echo.

echo [4/4] Oteviram AOTCSE Cockpit v prohlizeci...
start http://localhost:8501

echo.
echo ===================================================
echo System uspesne bezi! Pro sledovani logu pouzijte:
echo docker logs -f aotcse_streamlit
echo ===================================================
pause
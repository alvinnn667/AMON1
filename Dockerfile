# Image de l'app pour un serveur (VPS Hetzner). Construite par docker compose (voir docker-compose.yml).
FROM python:3.12-slim

# Pas de fichiers .pyc, logs Python affichés immédiatement (docker compose logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed

WORKDIR /app

# 1. Dépendances d'abord : Docker garde cette couche en cache tant que requirements.txt ne change pas,
#    donc une mise à jour du code seul se reconstruit en quelques secondes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2. Modèle d'embeddings téléchargé pendant la construction (et pas au premier import de cours) :
#    l'app démarre prête, sans dépendre de Hugging Face à chaque redémarrage.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

# 3. Le code de l'app
COPY . .

# L'app tourne avec un utilisateur sans droits (jamais root dans le conteneur).
RUN useradd --create-home --uid 1000 coach && chown -R coach:coach /app
USER coach

EXPOSE 8501

# Docker vérifie toutes les 30 s que Streamlit répond ; sinon le conteneur est marqué « unhealthy ».
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]

# -----------------------------------------------------------
# Lex — UAE Claim Presubmission Validation
# Base: python:3.12-slim-bookworm + WeasyPrint system deps
# Engine: validator installed editable from vendor/ so that
#         its reference-data loader resolves docs/ correctly.
# -----------------------------------------------------------
FROM python:3.12-slim-bookworm

# --- System dependencies for WeasyPrint ---------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi-dev \
        shared-mime-info \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# --- uv for fast dependency installation --------------------
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# --- Validator engine (editable install) --------------------
# Vendored from ../ip-claim-validator via scripts/vendor-validator.sh.
# Editable so Path(__file__).parents[3] / "docs" resolves to
# /opt/ip-claim-validator/docs/ at runtime.
COPY vendor/ip-claim-validator /opt/ip-claim-validator
RUN uv pip install --system -e /opt/ip-claim-validator

# --- Lex application dependencies --------------------------
WORKDIR /app
COPY pyproject.toml /app/

# Install deps listed in pyproject.toml (except "validator" — already installed)
RUN uv pip install --system \
        "streamlit>=1.45,<2.0" \
        "pyyaml>=6.0,<7.0" \
        "openpyxl>=3.1,<4.0" \
        "weasyprint>=62.0" \
        "jinja2>=3.1,<4.0" \
        "supabase>=2.0,<3.0"

# --- Application source ------------------------------------
COPY app.py /app/
COPY src/   /app/src/
RUN uv pip install --system -e /app

# --- Non-root user -----------------------------------------
RUN groupadd --gid 1000 lex \
    && useradd --uid 1000 --gid lex --create-home lex \
    && chown -R lex:lex /app
USER lex

# --- Runtime ------------------------------------------------
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.address", "0.0.0.0", \
     "--server.port", "8501", \
     "--server.headless", "true"]

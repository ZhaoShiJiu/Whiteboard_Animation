import os
from dotenv import load_dotenv

load_dotenv()

# SAM Segmentation Model URL
# To host SAM 3 on your own Cloud Run endpoint, follow instructions in sam3-hosting/README.md
# SAM_API_URL = "https://sam3-app-1040077537378.us-east4.run.app/predict"
SAM_API_URL = os.getenv("SAM_API_URL", "")
SAM_API_TOKEN = os.getenv("SAM_API_TOKEN", "")

# ---------------------------------------------------------------------------
# All AI model configuration has moved to ai_gateway/gateway.yaml
# All tool modules now call: from ai_gateway import generate
# See docs/AI_Gateway_Implementation_Plan.md for the full architecture.
# ---------------------------------------------------------------------------

# SAM 3 Hosting & Deployment Guide

This directory contains the tools and configuration needed to deploy **Segment Anything Model 3 (SAM 3)** as a high-performance self-hosted API endpoint on **Google Cloud Run** using GPU acceleration.

---

## 📦 1. Download Model Weights

SAM 3 weights are hosted on Hugging Face and require requesting access before downloading.

### Step 1: Request Access on Hugging Face
1. Visit the [SAM 3 Hugging Face repository](https://huggingface.co/facebook/sam3).
2. Accept the licensing agreement and request access.

### Step 2: Download the Weights
Once access is approved:
1. Generate a **User Access Token** with `read` permissions from your [Hugging Face settings](https://huggingface.co/settings/tokens).
2. Authenticate locally:
   ```bash
   pip install huggingface_hub
   huggingface-cli login
   ```
3. Download the `sam3.pt` weight file (approx. 3.4 GB) and place it in the `sam3/model_wts` directory:
   ```bash
   # Download sam3.pt
   huggingface-cli download facebook/sam3 sam3.pt --local-dir sam3/model_wts
   ```
4. **Config JSON**: If you do not have `config.json` inside `sam3/model_wts/`, download it as well:
   ```bash
   huggingface-cli download facebook/sam3 config.json --local-dir sam3/model_wts
   ```

At this stage, your directory structure under `sam3-hosting/sam3/` should look like this:
```text
sam3/
├── model_wts/
│   ├── config.json  # Model configuration
│   └── sam3.pt      # 3.4GB checkpoint file
├── sam3/            # Model implementation code
└── server.py        # FastAPI server script
```

---

## 🐳 2. Docker Container Setup

The provided [Dockerfile](file:///d:/software-dev/storyboard-ai/sam3-hosting/Dockerfile) is fully configured for a GPU-optimized environment. 

### Key Docker Details:
- **Base Image**: Uses `pytorch/pytorch:2.7.0-cuda12.6-cudnn9-runtime` for CUDA execution compatibility.
- **Dependencies**: Installs compilation tools, OpenCV helper libraries, and Hugging Face dependencies.
- **Exposed Port**: Runs FastAPI using Uvicorn on port `8080`.

To build and test the container locally (requires a local GPU and [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)):
```bash
# Build the Docker image
docker build -t sam3-service:latest .

# Run the container locally with GPU access
docker run --gpus all -p 8080:8080 sam3-service:latest
```

---

## ☁️ 3. Deploy to GCP Cloud Run with GPU

Google Cloud Run now supports deploying containers with dedicated GPU acceleration (NVIDIA L4 GPUs). Follow these steps to deploy:

### Step 1: Authenticate with Google Cloud
Ensure your local `gcloud` CLI is logged in and configured to the correct project:
```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

### Step 2: Configure Artifact Registry
Create a Docker repository in Google Artifact Registry if you don't already have one:
```bash
# Choose a region that supports L4 GPUs, e.g., us-east4 or us-central1
gcloud artifacts repositories create storyboard-ai-repo \
    --repository-format=docker \
    --location=us-east4 \
    --description="Docker repository for SAM 3 model hosting"

# Authenticate Docker daemon to push to the registry
gcloud auth configure-docker us-east4-docker.pkg.dev
```

### Step 3: Build, Tag, and Push the Image
Tag the container image using the Artifact Registry path and push it:
```bash
# Tag the image
docker tag sam3-service:latest us-east4-docker.pkg.dev/YOUR_PROJECT_ID/storyboard-ai-repo/sam3-service:latest

# Push to Artifact Registry
docker push us-east4-docker.pkg.dev/YOUR_PROJECT_ID/storyboard-ai-repo/sam3-service:latest
```

*(Optional: Use Google Cloud Build to build directly on GCP if your local bandwidth is limited)*
```bash
gcloud builds submit --tag us-east4-docker.pkg.dev/YOUR_PROJECT_ID/storyboard-ai-repo/sam3-service:latest .
```

### Step 4: Deploy the Cloud Run Service with GPU
Deploy using the `gcloud beta run deploy` command to enable GPU allocations:
```bash
gcloud beta run deploy sam3-service \
    --image=us-east4-docker.pkg.dev/YOUR_PROJECT_ID/storyboard-ai-repo/sam3-service:latest \
    --gpu=1 \
    --gpu-type=nvidia-l4 \
    --cpu=4 \
    --memory=16Gi \
    --no-cpu-throttling \
    --port=8080 \
    --region=us-east4 \
    --allow-unauthenticated
```

> [!IMPORTANT]
> - **`--gpu-type=nvidia-l4`**: Allocates the NVIDIA L4 GPU instance.
> - **`--no-cpu-throttling`**: Essential prerequisite when configuring GPUs.
> - **`--cpu=4` & `--memory=16Gi`**: Minimum resources recommended to prevent out-of-memory crashes when serving SAM 3.

---

## 🔗 4. Update Application Configuration

Once Cloud Run completes the deployment, it will output a Service URL (e.g., `https://sam3-service-xxxx-xx.a.run.app`).

1. Open the configuration file [genai-pipeline/config.py](file:///d:/software-dev/storyboard-ai/genai-pipeline/config.py).
2. Locate the `SAM_API_URL` configuration line (Line 21).
3. Replace the endpoint URL with your newly deployed service endpoint URL, appending `/predict`:
   ```python
   # SAM Segmentation Model URL
   SAM_API_URL = "https://sam3-service-xxxx-xx.a.run.app/predict"
   ```
4. Verify by starting the pipeline:
   ```bash
   cd genai-pipeline
   python pipeline.py
   ```

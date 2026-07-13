# Use the lighter runtime image as the base
FROM pytorch/pytorch:2.7.0-cuda12.6-cudnn9-runtime

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies
# We add build-essential (gcc, etc.) so we can install pycocotools/decord
# We add libgl1/libglib for opencv
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# Install dependencies (First list)
RUN pip install \
    "timm>=1.0.17" \
    "numpy>=1.26" \
    "tqdm" \
    "ftfy==6.1.1" \
    "regex" \
    "iopath>=0.1.10" \
    "typing_extensions" \
    "huggingface_hub" \
    "fastapi" \
    "uvicorn" \
    "python-multipart"

# Install Notebooks/Vision extra dependencies
RUN pip install \
    matplotlib \
    jupyter \
    notebook \
    ipywidgets \
    ipycanvas \
    ipympl \
    pycocotools \
    decord \
    opencv-python \
    einops \
    scikit-image \
    scikit-learn

# Set the working directory
WORKDIR /app

# This creates /app/sam3/ and puts your code inside it
COPY ./sam3/sam3 ./sam3
COPY ./sam3/model_wts ./model_wts
COPY ./sam3/server.py ./

# Expose the port the app runs on
EXPOSE 80

# Command to run the application with Uvicorn
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
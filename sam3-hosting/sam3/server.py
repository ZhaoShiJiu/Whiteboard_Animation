import io
import base64
import torch
import numpy as np
from PIL import Image
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse

# --- SAM3 Imports ---
# Ensure your python environment can find these modules
from sam3.model_builder import build_sam3_image_model
from sam3.model.sam3_image_processor import Sam3Processor

# --- Configuration ---
CHECKPOINT_PATH = "./model_wts/sam3.pt"
device = "cuda" if torch.cuda.is_available() else "cpu"

# Global dictionary to hold model resources
ml_models = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Load the model once when the server starts.
    """
    print("Loading SAM3 Model... this may take a moment.")
    try:
        model = build_sam3_image_model(
            checkpoint_path=CHECKPOINT_PATH,
            load_from_HF=False,
            device=device,
        )
        processor = Sam3Processor(model)
        ml_models["processor"] = processor
        print("SAM3 Model loaded successfully.")
    except Exception as e:
        print(f"Failed to load model: {e}")
        raise e

    yield

    # Clean up resources if necessary
    ml_models.clear()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

app = FastAPI(lifespan=lifespan)

def process_sam3_inference(image: Image.Image, prompt: str):
    """
    Helper function to run the inference logic.
    """
    processor = ml_models["processor"]

    # 1. Set Image
    inference_state = processor.set_image(image)

    # 2. Set Text Prompt
    output = processor.set_text_prompt(state=inference_state, prompt=prompt)

    # 3. Extract Results
    # Output contains tensors, we need to convert them to standard Python types for JSON
    masks = output["masks"] # [N, 1, H, W]
    boxes = output["boxes"] # [N, 4]
    scores = output["scores"] # [N]

    # Convert to CPU and Numpy/List
    boxes_list = boxes.to("cpu").numpy().tolist()
    scores_list = scores.to("cpu").numpy().tolist()

    # Handling Masks:
    # Sending full mask arrays (H x W) via JSON is heavy.
    # Here, we will convert the first mask to a Base64 string so you can visualize it,
    # and return metadata for the rest.
    masks_np = masks.to("cpu").numpy().astype(np.uint8) * 255

    results = {
        "boxes": boxes_list,
        "scores": scores_list,
        "mask_shape": masks_np.shape,
        "masks_base64": []
    }

    # Convert each mask to a base64 string (optional, helpful for debugging)
    for i in range(masks_np.shape[0]):
        mask_im = Image.fromarray(masks_np[i].squeeze())
        buff = io.BytesIO()
        mask_im.save(buff, format="PNG")
        img_str = base64.b64encode(buff.getvalue()).decode("utf-8")
        results["masks_base64"].append(img_str)

    return results

@app.post("/predict")
async def predict(
    prompt: str = Form(...),
    file: UploadFile = File(None),
    image_path: str = Form(None)
):
    """
    Endpoint that accepts a text prompt and EITHER:
    1. An uploaded image file.
    2. A file path string to an image on the server.
    """
    if "processor" not in ml_models:
        raise HTTPException(status_code=503, detail="Model is not loaded.")

    image = None

    # Logic to handle Input Source
    try:
        if file:
            # Case 1: Client uploaded an image file
            contents = await file.read()
            image = Image.open(io.BytesIO(contents))
        elif image_path:
            # Case 2: Client provided a server-side path
            try:
                image = Image.open(image_path)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"Image not found at path: {image_path}")
        else:
            raise HTTPException(status_code=400, detail="Either 'file' or 'image_path' must be provided.")
        print("Image Shape: ", image.size)

        # Run Inference
        result = process_sam3_inference(image, prompt)
        return JSONResponse(content=result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ping")
async def health_check():
    if "processor" in ml_models:
        return {"status": "Healthy"}
    else:
        raise HTTPException(status_code=503, detail="Model loading")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

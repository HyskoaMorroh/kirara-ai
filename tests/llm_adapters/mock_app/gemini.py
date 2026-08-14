from fastapi import APIRouter, Body, Query, Depends
from fastapi.exceptions import HTTPException
from . import REFERENCE_VECTOR
from .app import AUTH_KEY
from .models.gemini import BatchEmbeddingRequest, ChatRequest


async def gemini_authenticate(key: str = Query(...)) -> None:
    if key != AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid authentication key")

router = APIRouter(tags=["gemini"], dependencies=[Depends(gemini_authenticate)])

@router.post("/models/{model}:generateContent")
async def chat(model: str, request:ChatRequest = Body()) -> dict:
    # 极度简略版本，gemini api 的返回实例就是依托
    if request.tools is None:
        return {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "mock_response"}
                    ]
                },
                "finishReason": "STOP",
            }],
            "usageMetadata": {
                "totalTokenCount": 114,
                "promptTokenCount": 514,
                "cachedContentTokenCount": 1919
            },
            "modelVersion": "mock_chat"
        }
    else:
        # 还没想好，不想做适配了。交给后人的智慧
        return {}

@router.post("/models/{model}:batchEmbedContents")
async def batch_embed_contents(model: str, _: BatchEmbeddingRequest = Body()) -> dict:
    return {
        "embeddings": [
            {
                "values": REFERENCE_VECTOR
            }
        ]
    }
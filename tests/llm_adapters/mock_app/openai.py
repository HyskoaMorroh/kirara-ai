from fastapi import APIRouter, Body

from . import REFERENCE_VECTOR
from .models.openai import ChatRequest, EmbeddingRequest

router = APIRouter(tags=["openai"])


@router.post("/chat/completions")
async def completions(request: ChatRequest = Body(...)) -> dict:
    return {
        "id": "chatcmpl-B9MBs8CjcvOU2jLn4n570S5qMJKcT",
        "object": "chat.completion",
        "created": 1741569952,
        "model": "mock_chat",
        "choices": [
            {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "mock_response",
                "refusal": None,
                "annotations": []
            },
            "logprobs": None,
            "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 19,
            "completion_tokens": 10,
            "total_tokens": 29,
            "prompt_tokens_details": {
            "cached_tokens": 0,
            "audio_tokens": 0
            },
            "completion_tokens_details": {
            "reasoning_tokens": 0,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0
            }
        },
        "service_tier": "default"
    }


@router.post("/embeddings")
async def embeddings(request: EmbeddingRequest = Body(...)) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "embedding": REFERENCE_VECTOR,
                "index": 0
            }
        ],
        "model": "mock_embedding",
        "usage": {
            "prompt_tokens": 8,
            "total_tokens": 8
        }
    }
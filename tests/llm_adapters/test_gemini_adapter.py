from kirara_ai.plugins.llm_preset_adapters.gemini_adapter import GeminiAdapter, GeminiConfig
from kirara_ai.llm.format.embedding import LLMEmbeddingRequest, LLMEmbeddingResponse
from kirara_ai.llm.format.message import LLMChatTextContent, LLMChatImageContent, LLMChatMessage
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Usage

import pytest
from typing import cast

from .mock_app import AUTH_KEY, GEMINI_ENDPOINT, REFERENCE_VECTOR

class TestGeminiAdapter:
    @pytest.fixture(scope="class")
    def gemini_adapter(self, mock_media_manager, mock_tracer) -> GeminiAdapter:
        config = GeminiConfig(
            api_key=AUTH_KEY,
            api_base=GEMINI_ENDPOINT
        )
        adapter = GeminiAdapter(config)
        adapter.media_manager = mock_media_manager
        adapter.backend_name = "gemini"
        adapter.tracer = mock_tracer
        return adapter

    def test_chat(self, gemini_adapter):
        req = LLMChatRequest(
            messages=[LLMChatMessage(
                content=[LLMChatTextContent(text="hello world")],
                role="user"
            )],
            model="mock_chat"
        )

        response = gemini_adapter.chat(req)
        assert isinstance(response, LLMChatResponse)
        print (response.message.content)
        assert isinstance(response.message.content[0], LLMChatTextContent)
        content = cast(LLMChatTextContent, response.message.content[0])
        assert content.text == "mock_response"
        assert isinstance(response.usage, Usage)
        assert response.usage.total_tokens == 114
        assert response.usage.prompt_tokens == 514
        assert response.usage.cached_tokens == 1919
        assert response.usage.completion_tokens == 0

    def test_embed(self, gemini_adapter: GeminiAdapter):
        req = LLMEmbeddingRequest(
            inputs=[
                LLMChatTextContent(text="hello world", type="text")
            ],
            model = "mock_embedding"
        )
        
        response = gemini_adapter.embed(req)
        assert isinstance(response, LLMEmbeddingResponse)
        assert response.vectors[0] == REFERENCE_VECTOR

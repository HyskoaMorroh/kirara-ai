from kirara_ai.llm.adapter import LLMBackendAdapter


class LLMAdapterEvent:
    def __init__(self, adapter: LLMBackendAdapter, backend_name: str):
        self.adapter = adapter
        self.backend_name = backend_name
        
    def __repr__(self):
        return f"{self.__class__.__name__}(adapter={self.adapter}, backend_name={self.backend_name})"

class LLMAdapterLoaded(LLMAdapterEvent):
    pass

class LLMAdapterUnloaded(LLMAdapterEvent):
    pass



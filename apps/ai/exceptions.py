class ProviderError(Exception):
    def __init__(self, message: str, provider: str, recoverable: bool = True):
        self.message = message
        self.provider = provider
        self.recoverable = recoverable
        super().__init__(f"[{provider}] {message}")

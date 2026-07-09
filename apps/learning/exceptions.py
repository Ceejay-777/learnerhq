class GenerationError(Exception):
    def __init__(self, message: str, phase: str, recoverable: bool = True):
        self.message = message
        self.phase = phase
        self.recoverable = recoverable
        super().__init__(f"[{phase}] {message}")

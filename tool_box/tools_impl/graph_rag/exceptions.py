class GraphRAGError(Exception):
    """Graph RAG related exception"""

    def __init__(self, message: str, *, code: str = "graph_rag_error"):
        super().__init__(message)
        self.code = code
        self.message = message

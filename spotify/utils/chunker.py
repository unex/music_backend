class Chunker:
    def __init__(self, chunked, chunk_size) -> None:
        self.chunked = chunked
        self.chunk_size = chunk_size

    def __iter__(self):
        self.range = iter(range(0, len(self.chunked), self.chunk_size))
        return self

    def __next__(self):
        i = next(self.range)
        return self.chunked[i : i + self.chunk_size]

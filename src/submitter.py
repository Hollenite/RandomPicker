from typing import Protocol


class Submitter(Protocol):
    def submit(self, question_id: int) -> bool: ...

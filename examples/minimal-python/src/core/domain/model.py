"""Domain model: pure, framework-free."""


class Invoice:
    def __init__(self, row):
        self.row = row

    @property
    def total(self) -> float:
        return float(self.row.get("total", 0.0))

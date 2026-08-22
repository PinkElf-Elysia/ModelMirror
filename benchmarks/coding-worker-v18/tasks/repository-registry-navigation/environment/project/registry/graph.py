class AliasCycleError(ValueError):
    def __init__(self, path: tuple[str, ...]) -> None:
        self.path = path
        super().__init__('alias cycle: ' + ' -> '.join(path))


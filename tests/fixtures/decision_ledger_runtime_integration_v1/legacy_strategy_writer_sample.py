class Sample:
    _decision_log_paths = []

    def _write_decision(self, payload):
        for path in self._decision_log_paths:
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(str(payload) + "\n")
                return
            except Exception:
                continue

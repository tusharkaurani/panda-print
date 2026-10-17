.PHONY: dev test docker
PY := .venv/bin/python

.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.txt

# Run the whole UI with fake data: http://localhost:8095
dev: .venv/bin/python
	MOCK=1 CONFIG_DIR=config $(PY) -m app

test: .venv/bin/python
	$(PY) -m pytest

docker:
	docker build -t panda-print .

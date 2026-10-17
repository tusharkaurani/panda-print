.PHONY: dev test docker
PY := .venv/bin/python

.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.txt

# Run the real app against your network: http://localhost:8095 (data in .local/data)
dev: .venv/bin/python
	DATA_DIR=.local/data CONFIG_DIR=config $(PY) -m app

test: .venv/bin/python
	$(PY) -m pytest

docker:
	docker build -t panda-print .

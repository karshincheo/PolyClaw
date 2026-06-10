.PHONY: demo web test lint

demo:
	python3 run_selector.py --input data/sample_markets.json --pretty

web:
	polyclaw web --host 127.0.0.1 --port 8000

test:
	pytest -q

lint:
	ruff check .

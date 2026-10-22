PY ?= .venv/bin/python

.PHONY: venv boundaries data test dev build

venv:            ## create the Python env (uses uv; any python 3.11+ works)
	uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r pipeline/requirements.txt

boundaries:      ## fetch PSGC barangay/city boundaries into cache/ (slow, cached)
	$(PY) -m pipeline.fetch_boundaries

data: boundaries ## file raw/bir/_inbox, parse BIR schedules, rebuild public/data/ and public/og.png
	$(PY) -W ignore -m pipeline.inventory
	$(PY) -W ignore -m pipeline.parse_bir
	$(PY) -W ignore -m pipeline.build
	$(PY) -W ignore -m pipeline.streets --all
	$(PY) -m pipeline.transport
	$(PY) -W ignore -m pipeline.condos
	$(PY) -m pipeline.report_staleness
	$(PY) -m pipeline.og_image

test:
	$(PY) -m pytest pipeline/tests -q

dev:
	npm run dev

build:
	npm run build

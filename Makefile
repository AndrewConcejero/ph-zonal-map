PY ?= .venv/bin/python

.PHONY: venv boundaries data test dev build

venv:            ## create the Python env (uses uv; any python 3.11+ works)
	uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r pipeline/requirements.txt

boundaries:      ## fetch PSGC barangay/city boundaries into cache/ (slow, cached)
	$(PY) -m pipeline.fetch_boundaries

data: boundaries ## rebuild public/data/zonal.geojson + meta.json from raw/ and data/
	$(PY) -m pipeline.build

test:
	$(PY) -m pytest pipeline/tests -q

dev:
	npm run dev

build:
	npm run build

PY ?= .venv/bin/python

.PHONY: venv boundaries data test dev build

venv:            ## create the Python env (uses uv; any python 3.11+ works)
	uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r pipeline/requirements.txt

boundaries:      ## fetch PSGC barangay/city boundaries into cache/ (slow, cached)
	$(PY) -m pipeline.fetch_boundaries

data: boundaries ## rebuild public/data/ (zonal.geojson, cities.geojson, meta.json) and public/og.png
	$(PY) -m pipeline.build
	$(PY) -m pipeline.og_image

test:
	$(PY) -m pytest pipeline/tests -q

dev:
	npm run dev

build:
	npm run build

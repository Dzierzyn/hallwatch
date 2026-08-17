PY := .venv/bin/python

# Pas i szelki: nawet gdy editable install nie zadziala (patrz fix-pth ponizej),
# PYTHONPATH sprawia, ze pakiet jest importowalny z repo.
export PYTHONPATH := src

.PHONY: install fix-pth test run probe scan zones wake prune clean

install:
	uv venv --python 3.12
	uv pip install --python $(PY) -e .
	$(MAKE) fix-pth
	@echo "\nGotowe. Sprawdz: make test"

# macOS + uv + Python >= 3.12: uv ustawia na plikach .pth flage UF_HIDDEN,
# a site.py od 3.12 celowo POMIJA ukryte .pth. Efekt: editable install
# instaluje sie bez bledu, ale 'import hallwatch' nie dziala.
# Objaw: ModuleNotFoundError mimo 'uv pip install -e .' zakonczonego sukcesem.
fix-pth:
	@chflags nohidden .venv/lib/python3.12/site-packages/*.pth 2>/dev/null || true
	@ls -lO .venv/lib/python3.12/site-packages/*.pth 2>/dev/null | grep -q hidden \
		&& echo "UWAGA: pliki .pth nadal ukryte - polegaj na PYTHONPATH=src" \
		|| echo "pliki .pth widoczne dla site.py"

test:
	$(PY) -m hallwatch selftest

run:
	$(PY) -m hallwatch run

probe:
	$(PY) -m hallwatch probe

scan:
	$(PY) -m hallwatch scan

zones:
	$(PY) -m hallwatch zones

wake:
	$(PY) -m hallwatch wake

prune:
	$(PY) -m hallwatch prune

clean:
	rm -rf data/selftest data/ondemand
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

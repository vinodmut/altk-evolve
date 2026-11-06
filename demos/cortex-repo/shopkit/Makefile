PYTHON ?= python3

.PHONY: generate migration check test

generate:
	$(PYTHON) tools/generate.py

migration:
	$(PYTHON) tools/migrate.py new $(NAME)

check:
	$(PYTHON) tools/check.py

test:
	./scripts/test

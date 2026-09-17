.PHONY: setup build validate clean

setup:
	python -m pip install --require-hashes -r requirements.lock

build:
	python scripts/build.py

validate: build
	python scripts/validate.py

clean:
	python -c "from pathlib import Path; [p.unlink() for p in Path('dist').glob('Samuel-Andrade-Resume-*') if p.is_file()]"

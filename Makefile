VENV := $(CURDIR)/.venv
export PATH := $(VENV)/bin:$(PATH)

.PHONY: setup plugin dev test dev-vault obsidian

setup:          ## venv with node, SingleFile, the sidecar (editable, with GLiNER) and plugin packages
	test -d $(VENV) || uv venv -q --python 3.12 $(VENV)
	test -x $(VENV)/bin/node || { uv pip install -q --python $(VENV) nodeenv && $(VENV)/bin/nodeenv -p --node=lts -q; }
	uv pip install -q --python $(VENV) -e 'sidecar[dev,entities]'
	npm install -g --silent single-file-cli@2.18.5
	cd plugin && npm ci

plugin:         ## type-check and build plugin/main.js
	cd plugin && npm run build

dev:            ## rebuild the plugin on every change
	cd plugin && npm run dev

test:           ## plugin (vitest) and sidecar (pytest) tests
	cd plugin && npm test
	python -m pytest sidecar -q

dev-vault: plugin  ## a scratch vault in dev-vault/ with the plugin symlinked
	scripts/new-vault.sh dev-vault --link

obsidian:       ## open dev-vault/ in its own Obsidian profile (DevTools on port 9333)
	scripts/dev-obsidian.sh

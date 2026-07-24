.PHONY: install check dev infra-up infra-down smoke
install:
	corepack enable
	corepack pnpm install --frozen-lockfile
	python3.12 -m venv .venv
	.venv/bin/pip install -e '.[dev]'
check:
	corepack pnpm check
dev:
	corepack pnpm dev
infra-up:
	corepack pnpm infra:up
infra-down:
	corepack pnpm infra:down
smoke:
	corepack pnpm smoke

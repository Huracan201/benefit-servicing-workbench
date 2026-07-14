# BenefitServicing Workbench — developer entry points (specs/21 §21.5).
#
# `make help` lists targets. The demo target shells out to the Firebase Emulator Suite via
# firebase-tools (needs Java 21 + Node 20 + Python 3.12 with deps). See
# firebase/emulator/README.md for the full prereqs.

PROJECT         := demo-benefitservicing-workbench
FIREBASE_CONFIG := firebase/firebase.json

.DEFAULT_GOAL := help
.PHONY: help demo test-core test-frontend

help: ## List the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

demo: ## Bring up the FULL stack locally (emulator + API + workbench + seed); Ctrl-C to stop
	firebase emulators:exec --project=$(PROJECT) --config $(FIREBASE_CONFIG) \
		"bash infrastructure/scripts/demo-up.sh"

test-core: ## Run the framework-free safety-critical core tests (offline, no deps needed)
	cd backend && python -m unittest discover -s common/tests -p 'test_*.py' -t .

test-frontend: ## Run the frontend unit tests (needs `npm install` in frontend/ first)
	cd frontend && npm test

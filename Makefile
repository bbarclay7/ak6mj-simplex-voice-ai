CONDA_ENV   := aioc-bot
CONDA_PREFIX := $(shell conda run -n $(CONDA_ENV) python -c "import sys; print(sys.prefix)")
PYTHON       := $(CONDA_PREFIX)/bin/python
PIP          := $(CONDA_PREFIX)/bin/pip

.PHONY: setup install download-models run run-online dry-run dry-run-online dry-run-claude claude monitor clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create conda env + install deps
	conda create -n $(CONDA_ENV) python=3.12 -y
	$(PIP) install -r requirements.txt
	@echo ""
	@echo "Setup complete. Before running:"
	@echo "  1. ollama serve"
	@echo "  2. ollama pull llama3.1:8b"

install: ## Update deps in existing env
	$(PIP) install -r requirements.txt

download-models: ## Download all models (STT/TTS via HuggingFace, LLM via Ollama) for offline use
	HF_HUB_OFFLINE=0 $(PYTHON) download_models.py

run: ## Run the bot (offline: models must already be cached)
	$(PYTHON) main.py

run-online: ## Run the bot with HF downloads enabled (use to fetch models on first run)
	HF_HUB_OFFLINE=0 $(PYTHON) main.py

claude: ## Run the bot with Claude API + web search (requires ANTHROPIC_API_KEY)
	LLM_MODE=claude $(PYTHON) main.py

dry-run: ## Dry-run with Ollama (system mic/speakers, no PTT)
	$(PYTHON) main.py --dry-run

dry-run-online: ## Dry-run with HF downloads enabled (use to fetch models on first run)
	HF_HUB_OFFLINE=0 $(PYTHON) main.py --dry-run

dry-run-claude: ## Dry-run with Claude API (system mic/speakers, no PTT)
	LLM_MODE=claude $(PYTHON) main.py --dry-run

monitor: ## Show live audio levels (calibrate VOX threshold)
	$(PYTHON) main.py --dry-run --monitor

clean: ## Remove logs
	rm -rf logs/*.wav logs/*.log

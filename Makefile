.PHONY: backup-labeled-data

DATASET_DIR ?= data/learned_extraction/raw
BACKUP_DIR ?= data/learned_extraction/backups

backup-labeled-data:
	uv run python -m modeling.dom_extractor.commands.backup_labeled_data --dataset-dir "$(DATASET_DIR)" --output-dir "$(BACKUP_DIR)"

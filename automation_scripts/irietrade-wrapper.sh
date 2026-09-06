#!/usr/bin/env bash
# Store this file at: ~/.local/bin/irietrade-wrapper.sh
set -euo pipefail

# Source environment file if it exists
if [[ -f "${HOME}/.config/irietrade/env" ]]; then
    source "${HOME}/.config/irietrade/env"
fi

# Change to working directory
PROJECT_DIR="${HOME}/Projects 2026/trading_bot"
cd "${PROJECT_DIR}"

# Activate virtual environment
source "${PROJECT_DIR}/venv/bin/activate"

# Run the launcher script
exec "${HOME}/.local/bin/irietrade-start"

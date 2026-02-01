#!/bin/bash
# Setup script for Journal - Local LLM TUI
# Installs Ollama and pulls the recommended model

set -e

echo "==================================="
echo "Journal Setup Script"
echo "==================================="
echo

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Ollama is installed
check_ollama() {
    if command -v ollama &> /dev/null; then
        echo -e "${GREEN}✓ Ollama is already installed${NC}"
        ollama --version
        return 0
    else
        return 1
    fi
}

# Install Ollama
install_ollama() {
    echo -e "${YELLOW}Installing Ollama...${NC}"
    curl -fsSL https://ollama.com/install.sh | sh
    echo -e "${GREEN}✓ Ollama installed successfully${NC}"
}

# Check if Ollama server is running
check_server() {
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Ollama server is running${NC}"
        return 0
    else
        return 1
    fi
}

# Start Ollama server
start_server() {
    echo -e "${YELLOW}Starting Ollama server...${NC}"
    ollama serve &
    sleep 3
    if check_server; then
        echo -e "${GREEN}✓ Ollama server started${NC}"
    else
        echo -e "${RED}✗ Failed to start Ollama server${NC}"
        exit 1
    fi
}

# Pull the model
pull_model() {
    local MODEL="$1"
    echo -e "${YELLOW}Pulling model: ${MODEL}${NC}"
    echo "This may take a while depending on your connection speed..."
    echo

    if ollama pull "$MODEL"; then
        echo -e "${GREEN}✓ Model pulled successfully${NC}"
        return 0
    else
        echo -e "${RED}✗ Failed to pull model${NC}"
        return 1
    fi
}

# Main setup
main() {
    # Step 1: Install Ollama if needed
    if ! check_ollama; then
        install_ollama
    fi
    echo

    # Step 2: Ensure server is running
    if ! check_server; then
        start_server
    fi
    echo

    # Step 3: Pull the model
    PRIMARY_MODEL="qwen2.5:7b-instruct"
    FALLBACK_MODEL="qwen2.5:7b-instruct"

    echo "Attempting to pull model: $PRIMARY_MODEL"
    echo "(~4.5GB download)"
    echo

    if pull_model "$PRIMARY_MODEL"; then
        echo
        echo -e "${GREEN}==================================="
        echo "Setup complete!"
        echo "===================================${NC}"
        echo
        echo "Model: $PRIMARY_MODEL"
    else
        echo
        echo -e "${YELLOW}Primary model not available, trying fallback...${NC}"
        if pull_model "$FALLBACK_MODEL"; then
            echo
            echo -e "${GREEN}==================================="
            echo "Setup complete!"
            echo "===================================${NC}"
            echo
            echo "Model: $FALLBACK_MODEL"
        else
            echo
            echo -e "${RED}==================================="
            echo "Setup failed!"
            echo "===================================${NC}"
            echo
            echo "Could not pull model. You may need to:"
            echo "1. Check your internet connection"
            echo "2. Try a different model: ollama pull qwen2.5:3b"
            exit 1
        fi
    fi

    echo
    echo "To start journaling, run:"
    echo "  cd $(dirname "$0")/.."
    echo "  pip install -e ."
    echo "  journal"
    echo
}

main "$@"

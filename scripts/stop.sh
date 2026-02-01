#!/bin/bash
# Stop the Ollama server

if systemctl is-active --quiet ollama; then
    echo "Stopping Ollama service..."
    sudo systemctl stop ollama
    echo "Ollama stopped."
elif pgrep -x ollama > /dev/null; then
    echo "Stopping Ollama process..."
    pkill ollama
    echo "Ollama stopped."
else
    echo "Ollama is not running."
fi

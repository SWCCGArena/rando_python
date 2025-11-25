#!/bin/bash
# Quick start script for Rando Cal bot

echo "🤖 Starting Rando Cal Bot..."
echo ""

# Activate virtual environment
source venv/bin/activate

# Check if GEMP credentials are set
if [ -z "$GEMP_USERNAME" ]; then
    echo "⚠️  Warning: GEMP_USERNAME not set"
    echo "   Set it with: export GEMP_USERNAME='rando_blu'"
fi

if [ -z "$GEMP_PASSWORD" ]; then
    echo "⚠️  Warning: GEMP_PASSWORD not set"
    echo "   Set it with: export GEMP_PASSWORD='your_password'"
fi

echo ""
echo "📍 Admin UI will be available at: http://127.0.0.1:5001"
echo ""
echo "Press Ctrl+C to stop the bot"
echo ""

# Run the Flask app
python app.py

#!/bin/bash

echo "========================================"
echo "  Rando Cal Bot - Startup Script"
echo "========================================"
echo

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check Python version
echo "Checking Python installation..."
if command -v python3 &> /dev/null; then
    PYTHON_CMD=python3
elif command -v python &> /dev/null; then
    PYTHON_CMD=python
else
    echo -e "${RED}ERROR: Python is not installed.${NC}"
    echo
    echo "Please install Python 3.10 or newer:"
    echo "  Mac: https://www.python.org/downloads/"
    echo "  Linux: Use your package manager (apt, yum, etc.)"
    exit 1
fi

# Get Python version
PYVER=$($PYTHON_CMD --version 2>&1 | cut -d' ' -f2)
PYMAJOR=$(echo $PYVER | cut -d'.' -f1)
PYMINOR=$(echo $PYVER | cut -d'.' -f2)

if [ "$PYMAJOR" -lt 3 ] || ([ "$PYMAJOR" -eq 3 ] && [ "$PYMINOR" -lt 10 ]); then
    echo -e "${RED}ERROR: Python 3.10 or newer required. You have Python $PYVER${NC}"
    exit 1
fi
echo -e "${GREEN}Found Python $PYVER - OK!${NC}"
echo

# Check for card data
echo "Checking for card data..."
if [ ! -d "../swccg-card-json" ]; then
    echo "Card data not found. Downloading..."
    if command -v git &> /dev/null; then
        cd ..
        git clone https://github.com/swccgpc/swccg-card-json.git
        cd "$SCRIPT_DIR"
    else
        echo "Git not found. Downloading via Python..."
        $PYTHON_CMD -c "
import urllib.request, zipfile, io, os
url = 'https://github.com/swccgpc/swccg-card-json/archive/refs/heads/main.zip'
data = urllib.request.urlopen(url).read()
z = zipfile.ZipFile(io.BytesIO(data))
z.extractall('..')
os.rename('../swccg-card-json-main', '../swccg-card-json')
"
    fi
fi

if [ -d "../swccg-card-json" ]; then
    echo -e "${GREEN}Card data found - OK!${NC}"
else
    echo -e "${YELLOW}WARNING: Could not download card data. Bot may not work correctly.${NC}"
fi
echo

# Create virtual environment if needed
echo "Checking virtual environment..."
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv venv
    if [ $? -ne 0 ]; then
        echo -e "${RED}ERROR: Failed to create virtual environment.${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}Virtual environment ready - OK!${NC}"
echo

# Activate virtual environment
source venv/bin/activate

# Install/update requirements
echo "Installing dependencies..."
pip install -r requirements.txt --quiet
if [ $? -ne 0 ]; then
    echo -e "${RED}ERROR: Failed to install dependencies.${NC}"
    exit 1
fi
echo -e "${GREEN}Dependencies installed - OK!${NC}"
echo

# Check for credentials
if [ ! -f "credentials.py" ]; then
    echo
    echo "========================================"
    echo "  First-time Setup: GEMP Credentials"
    echo "========================================"
    echo
    echo "You need a GEMP account on the TEST server:"
    echo "https://www.200monkeys.com/gemp-swccg/"
    echo
    echo "(This is separate from the main GEMP server)"
    echo
    read -p "Enter your GEMP username: " USERNAME
    read -sp "Enter your GEMP password: " PASSWORD
    echo
    echo
    cat > credentials.py << EOF
# GEMP credentials - keep this file private!
GEMP_USERNAME = '$USERNAME'
GEMP_PASSWORD = '$PASSWORD'
EOF
    echo "Credentials saved to credentials.py"
    echo
fi

# Start the bot
echo "========================================"
echo "  Starting Rando Cal Bot"
echo "========================================"
echo
echo "Server: https://www.200monkeys.com/gemp-swccg-server/"
echo "Admin panel will open at: http://127.0.0.1:5001"
echo
echo "Press Ctrl+C to stop the bot."
echo

# Open browser after short delay (works on Mac and Linux)
(sleep 3 && {
    if command -v open &> /dev/null; then
        open http://127.0.0.1:5001  # Mac
    elif command -v xdg-open &> /dev/null; then
        xdg-open http://127.0.0.1:5001  # Linux
    fi
}) &

# Run the bot
python app.py

# Deactivate on exit
deactivate

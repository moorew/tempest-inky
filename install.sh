#!/bin/bash

# Tempest Inky Dashboard Installer
# Installs dependencies, fonts, and sets up auto-run.

set -e  # Exit on error

echo "🌩️  Installing Tempest Inky Dashboard..."

# 1. Install System Dependencies
echo "📦 Installing system libraries..."
sudo apt update
sudo apt install -y python3-full python3-pip libopenjp2-7 libtiff6 git unzip wget

# 2. Setup Python Environment
echo "🐍 Setting up Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv --system-site-packages
fi
source venv/bin/activate

# 3. Install Python Libraries
echo "📚 Installing Python dependencies..."
pip install -r requirements.txt

# 4. Download Assets (Fonts)
echo "🎨 Downloading fonts..."
mkdir -p assets

# Download Weather Icons (Source: Erik Flowers)
if [ ! -f "assets/weathericons.ttf" ]; then
    echo "   - Downloading Weather Icons..."
    wget -q -O assets/weathericons.ttf https://github.com/erikflowers/weather-icons/raw/master/font/weathericons-regular-webfont.ttf
fi

# Download Merriweather (Source: SorkinType)
if [ ! -f "assets/Merriweather-Bold.ttf" ]; then
    echo "   - Downloading Merriweather..."
    wget -q -O temp_mw.zip https://github.com/SorkinType/Merriweather/archive/refs/heads/master.zip
    unzip -q -o temp_mw.zip -d temp_mw
    
    # Find and move the TTF files regardless of folder structure
    find temp_mw -name "Merriweather-Bold.ttf" -exec mv {} assets/ \;
    find temp_mw -name "Merriweather-Regular.ttf" -exec mv {} assets/ \;
    
    # Clean up
    rm -rf temp_mw temp_mw.zip
fi

# 5. Setup Cron (Auto-run)
echo "⏰ Setting up auto-run (Every 20 mins)..."
CURRENT_DIR=$(pwd)
CRON_CMD="*/20 * * * * $CURRENT_DIR/venv/bin/python3 $CURRENT_DIR/main.py >> $CURRENT_DIR/weather.log 2>&1"

# Add to crontab if not already there
(crontab -l 2>/dev/null | grep -F "main.py") || (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -

echo "✅ Installation Complete!"
echo "--------------------------------------------------------"
echo "👉 NEXT STEP: Edit main.py and add your API keys:"
echo "   nano main.py"
echo ""
echo "   Then run it manually to test:"
echo "   ./venv/bin/python3 main.py"
echo "--------------------------------------------------------"
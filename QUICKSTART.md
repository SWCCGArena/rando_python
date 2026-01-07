# Rando Cal Bot - Quick Start Guide

This guide will help you run the Rando Cal SWCCG bot on your computer. No programming experience required!

## What You Need

- A computer (Windows, Mac, or Linux)
- An internet connection
- A GEMP account on the **test server** at https://www.200monkeys.com/gemp-swccg/
  (Create an account there if you don't have one - this is separate from the main GEMP server)

## Step 1: Install Python

The bot requires Python 3.10 or newer.

### Windows
1. Go to https://www.python.org/downloads/
2. Click the big yellow "Download Python 3.x.x" button
3. Run the installer
4. **IMPORTANT**: Check the box that says "Add Python to PATH" at the bottom of the installer
5. Click "Install Now"

### Mac
1. Go to https://www.python.org/downloads/
2. Download the macOS installer
3. Run the installer and follow the prompts

### Linux
Python is usually pre-installed. Open a terminal and type:
```
python3 --version
```
If it shows 3.10 or higher, you're good! If not, install it with your package manager.

## Step 2: Download the Bot

### Option A: Download ZIP (Easiest)
1. Go to the GitHub repository page
2. Click the green "Code" button
3. Click "Download ZIP"
4. Extract the ZIP file somewhere on your computer (like your Documents folder)

### Option B: Using Git (if you have it)
```
git clone <repository-url>
```

## Step 3: Run the Setup Script

### Windows
1. Open the folder where you extracted the bot
2. Double-click `start_rando.bat`
3. The first time you run it, it will:
   - Check your Python version
   - Download the card data (this may take a minute)
   - Install all required packages
   - Ask for your GEMP username and password
4. A browser window will open with the admin panel

### Mac / Linux
1. Open Terminal
2. Navigate to the bot folder:
   ```
   cd /path/to/new_rando
   ```
3. Make the script executable (first time only):
   ```
   chmod +x start_rando.sh
   ```
4. Run it:
   ```
   ./start_rando.sh
   ```

## Step 4: Using the Admin Panel

Once the bot starts, your browser will open to http://127.0.0.1:5001

From here you can:
- **Start/Stop** the bot
- **Watch** the bot play games in real-time
- **Adjust settings** like aggressiveness and play style
- **View statistics** and achievements

## Connecting to Different Servers

By default, the bot connects to the **test server** at https://www.200monkeys.com/gemp-swccg-server/

This is intentional - please don't run experimental bots on the main GEMP server without permission!

If you need to connect to a different server, set the environment variable before running:

**Windows:**
```
set GEMP_SERVER_URL=https://other-server.com/gemp-swccg-server/
start_rando.bat
```

**Mac/Linux:**
```
GEMP_SERVER_URL=https://other-server.com/gemp-swccg-server/ ./start_rando.sh
```

## Troubleshooting

### "Python is not recognized" (Windows)
You need to reinstall Python and check "Add Python to PATH" during installation.

### "No module named X" errors
The setup script should install everything, but if you see this:
1. Open a terminal/command prompt in the bot folder
2. Run: `pip install -r requirements.txt`

### Bot can't connect to GEMP
- Check your username and password in `credentials.py`
- Make sure you can log in to the test server at https://www.200monkeys.com/gemp-swccg/
- Your test server account is separate from your main GEMP account

### Card data errors
Delete the `swccg-card-json` folder and run the start script again to re-download.

## Stopping the Bot

- Close the terminal/command prompt window, OR
- Press `Ctrl+C` in the terminal

## Files the Bot Creates

- `credentials.py` - Your GEMP login (keep this private!)
- `logs/` - Game logs and debug info
- `data/rando.db` - Statistics database

## Getting Help

If you run into problems:
1. Check the `logs/rando.log` file for error messages
2. Ask in the SWCCG community Discord
3. Open an issue on GitHub

Happy gaming!

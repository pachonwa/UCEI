#!/bin/bash
# Start the OctoPrint Server in the background (&)
OCTO_PATH="/home/ucei/OctoPrint/venv/bin/octoprint"
GUI_DIR="/home/ucei/Documents/UCEI"

# Activate virtual environment
cd "$GUI_DIR"
source venv/bin/activate

# Check that dependencies are installed
echo "Checking dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

#S tart OctoPrint in the background (if not already running)
if ! pgrep -f "octoprint" 
then
    echo "Starting OctoPrint..."
    $OCTO_PATH serve --port 5001 &
    sleep 10 #Wait for the server to fully initialize its API
fi

# launch  GUI
python3 -u  sprayer_controller.py

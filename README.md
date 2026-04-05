# Robotic Fuel Cell Sprayer: Deposition Control System
This project provides a centralized control interface for an automated robotic arm sprayer used in fuel cell fabrication. It integrates a custom Python GUI with an OctoPrint backend to manage G-code generation and hardware execution.

1. Systems Overview
   - **Controller:** Raspberry Pi 4
   - **Hardware Interface:** OctoPrint API
   - **Logic:** sprayer_controller.py
   - **Automation:** launch_gui.sh
  
2. Project Structure
       .
    ├── deposition_control.py   # Main Python GUI and logic
    ├── launch_project.sh       # Bash script to initialize venv and services
    ├── requirements.txt        # Python dependencies (requests, etc.)
    ├── .gitignore              # Prevents venv and OctoPrint files from being tracked
    └── README.md               # Project documentation

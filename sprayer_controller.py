import tkinter as tk
from tkinter import ttk
from tkinter import messagebox as mb
import sys
import logging
import webbrowser
import requests
import subprocess
import numpy as np
import serial
import time
from shapely.geometry import Polygon, LineString, Point
from shapely.affinity import rotate

# =========================
# CONFIG
# =========================
CANVAS_W = 500
CANVAS_H = 500
SPRAYER_WIDTH = 5.0   # in millimeters; CHANGE WHEN THIS IS ACTUALLY CALCULATED
FEEDRATE = 1200
RECTANGLE = "Rectangle"
OVAL = "Oval"
SPIRAL = "Spiral"
ZIGZAG = "ZigZag"
CROSSHATCH = "Cross-Hatch"
ANGLED = "Angled Cross-Hatch"
ISOTROPIC = "Isotropic"
CIRCLE = "Circle"
CENTIMETERS = "cm"
MILLIMETERS = "mm"
INCHES = "in"
path_file = ""
shape_to_draw = None
ARDUINO_PORT = '/dev/ttyACM0'
# ARDUINO_PORT = '/dev/cu.usbmodem11301' # woodpecker arduino - initial prototype
OCTOPRINT_PORT = 5001
OCTOPRINT_URL = f"http://127.0.0.1:{OCTOPRINT_PORT}/"
API_KEY = "r2W1qV4ZPIbhz9h5-Oj2syPf_bktfvAgTiDMi8kwgQ4"
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
logger = logging.getLogger()


# =========================
# PATH GENERATORS
# =========================
def spiral_paths(poly, spacing):
    paths = []
    p = poly
    while p.area > spacing**2:
        paths.append(list(p.exterior.coords))
        p = p.buffer(-spacing)
        if p.is_empty:
            break
    return paths

# def raster_paths(poly, spacing):
#     minx, miny, maxx, maxy = poly.bounds
#     paths = []
#     direction = 1

#     for y in np.arange(miny, maxy, spacing):
#         line = LineString([(minx, y), (maxx, y)])
#         clipped = line.intersection(poly)
#         # print(clipped)

#         if clipped.is_empty:
#             continue

#         if clipped.geom_type == "MultiLineString":
#             for seg in clipped:
#                 coords = list(seg.coords)
#                 if len(coords) >= 2:
#                     paths.append(coords if direction > 0 else coords[::-1])
#         else:
#             coords = list(clipped.coords)
#             if len(coords) >= 2:
#                 paths.append(coords if direction > 0 else coords[::-1])

#         direction *= -1

#     return paths

def raster_paths(poly, spacing, overrun=5.0):
    minx, miny, maxx, maxy = poly.bounds
    paths = []
    direction = 1
    numofpasses = 1
    while numofpasses > 0:
        print("passed through here")
        for y in np.arange(miny, maxy, spacing):
            line = LineString([(minx, y), (maxx, y)])
            clipped = line.intersection(poly)

            if clipped.is_empty:
                continue

            segments = []
            if clipped.geom_type == "MultiLineString":
                segments = list(clipped)
            else:
                segments = [clipped]

            for seg in segments:
                coords = list(seg.coords)
                if len(coords) < 2:
                    continue

                if direction < 0:
                    coords = coords[::-1]

                # ---- EXTEND SEGMENT ----
                x1, y1 = coords[0]
                x2, y2 = coords[-1]

                dx = x2 - x1
                dy = y2 - y1
                length = np.hypot(dx, dy)

                if length == 0:
                    continue

                ux = dx / length
                uy = dy / length

                # extend both ends
                x1_ext = x1 - ux * overrun
                y1_ext = y1 - uy * overrun
                x2_ext = x2 + ux * overrun
                y2_ext = y2 + uy * overrun

                paths.append([(x1_ext, y1_ext), (x2_ext, y2_ext)])

            direction *= -1
        numofpasses = numofpasses - 1
    return paths

def isotropic_paths(poly, spacing):
    """
    Combines Standard (0, 90) and Angled (45, 135) for 
    maximum coating uniformity.
    """
    all_paths = []
    # This combines both sets of angles into one list of G-code paths
    for angle in [0, 90, 45, 135]:
        rot = rotate(poly, angle, origin='centroid')
        raster = raster_paths(rot, spacing)

        for path in raster:
            restored = []
            for x, y in path:
                p = rotate(Point(x, y), -angle, origin=poly.centroid)
                restored.append(p.coords[0])
            if len(restored) >= 2:
                all_paths.append(restored)
                
    return all_paths

def crosshatch_paths(poly, spacing):
    all_paths = []
    for angle in [0, 90]:
        rot = rotate(poly, angle, origin='centroid')
        raster = raster_paths(rot, spacing)

        for path in raster:
            restored = []
            for x, y in path:
                p = rotate(Point(x, y), -angle, origin=poly.centroid)
                restored.append(p.coords[0])
            if len(restored) >= 2:
                all_paths.append(restored)

    return all_paths

def angled_crosshatch_paths(poly, spacing):
    all_paths = []
    for angle in [45, 135]:
        rot = rotate(poly, angle, origin='centroid')
        raster = raster_paths(rot, spacing)

        for path in raster:
            restored = []
            for x, y in path:
                p = rotate(Point(x, y), -angle, origin=poly.centroid)
                restored.append(p.coords[0])
            if len(restored) >= 2:
                all_paths.append(restored)

    return all_paths


def metric_to_mm_converter(value, metric): # converts metric to mm
    numeric_value = float(value)
    if metric == CENTIMETERS:
        scale_factor = 10
    elif metric == INCHES:
        scale_factor = 25.4
    elif metric == MILLIMETERS:
        scale_factor = 1.0
    else:
        scale_factor = 1.0
    mm = numeric_value * scale_factor
    return mm

# =========================
# G-CODE WRITER
# =========================
def write_gcode(filename, paths):
    global new_angle
    servo_angle = new_angle
    with open(filename, "w") as f:

        f.write("G21\n")      # mm
        f.write("G92 X0 Y0 Z0")
        f.write("G90\n")      # absolute
        f.write("G0 Z5\n")    # safe height
        f.write(f"G0 X0 Y0\n") 
        # f.write("G28\n")          # Home all axes

        f.write("G0 Z0\n")    # safe height

        # Optional: wait for heater
        # f.write("M104 S60\n")    # Set temp (example)
        # f.write("M109 S60\n")    # Wait for temp

        E=0 #needed for extrusion. octoprint is for 3d printers so if extrusion isn't mentioned, it thinks that nothing is happening

        for path in paths:        
            x0, y0 = path[0]

            f.write(f"G0 X{x0:.2f} Y{y0:.2f}\n")

            # Spray ON
            f.write("M280 P0 S0\n")
            f.write("G4 P250\n")  #Dwell 250ms for servo to move
            
            for x, y in path:
                E+=1
                f.write(f"G1 X{x:.2f} Y{y:.2f} E{E} F{FEEDRATE}\n")

            # Spray OFF
            f.write(f"M280 P0 S{servo_angle}\n")
            f.write("G4 P250\n")  #Dwell 250ms for servo to move
            
        f.write("M280 P0 S0\n")
        f.write("G4 P250\n")  #Dwell 250ms for servo to move

        # ===== Shutdown =====
        # f.write("G0 Z5\n")
        # Return to origin
        f.write("G0 X0 Y0\n")

def move_servo():  #updated marlin function
    global ser
    global new_angle
    angle = servoDegreetb.get()
    if angle == "" or not isinstance(int(angle), int):  # no input / incorrect value
        logger.warning("Please input a valid integer")
        new_angle = 0
        return
    elif int(angle) >= 0 and int(angle) <= 270: #valid angle set
        new_angle = int(angle)
        logger.info(f"New angle = {new_angle}")
        
        headers = {
            "X-Api-Key": API_KEY,
            "Content-Type": "application/json"
        }

        payload = {"command": f"M280 P0 S{new_angle}"}

        try:
            response = requests.post(f"{OCTOPRINT_URL}api/printer/command", headers=headers, json=payload)
            if response.status_code == 204:
                logger.info("Successfully sent servo command to OctoPrint!")
            else:
                logger.error(f"Error: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Failed to connect to OctoPrint / move servo: {e}")

    else:  # if angle specified is too high or low / invalid input, pass 
        new_angle = 0
        logger.warning("Inputted integer outside of bounds. Select an angle between 0 & 270")
        return
    return

# def heat_bed():  #updated marlin function
#     global ser
#     global new_temp
#     temp =  # get the desired temp
#     if temp == "" or not isinstance(int(temp), int):  # no input / incorrect value
#         logger.warning("Please input a valid integer")
#         new_temp = 0
#         return
#     elif int(temp) >= 0 and int(temp) <= 100: #valid temp set
#         new_temp = int(temp)
#         logger.info(f"New temperature = {new_temp}")
        
#         headers = {
#             "X-Api-Key": API_KEY,
#             "Content-Type": "application/json"
#         }

#         payload = {"command": f"M140 S{new_temp}"}

#         try:
#             response = requests.post(f"{OCTOPRINT_URL}api/printer/command", headers=headers, json=payload)
#             if response.status_code == 204:
#                 logger.info("Successfully sent servo the temperature command to OctoPrint!")
#             else:
#                 logger.error(f"Error: {response.status_code} - {response.text}")
#         except Exception as e:
#             logger.error(f"Failed to connect to OctoPrint / move servo: {e}")

#     else:  # if temp specified is too high or low / invalid input, pass 
#         new_temp = 0
#         logger.warning("Inputted integer outside of bounds. Select a temperature between 0 & 270")
#         return
#     return

def cm_to_mm_converter(coords): # converts cm to mm
    CM_TO_MM = 10
    square_mm = [(x*CM_TO_MM, y*CM_TO_MM) for x,y in coords]
    return square_mm

# CIRCULAR SUBSTRATE - default test
def default_circle_path_generator(): 
    circle = Point(0, 0).buffer(5*10)  #buffer = radius; 10 is the converter (to cm->mm)

    spiral = spiral_paths(circle, SPRAYER_WIDTH)
    raster = raster_paths(circle, SPRAYER_WIDTH)
    cross  = crosshatch_paths(circle, SPRAYER_WIDTH)

    write_gcode("spiral.nc", spiral)
    write_gcode("raster.nc", raster)
    write_gcode("crosshatch.nc", cross)

# 5 INCH SQUARE SUBSTRATE - default test
def default_rect_path_generator(): 
    pointss = [(0,0), (5,0), (5,5), (0,5)] #in centimeters
    square_mm = cm_to_mm_converter(pointss)
    poly = Polygon(square_mm)

    spiral = spiral_paths(poly, SPRAYER_WIDTH)
    raster = raster_paths(poly, SPRAYER_WIDTH)
    cross  = crosshatch_paths(poly, SPRAYER_WIDTH)

    write_gcode("spiral.nc", spiral)
    write_gcode("raster.nc", raster)
    write_gcode("crosshatch.nc", cross)



##########################
# HELPER FUNCTIONS
##########################
def is_integer(s):
    try:
        int(s)
        return True
    except ValueError:
        return False


def open_in_candle(gcode_filename):
    os = sys.platform
    if os.startswith("win"):  #windows configuration
        print("windows config")
        subprocess.Popen([ r"C:\Program Files\Candle\candle.exe",
        gcode_filename
    ])
    elif os == "darwin":    #mac configuration
        print("macos config")
        # subprocess.Popen([
        #     "open",
        #     "-a",
        #     "Candle",
        #     gcode_filename
        # ])
        subprocess.run(['open', '-a', "Candle", gcode_filename], check=True)
        # candle_path = "/Applications/Candle.app/Contents/MacOS/Candle"
        # subprocess.Popen([candle_path, gcode_filename])
    elif os == "linux":
        print("linux config")
        subprocess.Popen(["/usr/bin/candle", gcode_filename])
    else:
        print("Unsupported operating system")


def open_in_octoprint(gcode_filename):
    # API endpoint for file uploads
    url = f"{OCTOPRINT_URL}api/files/local"
    headers = {"X-Api-Key": API_KEY}
    
    # 'select': 'true' tells OctoPrint to load it into the viewer immediately
    # 'print': 'false' ensures it doesn't start moving until you click 'Print'
    payload = {"select": "true", "print": "false"}

    with open(gcode_filename, 'rb') as f:
        files = {'file': f}
        response = requests.post(url, headers=headers, files=files, data=payload)

    if response.status_code == 201:
        logger.info("File uploaded and loaded successfully!")
    else:
        logger.error(f"Upload failed: {response.text}")

    webbrowser.open(OCTOPRINT_URL, autoraise=True, new=0)

def shape_clicked(event): #executed when shape from listbox is selected
    global shape_to_draw
    global shape_original_coords  # these are the coordinates before moving the shape on the canvas

    #### check whether textbox has an input or not ###
    selected_shape = lb.curselection()[0] #checks the index of the selected shape
    shape = lb.get(selected_shape) #gets the text value of the selected shape

    canvas.delete("all") # delete previous drawing

    # Finding center of canvas
    canvas_center_x = CANVAS_W // 2   
    canvas_center_y = CANVAS_H // 2

    width = width_tb.get()
    length = length_tb.get()
    logger.debug(f"Width input is an integer?: {is_integer(width)}, Length input is an integer?: {is_integer(length)}")

    if width == "" or length == "" or not is_integer(width) or not is_integer(length):
        print("Using default value of 5 x 5")
        width = 5
        length = 5

    metric = metric_option.get()
    if metric == "Option 1": 
        logger.warning("Please select a metric")
        mb.showwarning("Warning!!", "Please select a metric")
        return
    shape_width = metric_to_mm_converter(width, metric)
    shape_height = metric_to_mm_converter(length, metric)
    logger.info(f"Shape Dimensions = {shape_width}W x {shape_height}L in mm.")
    # print(f"type: {type(shape_width)}, {type(shape_height)}")

    # Depending on shape selected, the canvas will display the shape
    if shape == RECTANGLE:
        shape_to_draw = canvas.create_rectangle(0, 
                                                0, 
                                                shape_width, 
                                                shape_height, 
                                                outline="black") #for testing in cm; multiply by 10 for cm->mm
    elif shape == CIRCLE:
        shape_to_draw = canvas.create_oval(0, 
                                           0, 
                                           shape_width, 
                                           shape_height, 
                                           outline="black")  #for testing in cm; multiply by 10 for cm->mm
    elif shape == "Oval":
        pass
    
    shape_original_coords = canvas.coords(shape_to_draw)  #stores the coordinates of the shape before it's moved on the canvas
    print(shape_original_coords)

    # scales the shape to fit into the gui canvas
    scale_factor = min((CANVAS_W * 0.8) / shape_width, 
                       (CANVAS_H * 0.8) / shape_height)
    print(scale_factor)
    print((CANVAS_W * 0.8) / metric_to_mm_converter(shape_width, metric), 
                       (CANVAS_H * 0.8) / metric_to_mm_converter(shape_height, metric))
    canvas.scale(shape_to_draw, 0, 0, scale_factor, scale_factor)

    x1, y1, x2, y2 = canvas.bbox(shape_to_draw)

    #Finding center of the shape
    shape_center_x = (x1 + x2) / 2
    shape_center_y = (y1 + y2) / 2

    offset_x = canvas_center_x - shape_center_x
    offset_y = canvas_center_y - shape_center_y

    # moves the shape to the center of the canvas
    canvas.move("all", 
                offset_x, 
                offset_y
            )

    logger.debug(f"Shape ID: {shape_to_draw}")

    return shape_to_draw

def path_clicked(event): #executed when path from listbox is selected
    global path_file
    global original_paths
    #checks that a shape is selected first
    if shape_to_draw == None:  
        mb.showwarning("Warning!!", "Please select a shape")
        return
    
    selected_path = path_lb.curselection()[0] #curselection outputs a tuple of ints; takes the first element
    path = path_lb.get(selected_path) # gets the text associated with element
    logger.debug(f"Selected Path: {path}")

    coords = canvas.coords(shape_to_draw) #gets coordinates of the drawn shape on the canvas
    x0, y0, x1, y1 = coords
    x_0, y_0, x_1, y_1 = shape_original_coords 

    #checks the shape that the user has selected
    selected_shape = lb.get(lb.curselection()[0])
    if selected_shape == RECTANGLE:
        poly = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])  #creates polygon object; necessary for shapely library; used for displaying in tkinter
        original_poly = Polygon([(x_0, y_0), (x_1, y_0), (x_1, y_1), (x_0, y_1)])  #used for accurate coordinates in Candle

    elif selected_shape == CIRCLE:
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        r = (x1 - x0) / 2
        poly = Point(cx, cy).buffer(r)  #buffer = radius; creates object necessary for shapely library; used for displaying in tkinter

        c_x = (x_0 + x_1) / 2
        c_y = (y_0 + y_1) / 2
        r = (x_1 - x_0) / 2
        original_poly = Point(c_x, c_y).buffer(r) #used for accurate coordinates in Candle


    # generates gcode depending on selected path
    # the paths variable is used to display the paths in gui/canvas; coordinates are offseted to position shape in the middle of the gui
    # the original paths variable is used to get accurate coordinates in Candle; coordinates start from the origin (0,0)
    if path == SPIRAL:
        paths = spiral_paths(poly, SPRAYER_WIDTH*10)
        original_paths = spiral_paths(original_poly, SPRAYER_WIDTH)
        path_file = "spiral.gcode"
        print("spiral path generated")
    elif path == ZIGZAG:
        paths = raster_paths(poly, SPRAYER_WIDTH*10)
        original_paths = raster_paths(original_poly, SPRAYER_WIDTH)
        path_file = "raster.gcode"
        print("raster path generated")
    elif path == CROSSHATCH:
        paths = crosshatch_paths(poly, SPRAYER_WIDTH*10)
        original_paths = crosshatch_paths(original_poly, SPRAYER_WIDTH)
        path_file = "crosshatch.gcode"
        print("crosshatch path generated")
    elif path == ANGLED:
        paths = angled_crosshatch_paths(poly, SPRAYER_WIDTH*10)
        original_paths = angled_crosshatch_paths(original_poly, SPRAYER_WIDTH)
        path_file = "angledcrosshatch.gcode"
        print("angled crosshatch path generated")
    elif path == ISOTROPIC: 
        paths = isotropic_paths(poly, SPRAYER_WIDTH*10)
        original_paths = isotropic_paths(original_poly, SPRAYER_WIDTH)
        path_file = "isotropic.gcode"
        print("isotropic path generated")
    print(path_file)
    
    # Displays selected paths on the canvas
    canvas.delete("path_lines") #deletes previously traced path
    for path in paths:
        canvas.create_line(path, fill="blue", tags="path_lines")
    # print(f"FILE PATH = {path_file}")
    # write_gcode(path_file, original_paths)

# =========================
# SETUP
# =========================
def background_setup(): # connects to arduino and connects printer to octoprint
    # OPEN arduino connection once at the start
    try:
        ser = serial.Serial(ARDUINO_PORT, 250000)
        logger.info("Successfully  connected to Servo")
        time.sleep(2) # Wait for the reboot
    except:
        logger.error("Could not connect to arduino. Check the port name!")

    headers = {
        "X-Api-Key": API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "command": "connect",
        "printer_profile": "_default",
        "save": True,
        "autoconnect": True 
        }

    try:  # connect to printer after creating the server
        response = requests.post(f"{OCTOPRINT_URL}/api/connection", headers=headers, json=payload)
        if response.status_code == 204:
            logger.info("Successfully connected to printer")
        else:
            logger.warning("Printer was not connected")
    except Exception as e:
        logger.error(f"Failed to connect to OctoPrint/printer: {e}")


# def click(event):
#     points.append((event.x, event.y))
#     canvas.create_oval(event.x-7, event.y-7, event.x+7, event.y+7, fill="black")

def on_closing():
    global ser
    try:
        # CLOSE connection when the window is closed
        ser.close()
    except:
        print("Serial is not connected, nothing to close\n")
    # root.destroy()
    # generate_button.config(state="disabled")
    

def finish(): # Runs when the finish shape button is clicked
    global path_file
    global original_paths
    if len(path_lb.curselection()) == 0:  #checks that a shape is selected first
        mb.showwarning("Warning!!", "Please select a path")
        return
    print(path_file)
    write_gcode(path_file, original_paths)
    logger.info(f"{path_file} generated")
    open_in_octoprint(f"/home/ucei/Documents/UCEI/{path_file}")
    on_closing()




###################
# START OF PROGRAM#
###################

#### SETUP #####
background_setup() #connects to arduino and octoprint server

root = tk.Tk()
root.title("Draw Substrate Boundary")

# ##### White Canvas above selectors
canvas = tk.Canvas(root, width=CANVAS_W, height=CANVAS_H, bg="white")
canvas.grid(row = 0, column=0) # Put the Canvas in row 0, col 0 
# #####

# Frame for the "Controls" on the right of canvas
control_frame = tk.Frame(root)
control_frame.grid(row=0, column=1, sticky="n")

###### DIMENSIONS SELECTION ######
width_label = tk.Label(control_frame, font=("Lexend", 14), text="Enter width: ")
width_label.grid(row=0, column=0)

# # For inputting dimensions
width_tb = tk.Entry(control_frame, width=15)
width_tb.grid(row=1, column=0)

length_label = tk.Label(control_frame, font=("Lexend", 14), text="Enter length: ")
length_label.grid(row=2, column=0)
length_tb = tk.Entry(control_frame, width=15)
length_tb.grid(row=3, column=0)

metric_option = tk.StringVar(value="Option 1")
rb1 = ttk.Radiobutton(control_frame, text="mm", variable=metric_option, value="mm")
rb1.grid(row=1, column=1, sticky="w")

rb2 = ttk.Radiobutton(control_frame, text="cm", variable=metric_option, value="cm")
rb2.grid(row=2, column=1, sticky="w")

rb3 = ttk.Radiobutton(control_frame, text="in", variable=metric_option, value="in")
rb3.grid(row=3, column=1, sticky="w")
# tk.Button(control_frame, text="Set").grid(row=4, column=0)
###### DIMENSIONS SELECTION ######


###### SHAPE SELECTION #######
shape_label = tk.Label(control_frame, font=("Lexend", 14), text="Select a shape:")
shape_label.grid(row=5, column=0, pady=(35, 0))

# Listbox for selecting shapes
lb = tk.Listbox(control_frame, height=3, width=15, exportselection=False)
lb.grid(row=6, column=0)
lb.insert(1, "Rectangle")
lb.insert(3, "Circle")
lb.bind("<<ListboxSelect>>", shape_clicked)
##### SHAPE SELECTION #######

###### PATH SELECTION #######
path_label = tk.Label(control_frame, text="Select a path:")
path_label.grid(row=7, column=0, pady=(10, 0))

# Listbox for selecting path
path_lb = tk.Listbox(control_frame, height=6, width=15, exportselection=False)
path_lb.insert(1, SPIRAL)
path_lb.insert(2, CROSSHATCH)
path_lb.insert(3, ZIGZAG)
path_lb.insert(4, ANGLED)
path_lb.insert(5, ISOTROPIC)
path_lb.grid(row=8, column=0)
path_lb.bind("<<ListboxSelect>>", path_clicked)
###### PATH SELECTION #######

###### SERVO DEGREE SELECTION #######
# Textbox for servo degrees 
servoDegreetb = tk.Entry(control_frame, width=15)
servoDegreetb.grid(row=9, column=0, pady=(20, 0))
tk.Button(control_frame, text="Move servo", command=move_servo).grid(row=10, column=0)
###### SERVO DEGREE SELECTION #######

generate_button = tk.Button(control_frame, text="Generate!", command=finish)
generate_button.grid(row=11, column=0, pady=(25, 0))

# canvas.bind("<Button-1>", click)

root.mainloop()

# =========================
# GEOMETRY
# =========================
# print(points)
# polygon = Polygon(points)


# =========================
# RUN TEST
# =========================
# default_rect_path_generator()
# default_circle_path_generator()

# spiral = spiral_paths(polygon, SPRAYER_WIDTH)
# raster = raster_paths(polygon, SPRAYER_WIDTH)
# cross  = crosshatch_paths(polygon, SPRAYER_WIDTH)

# write_gcode("spiral.nc", spiral)
# write_gcode("raster.nc", raster)
# write_gcode("crosshatch.nc", cross)

# print("Generated:")
# print(" - spiral.nc")
# print(" - raster.nc")
# print(" - crosshatch.nc")
# print("Open in Candle to preview & run")

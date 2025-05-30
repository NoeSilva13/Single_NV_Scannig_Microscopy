import numpy as np 
import napari
import time
import json
import nidaqmx
from nidaqmx.constants import (TerminalConfiguration, Edge, CountDirection, AcquisitionType, SampleTimingType)
from galvo_controller import GalvoScannerController
from data_manager import DataManager
import threading
from magicgui import magicgui
from napari.utils.notifications import show_info
#import random
# --------------------- INITIAL CONFIGURATION ---------------------
config = json.load(open("config_template.json"))
galvo_controller = GalvoScannerController()
data_manager = DataManager()

x_range = config['scan_range']['x']
y_range = config['scan_range']['y']
x_res = config['resolution']['x']
y_res = config['resolution']['y']

original_x_points = np.linspace(x_range[0], x_range[1], x_res)
original_y_points = np.linspace(y_range[0], y_range[1], y_res)

# Global state
zoom_level = 0
max_zoom = 3
contrast_limits = (0, 10000)
scan_history = []  # For going back
image = np.zeros((y_res, x_res), dtype=np.float32)
data_path = None
# --------------------- VISOR NAPARI ---------------------
viewer = napari.Viewer()
layer = viewer.add_image(image, name="live scan", colormap="viridis", scale=(1, 1), contrast_limits=contrast_limits)
shapes = viewer.add_shapes(name="zoom area", shape_type="rectangle", edge_color='red', face_color='transparent', edge_width=0)

# --------------------- SCANNING ---------------------
def scan_pattern(x_points, y_points):
    global image, layer, data_path

    height, width = len(y_points), len(x_points)
    image = np.zeros((height, width), dtype=np.float32)
    layer.data = image  # update layer
    layer.contrast_limits = contrast_limits
    with nidaqmx.Task() as ao_task, nidaqmx.Task() as counter_task:
        ao_task.ao_channels.add_ao_voltage_chan(galvo_controller.xin_control)
        ao_task.ao_channels.add_ao_voltage_chan(galvo_controller.yin_control)

        counter_task.ci_channels.add_ci_count_edges_chan(
            galvo_controller.spd_counter,
            edge=Edge.RISING,
            initial_count=0
        )
        counter_task.ci_channels[0].ci_count_edges_term = galvo_controller.spd_edge_source

        for y_idx, y in enumerate(y_points):
            for x_idx, x in enumerate(x_points):
                ao_task.write([x, y])
                time.sleep(0.001)

                counter_task.start()
                time.sleep(config['dwell_time'])
                counts = counter_task.read()
                counter_task.stop()

                counts_per_second = counts / config['dwell_time']
                
                image[y_idx, x_idx] = counts_per_second
                #image[y_idx, x_idx] = random.randint(0, 10000) # for testing
                layer.data = image
    layer.contrast_limits = (np.min(image), np.max(image))
    data_path = data_manager.save_scan_data(image)
    return x_points, y_points  # Returns for history

# --------------------- ZOOM BY REGION ---------------------

zoom_in_progress = False  # Flag global
@shapes.events.data.connect
def on_shape_added(event):
    global zoom_level, max_zoom, scan_history
    global original_x_points, original_y_points, zoom_in_progress

    if zoom_in_progress:
        return  # Ignore if already running

    if zoom_level >= max_zoom:
        print(f"⚠️ Max zoom reached ({max_zoom} levels).")
        return

    if len(shapes.data) == 0:
        return

    rect = shapes.data[-1]
    min_y, min_x = np.floor(np.min(rect, axis=0)).astype(int)
    max_y, max_x = np.ceil(np.max(rect, axis=0)).astype(int)

    # Limit to current image size
    height, width = layer.data.shape
    min_x = max(0, min_x)
    max_x = min(width, max_x)
    min_y = max(0, min_y)
    max_y = min(height, max_y)

    # History: save current state before zoom
    scan_history.append((original_x_points, original_y_points))

    # Adjust resolution to new range (keep original resolution)
    #x_zoom = np.linspace(original_x_points[min_x], original_x_points[max_x - 1], max_x - min_x)
    #y_zoom = np.linspace(original_y_points[min_y], original_y_points[max_y - 1], max_y - min_y)
    # Same resolution as original but zoom in
    x_zoom = np.linspace(original_x_points[min_x], original_x_points[max_x - 1], x_res)
    y_zoom = np.linspace(original_y_points[min_y], original_y_points[max_y - 1], y_res)

    def run_zoom():
        global original_x_points, original_y_points, zoom_level, zoom_in_progress
        zoom_in_progress = True  # Activate flag
        original_x_points, original_y_points = scan_pattern(x_zoom, y_zoom)
        zoom_level += 1
        shapes.data = []  # Clear rectangle
        zoom_in_progress = False  # Release flag

    threading.Thread(target=run_zoom, daemon=True).start()
# --------------------- RESET BUTTON ---------------------
@magicgui(call_button="🔄 Reset Zoom")
def reset_zoom():
    global zoom_level, scan_history, original_x_points, original_y_points
    shapes.data = []  # Clear rectangle
    if zoom_level == 0:
        print("🔁 You are already in the original view.")
        return

    # Restore original view (last saved)
    original_x_points = np.linspace(x_range[0], x_range[1], x_res)
    original_y_points = np.linspace(y_range[0], y_range[1], y_res)
    scan_history.clear()
    zoom_level = 0

    def run_reset():
        scan_pattern(original_x_points, original_y_points)
        shapes.data = []

    threading.Thread(target=run_reset, daemon=True).start()

@magicgui(call_button="📷 New Scan")
def new_scan():
    global original_x_points, original_y_points
    
    def run_new_scan():
        scan_pattern(original_x_points, original_y_points)
        shapes.data = []
    
    threading.Thread(target=run_new_scan, daemon=True).start()
    show_info("New scan started")

@magicgui(call_button="🎯 Set to Zero")
def close_scanner():
    def run_close():
        galvo_controller.close()
    
    threading.Thread(target=run_close, daemon=True).start()
    show_info("Scanner set to zero")

@magicgui(call_button="📷 Save Image")
def save_image():
    viewer.screenshot(path=f"{data_path}.png", canvas_only=True, flash=True)
    show_info("Image saved")

# Add buttons to the interface
viewer.window.add_dock_widget(reset_zoom, area="right")
viewer.window.add_dock_widget(new_scan, area="right")
viewer.window.add_dock_widget(save_image, area="right")
viewer.window.add_dock_widget(close_scanner, area="right")


napari.run()

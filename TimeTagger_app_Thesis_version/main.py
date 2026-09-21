import os
import sys
import time
import json
import csv
import logging
import asyncio
from typing import Dict, List, Set, Optional
from datetime import datetime
import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TimeTaggerBackend")

# Import Swabian Instruments TimeTagger
try:
    import TimeTagger
    logger.info("TimeTagger library imported successfully.")
except ImportError as e:
    logger.error("Failed to import TimeTagger library! Make sure it is installed.")
    sys.exit(1)

app = FastAPI(title="Swabian TimeTagger Dashboard")

# App directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Helper functions to load/save config.json
def load_config_file() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
                logger.info(f"Loaded configuration from {CONFIG_PATH}")
                return config
        except Exception as e:
            logger.error(f"Failed to read config.json: {e}")
            
    # Default configuration
    default_config = {
        "active_channels": [1, 2],
        "channel_aliases": {str(ch): f"Channel {ch}" for ch in range(1, 21)},
        "trigger_voltages": {str(ch): 0.5 for ch in range(1, 21)},
        "integration_time_sec": 0.1,
        "normalize": True,
        "hist_start_channel": 1,
        "hist_click_channel": 2,
        "hist_binwidth_ps": 100,
        "hist_n_bins": 400,
        "measure_counter_trace": True,
        "measure_coincidence_histogram": True,
        "measure_jsi": True,
        "jsi_binwidth_ps": 100,
        "jsi_n_bins": 2000,
        "simulation_mode": True
    }
    save_config_file(default_config)
    return default_config

def save_config_file(config: dict):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        logger.info(f"Configuration saved to {CONFIG_PATH}")
    except Exception as e:
        logger.error(f"Failed to write config.json: {e}")

class TaggerManager:
    def __init__(self):
        self.tagger = None
        self.is_virtual = False
        
        # Load from config.json
        config = load_config_file()
        
        # Config properties
        self.active_channels: List[int] = config.get("active_channels", [1, 2])
        self.channel_aliases: Dict[int, str] = {int(k): v for k, v in config.get("channel_aliases", {}).items()}
        self.trigger_voltages: Dict[int, float] = {int(k): float(v) for k, v in config.get("trigger_voltages", {}).items()}
        
        self.integration_time_sec: float = float(config.get("integration_time_sec", 0.1))
        self.normalize: bool = bool(config.get("normalize", True))
        
        self.hist_start_channel: int = int(config.get("hist_start_channel", 1))
        self.hist_click_channel: int = int(config.get("hist_click_channel", 2))
        self.hist_binwidth_ps: int = int(config.get("hist_binwidth_ps", 100))
        self.hist_n_bins: int = int(config.get("hist_n_bins", 400))
        
        self.measure_counter_trace: bool = bool(config.get("measure_counter_trace", True))
        self.measure_coincidence_histogram: bool = bool(config.get("measure_coincidence_histogram", True))
        self.measure_jsi: bool = bool(config.get("measure_jsi", True))
        self.jsi_binwidth_ps: int = int(config.get("jsi_binwidth_ps", 100))
        self.jsi_n_bins: int = int(config.get("jsi_n_bins", 2000))
        self.simulation_mode: bool = bool(config.get("simulation_mode", True))
        
        self.history_size: int = 100
        
        # Swabian Measurement objects
        self.counter: Optional[TimeTagger.Counter] = None
        self.histogram: Optional[TimeTagger.Histogram] = None
        self.simulated_histogram_accum: Optional[np.ndarray] = None
        self.jsi_corr: Optional[TimeTagger.CorrelationPairs] = None
        
        # Virtual generators dictionary
        self.virtual_generators: Dict[int, object] = {}
        
        # Recording State
        self.recording_active: bool = False
        self.recording_file: Optional[str] = None
        self.recording_csv_writer = None
        self.recording_fh = None
        self.recording_start_time: float = 0.0
        self.recorded_points_count: int = 0

    def dump_config_state(self) -> dict:
        """Translates current state back into config.json format."""
        return {
            "active_channels": self.active_channels,
            "channel_aliases": {str(k): v for k, v in self.channel_aliases.items()},
            "trigger_voltages": {str(k): v for k, v in self.trigger_voltages.items()},
            "integration_time_sec": self.integration_time_sec,
            "normalize": self.normalize,
            "hist_start_channel": self.hist_start_channel,
            "hist_click_channel": self.hist_click_channel,
            "hist_binwidth_ps": self.hist_binwidth_ps,
            "hist_n_bins": self.hist_n_bins,
            "measure_counter_trace": self.measure_counter_trace,
            "measure_coincidence_histogram": self.measure_coincidence_histogram,
            "measure_jsi": self.measure_jsi,
            "jsi_binwidth_ps": self.jsi_binwidth_ps,
            "jsi_n_bins": self.jsi_n_bins,
            "simulation_mode": self.simulation_mode
        }

    def save_state_to_disk(self):
        save_config_file(self.dump_config_state())

    def initialize_tagger(self):
        """Attempts to initialize physical tagger, falls back to virtual."""
        self.close_tagger()
        
        try:
            logger.info("Attempting to connect to hardware TimeTagger...")
            self.tagger = TimeTagger.createTimeTagger()
            self.is_virtual = False
            logger.info(f"Connected to physical TimeTagger. Serial: {self.tagger.getSerial()}")
        except Exception as e:
            logger.warning(f"Could not connect to physical TimeTagger: {e}")
            logger.info("Falling back to simulated TimeTaggerVirtual...")
            self.tagger = TimeTagger.createTimeTaggerVirtual()
            self.is_virtual = True
            logger.info("Initialized simulated TimeTaggerVirtual.")

        # Set default channel schemes
        TimeTagger.setTimeTaggerChannelNumberScheme(TimeTagger.TT_CHANNEL_NUMBER_SCHEME_ONE)
        
        # Start virtual clock if virtual
        if self.is_virtual:
            self.tagger.run()
            logger.info("Simulated TimeTagger clock started.")
            
        # Re-apply configuration
        self.apply_configuration()

    def apply_configuration(self):
        """Applies channel, trigger, and measurement settings to the tagger."""
        if not self.tagger:
            return
            
        logger.info("Applying tagger configuration...")
        
        # Deleting python references stops/releases measurements on the C++ side.
        # Calling TimeTagger.freeTimeTagger on measurement objects triggers errors.
        self.counter = None
        self.histogram = None
        self.jsi_corr = None
        self.virtual_generators.clear()

        # Set trigger levels for hardware tagger
        if not self.is_virtual and hasattr(self.tagger, 'setTriggerLevel'):
            for ch in range(1, 21):
                voltage = self.trigger_voltages.get(ch, 0.5)
                try:
                    self.tagger.setTriggerLevel(ch, voltage)
                    logger.info(f"Set hardware trigger level for Channel {ch} to {voltage} V")
                except Exception as e:
                    logger.error(f"Error setting trigger level on Channel {ch}: {e}")

        # Setup virtual test generators if simulated
        channels_to_simulate = [ch for ch in self.active_channels if ch <= 18] if self.is_virtual else self.active_channels
        
        if self.is_virtual:
            for ch in channels_to_simulate:
                # Setup simple background counts so graphs are not zero when simulating
                target_cps = ((ch * 73) % 250 + 50) * 1000  # 50k to 300k CPS
                upper_bound = int(2.0 * 1e12 / target_cps)
                try:
                    gen = TimeTagger.Experimental.UniformSignalGenerator(
                        self.tagger,
                        upper_bound=upper_bound,
                        base_channel=ch
                    )
                    self.virtual_generators[ch] = gen
                except Exception as e:
                    logger.error(f"Failed to create virtual generator for Channel {ch}: {e}")

        # Setup Counter (always useful for rates or counts)
        binwidth_ps = int(self.integration_time_sec * 1e12)
        channels_to_measure = channels_to_simulate if channels_to_simulate else [1]
        
        if self.measure_counter_trace:
            logger.info(f"Creating Counter: channels={channels_to_measure}, binwidth={self.integration_time_sec}s")
            try:
                self.counter = TimeTagger.Counter(
                    self.tagger,
                    channels_to_measure,
                    binwidth_ps,
                    self.history_size
                )
            except Exception as e:
                logger.error(f"Failed to create Counter: {e}")
                self.counter = None

        # Setup Coincidence Histogram
        if self.measure_coincidence_histogram:
            start_ch = self.hist_start_channel
            click_ch = self.hist_click_channel
            if self.is_virtual and (start_ch > 18 or click_ch > 18):
                logger.warning(f"Coincidence Histogram start ({start_ch}) or click ({click_ch}) channel exceeds virtual tagger limit (18). Skipping creation.")
                self.histogram = None
                self.simulated_histogram_accum = None
            else:
                logger.info(f"Creating Histogram: start={start_ch}, click={click_ch}, binwidth={self.hist_binwidth_ps}ps, bins={self.hist_n_bins}")
                try:
                    self.histogram = TimeTagger.Histogram(
                        self.tagger,
                        click_ch,
                        start_ch,
                        self.hist_binwidth_ps,
                        self.hist_n_bins
                    )
                    # Initialize simulated histogram accumulator
                    self.simulated_histogram_accum = np.zeros(self.hist_n_bins, dtype=float)
                except Exception as e:
                    logger.error(f"Failed to create histogram: {e}")
                    self.histogram = None
                    self.simulated_histogram_accum = None

        # Setup CorrelationPairs for JSI
        if self.measure_jsi:
            channels_to_measure = [ch for ch in self.active_channels if ch <= 18] if self.is_virtual else self.active_channels
            if len(channels_to_measure) > 1:
                logger.info(f"Creating CorrelationPairs for JSI: channels={channels_to_measure}, binwidth={self.jsi_binwidth_ps}ps, bins={self.jsi_n_bins}")
                try:
                    self.jsi_corr = TimeTagger.CorrelationPairs(
                        self.tagger,
                        channels_to_measure,
                        self.jsi_binwidth_ps,
                        self.jsi_n_bins
                    )
                except Exception as e:
                    logger.error(f"Failed to create CorrelationPairs for JSI: {e}")
                    self.jsi_corr = None

    def start_recording(self) -> str:
        """Starts writing count rate trace to a CSV file."""
        if self.recording_active:
            self.stop_recording()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recording_{timestamp}.csv"
        filepath = os.path.join(RECORDINGS_DIR, filename)
        
        self.recording_fh = open(filepath, mode="w", newline="")
        self.recording_csv_writer = csv.writer(self.recording_fh)
        
        # Headers: Time (s), Channel 1 CPS, Channel 2 CPS, ...
        unit = "CPS" if self.normalize else "Counts"
        headers = ["Elapsed Time (s)", "Timestamp"]
        for ch in self.active_channels:
            alias = self.channel_aliases.get(ch, f"Channel {ch}")
            headers.append(f"{alias} ({unit})")
            
        self.recording_csv_writer.writerow(headers)
        self.recording_file = filepath
        self.recording_start_time = time.time()
        self.recorded_points_count = 0
        self.recording_active = True
        logger.info(f"Started recording to {filepath} ({unit})")
        return filename

    def record_data_point(self, time_elapsed: float, values: List[float]):
        """Writes a single row of counts/CPS data to the CSV file."""
        if not self.recording_active or not self.recording_csv_writer:
            return
        
        row = [f"{time_elapsed:.3f}", datetime.now().isoformat()]
        row.extend([f"{val:.2f}" for val in values])
        self.recording_csv_writer.writerow(row)
        self.recording_fh.flush()
        self.recorded_points_count += 1

    def stop_recording(self) -> Optional[str]:
        """Stops recording and closes the CSV file."""
        if not self.recording_active:
            return None
            
        self.recording_active = False
        if self.recording_fh:
            self.recording_fh.close()
            self.recording_fh = None
            
        filename = os.path.basename(self.recording_file) if self.recording_file else None
        logger.info(f"Stopped recording. Total points: {self.recorded_points_count}. File: {self.recording_file}")
        
        self.recording_file = None
        self.recording_csv_writer = None
        return filename

    def close_tagger(self):
        """Releases the TimeTagger resources."""
        self.counter = None
        self.histogram = None
        self.jsi_corr = None
        self.virtual_generators.clear()
        
        if self.tagger:
            try:
                TimeTagger.freeTimeTagger(self.tagger)
                logger.info("TimeTagger resources released.")
            except Exception as e:
                logger.error(f"Error freeing TimeTagger: {e}")
            self.tagger = None

manager = TaggerManager()
websocket_clients: Set[WebSocket] = set()

@app.on_event("startup")
async def startup_event():
    manager.initialize_tagger()
    asyncio.create_task(broadcast_loop())

@app.on_event("shutdown")
async def shutdown_event():
    manager.stop_recording()
    manager.close_tagger()

# Serve static dashboard
@app.get("/")
async def get_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/api/config")
async def get_config():
    return get_current_config_payload()

def get_current_config_payload():
    return {
        "is_virtual": manager.is_virtual,
        "active_channels": manager.active_channels,
        "channel_aliases": {str(k): v for k, v in manager.channel_aliases.items()},
        "trigger_voltages": {str(k): v for k, v in manager.trigger_voltages.items()},
        "integration_time_sec": manager.integration_time_sec,
        "normalize": manager.normalize,
        "hist_start_channel": manager.hist_start_channel,
        "hist_click_channel": manager.hist_click_channel,
        "hist_binwidth_ps": manager.hist_binwidth_ps,
        "hist_n_bins": manager.hist_n_bins,
        "measure_counter_trace": manager.measure_counter_trace,
        "measure_coincidence_histogram": manager.measure_coincidence_histogram,
        "measure_jsi": manager.measure_jsi,
        "jsi_binwidth_ps": manager.jsi_binwidth_ps,
        "jsi_n_bins": manager.jsi_n_bins,
        "simulation_mode": manager.simulation_mode,
        "recording_active": manager.recording_active,
        "recorded_points_count": manager.recorded_points_count
    }

async def broadcast_loop():
    """Background loop to fetch data from tagger and broadcast to clients."""
    logger.info("Starting background broadcast loop...")
    while True:
        try:
            sleep_time = max(0.05, min(0.5, manager.integration_time_sec / 2.0))
            await asyncio.sleep(sleep_time)
            
            if not manager.tagger or not websocket_clients:
                continue

            # Read Counter Trace if enabled
            channels = manager.active_channels if manager.active_channels else [1]
            aliases = [manager.channel_aliases.get(ch, f"Channel {ch}") for ch in channels]
            
            history_data = [[] for _ in channels]
            time_axis = []
            latest_values = [0.0] * len(channels)
            
            if manager.measure_counter_trace and manager.counter:
                raw_data = manager.counter.getData() # shape: (len(channels), history_size)
                
                # Check for normalization (CPS vs raw counts)
                if manager.normalize:
                    norm_factor = manager.integration_time_sec
                    data_to_send = raw_data / norm_factor if norm_factor > 0 else raw_data
                else:
                    data_to_send = raw_data.astype(float)
                
                history_data = data_to_send.tolist()
                
                indices_ps = manager.counter.getIndex()
                if len(indices_ps) > 0:
                    time_axis = [(t - indices_ps[0]) / 1e12 for t in indices_ps]
                else:
                    time_axis = list(range(manager.history_size))
                
                # Get the latest values for the current bar chart and recording
                for ch_idx in range(len(channels)):
                    latest_val = data_to_send[ch_idx][-1] if len(data_to_send[ch_idx]) > 0 else 0.0
                    latest_values[ch_idx] = float(latest_val)
            else:
                # Fallback if trace is disabled
                history_data = [[0.0] * manager.history_size for _ in channels]
                time_axis = list(range(manager.history_size))
                latest_values = [0.0] * len(channels)

            # Record if active
            if manager.recording_active:
                elapsed = time.time() - manager.recording_start_time
                manager.record_data_point(elapsed, latest_values)

            # Read Coincidence Histogram if enabled
            hist_values = []
            hist_indices = []
            
            if manager.measure_coincidence_histogram and manager.histogram:
                raw_hist = manager.histogram.getData().astype(float)
                indices_ps = manager.histogram.getIndex()
                hist_indices = (indices_ps / 1000.0).tolist() # ps to ns
                
                # If simulation mode is active and we're virtual, accumulate synthetic coincidence delays
                if manager.simulation_mode and manager.is_virtual:
                    # Let's generate synthetic delays around 1.5 ns with standard deviation 0.2 ns
                    coinc_rate = 300.0  # Hz
                    dt = sleep_time
                    n_new_events = int(np.random.poisson(coinc_rate * dt))
                    
                    if n_new_events > 0 and manager.simulated_histogram_accum is not None:
                        mean_ns = 1.5
                        std_ns = 0.2
                        delays = np.random.normal(mean_ns, std_ns, n_new_events)
                        
                        bin_width_ns = manager.hist_binwidth_ps / 1000.0
                        start_ns = hist_indices[0] if len(hist_indices) > 0 else 0.0
                        
                        for d in delays:
                            bin_idx = int((d - start_ns) / bin_width_ns)
                            if 0 <= bin_idx < manager.hist_n_bins:
                                manager.simulated_histogram_accum[bin_idx] += 1.0
                    
                    if manager.simulated_histogram_accum is not None:
                        hist_values = (raw_hist + manager.simulated_histogram_accum).tolist()
                    else:
                        hist_values = raw_hist.tolist()
                else:
                    hist_values = raw_hist.tolist()
            else:
                hist_values = [0] * manager.hist_n_bins
                hist_indices = [i * (manager.hist_binwidth_ps / 1000.0) for i in range(manager.hist_n_bins)]

            # Read JSI correlation pairs if enabled
            jsi_matrix = []
            jsi_channels = []
            
            if manager.measure_jsi:
                jsi_channels = [ch for ch in manager.active_channels if ch <= 18] if manager.is_virtual else manager.active_channels
                n_ch = len(jsi_channels)
                
                if n_ch > 1:
                    if manager.jsi_corr:
                        try:
                            # Retrieve data from CorrelationPairs
                            data = manager.jsi_corr.getDataObject()
                            counts = data.getCounts(exclude_self_coincidences=True)
                            
                            # counts shape: (n_ch, n_ch, jsi_n_bins)
                            # Build JSI matrix by summing peak area
                            jsi_arr = np.zeros((n_ch, n_ch))
                            for i in range(n_ch):
                                for j in range(n_ch):
                                    hist = counts[i, j, :]
                                    if np.sum(hist) == 0:
                                        continue
                                    peak = np.argmax(hist)
                                    left = max(0, peak - 5)
                                    right = min(len(hist), peak + 6)
                                    jsi_arr[i, j] = float(np.sum(hist[left:right]))
                            jsi_matrix = jsi_arr.tolist()
                        except Exception as e:
                            logger.error(f"Error reading CorrelationPairs counts: {e}")
                            jsi_matrix = np.zeros((n_ch, n_ch)).tolist()
                    elif manager.simulation_mode and manager.is_virtual:
                        # Simulated JSI matrix
                        jsi_arr = np.zeros((n_ch, n_ch))
                        for i in range(n_ch):
                            for j in range(n_ch):
                                dist = abs(i + j - (n_ch - 1))
                                val = 1500.0 * np.exp(-dist**2 / 1.5)
                                if i == j:
                                    val += 300.0
                                jsi_arr[i, j] = float(max(0.0, np.random.normal(val, np.sqrt(val) + 5)))
                        jsi_matrix = jsi_arr.tolist()
                    else:
                        jsi_matrix = np.zeros((n_ch, n_ch)).tolist()

            # Construct message
            payload = {
                "type": "data",
                "is_virtual": manager.is_virtual,
                "normalize": manager.normalize,
                "cps": {
                    "channels": channels,
                    "aliases": aliases,
                    "history": history_data,
                    "time_index": time_axis,
                    "latest": latest_values
                },
                "histogram": {
                    "start_channel": manager.hist_start_channel,
                    "click_channel": manager.hist_click_channel,
                    "values": hist_values,
                    "index": hist_indices
                },
                "jsi": {
                    "channels": jsi_channels,
                    "matrix": jsi_matrix
                },
                "recording": {
                    "active": manager.recording_active,
                    "points": manager.recorded_points_count,
                    "elapsed": time.time() - manager.recording_start_time if manager.recording_active else 0.0
                }
            }

            # Send to all connected websockets
            message = json.dumps(payload)
            await asyncio.gather(
                *[client.send_text(message) for client in websocket_clients],
                return_exceptions=True
            )

        except Exception as e:
            logger.error(f"Error in broadcast loop: {e}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.add(websocket)
    logger.info(f"WebSocket client connected. Total clients: {len(websocket_clients)}")
    
    # Send current configuration immediately
    try:
        await websocket.send_text(json.dumps({
            "type": "config",
            "config": get_current_config_payload()
        }))
    except Exception as e:
        logger.error(f"Error sending initial config: {e}")
        
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type")
            
            logger.info(f"Received WebSocket message: {msg_type}")
            
            if msg_type == "save_config":
                # Save configuration details coming from main config page
                cfg = message.get("config", {})
                
                # Active channels
                manager.active_channels = sorted([int(ch) for ch in cfg.get("active_channels", [1, 2]) if 1 <= int(ch) <= 20])
                
                # Aliases
                for ch_str, alias in cfg.get("channel_aliases", {}).items():
                    manager.channel_aliases[int(ch_str)] = alias
                    
                # Voltages
                for ch_str, volt in cfg.get("trigger_voltages", {}).items():
                    manager.trigger_voltages[int(ch_str)] = float(volt)
                    
                # Other settings
                manager.integration_time_sec = max(0.01, min(10.0, float(cfg.get("integration_time_sec", 0.1))))
                manager.normalize = bool(cfg.get("normalize", True))
                
                # Histogram settings
                manager.hist_start_channel = int(cfg.get("hist_start_channel", 1))
                manager.hist_click_channel = int(cfg.get("hist_click_channel", 2))
                manager.hist_binwidth_ps = int(cfg.get("hist_binwidth_ps", 100))
                manager.hist_n_bins = int(cfg.get("hist_n_bins", 400))
                
                # JSI settings
                manager.measure_jsi = bool(cfg.get("measure_jsi", True))
                manager.jsi_binwidth_ps = int(cfg.get("jsi_binwidth_ps", 100))
                manager.jsi_n_bins = int(cfg.get("jsi_n_bins", 2000))
                
                # Measurement selections
                manager.measure_counter_trace = bool(cfg.get("measure_counter_trace", True))
                manager.measure_coincidence_histogram = bool(cfg.get("measure_coincidence_histogram", True))
                manager.simulation_mode = bool(cfg.get("simulation_mode", True))
                
                # Apply and save to file
                manager.apply_configuration()
                manager.save_state_to_disk()
                
            elif msg_type == "update_normalize":
                manager.normalize = bool(message.get("normalize", True))
                manager.apply_configuration()
                manager.save_state_to_disk()
                
            elif msg_type == "start_recording":
                filename = manager.start_recording()
                await websocket.send_text(json.dumps({
                    "type": "recording_started",
                    "filename": filename
                }))
                
            elif msg_type == "stop_recording":
                filename = manager.stop_recording()
                await websocket.send_text(json.dumps({
                    "type": "recording_stopped",
                    "filename": filename
                }))
                
            elif msg_type == "reconnect_hardware":
                logger.info("User requested hardware reconnection.")
                manager.initialize_tagger()
                
            # Broadcast the updated configuration back to all clients
            config_payload = json.dumps({
                "type": "config",
                "config": get_current_config_payload()
            })
            await asyncio.gather(
                *[client.send_text(config_payload) for client in websocket_clients],
                return_exceptions=True
            )
            
    except WebSocketDisconnect:
        websocket_clients.remove(websocket)
        logger.info(f"WebSocket client disconnected. Total clients: {len(websocket_clients)}")
    except Exception as e:
        logger.error(f"Error handling WebSocket client: {e}")
        if websocket in websocket_clients:
            websocket_clients.remove(websocket)

# Mount static directory
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

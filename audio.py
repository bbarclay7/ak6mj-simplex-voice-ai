"""AIOC hardware interface: audio I/O, PTT control, VOX recording."""

import threading
import time
import logging

import numpy as np
import sounddevice as sd
import serial
import serial.tools.list_ports

logger = logging.getLogger(__name__)

AIOC_VID = 0x1209
AIOC_PID = 0x7388

# Digirig Mobile (CP2102N)
DIGIRIG_VID = 0x10C4
DIGIRIG_PID = 0xEA60


class AIOC:
    """AIOC cable interface: audio device discovery + PTT via serial DTR/RTS."""

    def __init__(self, config: dict, dry_run: bool = False):
        self.dry_run = dry_run
        self.sample_rate = config["aioc"]["sample_rate"]
        self.channels = config["aioc"]["channels"]
        self._serial_port_cfg = config["aioc"]["serial_port"]
        self._audio_device_name = config["aioc"]["audio_device"]
        self.serial_port: serial.Serial | None = None
        self.input_device: int | None = None
        self.output_device: int | None = None

    def open(self):
        """Discover devices and open serial port."""
        self._discover_audio()
        if not self.dry_run:
            self._discover_serial()
            self._open_serial()
        self.ptt_off()
        logger.info(
            f"AIOC ready — audio in={self.input_device} out={self.output_device} "
            f"serial={'DRY RUN' if self.dry_run else self.serial_port.port}"
        )

    def close(self):
        """Release resources."""
        self.ptt_off()
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            logger.info("Serial port closed.")

    # --- Device Discovery ---

    def _discover_audio(self):
        """Find AIOC audio input/output device indices."""
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if self._audio_device_name.lower() in dev["name"].lower():
                if dev["max_input_channels"] > 0 and self.input_device is None:
                    self.input_device = i
                if dev["max_output_channels"] > 0 and self.output_device is None:
                    self.output_device = i
        if self.dry_run:
            # Use system defaults
            if self.input_device is None:
                self.input_device = sd.default.device[0]
            if self.output_device is None:
                self.output_device = sd.default.device[1]
            logger.info(f"Dry run: using default audio devices in={self.input_device} out={self.output_device}")
        else:
            if self.input_device is None or self.output_device is None:
                raise RuntimeError(
                    f"AIOC audio device '{self._audio_device_name}' not found. "
                    f"Available: {[d['name'] for d in devices]}"
                )
            logger.info(f"AIOC audio: in={self.input_device} ({devices[self.input_device]['name']})")

    def _discover_serial(self):
        """Find AIOC serial port by USB VID:PID."""
        if self._serial_port_cfg != "auto":
            self._serial_path = self._serial_port_cfg
            return

        ports = serial.tools.list_ports.comports()
        for port in ports:
            if (port.vid == AIOC_VID and port.pid == AIOC_PID) or \
               (port.vid == DIGIRIG_VID and port.pid == DIGIRIG_PID):
                # Prefer /dev/cu.* on macOS (non-blocking)
                path = port.device
                if "/dev/tty." in path:
                    path = path.replace("/dev/tty.", "/dev/cu.")
                self._serial_path = path
                logger.info(f"AIOC serial: {self._serial_path}")
                return

        raise RuntimeError(
            f"AIOC serial port not found (VID:{AIOC_VID:04X} PID:{AIOC_PID:04X}). "
            f"Available: {[p.device for p in ports]}"
        )

    def _open_serial(self):
        """Open the serial port with PTT-safe initial state."""
        ser = serial.Serial()
        ser.port = self._serial_path
        ser.baudrate = 9600
        ser.rtscts = False  # disable hardware flow control so we own RTS
        ser.dtr = False
        ser.rts = False
        ser.open()
        self.serial_port = ser

    # --- PTT Control ---

    def ptt_on(self):
        """Key transmitter: RTS=True (Digirig) or DTR=True (AIOC legacy)."""
        if self.dry_run or not self.serial_port:
            logger.debug("[DRY RUN] PTT ON")
            return
        self.serial_port.rts = True
        time.sleep(0.3)  # let TX relay + CTCSS settle

    def ptt_off(self):
        """Unkey transmitter: RTS=False."""
        if self.dry_run or not self.serial_port:
            logger.debug("[DRY RUN] PTT OFF")
            return
        self.serial_port.rts = False


class VOXRecorder:
    """Record audio when squelch opens, return when it closes."""

    def __init__(self, aioc: AIOC, config: dict):
        self.aioc = aioc
        self.threshold_dbfs = config["vox"]["threshold_dbfs"]
        self.hang_time = config["vox"]["hang_time_sec"]
        self.min_duration = config["vox"]["min_transmission_sec"]
        self.max_duration = config["vox"]["max_transmission_sec"]
        self.sample_rate = aioc.sample_rate
        self.channels = aioc.channels
        self._stop = threading.Event()
        self._muted = threading.Event()  # set = muted (ignore audio)

    def stop(self):
        """Signal the recorder to stop waiting."""
        self._stop.set()

    def mute(self):
        """Mute VOX detection (e.g., while transmitting to avoid self-trigger)."""
        self._muted.set()

    def unmute(self):
        """Resume VOX detection."""
        self._muted.clear()

    def wait_for_transmission(self) -> np.ndarray | None:
        """
        Block until a transmission is detected, record it, return audio.
        Returns None if too short (noise burst). Returns float32 numpy array.
        """
        self._muted.clear()  # ensure unmuted at start of each listen cycle
        frames: list[np.ndarray] = []
        recording = False
        last_above = 0.0
        done_event = threading.Event()
        max_frames = int(self.max_duration * self.sample_rate)
        total_frames = 0
        last_level_log = [0.0]  # mutable for closure

        def callback(indata, frame_count, time_info, status):
            nonlocal recording, last_above, total_frames
            if status:
                logger.debug(f"Audio status: {status}")

            level = rms_dbfs(indata)
            now = time.monotonic()

            # Log level periodically so we can diagnose threshold issues
            if now - last_level_log[0] > 2.0:
                logger.debug(
                    f"Audio level: {level:.1f} dBFS "
                    f"(threshold: {self.threshold_dbfs} dBFS) "
                    f"{'[RECORDING]' if recording else ''}"
                )
                last_level_log[0] = now

            if self._muted.is_set():
                return

            if level >= self.threshold_dbfs:
                last_above = now
                if not recording:
                    recording = True
                    logger.info(f"VOX open ({level:.1f} dBFS)")
                frames.append(indata.copy())
                total_frames += frame_count
            elif recording:
                frames.append(indata.copy())
                total_frames += frame_count
                if (now - last_above) > self.hang_time:
                    logger.info("VOX closed (hang time expired)")
                    done_event.set()

            if total_frames >= max_frames:
                logger.warning("Max recording length reached")
                done_event.set()

        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            device=self.aioc.input_device,
            dtype="float32",
            blocksize=1024,
            callback=callback,
        )

        with stream:
            # Poll so we can be interrupted by stop()
            while not done_event.is_set() and not self._stop.is_set():
                done_event.wait(timeout=0.5)

        if not frames:
            return None

        audio = np.concatenate(frames, axis=0).squeeze()
        duration = len(audio) / self.sample_rate

        if duration < self.min_duration:
            logger.debug(f"Ignoring short burst ({duration:.2f}s)")
            return None

        logger.info(f"Recorded {duration:.1f}s of audio")
        return audio


def rms_dbfs(block: np.ndarray) -> float:
    """Compute RMS level in dBFS from a float32 audio block."""
    rms = np.sqrt(np.mean(block.astype(np.float64) ** 2))
    if rms < 1e-10:
        return -100.0
    return 20.0 * np.log10(rms)


def play_audio(audio: np.ndarray, sample_rate: int, aioc: AIOC):
    """Play audio through AIOC output (blocking)."""
    sd.play(audio, samplerate=sample_rate, device=aioc.output_device)
    sd.wait()


def monitor_levels(aioc: AIOC):
    """Print live audio levels from the input device. Ctrl+C to stop."""
    import sys

    last_print = [0.0]
    peak = [-100.0]

    def callback(indata, frame_count, time_info, status):
        level = rms_dbfs(indata)
        peak[0] = max(peak[0], level)
        now = time.monotonic()
        if now - last_print[0] < 0.2:  # update 5x/sec, not 47x
            return
        last_print[0] = now
        bar_len = max(0, int((level + 60) * 1.5))
        bar = "#" * bar_len
        peak_marker = f"  peak: {peak[0]:.1f}"
        sys.stdout.write(f"\r  {level:6.1f} dBFS |{bar:<60}|{peak_marker}")
        sys.stdout.flush()
        peak[0] = -100.0  # reset peak each print

    stream = sd.InputStream(
        samplerate=aioc.sample_rate,
        channels=aioc.channels,
        device=aioc.input_device,
        dtype="float32",
        blocksize=1024,
        callback=callback,
    )
    print("Speak into mic — watch the levels. Ctrl+C to stop.\n", flush=True)
    stream.start()
    try:
        while True:
            sd.sleep(100)
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()
        stream.close()
        print("\n", flush=True)

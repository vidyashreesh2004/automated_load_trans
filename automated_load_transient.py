#!/usr/bin/env python3
"""
Automated load transient test.
Measures voltage response when a DC electronic load steps between two current levels.
Uses a PSU, electronic load, and oscilloscope over VISA (TCP/IP).
"""

import os
import time
import csv
from datetime import datetime

import pyvisa
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks  # not used but kept for potential peak detection


# ----- Configuration -----------------------------------------------------------
CONFIG = {
    "psu_ip": "192.168.1.100",
    "load_ip": "192.168.1.101",
    "scope_ip": "192.168.1.102",
    "vout_nominal": 3.3,             # V
    "i_min": 0.5,                    # A (low level)
    "i_max": 4.5,                    # A (high level)
    "v_undershoot_limit": 0.15,      # V max allowed dip
    "v_overshoot_limit": 0.15,       # V max allowed peak
    "settling_time_limit": 50e-6,    # seconds (50 µs)
    "scope_timeout": 10000,          # ms
    "report_dir": "./test_reports/"
}
os.makedirs(CONFIG['report_dir'], exist_ok=True)


# ----- Instrument drivers ------------------------------------------------------
class KeithleyPSU:
    """Simple interface for a Keithley DC power supply."""
    def __init__(self, rm, resource_str):
        self.inst = rm.open_resource(resource_str)
        self.inst.timeout = 5000
        self.inst.write("*RST")

    def set_voltage(self, v):
        self.inst.write(f"VOLT {v}")

    def set_current_limit(self, i):
        self.inst.write(f"CURR {i}")

    def output(self, state):
        self.inst.write(f"OUTP {1 if state else 0}")


class KeithleyLoad:
    """Simple interface for a Keithley DC electronic load."""
    def __init__(self, rm, resource_str):
        self.inst = rm.open_resource(resource_str)
        self.inst.timeout = 5000
        self.inst.write("*RST")
        self.inst.write("MODE CURR")   # constant current mode

    def set_current(self, i):
        self.inst.write(f"CURR {i}")

    def set_transient(self, i_low, i_high, freq, duty):
        """Set up continuous transient between low and high current."""
        self.inst.write(f"CURR:LEV {i_high}")          # high level
        self.inst.write(f"CURR:LEV:LOW {i_low}")       # low level
        self.inst.write("FUNC:MODE TRAN")              # enable transient
        self.inst.write(f"TRAN:FREQ {freq}")           # frequency (Hz)
        self.inst.write(f"TRAN:DUTY {duty}")           # duty cycle in %
        self.inst.write("TRAN:STAR")                   # start continuous transient

    def stop(self):
        self.inst.write("TRAN:STOP")

    def input(self, state):
        self.inst.write(f"INP {1 if state else 0}")


class KeysightScope:
    """Simple interface for a Keysight InfiniiVision oscilloscope."""
    def __init__(self, rm, resource_str):
        self.inst = rm.open_resource(resource_str)
        self.inst.timeout = 10000
        self.inst.write("*RST")

    def setup_transient_capture(self, channel=1, v_scale=0.2, t_scale=100e-6):
        """Configure scope for capturing a transient on a voltage channel."""
        self.inst.write(f":CHAN{channel}:SCAL {v_scale}")     # V/div
        self.inst.write(f":TIM:SCAL {t_scale}")               # s/div
        self.inst.write(":TRIG:MODE EDGE")
        self.inst.write(f":TRIG:EDGE:SOUR CH{channel}")
        self.inst.write(":TRIG:EDGE:SLOP POS")
        self.inst.write(":TRIG:LEV 0.1")                      # trigger at 100 mV above zero
        self.inst.write(":ACQ:TYPE AVER")                     # average 16 acquisitions
        self.inst.write(":ACQ:COUN 16")
        self.inst.write(":WAV:POIN:MODE MAX")                 # use maximum points

    def arm_trigger(self):
        self.inst.write(":ARM")

    def wait_for_trigger(self, timeout=5):
        """Poll until trigger occurs or timeout."""
        start = time.time()
        while True:
            trig_state = self.inst.query(":TRIG:STAT?").strip()
            if trig_state == "TRIG":
                return True
            if time.time() - start > timeout:
                return False
            time.sleep(0.1)

    def get_waveform(self, channel=1):
        """Fetch waveform as time and voltage arrays."""
        self.inst.write(f":WAV:SOUR CHAN{channel}")
        self.inst.write(":WAV:FORM ASC")
        data_str = self.inst.query(":WAV:DATA?").strip()

        # Parse ASCII data: #<digits><length><values>
        header = data_str[0:2]          # e.g., "#9"
        num_digits = int(header[1])
        length = int(data_str[2:2+num_digits])
        raw_values = data_str[2+num_digits:].split(',')
        volts = [float(v) for v in raw_values]

        # Get time scale from preamble
        preamble = self.inst.query(":WAV:PRE?").split(',')
        x_inc = float(preamble[4])      # seconds per point
        x_orig = float(preamble[5])
        t = [x_orig + i * x_inc for i in range(len(volts))]

        return np.array(t), np.array(volts)


# ----- Main test function ------------------------------------------------------
def run_transient_test(config):
    """Run one transient test iteration and return results."""
    rm = pyvisa.ResourceManager()

    # Connect instruments
    psu = KeithleyPSU(rm, f"TCPIP::{config['psu_ip']}::INSTR")
    load = KeithleyLoad(rm, f"TCPIP::{config['load_ip']}::INSTR")
    scope = KeysightScope(rm, f"TCPIP::{config['scope_ip']}::INSTR")

    # Power up the DUT
    psu.set_voltage(5.0)
    psu.set_current_limit(10.0)         # generous limit
    psu.output(True)
    time.sleep(0.5)

    # Start load transient (square wave current)
    load.set_transient(config['i_min'], config['i_max'], freq=1000, duty=50)
    load.input(True)

    # Arm scope and wait for trigger
    scope.setup_transient_capture(channel=1, v_scale=0.2, t_scale=100e-6)
    scope.arm_trigger()
    if not scope.wait_for_trigger(timeout=5):
        raise RuntimeError("Scope trigger timeout – check load transient is running.")

    # Acquire waveform
    t, v = scope.get_waveform(channel=1)

    # Analyse data
    v_nom = config['vout_nominal']
    v_peak = np.max(v)
    v_valley = np.min(v)
    undershoot = v_nom - v_valley
    overshoot = v_peak - v_nom

    # Settling time: find step edge via derivative, then search for stable point
    dv = np.gradient(v, t)
    edge_idx = np.argmax(np.abs(dv))
    final_value = np.mean(v[-100:])          # assume settled at end
    tolerance = 0.01 * v_nom                 # 1% settling band
    settle_idx = None
    for i in range(edge_idx, len(v)):
        if np.all(np.abs(v[i:i+20] - final_value) < tolerance):
            settle_idx = i
            break
    if settle_idx is not None:
        settling_time = t[settle_idx] - t[edge_idx]
    else:
        settling_time = np.nan

    # Pass/fail checks
    pass_undershoot = undershoot <= config['v_undershoot_limit']
    pass_overshoot = overshoot <= config['v_overshoot_limit']
    pass_settling = (settling_time <= config['settling_time_limit']) if not np.isnan(settling_time) else False
    overall_pass = pass_undershoot and pass_overshoot and pass_settling

    # Log results to CSV
    result_row = {
        'timestamp': datetime.now().isoformat(),
        'v_nominal': v_nom,
        'i_min': config['i_min'],
        'i_max': config['i_max'],
        'undershoot_V': undershoot,
        'overshoot_V': overshoot,
        'settling_time_us': settling_time * 1e6 if not np.isnan(settling_time) else np.nan,
        'pass_undershoot': pass_undershoot,
        'pass_overshoot': pass_overshoot,
        'pass_settling': pass_settling,
        'overall_pass': overall_pass
    }
    df_results = pd.DataFrame([result_row])
    csv_file = f"{config['report_dir']}transient_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df_results.to_csv(csv_file, mode='a', header=not os.path.exists(csv_file), index=False)

    # Generate plot
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(t * 1e6, v, linewidth=1.5, color='blue')
    ax.axhline(y=v_nom, color='green', linestyle='--', label=f'Nominal {v_nom}V')
    ax.axhline(y=v_nom - config['v_undershoot_limit'], color='red', linestyle=':', label='Undershoot limit')
    ax.axhline(y=v_nom + config['v_overshoot_limit'], color='red', linestyle=':', label='Overshoot limit')
    ax.set_xlabel('Time (µs)')
    ax.set_ylabel('Voltage (V)')
    ax.set_title('Load Transient Response')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.text(0.05, 0.95,
            f"Undershoot: {undershoot*1000:.1f} mV\nOvershoot: {overshoot*1000:.1f} mV\nSettling: {settling_time*1e6:.1f} µs",
            transform=ax.transAxes, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    plot_file = f"{config['report_dir']}transient_plot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    plt.savefig(plot_file, dpi=150)
    plt.close()

    # Clean up
    load.stop()
    load.input(False)
    psu.output(False)

    return overall_pass, result_row, plot_file


# ----- Run multiple iterations and produce summary -----------------------------
if __name__ == "__main__":
    iterations = 10
    all_results = []
    for i in range(iterations):
        print(f"Iteration {i+1}/{iterations} ...")
        try:
            passed, row, plot = run_transient_test(CONFIG)
            all_results.append(row)
        except Exception as e:
            print(f"Error: {e}")
            all_results.append({'overall_pass': False, 'error': str(e)})

    # Summary
    summary_df = pd.DataFrame(all_results)
    summary_file = f"{CONFIG['report_dir']}summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    summary_df.to_csv(summary_file, index=False)
    print("\nTest complete. Summary of passes/fails:")
    print(summary_df['overall_pass'].value_counts())

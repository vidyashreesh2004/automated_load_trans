# Automated Load Transient Test

This Python script automates the measurement of a DC-DC converter’s or power supply’s load transient response. It controls a programmable DC power supply (Keithley), an electronic load (Keithley) operating in transient current mode, and an oscilloscope (Keysight InfiniiVision) to capture and analyse the voltage waveform when the load steps between two current levels.

The script:

- Configures the PSU to power the DUT.
- Sets the electronic load to generate a square‑wave current (low/high levels).
- Triggers the oscilloscope on the transient edge.
- Acquires the voltage waveform.
- Computes undershoot, overshoot and settling time.
- Evaluates pass/fail against user‑defined limits.
- Logs results to CSV and generates a plot.
- Runs multiple iterations to catch intermittent failures.

---

## Requirements

- **Python 3.6+**
- **PyVISA** – instrument communication over VISA (TCP/IP)
- **NumPy**, **Pandas** – data analysis
- **Matplotlib** – plotting
- **SciPy** – (optional) used for peak finding (kept for possible extension)

Install all dependencies with:

```bash
pip install pyvisa pandas numpy matplotlib scipy

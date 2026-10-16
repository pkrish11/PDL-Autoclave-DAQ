
import threading
import time
import math
from datetime import datetime

import pandas as pd
import requests
import plotly.graph_objects as go

from dash import Dash, dcc, html, Input, Output, State
from dash.exceptions import PreventUpdate


# ============================================================
# Configuration
# ============================================================

TEENSY_URL = "http://192.168.1.10"

SAMPLING_INTERVAL = 0.5  # Seconds between acquisitions : keep smaller than sampling time on Teensy
                        # to ensure most recent update recieved always.
PLOT_INTERVAL = 1000     # Dashboard refresh interval (ms)

TEENSY_SAMPLING_TIME = 1.0  # Seconds between samples on the Teensy (SAMPLING_TIME / 1000)

# If two consecutive Teensy samples are further apart than this (seconds),
# the plot leaves that span empty instead of drawing a line across it.
GAP_THRESHOLD = 3 * TEENSY_SAMPLING_TIME

# ============================================================
# Shared data and state
# ============================================================

measurements = []
data_lock = threading.Lock()

recording_event = threading.Event()

connection_status = "Not recording"
last_error = ""
last_sample_ms = None  # Teensy millis() of the last stored sample, used to skip duplicates

# ============================================================
# HTTP setpoint communication
# ============================================================

def send_setpoints(temp_setpoint, pressure_setpoint):
    """
    Send both setpoints to the Teensy.

    Returns (success, message).
    """

    if temp_setpoint is None or pressure_setpoint is None:
        return False, "Enter both setpoints."

    try:
        temp_setpoint = float(temp_setpoint)
        pressure_setpoint = float(pressure_setpoint)

        if not (
            math.isfinite(temp_setpoint)
            and math.isfinite(pressure_setpoint)
        ):
            return False, "Setpoints must be finite numbers."

        # requests encodes the query parameters automatically.
        with requests.Session() as session:
            session.trust_env = False

            response = session.get(
                TEENSY_URL + "/setpoints",
                params={
                    "temp": temp_setpoint,
                    "pressure": pressure_setpoint,
                },
                timeout=3,
            )

            response.raise_for_status()

            result = response.json()

            if result.get("status") != "ok":
                return False, f"Teensy rejected setpoints: {result}"

        return (
            True,
            f"Setpoints sent: {temp_setpoint:.2f} °C, "
            f"{pressure_setpoint:.2f} psi-G",
        )

    except (requests.RequestException, ValueError) as e:
        return False, f"Setpoint communication error: {e}"


# ============================================================
# Helpers
# ============================================================

def heater_text(heater_stat):
    if heater_stat is None:
        return "--"
    return "ON" if heater_stat else "OFF"


def insert_gaps(timestamps, values, max_gap_s):
    """
    Return x/y lists with a None inserted wherever consecutive samples
    are more than max_gap_s seconds apart (laptop time). Plotly does not
    draw a line across None values, so those spans stay empty on the plot.
    """

    xs = []
    ys = []
    previous = None

    for t, v in zip(timestamps, values):
        if previous is not None and (t - previous).total_seconds() > max_gap_s:
            xs.append(previous)
            ys.append(None)
        xs.append(t)
        ys.append(v)
        previous = t

    return xs, ys


# ============================================================
# Ethernet acquisition thread
# ============================================================

def acquire_data():
    global connection_status, last_error, last_sample_ms

    session = requests.Session()
    session.trust_env = False

    while True:

        # Wait here until Start has been clicked successfully.
        recording_event.wait()

        start = time.monotonic()

        try:
            response = session.get(TEENSY_URL + "/", timeout=2)
            response.raise_for_status()

            data = response.json()

            # millis() on the Teensy when this sample was taken.
            sample_ms = int(data["sample_ms"])
            temperature = float(data["temperature"])
            pressure = float(data["pressure"])

            if not (math.isfinite(temperature)
                    and math.isfinite(pressure)):
                raise ValueError("Invalid sensor measurements")

            # Heater status: 1 = on, 0 = off.
            # None if the Teensy firmware does not send it yet.
            heater_raw = data.get("heater_stat")
            heater_stat = None if heater_raw is None else int(bool(heater_raw))

            timestamp = datetime.now().astimezone().isoformat(
                timespec="milliseconds"
            )

            # The laptop polls faster than the Teensy samples, so the same
            # sample is often received twice. Only store new ones.
            if sample_ms == last_sample_ms:
                with data_lock:
                    connection_status = "Connected / Recording"
                    last_error = ""
                elapsed = time.monotonic() - start
                time.sleep(max(0, SAMPLING_INTERVAL - elapsed))
                continue

            sample = {
                "teensy_time_s": sample_ms / 1000.0,
                "timestamp": timestamp,
                "temperature_C": temperature,
                "pressure_psiG": pressure,
                "heater_stat": heater_stat,
            }

            with data_lock:
                measurements.append(sample)
                last_sample_ms = sample_ms
                connection_status = "Connected / Recording"
                last_error = ""

            print(
                f"{timestamp} | "
                f"Teensy t: {sample_ms / 1000.0:.3f} s | "
                f"Temp: {temperature:.2f} °C | "
                f"Pressure: {pressure:.2f} psi-G | "
                f"Heater: {heater_text(heater_stat)}"
            )

        except (requests.RequestException, ValueError, KeyError) as e:

            with data_lock:
                connection_status = "Recording / Ethernet error"
                last_error = str(e)

            print("Ethernet error:", e)

        elapsed = time.monotonic() - start
        time.sleep(max(0, SAMPLING_INTERVAL - elapsed))


# ============================================================
# Dash application
# ============================================================

app = Dash(__name__)

app.layout = html.Div(
    [
        html.H1("PDL Autoclave DAQ Dashboard"),

        # -----------------------
        # Setpoint controls
        # -----------------------
        html.Div(
            [
                html.H3("Setpoint Configuration"),

                html.Div(
                    [
                        html.Div(
                            [
                                html.Label("Temperature Setpoint (°C)"),
                                dcc.Input(
                                    id="temp-setpoint",
                                    type="number",
                                    value=80.0,
                                    debounce=True,
                                    step="any",
                                    style={"width": "180px"},
                                ),
                            ]
                        ),

                        html.Div(
                            [
                                html.Label("Pressure Setpoint (psi-G)"),
                                dcc.Input(
                                    id="pressure-setpoint",
                                    type="number",
                                    value=20.0,
                                    debounce=True,
                                    step="any",
                                    style={"width": "180px"},
                                ),
                            ]
                        ),
                    ],
                    style={
                        "display": "flex",
                        "gap": "35px",
                        "flexWrap": "wrap",
                    },
                ),

                html.Br(),

                html.Button(
                    "Send Setpoints",
                    id="setpoint-button",
                    n_clicks=0,
                    style={
                        "padding": "12px 24px",
                        "fontSize": "16px",
                        "cursor": "pointer",
                    },
                ),

                html.P(
                    "Enter setpoints, then press Send Setpoints.",
                    id="setpoint-status",
                ),

                html.Hr(),

                html.Button(
                    "Start Plotting / Recording",
                    id="start-button",
                    n_clicks=0,
                    style={
                        "padding": "12px 24px",
                        "fontSize": "16px",
                        "cursor": "pointer",
                    },
                ),

                html.P(
                    "Press Start to begin recording.",
                    id="control-status",
                ),
            ],
            style={
                "backgroundColor": "#f4f6f8",
                "padding": "20px",
                "borderRadius": "8px",
                "marginBottom": "20px",
            },
        ),

        # -----------------------
        # Current measurements
        # -----------------------
        html.Div(
            [
                html.H3("Live Measurements"),
                html.H2("-- °C", id="temp-display"),
                html.H2("-- psi-G", id="pressure-display"),
                html.H2("Heater status: --", id="heater-display"),
                html.P("Not recording", id="status-display"),
                html.P("", id="error-display"),
                html.P("Samples collected: 0", id="sample-count"),
            ],
            style={
                "backgroundColor": "#f4f6f8",
                "padding": "20px",
                "borderRadius": "8px",
                "marginBottom": "20px",
            },
        ),

        dcc.Graph(id="temperature-graph"),
        dcc.Graph(id="pressure-graph"),

        html.Button(
            "Save CSV",
            id="save-button",
            n_clicks=0,
            style={
                "padding": "12px 24px",
                "fontSize": "16px",
            },
        ),

        dcc.Download(id="csv-download"),

        dcc.Interval(
            id="plot-interval",
            interval=PLOT_INTERVAL,
            n_intervals=0,
        ),
    ],
    style={
        "maxWidth": "1100px",
        "margin": "auto",
        "padding": "30px",
        "fontFamily": "Arial, sans-serif",
    },
)


# ============================================================
# Send Setpoints button callback
# ============================================================

@app.callback(
    Output("setpoint-status", "children"),
    Input("setpoint-button", "n_clicks"),
    State("temp-setpoint", "value"),
    State("pressure-setpoint", "value"),
    prevent_initial_call=True,
)
def update_setpoints(n_clicks, temp_setpoint, pressure_setpoint):

    # Setpoints are only sent when the button is clicked;
    # editing the input fields alone does nothing.
    success, message = send_setpoints(
        temp_setpoint,
        pressure_setpoint,
    )

    timestamp = datetime.now().strftime("%H:%M:%S")

    return f"[{timestamp}] {message}"


# ============================================================
# Start button callback
# ============================================================

@app.callback(
    Output("control-status", "children"),
    Output("start-button", "disabled"),
    Input("start-button", "n_clicks"),
    prevent_initial_call=True,
)
def start_recording(n_clicks):

    recording_event.set()

    return "Recording started.", True


# ============================================================
# Plot update callback
# ============================================================

@app.callback(
    Output("temperature-graph", "figure"),
    Output("pressure-graph", "figure"),
    Output("temp-display", "children"),
    Output("pressure-display", "children"),
    Output("heater-display", "children"),
    Output("status-display", "children"),
    Output("error-display", "children"),
    Output("sample-count", "children"),
    Input("plot-interval", "n_intervals"),
)
def update_dashboard(n):

    with data_lock:
        data_copy = measurements.copy()
        status = connection_status
        error = last_error

    # Limit plotted history for responsiveness.
    # All samples are still retained for CSV export.
    plot_data = data_copy[-1000:]

    df = pd.DataFrame(plot_data)

    fig_temp = go.Figure()
    fig_pressure = go.Figure()

    if not df.empty:

        # Plot against laptop system time.
        timestamps = list(pd.to_datetime(df["timestamp"]))

        # Break the lines wherever data collection stopped for a while.
        temp_x, temp_y = insert_gaps(
            timestamps, df["temperature_C"], GAP_THRESHOLD
        )
        pressure_x, pressure_y = insert_gaps(
            timestamps, df["pressure_psiG"], GAP_THRESHOLD
        )

        fig_temp.add_trace(
            go.Scatter(
                x=temp_x,
                y=temp_y,
                mode="lines",
                name="Temperature",
                line=dict(color="red"),
                connectgaps=False,
            )
        )

        fig_pressure.add_trace(
            go.Scatter(
                x=pressure_x,
                y=pressure_y,
                mode="lines",
                name="Pressure",
                line=dict(color="blue"),
                connectgaps=False,
            )
        )

        latest = df.iloc[-1]

        temp_text = f"{latest['temperature_C']:.2f} °C"
        pressure_text = f"{latest['pressure_psiG']:.2f} psi-G"
        heater_value = latest["heater_stat"]
        if pd.isna(heater_value):
            heater_value = None
        heater_display = f"Heater status: {heater_text(heater_value)}"

    else:
        temp_text = "-- °C"
        pressure_text = "-- psi-G"
        heater_display = "Heater status: --"

    fig_temp.update_layout(
        title="Temperature vs Time",
        xaxis_title="Laptop time",
        yaxis_title="Temperature (°C)",
        template="plotly_white",
        uirevision="temperature",
    )

    fig_pressure.update_layout(
        title="Pressure vs Time",
        xaxis_title="Laptop time",
        yaxis_title="Pressure (psi-G)",
        template="plotly_white",
        uirevision="pressure",
    )

    return (
        fig_temp,
        fig_pressure,
        temp_text,
        pressure_text,
        heater_display,
        status,
        error,
        f"Samples collected: {len(data_copy)}",
    )


# ============================================================
# CSV export
# ============================================================

@app.callback(
    Output("csv-download", "data"),
    Input("save-button", "n_clicks"),
    prevent_initial_call=True,
)
def save_csv(n_clicks):

    with data_lock:
        data_copy = measurements.copy()

    if not data_copy:
        raise PreventUpdate

    df = pd.DataFrame(data_copy)

    filename = (
        "autoclave_data_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )

    return dcc.send_data_frame(
        df.to_csv,
        filename,
        index=False,
    )


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    acquisition_thread = threading.Thread(
        target=acquire_data,
        daemon=True,
    )

    acquisition_thread.start()

    app.run(
        host="127.0.0.1",
        port=8050,
        debug=False,
        use_reloader=False,
    )

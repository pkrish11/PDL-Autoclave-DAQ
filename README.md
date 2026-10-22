# PDL Autoclave DAQ 

This repository contains the code used to control the DAQ system for the PDL autoclave. The .ino file can be programmed onto a Teensy 4.1 microcontroller to enable pressure and temperature data capture in the autoclave. A python script running on a computer will create an interactive HTML dashboard to plot data recieved from the autoclave.


## Pre-Requisites
* Python 3.10 or newer (recommended)
* Arduino IDE (recommended for .ino file)
* Ethernet connection between Teensy and computer
* 24V DC power supply to PCB 

## How to use
### Installation
* Clone the repository and navigate to the project directory
* Create and activate a Python virtual environment
* Install the Python dependencies from `requirements.txt`
### Network Configuration
The Teensy 4.1 communicates with the host computer over Ethernet using the QNEthernet library. The Teensy runs an HTTP server, while the Python application sends HTTP requests to retrieve temperature/pressure measurements and update setpoints.
* Ensure the Teensy and computer are connnected via an Ethernet cable.
* The Teensy IP address is already configured in the Arduino firmware. The IPv4 address is `192.168.1.10` with subnet mask `255.255.255.0`. 
* Configure the host computer to have IPv4 address as `192.168.1.20` with subnet mask `255.255.255.0` (navigate to system settings, select the usb ethernet interface, select TCP/IP and configure IPv4 manually).
* To test the connection and test the Teensy's HTTP server, from the host computer's terminal run: 
```
ping 192.168.1.10
curl http://192.168.1.10/
```
The server should return the latest temperature and pressure in JSON format.


Note: If another active network already uses `192.168.1.x`, select a different private subnet for both devices to avoid routing conflicts.

### Running the Dashboard
After ensuring the network is configured and Teensy server is running,  
* Run:
```
python3 dashboard.py
```
* Open http://127.0.0.1:8050 in a browser to view the dashboard.

### Using the Dashboard
* The dashboard displays the live autoclave temperature, pressure and heater status (for now), as well as plots of the data over time.
* The temperature and pressure setpoints for the autoclave can be set using the dashboard. Clicking the button will communicate the setpoints to the DAQ and update them internally.
* Data can be saved in a .csv file using the save_data button. Losing the Ethernet connection does not cause the data to be lost, however stopping the Python script will cause the data to be lost.

### Firmware Notes
* The microcontroller is configured to save data onto a SD card as well, if one is inserted into it. It will store the time-since-execution started, temperature, pressure and heater status a .csv file on the microcontoller.
* The sampling rate for pressure and temperature is set to ***1***s by default, this can be modified in the .ino file.
* Heater power should be connected across the COM and NO terminals.

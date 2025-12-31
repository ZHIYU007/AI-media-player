# RTSP Player Application

This project is a simple RTSP video player application that allows users to select and play two RTSP video streams in real-time. The application is built using Python and utilizes libraries such as OpenCV and Tkinter for video handling and GUI creation.

## Project Structure

```
rtsp-player-app
├── src
│   ├── main.py               # Entry point of the application
│   ├── gui
│   │   └── player_window.py   # GUI for the video player
│   ├── rtsp
│   │   └── stream_handler.py   # RTSP stream handling
│   └── utils
│       └── config.py          # Configuration settings
├── requirements.txt           # Required Python libraries
└── README.md                  # Project documentation
```

## Installation

To install the required dependencies, run the following command:

```
pip install -r requirements.txt
```

## Usage

1. Run the application by executing the `main.py` file:

   ```
   python src/main.py
   ```

2. The main window will open, allowing you to select two RTSP streams.

3. After selecting the streams, click the play button to start streaming.

## Configuration

You can modify the default RTSP stream addresses and other settings in the `src/utils/config.py` file.

## Dependencies

This project requires the following Python libraries:

- opencv-python
- tkinter

Make sure to have these installed before running the application.
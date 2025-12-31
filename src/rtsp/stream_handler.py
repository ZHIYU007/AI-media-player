class StreamHandler:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.capture = None

    def start_stream(self):
        import cv2
        self.capture = cv2.VideoCapture(self.rtsp_url)
        if not self.capture.isOpened():
            raise Exception("Could not open RTSP stream")

    def stop_stream(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None
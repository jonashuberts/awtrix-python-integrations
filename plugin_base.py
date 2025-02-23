# plugin_base.py
class ClockApp:
    def __init__(self):
        # Plugins can be disabled/enabled via this flag.
        self.enabled = True

    def update(self):
        """
        Fetch or compute the data that this plugin should display.
        Must be implemented by the subclass.
        """
        raise NotImplementedError("The update() method must be implemented by the plugin.")

    def send(self, data):
        """
        Send the processed data to the AWTRIX clock or another endpoint.
        Must be implemented by the subclass.
        """
        raise NotImplementedError("The send() method must be implemented by the plugin.")

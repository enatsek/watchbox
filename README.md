# watchbox
Automated Service Checking Tool

WatchBox (AKA watchbox) is planned to be a Systemd service for starting at the power-up and making periodic checks.

Currently it has 6 types of checks:

- IPPing: Checks if an IP address or hostname can be pinged
- IPPort: Checks if an IP address or hostname can be connected through a TCP Port
- Webpage: Checks if a webpage exists
- WebpageContent: Checks if a webpage has a content
- LocalPath: Checks if a path exists
- LocalService: Checks if a systemd service is active

Check status results can be collected to systemd journal, text file, and/or sqlite db file.

Configuration file watchbox.conf is expected to be in /etc directory. It is well documented inside.

Disclaimer: This program is far from being optimized, so it is not adviced to use it on production environments.

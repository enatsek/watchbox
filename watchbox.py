#!/usr/bin/env python3
'''
---Copyright (C) 2020 - 2023 Exforge exforge@x386.org
This document is free text: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
any later version.
This document is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
'''


'''
Version 0.9.1

WatchBox (AKA watchbox) is planned to be a Systemd service for starting at the power-up and making periodic checks.

Currently it has 6 types of checks:

IPPing: Checks if an IP address or hostname can be pinged
IPPort: Checks if an IP address or hostname can be connected through a TCP Port
Webpage: Checks if a webpage exists
WebpageContent: Checks if a webpage has a content
LocalPath: Checks if a path exists
LocalService: Checks if a systemd service is active

Check status results can be collected to systemd journal, text file, and/or sqlite db file.

Configuration file watchbox.conf is expected to be in /etc directory. It is well documented inside.

Disclaimer: This program is far from being optimized, so it is not adviced to use it on production environments.

'''

import configparser
import os
import datetime
import time
import requests
import subprocess
import socket
import os.path
import sqlite3
from systemd import journal
'''
# May require installing python3-systemd package
#   apt update
#   apt install python3-systemd
'''
# Necessary for converting HTML to text.
#https://gist.github.com/ye/050e898fbacdede5a6155da5b3db078d
from abc import ABC
from html.parser import HTMLParser
class HTMLFilter(HTMLParser, ABC):
    text = ""
    def handle_data(self, data):
        self.text += data
'''
f = HTMLFilter()
f.feed(data)
print(f.text)
'''

# Global variables, constants
service_start_time = 0     # Unix time in integer when the service starts. Also used as Session ID.
log_details = ""           # Log details to put in systemd journal, file or sqlite db
log_to_file = "no"         # If yes, log to output text log file
log_to_db = "no"           # If yes, log to sqlite output db log file
log_to_systemd = "no"      # If yes, log to systemd journal
log_text_file = ""         # Full path of the output text log file
log_db_file = ""           # Full path of the output db log file
log_record = []            # Log record to put in systemd journal, file or sqlite db
sleep_time = 0.5           # Sleep time in second between checks
# log_record format will be as:
#    [SessionID, "YYYYMMDDHHMMSS", Category, SubCategory, Level, Type, Details]
#    [145632, "202303151321", "Watchbox", "", "Error", "Service Start", "Error in service start"]
#    [145632, "202303151321", "Watchbox", "", "Information", "Service Start", "Service started succesfully"]
#    [145632, "202303151321", "Watchlist", "IPPort", "Warning", "Watchlist Check", "Cannot connect to IP and Port"]
initial_logs = []          # Contains initial logs before the log files prepared. They will be written later.

# Configuration parameters
conf_file = "/etc/watchbox.conf"

# Default section configs
# [SystemdLogs, Output, OutputFile]
default_watchbox_config = ["yes", "file", "/var/log/watchbox"]
default_watchlist_types = ["IPPing", "IPPort", "WebPage", "WebPageContent", "LocalPath", "LocalService"]
default_hostname = "8.8.8.8"
default_initial_wait = "5s"
default_check_interval = "15m"
default_url = "https://www.google.com/"
default_port_str = "443"
default_port = 443
default_content = "WatchBox"
default_path = "/var/www/html"
default_service = "apache2.service"
default_output_file = "/var/log/watchbox"

# WatchBox configs: A 2 dimensional list to keep all the configs
# Empty at the start
# The first element of this list will be the WatchBox section configs
# The Format of this sublist will be as:
# ["SystemdLogs", "Output", "OutputFile"]
#    ["yes", "file", "/var/log/watchbox"]
# This list will contains every watchlist as a list. All times are converted
#    to seconds.
# The formats for the sublists will be as:
# ["Watchlistname", "IPPing", "Hostname", "InitialWait", "CheckInterval", "LastCheckTime"]
#    ["Watch1", "IpPing", "8.8.8.8", 600, 900, 0]
# ["Watchlistname", "IPPort", "Hostname", "PortNumber", "InitialWait", "CheckInterval", "LastCheckTime"]
#    ["Watch1", "IPPort", "8.8.8.8", "443", 600, 900, 0]
# ["Watchlistname", "WebPage", "URL", "InitialWait", "CheckInterval", "LastCheckTime"]
#    ["Watch1", "WebPage", "https://www.google.com/", 600, 900, 0]
# ["Watchlistname", "WebPageContent", "URL", "Content", "InitialWait", "CheckInterval", "LastCheckTime"]
#    ["Watch1", "WebPageContent", "https://www.google.com/", "WatchBox", 600, 900, 0]
# ["Watchlistname", "LocalPath", "Path", "InitialWait", "CheckInterval", "LastCheckTime"]
#    ["Watch1", "LocalPath", "/var/www/html", 600, 900, 0]
# ["Watchlistname", "LocalService", "Service", "InitialWait", "CheckInterval", "LastCheckTime"]
#    ["Watch1", "LocalService", "apache2.service", 600, 900, 0]
watchbox_configs = []

# Valid and default values for some configs
valid_systemdlogs_values = ["Yes", "yes", "True", "True", "No", "no", "false", "False"]
positive_values = ["Yes", "yes", "True", "True"]
default_positive_value = "yes"
negative_values = ["No", "no", "false", "False"]
default_negative_value = "no"
valid_output_values = ["file", "sqlite", "both", "none"]
default_output_value = "file"
valid_watchlist_values = ["IPPing", "IPPort", "WebPage", "WebPageContent", "LocalPath", "LocalService"]
valid_time_suffixes = ["s", "m", "h", "d"]

# Table creation SQL scripts
create_table_watchbox_sessions = 'CREATE TABLE IF NOT EXISTS "WATCHBOX_SESSIONS" (\
	"SessionID"	INTEGER NOT NULL UNIQUE,\
   "Time" TEXT NOT NULL,\
	"SystemdLogs"	TEXT NOT NULL,\
	"Output"	TEXT NOT NULL,\
	"OutputFile"	TEXT NOT NULL,\
	PRIMARY KEY("SessionID")\
    );'

create_table_watchlist_details = 'CREATE TABLE IF NOT EXISTS "WATCHLIST_DETAILS" (\
	"SessionID"	INTEGER NOT NULL,\
	"Watchlist_Name"	TEXT NOT NULL,\
	"Watchlist_Type"	TEXT NOT NULL,\
	"Hostname"	TEXT,\
	"Port"	INTEGER,\
	"URL"	TEXT,\
	"Content"	TEXT,\
	"Path"	TEXT,\
	"Service"	TEXT,\
	"InitialWait"	INTEGER NOT NULL,\
	"CheckInterval"	INTEGER NOT NULL,\
	"LastCheckTime"	INTEGER NOT NULL DEFAULT 0\
    );'

create_table_logs = 'CREATE TABLE IF NOT EXISTS "LOGS" (\
	"SessionID"	INTEGER NOT NULL,\
	"Time"	TEXT NOT NULL,\
	"Category"	TEXT NOT NULL,\
	"SubCategory"	TEXT,\
	"Level"	TEXT NOT NULL,\
	"Type"	TEXT NOT NULL,\
	"Details"	TEXT NOT NULL\
    );'


def now():
   """ 
   Returns current Date-Time in format : 20230917105044  YYYYMMDDHHMMSS
   Used for timestamping in log files
   """
   now_raw = datetime.datetime.now()
   return (now_raw.strftime("%Y%m%d%H%M%S"))

def current_time():
   """ 
   Returns current time in Unix Time format (a big integer).
   Used for timestamping in log files
   """
   return(int(time.time()))


def convert_to_seconds(time_value):
   '''
   Converts given time_value to seconds. time_value will be in format of an integer (time interval) followed by a 
   time unit; s (seconds), m (minutes), h (hours), or d (days). If there is no time unit, it means seconds.
   
   Returns zero if the time_value is invalid, otherwise returns the time value in seconds.
   '''
   
   # Get time interval
   if time_value[-1] in ["s", "m", "h", "d"]:
      time_interval_str = time_value[0:-1]
      time_unit = time_value[-1]
   elif time_value[-1].isdigit():
      time_interval_str = time_value
      time_unit = "s"
   else:
      return(0)
   if time_unit == "s":
      time_multiplier = 1
   elif time_unit == "m":
      time_multiplier = 60
   elif time_unit == "h":
      time_multiplier = 60*60
   elif time_unit == "d":
      time_multiplier = 60*60*24

   # Check if time_interval is an integer   
   try:                      
      time_interval = int(time_interval_str)
   except:                   
      time_interval = 0
   time_seconds = time_interval * time_multiplier
   return(time_seconds)


def get_watchlist_parameters(watchlist_name, watchlist_type, config):
   """
   Gets the parameters for the given watchlist_name and watchlist_type from the given config object. 
   config is a configparser object which is already created.
   watchlist_type could be IPPing, IPPort, WebPage, WebPageContent, LocalPath, or LocalService.

   Tries to get parameters for each field. For nonexisting or invalid value fields, sets defaults.

   Returns a list of parameters, list items depends on the watchlist_type. 
   More description is at the comments of watchbox_configs variable.
   """
   if watchlist_type == "IPPing":
      hostname = config.get(watchlist_name, "Hostname", fallback = default_hostname)
      initial_wait = convert_to_seconds(config.get(watchlist_name, "InitialWait", fallback = default_initial_wait))
      check_interval = convert_to_seconds(config.get(watchlist_name, "CheckInterval", fallback = default_check_interval))
      watch_line = [watchlist_name, watchlist_type, hostname, initial_wait, check_interval, 0]
      return(watch_line)
   elif watchlist_type == "IPPort":
      hostname = config.get(watchlist_name, "Hostname", fallback = default_hostname)
      port_str = config.get(watchlist_name, "Port", fallback = default_port_str)
      try:                      
         port = int(port_str)
      except:                   
         port = default_port
      initial_wait = convert_to_seconds(config.get(watchlist_name, "InitialWait", fallback = default_initial_wait))
      check_interval = convert_to_seconds(config.get(watchlist_name, "CheckInterval", fallback = default_check_interval))
      watch_line = [watchlist_name, watchlist_type, hostname, port, initial_wait, check_interval, 0]
      return(watch_line)
   elif watchlist_type == "WebPage":
      url = config.get(watchlist_name, "URL", fallback = default_url)
      initial_wait = convert_to_seconds(config.get(watchlist_name, "InitialWait", fallback = default_initial_wait))
      check_interval = convert_to_seconds(config.get(watchlist_name, "CheckInterval", fallback = default_check_interval))
      watch_line = [watchlist_name, watchlist_type, url, initial_wait, check_interval, 0]
      return(watch_line)
   elif watchlist_type == "WebPageContent":
      url = config.get(watchlist_name, "URL", fallback = default_url)
      content = config.get(watchlist_name, "Content", fallback = default_content)
      initial_wait = convert_to_seconds(config.get(watchlist_name, "InitialWait", fallback = default_initial_wait))
      check_interval = convert_to_seconds(config.get(watchlist_name, "CheckInterval", fallback = default_check_interval))
      watch_line = [watchlist_name, watchlist_type, url, content, initial_wait, check_interval, 0]
      return(watch_line)
   elif watchlist_type == "LocalPath":
      path = config.get(watchlist_name, "Path", fallback = default_path)
      initial_wait = convert_to_seconds(config.get(watchlist_name, "InitialWait", fallback = default_initial_wait))
      check_interval = convert_to_seconds(config.get(watchlist_name, "CheckInterval", fallback = default_check_interval))
      watch_line = [watchlist_name, watchlist_type, path, initial_wait, check_interval, 0]
      return(watch_line)
   elif watchlist_type == "LocalService":
      service = config.get(watchlist_name, "Service", fallback = default_service)
      initial_wait = convert_to_seconds(config.get(watchlist_name, "InitialWait", fallback = default_initial_wait))
      check_interval = convert_to_seconds(config.get(watchlist_name, "CheckInterval", fallback = default_check_interval))
      watch_line = [watchlist_name, watchlist_type, service, initial_wait, check_interval, 0]
      return(watch_line)

def read_config_file():
   """
   Reads from config_file to get all configurations
   
   Returns status and prepared log record.

   Return Codes:
   0, log : OK
   1, log : No config file
   2, log: Fatal error in config file
   """
   # Timestamp startup log
   startup_log = now() + " Watchbox starting. "

   # Check if conf file exists
   startup_log += "Reading conf file. "
   if not os.path.isfile(conf_file):
      # No config file, nothing to do
      startup_log += "Config file not found. "
      return(1, startup_log)
   
   # Create a configparser object named config
   config = configparser.ConfigParser()
   # By default, configparser module changes the keys to lowercase, we need to avoid it
   startup_log += "Parsing conf file. "
   config.optionxform = str
   # Allow multiple options
   config._strict = False

   # Try parsing the conf file
   try:
      config.read(conf_file)
   except Exception as e:
      startup_log += "Parse error: " + str(e) + ". "
      return(2, startup_log)

   # Get [WatchBox] section parameters, assign default values for nonexisting fields.
   systemdlogs = config.get("WatchBox", "SystemdLogs", fallback = default_positive_value)
   output = config.get("WatchBox", "Output", fallback = default_output_value)
   outputfile = config.get("WatchBox", "OutputFile", fallback = default_output_file)

   # Check systemdlogs field
   if systemdlogs in valid_systemdlogs_values:
      if systemdlogs in negative_values :
         systemdlogs = default_negative_value
      else:
         systemdlogs = default_positive_value
   else:
      systemdlogs = default_positive_value
   
   # Check output field
   if output not in valid_output_values:
      output = default_output_value
   
   # Add the first line of the watchbox_configs
   watchbox_configs_line = [systemdlogs, output, outputfile]
   watchbox_configs.append(watchbox_configs_line)
   
   # Process [WatchLists] section
   startup_log += "Getting watchlists. "
   # Get watchlist items 
   watchlists = config["WatchLists"]
   for watchlist in watchlists:
      # Get watchlist type 
      watchlist_type = config.get("WatchLists", watchlist)
      # Ignore invalid watchlists
      if not watchlist_type in default_watchlist_types:
         startup_log += "Type " + watchlist_type + " is invalid for watchlist: " + watchlist + ". "
         continue
      # Ignore watchlists without a section
      # Check if the watchlist has its own section
      try:
         test = config[watchlist]
      except:
         startup_log += "Section not found for watchlist: " + watchlist + ". "
         continue
      # Get watchlist parameters
      startup_log += "Getting configuration for watchlist: " + watchlist + ". "
      watchlist_line = get_watchlist_parameters(watchlist, watchlist_type, config)
      watchbox_configs.append(watchlist_line)
   startup_log += "Configuration file process is complete. "
   return(0, startup_log)

# Functions for checking watchlists

def check_ip_ping(ip):
    '''
    Checks if given ip (or hostname) can be pinged

    Return Codes:
        0: Ping OK
        1: No reply
        2: Other error (ip or hostname may be wrong)
    '''
    
    # Run ping command with 2 seconds timeout
    command = "ping " + ip + " -w 2"
    proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, executable='/bin/bash')
    # Get stdout and stderr
    out, err = proc.communicate()
    ret = proc.returncode
    if (ret in [0,1]):
        return(ret)
    else:
        return(2)

def check_ip_port(ip, port):
    '''
    Check if the given ip can be connected at given TCP port.

    Return Code:
    0, "": Connection ok
    1, str(exception) : Connection error
    '''

    # Create a socket and try to connect in 2 seconds timeout
    check_socket = socket.socket()  # instantiate
    check_socket.settimeout(2)
    try:
        check_socket.connect((ip, port))  # connect to the server
    except Exception as e:
        return(1, e)
    check_socket.close()
    return(0, "")

def check_path(path):
    '''
    Check if the given path exists.

    Return Code:
    0: Path exists
    1: Path does not exist
    '''
    # Check if file,link,dir etc exists.
    ret = os.path.exists(path)
    if (ret == True):
        return(0)
    else:
        return(1)

def check_service(service):
    '''
    Check if the given service is active.

    Return Code:
    0: Service active
    1: Service inactive
    2: Service not found
    3: Unknown error
    '''

    # Run "systemctl status service" command
    command = "systemctl status " + service
    proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, executable='/bin/bash')
    # Get stdout and stderr
    out, err = proc.communicate()
    ret = proc.returncode
    if (ret == 0):      # Service is running
        return(0)
    elif (ret == 3):    # Service exists but not active
        return(1)
    elif (ret == 4):    # Service does not exist
        return(2)
    else:               # Unknown error
        return(3)       


def check_webpage_content(webpage, content):
    '''
    Checks if webpage exists and contains the content
        content being empty means, only check if webpage exists

    Return Codes:
        0, "" : webpage exists and contains the content
        1, "" : webpage exists but does not contain the content
        2, str(errorcode) : webpage error (404 or something else)
        3, str(exception) : webpage exception (possibly website does not exist)
    '''

    # try to get the webpage
    try:
        response = requests.get(webpage)
    except Exception as e:
        return (3, e)
    
    # Get webpage status code. 200 is OK, error otherwise
    return_code = response.status_code
    if (return_code != 200):
        return (2, str(return_code))
    else:
        # Convert HTML to text and check if it contains the given content.
        f = HTMLFilter()
        f.feed(str(response.content))
        page_content = f.text
        if (content in page_content):
            return(0, "")
        else:
            return(1, "")

# Functions for logging

def prepare_db_log_file():
    '''
    Check the path file if it is a valid sqlite db file. 
    If it does not exist create it. If it is not valid, rename and create it again.

    Check if given path exists
    If it exists:
        Try to connect to the file as a sqlite db and Create tables if they don't exist
        If Error: 
            Rename the path as path.backup
            If error: return error
    Create a new sqlite database with the name path
    Create tables
    If error: return error
    Return success

    Return Code:
    0, "": Success
    0, "log": Success with renaming the invalid file
    1, "log: Error
    '''
    log = ""
    path = log_db_file
    if (os.path.isfile(path)):
        # Try to connect to the db file and create tables if they do not exist
        try: 
            conn = sqlite3.connect(path)
            cursor = conn.cursor()
            cursor.execute(create_table_watchbox_sessions)
            cursor.execute(create_table_watchlist_details)
            cursor.execute(create_table_logs)
            conn.commit()
            conn.close()
        except Exception as e:
            log += "Cannot connect to db file. Error: " + str(e) + " Trying to rename it. "
            try:
               os.rename(path, path + ".backup")
            except Exception as e:
               log += "Rename error: " + str(e)
               return(1, log)
            log += "Rename successfull. "
        else:
            return(0, "")
    # At this point either we returned with an error, or we renamed the invalid file
    # So we need to create a new db file and create the tables.
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        cursor.execute(create_table_watchbox_sessions)
        cursor.execute(create_table_watchlist_details)
        cursor.execute(create_table_logs)
        conn.commit()
        conn.close()
    except: 
        log += "Database creation error."
        return(1, log)
    return(0, log)

def prepare_text_log_file():
    '''
    Check if the path file exists and we can access it.
    If it does not exist create it. 

    Check if given path exists
    If it exists:
        Try to open the file for appending.
        If Error: 
            Rename the path as path.backup
            If error: return error
    Create a new file with the name path
    If error: return error
    Return success

    Return Code:
    0, "": Success
    0, "log": Success with renaming the invalid file
    1, "log: Error
    '''
    log = ""
    path = log_text_file
    if (os.path.isfile(path)):
        # Try to open the file for appending.
        try: 
            with open(path, "a") as out_file:
                out_file.write("Starting a new session\n")               
        except Exception as e:
            log += "Cannot open text log file. Error: " + str(e) + " Trying to rename it. "
            try:
                os.rename(path, path + ".backup")
            except Exception as e:
                log += "Rename error: " + str(e)
                return(1, log)
            log += "Rename successfull. "
        else:
            return(0, "")
    # At this point either we returned with an error, or we renamed the invalid file
    # So we need to create a new text file.
    try:
        with open(path, "w") as out_file:
            out_file.write("Starting a new log file\n")               
    except: 
        log += "Text log file creation error."
        return(1, log)
    return(0, log)



def write_systemd_log(log, priority):
   '''
   Adds given log record (a string) to the systemd journal. 
   Priority can be from 0 to 7. As below.
   0	Emergency 	   emerg 	System is unusable 
   1 	Alert 		   alert 	Should be corrected immediately
   2 	Critical 	   crit 	   Critical conditions 	
   3 	Error 		   err 	   Error conditions 	
   4 	Warning 	      warning  Error will occur if action is not taken 	
   5 	Notice 		   notice 	Events that are unusual, but not error
   6 	Informational 	info 	   Normal operational messages 
   7 	Debug 		   debug 	For debugging purposes

   WatchBox uses 3 for errores, 4 for warnings (unsuccessfull checks), 6 for notices.

   No return codes.
   '''
   journal.send(log, PRIORITY=priority, SYSLOG_IDENTIFIER="WatchBox")

def write_file_log(log):
   '''
   Writes the given log to the text log file

   No return codes.
   '''
   path = log_text_file
   with open(path, "a") as out_file:
      out_file.write(log + "\n")               

'''
sqlite DB structure:

   There are 3 tables:
   WATCHBOX_SESSIONS : Contains a row of configuration parameters for WatchBox session with a Session ID.
   WATCHLIST_DETAILS : Contains rows of configuration parameters for Watchlists with a Session ID.
   LOGS              : Logs for watchlist checks
   Table Structures:
   WATCHBOX_SESSIONS
      SessionID   : Integer (Unix time of service start)
      SystemdLogs : Text ("yes" or "no")
      Output      : Text (file, sqlite, both, none)
      OutputFile  : Text  (Full path of the output file, .txt and/or .db appended)
   
   CREATE TABLE "WATCHBOX_SESSIONS" (
   	"SessionID"	INTEGER NOT NULL UNIQUE,
      "Time" TEXT NOT NULL,
   	"SystemdLogs"	TEXT NOT NULL,
   	"Output"	TEXT NOT NULL,
   	"OutputFile"	TEXT NOT NULL,
   	PRIMARY KEY("SessionID")
   );
   
   INSERT INTO "WATCHBOX_SESSIONS"
      ("SessionID", "Time", "SystemdLogs", "Output", "OutputFile") 
      VALUES (123456, "", "", 'both', '/var/log/watchbox');
   
   Text Log: (now);New Session:(str(SessionID));SystemdLogs:(SystemdLogs);Output:(Output);OutputFile:;(OutputFile)
   
   WATCHLIST_DETAILS
      SessionID      : Integer (Unix time of service start)
      Watchlist_Name : Text (User given name)
      Watchlist_Type : Text (IPPing, IPPort, Webpage, WebpageContent, LocalPath, LocalService)
      Hostname       : Text (For IPPing and IPPort)
      Port           : Integer (For IPPort)
      URL            : Text (For Webpage and WebpageContent)
      Content        : Text (For WebpageContent)
      Path           : Text (For LocalPath)
      Service        : Text (For LocalPath) 
      InitialWait    : Integer (For all watchlist types - in seconds)
      CheckInterval  : Integer (For all watchlist types - in seconds)
      LastCheckTime  : Integer (For all watchlist types - in seconds, always 0 at the beginning)
   
   CREATE TABLE "WATCHLIST_DETAILS" (
   	"SessionID"	INTEGER NOT NULL,
   	"Watchlist_Name"	TEXT NOT NULL,
   	"Watchlist_Type"	TEXT NOT NULL,
   	"Hostname"	TEXT,
   	"Port"	INTEGER,
   	"URL"	TEXT,
   	"Content"	TEXT,
   	"Path"	TEXT,
   	"Service"	TEXT,
   	"InitialWait"	INTEGER NOT NULL,
   	"CheckInterval"	INTEGER NOT NULL,
   	"LastCheckTime"	INTEGER NOT NULL DEFAULT 0
   );
   
   INSERT INTO "WATCHLIST_DETAILS"
      ("SessionID", "Watchlist_Name", "Watchlist_Type", "Hostname","Port", "URL", 
      "Content", "Path", "Service", "InitialWait", "CheckInterval", "LastCheckTime") 
      VALUES (0, '' ,'', NULL, NULL, NULL, NULL, NULL, NULL, 0, 0);
   
   Text Log: (now);New Watchlist;SessionID:(str(SessionID));Watchlist:(watchlist);Watchlist Type:(watchlist_type);\
      Hostname:(hostname);Port:(str(Port));URL:(url);Content:(content);Path:(path);Service:(service);\
      InitialWait:(str(initial_wait);CheckInterval:(str(check_interval);LastCheckTime:0
   
   LOGS
      SessionID   : Integer (Unix time of service start)
      Time        : Text (YYYYMMDDHHSS)
      Category    : Text ("WatchBox" or "Watchlist")
      SubCategory : Text (Watchlist Name for Category Watchlist, "" otherwise)
      Level       : Text ("Error", "Warning", "Information)
      Type        : Text ("Service Start", "Watchlist Check", "Log File")
      Details     : Text (Log Detail)
   
   CREATE TABLE "LOGS" (
   	"SessionID"	REAL NOT NULL,
   	"Time"	TEXT NOT NULL,
   	"Category"	TEXT NOT NULL,
   	"SubCategory"	TEXT,
   	"Level"	INTEGER NOT NULL,
   	"Type"	TEXT NOT NULL,
   	"Details"	TEXT NOT NULL
   );
      
   INSERT INTO "LOGS"
      ("SessionID", "Time", "Category", "SubCategory", "Level", "Type", "Details") 
      VALUES ('', '', '', "", 0, '', '');
   
   Text Log: now());Session ID:(str(sessionID));Time:(now());Category:(category);Subcategory:(subcategory);\
      Level:(level);Type:(type);Details:(log)
   
'''


def write_db_activity_log(log_record):
   '''
   Adds a record to sqlite db LOGS table as in the log_record.
   log_record format will be as:
   [SessionID, Time , Category, SubCategory, Level, Type, Details]
   [145632, "202303151321", "Watchbox", "", "Error", "Service Start", "Error in service start"

   No return codes.
   '''
   sessionID = log_record[0]
   time = log_record[1]
   category = log_record[2]
   subcategory = log_record[3]
   level = log_record[4]
   ltype = log_record[5]
   details = log_record[6]

   insert_into_logs = 'INSERT INTO "LOGS" \
      ("SessionID", "Time", "Category", "SubCategory", "Level", "Type", "Details") \
      VALUES (' + str(sessionID) + ', "' + time + '", "' + category + '", "' + subcategory + \
      '", "' + level + '", "' + ltype + '", "' + details + '");'

   path = log_db_file
   if (os.path.isfile(path)):
      try: 
         conn = sqlite3.connect(path)
         cursor = conn.cursor()
         cursor.execute(insert_into_logs)
         conn.commit()
         conn.close()
      except Exception as e:
   	   print("Exception", e)

def write_db_watchbox_log(watchbox_log_record):
   '''
   Adds a record to sqlite db WATCHBOX_SESSIONS table as in the watchbox_log_record.
   watchbox_log_record format will be as:

   [sessionID, time, systemdlogs, output, output_file]

   No return codes.
   '''
   sessionID = watchbox_log_record[0]
   time = watchbox_log_record[1]
   systemdlogs = watchbox_log_record[2]
   output =  watchbox_log_record[3]
   output_file = watchbox_log_record[4]
   insert_into_watchbox_sessions = 'INSERT INTO "WATCHBOX_SESSIONS" \
      ("SessionID", "Time", "SystemdLogs", "Output", "OutputFile") \
      VALUES (' + str(sessionID) + ', "' + time + '", "' + systemdlogs + '", "' + \
      output + '", "' + output_file + '");'
   path = log_db_file
   if (os.path.isfile(path)):
      try: 
         conn = sqlite3.connect(path)
         cursor = conn.cursor()
         cursor.execute(insert_into_watchbox_sessions)
         conn.commit()
         conn.close()
      except Exception as e:
   	   print("Exception", e)

def write_db_watchlist_log(watchlist_log_record):
   '''
   Adds a record to sqlite db WATCHBOX_DETAILS table as in the watchlist_log_record.
   watchlist_log_record format will be as:

   [sessionID, watchlist_name, watchlist_type, hostname, port, url, content, 
   path, service, initial_wait, check_interval, last_check_time]

   No return codes.
   '''
   sessionID = watchlist_log_record[0]
   watchlist_name = watchlist_log_record[1]
   watchlist_type = watchlist_log_record[2]
   hostname = watchlist_log_record[3]
   port = watchlist_log_record[4]
   url = watchlist_log_record[5]
   content = watchlist_log_record[6]
   path = watchlist_log_record[7]
   service = watchlist_log_record[8]
   initial_wait = watchlist_log_record[9]
   check_interval = watchlist_log_record[10]
   last_check_time = watchlist_log_record[11]

   insert_into_watchlist_details = 'INSERT INTO "WATCHLIST_DETAILS" \
      ("SessionID", "Watchlist_Name", "Watchlist_Type", "Hostname","Port", "URL", \
      "Content", "Path", "Service", "InitialWait", "CheckInterval", "LastCheckTime") \
      VALUES (' + str(sessionID) + ', "' + watchlist_name + '", "' + watchlist_type + '", "' + \
      hostname + '", ' + str(port) + ', "' + url + '", "' + content + '", "' + path + '", "' + \
      service + '", ' + str(initial_wait) + ', ' + str(check_interval) + ', ' + str(last_check_time) + \
      ');'

   path = log_db_file
   if (os.path.isfile(path)):
      try: 
         conn = sqlite3.connect(path)
         cursor = conn.cursor()
         cursor.execute(insert_into_watchlist_details)
         conn.commit()
         conn.close()
      except Exception as e:
   	   print("Exception", e)


def write_activity_log(log_record):
   '''
   Depending on the configuration, writes the given watchlist log to log file, sqlite db file and/or systemd journal.
   log_record format will be as:
   [SessionID, Time, Category, SubCategory, Level, Type, Details]
   [145632, "202303151321", "Watchbox", "", "Error", "Service Start", "Error in service start"

   No return codes.
   '''
   sessionID = log_record[0]
   time = log_record[1]
   category = log_record[2]
   subcategory = log_record[3]
   level = log_record[4]
   ltype = log_record[5]
   details = log_record[6]
   log = now() + ";Session ID:" + str(sessionID) + ";Time:" + time + ";Category:" + category + ";Subcategory:"
   log += subcategory + ";Level:" + level + ";Type:" + ltype + ";Details" + details
   priority = 6
   if (level == "Error"):
      priority = 3
   elif (level == "Warning"):
      priority = 4
   if (log_to_systemd == "yes"):
      write_systemd_log(log, priority)
   if (log_to_file == "yes"):
      write_file_log(log)
   if (log_to_db == "yes"):
      write_db_activity_log(log_record)

def write_startup_logs(watchbox_configs, sessionID):
   '''
   Depending on the configuration, writes conf file contents to log file, sqlite db file and/or systemd journal.

   No return codes.
   '''
   time = now()
   systemdlogs = watchbox_configs[0][0]
   output = watchbox_configs[0][1]
   output_file = watchbox_configs[0][2]
   watchbox_log = now() + ";WatchBox Config;Session ID:" + str(sessionID) 
   watchbox_log += ";SystemdLogs:" + systemdlogs + ";Output:" + output + ";Output File: " + output_file
   watchbox_log_record = [sessionID, time, systemdlogs, output, output_file]
   watchlist_logs = []
   watchlist_log_record = []
   watchlist_log_records = []
   for i in range (1,len(watchbox_configs)):
      watchlist_log_record = []
      # Get basic watchlist information
      watchlist = watchbox_configs[i]
      watchlist_name = watchlist[0]       # First item is watchlist name
      watchlist_type = watchlist[1]       # Second item is watchlist type
      initial_wait = watchlist[-3]        # Third from the last item is initial wait time
      check_interval = watchlist[-2]      # Second from the last item is check interval
      last_check_time = watchlist[-1]     # Last item is watchlist's last check time (initially 0)
      hostname = ""
      port = 1
      url = ""
      content = ""
      path = ""
      service = ""
      if (watchlist_type == "IPPing"):
         hostname = watchlist[2]
      elif (watchlist_type == "IPPort"):
         hostname = watchlist[2]
         port =  watchlist[3]
      elif (watchlist_type == "WebPage"):
         url = watchlist[2]
      elif (watchlist_type == "WebPageContent"):
         url = watchlist[2]
         content = watchlist[3]
      elif (watchlist_type == "LocalPath"):
         path = watchlist[2]
      elif (watchlist_type == "LocalService"):
         service = watchlist[2]
      watchlist_log = now() + ";Watchlist Config;SessionID:" + str(sessionID) + ";Watchlist Name:" + watchlist_name 
      watchlist_log += ";Watchlist Type:" + watchlist_type + ";Hostname:" + hostname + ";Port:" + str(port) 
      watchlist_log += ";URL:" + url + ";Content:" + content + ";Path:" + path + ";Service:" + service 
      watchlist_log += ";InitialWait:" + str(initial_wait) + ";CheckInterval:" + str(check_interval) + ";LastCheckTime: 0"
      watchlist_logs.append(watchlist_log)
      watchlist_log_record = [sessionID, watchlist_name, watchlist_type, hostname, port, url, content, path, service,\
          initial_wait, check_interval, last_check_time]
      watchlist_log_records.append(watchlist_log_record)
   if (log_to_file == "yes"):
      write_file_log(watchbox_log)
      for watchlist_log in watchlist_logs:
         write_file_log(watchlist_log)
   if (log_to_systemd == "yes"):
      write_systemd_log(watchbox_log, 6)
      for watchlist_log in watchlist_logs:
         write_systemd_log(watchlist_log, 6)
   if (log_to_db == "yes"):
      write_db_watchbox_log(watchbox_log_record)
      for watchlist_log_record in watchlist_log_records:
         write_db_watchlist_log(watchlist_log_record)
   

def process_watchlist(watchlist):
   '''
   Processes a given watchlist config line.
   First checks if the time is up for the checklist. 
   If it is:
      Checks the watchlist by calling the necessary function.
      Prepares and appends the log record.
      Updates the watchlist's last check time

   Return codes:
   -1: No check necessary, nothing is done
   0: Check successfull
   1 2 3 4: Check unsuccessfull
   '''
   # Get basic watchlist information
   watchlist_name = watchlist[0]       # First item is watchlist name
   watchlist_type = watchlist[1]       # Second item is watchlist type
   initial_wait = watchlist[-3]        # Third from the last item is initial wait time
   check_interval = watchlist[-2]      # Second from the last item is check interval
   last_check_time = watchlist[-1]     # Last item is watchlist's last check time (initially 0)
   
   ret = -1    # Return code

   # Check if watchlist's checktime
   # if last_check_time is 0, then this is the first time, we should wait for initial_wait seconds from the start
   #    otherwise, we should wait for check_interval seconds from the last check time
   ctime = current_time()
   check_time = ((((last_check_time == 0) and ((ctime - service_start_time) >= initial_wait))) or \
      ((last_check_time != 0) and (current_time()-last_check_time >= check_interval)))
   if (not check_time):
      return(ret)

   # Check time
   log_details = now() + " Checking watchlist " + watchlist_name + " of type " + watchlist_type + ". "
   if (watchlist_type == "IPPing"):
      hostname = watchlist[2]
      log_details += "IP/Hostname: " + hostname + ". "
      ret = check_ip_ping(hostname)
      if (ret == 0):
         log_details += "Check successfull."
      elif (ret == 1):
         log_details += "Check unsuccessfull, no reply."
      elif (ret == 2):
         log_details += "Check unsuccessfull, ip/hostname error."
   elif (watchlist_type == "IPPort"):
      hostname = watchlist[2]
      port =  watchlist[3]
      log_details += "IP/Hostname: " + hostname + ", Port: " + str(port) + ". "
      ret, note = check_ip_port(hostname, port)
      if (ret == 0):
         log_details += "Check successfull."
      elif (ret == 1):
         log_details += "Check unsuccessfull with error: " + note
   elif (watchlist_type == "WebPage"):
      url = watchlist[2]
      log_details += "Webpage: " + url + ". "
      ret, note = check_webpage_content(url, "")
      if (ret == 0 or ret == 1):
         log_details += "Check successfull."
      elif (ret == 2):
         log_details += "Check unsuccessfull. Webpage error: " + note
      elif (ret == 3):
         log_details += "Check unsuccessfull. Exception: " + note
   elif (watchlist_type == "WebPageContent"):
      url = watchlist[2]
      content = watchlist[3]
      log_details += "Webpage: " + url + ", Content: " + content + ". "
      ret, note = check_webpage_content(url, content)
      if (ret == 0):
         log_details += "Check successfull."
      elif (ret == 1):
         log_details += "Check unsuccessfull. Webpage exists but does not have the content."
      elif (ret == 2):
         log_details += "Check unsuccessfull. Webpage error: " + note
      elif (ret == 3):
         log_details += "Check unsuccessfull. Exception: " + note
   elif (watchlist_type == "LocalPath"):
      path = watchlist[2]
      log_details += "Path: " + path + ". "
      ret = check_path(path)
      if (ret == 0):
         log_details += "Check successfull."
      elif (ret == 1):
         log_details += "Check unsuccessfull. Path not found."
   elif (watchlist_type == "LocalService"):
      service = watchlist[2]
      log_details += "Service: " + service + ". "
      ret = check_service(service)
      if (ret == 0):
         log_details += "Check successfull."
      elif (ret == 1):
         log_details += "Check unsuccessfull. Service inactive."
      elif (ret == 2):
         log_details += "Check unsuccessfull. Service not found."
      elif (ret == 3):
         log_details += "Check unsuccessfull. Unknown error."
   if (ret == 0):
      level = "Information"      # Check successfull, informational log
   else:
      level = "Warning"          # Check unsuccessfull, warning log
   log_record = [service_start_time, now(), "Watchlist", watchlist_name, level, "Watchlist Check", log_details]
   write_activity_log(log_record)
   return(ret)

def set_watchlist_check_time(i):
   '''
   Update check time of ith watchlist element to current time.
   
   No return codes.
   '''
   watchbox_configs[i][-1] = current_time()



service_start_time = current_time()       # It is also the Session ID

# Try to parse config file
ret, note = read_config_file()

if (not ret):                             # Config file parsed successfully
   # Check log_to_systemd 
   log_to_systemd = watchbox_configs[0][0]
   # Add a initial log for successful parse of conf file
   # Because we have not prepared the text and db log files, we add logs to somewhere called initial_logs
   #   to later add to the read logs.
   log_record = [service_start_time, now(), "WatchBox", "", "Information", "Service Start", note]
   initial_logs.append(log_record)
   # Check log_to_file and log_to_db
   if (watchbox_configs[0][1] in ["file", "both"]):
      log_to_file = "yes"
      log_text_file = watchbox_configs[0][2] + ".txt"
   if (watchbox_configs[0][1] in ["sqlite", "both"]):
      log_to_db = "yes"
      log_db_file = watchbox_configs[0][2] + ".db"
else:             # Config file read fatal error, write systemd log and exit
   if (log_to_systemd == "yes"):
      log_record = [service_start_time, now(), "WatchBox", "", "Error", "Service Start", note] 
      write_systemd_log(log_record)
   exit(255)      # Exiting with 255 prevents the systemd service to restart automatically

# If it is active, prepare text log file.
if (log_to_file == "yes"):
   ret, note = prepare_text_log_file()
# If error preparing text log file, it means we cannot use it.
#    Disable it and add a record to initial_logs
if (ret == 1):
   log_to_file = "no"
   log_record = [service_start_time, now(), "WatchBox", "", "Warning", "Log File", "Text log file error:" + note] 
   initial_logs.append(log_record)

# If it is active, prepare db log file.
if (log_to_db == "yes"):
   ret, note = prepare_db_log_file()
# If error preparing db log file, it means we cannot use it.
#    Disable it and add a record to initial_logs
if (ret == 1):
   log_to_db = "no"
   log_record = [service_start_time, now(), "WatchBox", "", "Warning", "Log File", "DB log file error:" + note] 
   initial_logs.append(log_record)


# Write startup logs containing conf information and session id to the log
if (log_to_db == "yes"):
   write_startup_logs(watchbox_configs, service_start_time)

# Write collected initial logs
for log_record in initial_logs:
   write_activity_log(log_record)

print(watchbox_configs)

# Do an infinite loop. Sleep sleep_time seconds, check watchlists.
while True:
   # Skip the first line of watchbox_configs, because it has the main confs.
   #   The other lines contain the watchlists.
   time.sleep(sleep_time)
   for i in range (1,len(watchbox_configs)):
      ret = process_watchlist(watchbox_configs[i])
      # -1 means watchlist is skipped, others mean success or error
      if ret in [0, 1, 2, 3, 4]:
         set_watchlist_check_time(i)

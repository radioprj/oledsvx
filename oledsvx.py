#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SP2ONG 2022,2024 for SH1106 OLED 1.3 cala
# SP2AM 2024
# working only with LUMA driver 

# options in oledsvx.ini

import argparse
import configparser
import glob
import json
import logging
import os
import psutil
import re
import signal
import socket
import sys
import threading
import time

from datetime import datetime, timedelta
from json.decoder import JSONDecodeError
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1106, ssd1306, ssd1309
from pathlib import Path
from PIL import ImageFont, Image
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

def shutdown_signal_handler(signum, frame):
    global shutdown
    shutdown = True

class Call:
    def __init__(self, caller, tgnum, tgname, state, entrytime):
        self.caller = caller
        self.tgnum = tgnum
        self.tgname = tgname
        self.entrytime = entrytime
        allowed_states = [ 'start', 'stop' ]
        if state not in allowed_states:
            raise Exception("Call() with unknown state '%s'. Supported states: %s." % (state, ", ".join(allowed_states)))
        self.state = state
        self.entrytime = entrytime

    def __str__(self):
        return f"Caller: {self.caller}, TG Number: {self.tgnum}, TG Name: {self.tgname}, State: {self.state}, Entry time: {self.entrytime}"

    def __repr__(self):
        return self.__str__()

class SvxLogMonitor:
    class EventHandler(FileSystemEventHandler):
        def __init__(self, monitor):
            super().__init__()
            self.monitor = monitor

        def on_modified(self, event):
            if event.src_path != self.monitor.logfile:
                return
           
            logger.debug(f"EventHandler: on modified event: {event}")
            self.monitor.process()

        def on_created(self, event):
            if event.src_path != self.monitor.logfile:
                return

            logger.debug(f"EventHandler: on created event: {event}")
            self.monitor.reopen()

        def on_moved(self, event):
            if event.dest_path != self.monitor.logfile:
                return

            logger.debug(f"EventHandler: on moved event: {event}")
            self.monitor.reopen()

    def __init__(self, screen, logfile="/var/log/svxlink"):
        self.screen = screen
        self.logfile = logfile
        self.re_talker =  re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(?P<msecs>\d{3}))?: ReflectorLogic: Talker (?P<state>(start|stop)) on TG #(?P<tgnum>\d+): (?P<caller>.*)')
        self.re_tg_current =  re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(?P<msecs>\d{3}))?: ReflectorLogic: Selecting TG #(?P<tgnum>\d+)')
        self.re_start = re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(?P<msecs>\d{3}))?: Starting logic:')
        self.re_disconnected = re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(?P<msecs>\d{3}))?: ReflectorLogic: Disconnected from')
        self.re_connected = re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(?P<msecs>\d{3}))?: ReflectorLogic: Connection established')
        self.re_shutdown = re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(?P<msecs>\d{3}))?: .* Shutting down application')
        self.re_node_activity = re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(?P<msecs>\d{3}))?: ReflectorLogic: Node (joined|left)')

        self.buffer = ""

        self.open(notifier=False)
        self.initial_process()

        self.event_handler = self.EventHandler(self)
        self.observer = Observer()
        self.observer.schedule(self.event_handler, "/var/log", recursive=False)
        self.observer.start()

    def initial_process(self):
        # speed up by processing only last 5kB
        size_back = 5*1024

        self.fh.seek(0, 2)
        file_size = self.fh.tell()
        if file_size > size_back:
            self.fh.seek(file_size-size_back)
        else:
            self.fh.seek(0)

        # process to find out current tg group
        self.process()
        last_tg_current_call = None
        for call in self.screen.calls:
            if call.tgnum == self.screen.current_tg:
                last_tg_current_call = call
        # leave only last entry for a group
        if last_tg_current_call:
            self.screen.calls = [ last_tg_current_call ]

    def open(self, notifier=True):
        self.fh = open(self.logfile, 'r', encoding='utf-8')

    def close(self):
        self.fh.close()

    def reopen(self):
        logger.debug("SvxLogMonitor: reopening svxlink log file")
        self.close()
        self.open()

    def stop_monitoring(self):
        self.observer.stop()
        self.observer.join()

    def process(self):
        logger.debug(f"SvxLogMonitor process called")
        self.buffer += self.fh.read()
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)

            m = self.re_talker.match(line)
            if m:
                logger.debug(f"SvxLogMonitor process: matched talker line {line}")
                date = m.group('date')
                msecs = m.group('msecs')
                # handle format with and without microseconds
                if msecs is None:
                    date += ".000"
                else:
                    date += ".%03d" % int(msecs)
                state = m.group('state')
                tgnum = int(m.group('tgnum'))
                tgname = self.screen.get_tgname(tgnum)
                caller = m.group('caller')

                entrytime = datetime.strptime(date, '%Y-%m-%d %H:%M:%S.%f')

                self.screen.calls.append(Call(tgnum=tgnum, tgname=tgname, state=state, entrytime=entrytime, caller=caller))
                continue

            m = self.re_tg_current.match(line)
            if m:
                logger.debug(f"SvxLogMonitor process: matched current tg group line {line}")
                self.screen.current_tg = int(m.group('tgnum'))
                continue

            if self.re_node_activity.match(line):
                logger.debug(f"SvxLogMonitor process: matched node activity line {line}")
                # for node activity we don't zeroe calls
                self.screen.reflector_connected(clean_calls=False)
                continue

            if self.re_connected.match(line):
                logger.debug(f"SvxLogMonitor process: matched connected line {line}")
                self.screen.reflector_connected()
                continue

            if self.re_disconnected.match(line) or self.re_shutdown.match(line):
                logger.debug(f"SvxLogMonitor process: matched disconnected / shutdown line {line}")
                self.screen.reflector_disconnected()
                continue

            if self.re_start.match(line):
                logger.debug(f"SvxLogMonitor process: matched start line {line}")

                # initialize to default state
                self.screen.init_calls()
                continue

class Screen:
    def __init__(self, i2c_port=1, i2c_address=0x3C, screensaver_time=0,
                 contrast_normal_val=128, contrast_low_val=5, ext_temp_sensor=False):
        self.screensaver_time = screensaver_time
        self.contrast_normal_val = contrast_normal_val
        self.contrast_low_val = contrast_low_val
        self.ext_temp_sensor = ext_temp_sensor

        self.current_contrast = None

        # fons
        own_font= os.path.abspath(os.path.join(os.path.dirname(__file__),"fonts/Roboto-Light.ttf"))
        self.font11 = ImageFont.truetype(own_font, 11)
        self.font12 = ImageFont.truetype(own_font, 12)
        self.font14 = ImageFont.truetype(own_font, 14)
        self.font20 = ImageFont.truetype(own_font, 20)

        # icons
        self.cpu_icon = Image.open('icons/cpu.bmp').convert('1')
        self.temp_icon  = Image.open('icons/temp.bmp').convert('1')
        self.temp_home_icon = Image.open('icons/home.bmp').convert('1')
        self.antenna_icon = Image.open('icons/antenna.bmp').convert('1')

        serial = i2c(port=i2c_port, address=i2c_address)
        self.device = self.driver(serial)
        self.contrast_normal()
        self.device.clear()

        self.ips = ["---.---.---.---"]
        self.init_calls()

        self.tg_names = {}
        self.tg_names_update_time = 0

        self.reflector_connected_flag = False
        self.show_last = False

        self.__canvas = False
        self.redraw_oled() # initialize
        self.draw.rectangle(self.device.bounding_box, outline=0, fill=0)
        self.redraw_oled()

        self.contrast_lock()

    def contrast_normal(self):
        if self.current_contrast != self.contrast_normal_val:
            self.device.contrast(self.contrast_normal_val)
            self.current_contrast = self.contrast_normal_val

    def contrast_low(self):
        if self.contrast_locked:
            return
        if self.current_contrast != self.contrast_low_val:
            self.device.contrast(self.contrast_low_val)
            self.current_contrast = self.contrast_low_val

    def contrast_lock(self):
        self.contrast_locked = datetime.now()

    def check_contrast_lock(self):
        if self.contrast_locked:
            tdiff = datetime.now() - self.contrast_locked
            if tdiff > timedelta(minutes=1):
                self.contrast_locked = False

    def reflector_connected(self, clean_calls=True):
        self.reflector_connected_flag = True

        if clean_calls:
            # initialize to default state
            self.show_last = False
            self.init_calls()

    def reflector_disconnected(self):
        self.reflector_connected_flag = False
        self.show_last = False

        # initialize to default state
        self.init_calls()

    def init_calls(self):
        self.calls = []
        self.current_call = Call(caller=None, tgnum=0, tgname=None, state='stop', entrytime = datetime.now())
        self.current_tg = 0

    def __update_tgnames(self):
        tgfile = Path("/var/www/html/include/tgdb.json")
        if tgfile.exists():
            try:
                tgmtime = tgfile.stat().st_mtime
                if tgmtime > self.tg_names_update_time:
                    with tgfile.open(encoding='utf-8') as f:
                        self.tg_names = json.load(f)
                        self.tg_names_update_time = tgmtime
            except JSONDecodeError as e:
                pass

    def get_tgname(self, tg):
        tg = int(tg)
        if tg == 0:
            return "Brak aktywnej grupy"
        if tg >= 26099900:
            return "AUTO QSY"
        self.__update_tgnames()
        if str(tg) in self.tg_names:
            tgn = re.sub(r'[^a-zA-Z0-9ążźśćęńłóĄŻŹŚĆĘŃŁÓ:,\-\s]',"",self.tg_names[str(tg)])
            # limit characters
            return str(tgn)[:18]
        return "Nieznana"

    def save_screen(self):
        if not self.screensaver_time or len(self.calls):
            self.device.show()
            return False

        tdiff = datetime.now() - self.current_call.entrytime
        if tdiff > timedelta(seconds=self.screensaver_time):
            self.device.hide()
            return True

        self.device.show()
        return False

    def redraw_oled(self):
        if self.__canvas:
            self.__canvas.__exit__(None, None, None)

        self.__canvas = canvas(self.device)
        self.draw = self.__canvas.__enter__()

    def msg(self, msg, size):
        fonts = {
                    12: self.font12,
                    14: self.font14,
                    20: self.font20,
                    }

        if size not in fonts:
            raise Exception(f"Invalid font size: {size} (supported sizes: {', '.join(fonts.keys())})")

        if isinstance(msg, str):
            msg = [msg]

        msg_lines = len(msg)
        if msg_lines not in [1, 2]:
            raise Exception(f"Only 1 and 2 message lines are supported (got {msg_lines} lines).")

        if msg_lines == 1:
            self.draw.rectangle(self.shape, outline="white", fill="black")
            w = self.draw.textlength(msg[0],font=fonts[size])
            self.draw.text(((self.oled_width-w)/2,34), msg[0], font=fonts[size], fill=255)
        else:
            self.draw.rectangle(self.shape, outline="white", fill="black")
            w = self.draw.textlength(msg[0], font=fonts[size])
            self.draw.text(((self.oled_width-w)/2, 32), msg[0],  font=fonts[size], fill=255)
            w = self.draw.textlength(msg[1], font=fonts[size])
            self.draw.text(((self.oled_width-w)/2, 47), msg[1], font=fonts[size], fill=255)

    def __update_time(self):
        self.contrast_low()
        current_time = datetime.now().strftime("%H:%M")
        self.msg(current_time, 20)
        self.__update_reflector_connected_icon()

    def __update_talker(self, call):
        self.contrast_normal()
        self.msg([call.caller, call.tgname], size=14)
        self.__update_reflector_connected_icon()

    def __update_reflector_connected_icon(self):
        self.svxlink_alive()
        if self.reflector_connected_flag:
            # connection to reflector icon
            self.draw.bitmap((107, 43), self.antenna_icon, fill="white")


    def update_talkers_or_time(self):
        talker_shown = False
        if self.calls and len(self.calls):
            while self.calls:
                call = self.calls.pop(0)
                if self.current_tg == 0 or call.tgnum == self.current_tg:
                    self.__update_talker(call)
                    talker_shown = True
                    self.current_call = call
                    self.contrast_lock()
                    self.show_last = True

        if talker_shown:
            return
        
        if self.current_call.state == 'start':
            self.__update_talker(self.current_call)
        else:
            # show last caller for few seconds, otherwise time
            if self.show_last:
                tdiff = datetime.now() - self.current_call.entrytime
                if tdiff >= timedelta(seconds=0) and tdiff <= timedelta(seconds=5):
                    self.__update_talker(self.current_call)
                else:
                    self.show_last = False

            # show time otherwise
            if not self.show_last:
                self.__update_time()

    def __update_tg(self):
        if self.current_call.state == 'start' or self.show_last:
             msg = f"TG: {self.current_tg}"
        else:
            if self.current_tg == 0:
              msg = f"{self.get_tgname(self.current_tg)}"
            else:
              msg = f"Aktywna TG: {self.current_tg}"
        w = self.draw.textlength(msg, font=self.font11)
        self.draw.text(((self.oled_width-w)/2, 16), msg, font=self.font11, fill=255)

    def __update_ip(self):
        def __find_ips():
            ips = []
            ips4 = []
            ips6 = []
            nics = psutil.net_if_addrs()
            for nic, addrs in nics.items():
                if nic == "lo":
                    continue
                for addr in addrs:
                    if addr.family not in [ socket.AF_INET, socket.AF_INET6 ]:
                        continue
                    # skip IPv6 link local addresses
                    if '%' in addr.address:
                        continue
                    if addr.family == socket.AF_INET:
                        ips4.append(addr.address)
                    else:
                        ips6.append(addr.address)
            # prefer ipv4 and show ipv6 only if no ipv4 are available
            if ips4:
                ips = ips4
            elif ips6:
                ips = ips6
            ips.sort()
            if len(ips):
                self.ips = ips
            else:
                self.ips = ["---.---.---.---"]

        def __get_ip_index_to_display():
            time_per_ip = 60 / len(self.ips)
            ip_index = datetime.now().second // time_per_ip
            return int(ip_index)

        __find_ips()
        ip_index = __get_ip_index_to_display()

        msg = self.ips[ip_index]
        if len(self.ips) > 1:
            msg += " (%d/%d)" % (ip_index + 1, len(self.ips))

        w = self.draw.textlength(msg, font=self.font11)
        self.draw.text(((self.oled_width-w)/2, 16), msg, font=self.font11, fill=255)

    def update_ip_or_tg(self):
        current_second = int(time.time()) % 10
        if current_second in range(5):
            self.__update_ip()
        else:
            self.__update_tg()

    def update_temp_and_load(self):
        def __get_cpu():
            load1, load5, load15 = psutil.getloadavg()
            cpu_usage = (load1/os.cpu_count()) * 100
            return f'{str(int(float(cpu_usage))):>{2}}%'

        def __get_temp():
            try:
                with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding='utf-8') as temp:
                    return str(int(temp.read()[:2]))
            except Exception as e:
                return "?"

        def __get_ext_temp():
            if not self.ext_temp_sensor:
                return False
            sensors = glob.glob("/sys/bus/w1/devices/28*/w1_slave")
            if not sensors:
                return "?"
            # show temperature from first sensor only
            sensor_file = sensors[0]
            try:
                fc = ""
                with open(sensor_file, 'r', encoding='utf-8') as f:
                          fc = f.read()
                m = re.search(r't=(?P<temp>-?\d+)', fc)
                if not m:
                    return "?"
                return str(int(float(m.group('temp')) / 1000.0))
            except Exception as e:
                logger.debug(f"__get_ext_temp() failed: {e}")
            return "?"

        ext_temp = __get_ext_temp()
        if ext_temp:
            self.draw.bitmap((0, 0), self.cpu_icon, fill="white")
            msgc = __get_cpu()
            self.draw.text((18, 0), msgc, font=self.font12, fill=255)
            self.draw.bitmap((41, 0), self.temp_icon, fill="white")
            msgt = __get_temp() + "C"
            self.draw.text((60, 0), msgt, font=self.font12, fill=255)
            self.draw.bitmap((87, 0), self.temp_home_icon, fill="white")
            msgh = __get_ext_temp() + "C"
            self.draw.text((106, 0), msgh, font=self.font12, fill=255)
        else:
            self.draw.bitmap((16, 0), self.cpu_icon, fill="white")
            msgc = __get_cpu()
            self.draw.text((38, 0), msgc, font=self.font14, fill=255)
            self.draw.bitmap((68, 0), self.temp_icon, fill="white")
            msgt = __get_temp() + "°C"
            self.draw.text((88, 0), msgt, font=self.font14, fill=255)

    def svxlink_alive(self):
        def __is_svxlink_alive():
            binary_file = '/usr/bin/svxlink'
            pid_file = "/run/svxlink.pid"
            if not os.path.exists(pid_file):
                logger.debug("__is_svxlink_alive: no pid file: {pid_file}")
                return False

            with open(pid_file, 'r', encoding='utf-8') as file:
                pid = file.read().strip()

            proc_path = f"/proc/{pid}"
            if not os.path.exists(proc_path):
                logger.debug(f"__is_svxlink_alive: no {proc_path}")
                return False

            exe_path = os.path.join(proc_path, "exe")
            if not os.path.exists(exe_path):
                logger.debug(f"__is_svxlink_alive: no {exe_path}")
                return False

            real_path = os.path.realpath(exe_path)
            if real_path.find("svxlink") < 0:
                logger.debug(f"__is_svxlink_alive: {pid} exe path {real_path} probably isn't vxlink process")
                return False

            return True
        if not __is_svxlink_alive():
            self.reflector_disconnected()

    def shutdown(self):
        self.msg("Shutdown", 20)
        self.redraw_oled()
        time.sleep(2)
        sys.exit(0)

class ScreenSH1106(Screen):
    driver = sh1106
    oled_width = 128
    oled_height = 64
    shape = [(0, 30), (oled_width - 1, oled_height - 1)]

class ScreenSSD1306(Screen):
    driver = ssd1306
    oled_width = 128
    oled_height = 64
    shape = [(0, 30), (oled_width - 1, oled_height - 1)]

class ScreenSSD1309(Screen):
    driver = ssd1309
    oled_width = 128
    oled_height = 64
    shape = [(0, 30), (oled_width - 1, oled_height - 1)]

def load_config(config_path):
    config = configparser.ConfigParser()

    if not os.path.exists(config_path):
        print(f"Error: Configuration file '{config_path}' does not exist.")
        sys.exit(1)

    try:
        config.read(config_path)
    except configparser.Error as e:
        print(f"Error reading configuration file '{config_path}': {e}")
        sys.exit(1)

    return config

def get_config_value(config, option, value_type=str, section='oled', default=None):
    try:
        if value_type == bool:
            return config.getboolean(section, option)
        elif value_type == int:
            return config.getint(section, option)
        elif value_type == float:
            return config.getfloat(section, option)
        else:
            return config.get(section, option)
    except configparser.NoSectionError:
        print(f"Error: Section '{section}' not found in the configuration file.", file=sys.stderr)
        sys.exit(1)
    except configparser.NoOptionError:
        if default is not None:
            return default
        print(f"Error: Option '{option}' not found in section '{section}'.", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: Invalid value for option '{option}' in section '{section}': {e}", file=sys.stderr)
        sys.exit(1)

try:
    svxlog = None
    shutdown = False
    signal.signal(signal.SIGTERM, shutdown_signal_handler)
    signal.signal(signal.SIGINT, shutdown_signal_handler)

    logger = logging.getLogger('oled')
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.setLevel(logging.WARNING)

    config_file = 'oledsvx.ini'
    config = load_config(config_file)

    driver = get_config_value(config, 'driver', str)
    i2c_port = get_config_value(config, 'i2c_port', int, default=1)
    i2c_address = get_config_value(config, 'i2c_address', str, default="0x3C")
    i2c_address = int(i2c_address, 16)
    contrast_nor = get_config_value(config, 'contrast_nor', int)
    contrast_low = get_config_value(config, 'contrast_low', int)
    screensaver_time = get_config_value(config, 'screensaver_time', int, default=0)
    ext_temp_sensor = get_config_value(config, 'ext_temp_sensor', bool)
    debug = get_config_value(config, 'debug', bool, default=False)

    supported_drivers = ["sh1106", "ssd1306", "ssd1309"]

    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", help="Show debugging information.", action="store_true", default=None)
    args = parser.parse_args()

    if args.debug is not None:
        debug = args.debug

    if debug:
        logger.setLevel(logging.DEBUG)

    if driver not in supported_drivers:
        print("Unsupported driver: %s. Supported drivers are: %s" % (driver, supported_drivers), file=sys.stderr)
        sys.exit(1)

    driver_class_name = "Screen%s" % driver.upper()
    driver_class = globals()[driver_class_name]

    sc = driver_class(i2c_port=i2c_port, i2c_address=i2c_address,
                      screensaver_time=screensaver_time, contrast_normal_val=contrast_nor,
                      contrast_low_val=contrast_low, ext_temp_sensor=ext_temp_sensor)
    svxlog = SvxLogMonitor(screen=sc)

    while True:
        if shutdown:
            svxlog.stop_monitoring()
            sc.shutdown()

        save_screen = sc.save_screen()

        logger.debug(f"Current TG: |{sc.current_tg}|, Last Call: |{sc.current_call}|, Pending calls: |{sc.calls}|, Save screen: {save_screen}")

        if save_screen:
            time.sleep(0.5)
            continue

        sc.update_ip_or_tg()
        sc.update_temp_and_load()
        sc.update_talkers_or_time()
        sc.check_contrast_lock()
        sc.redraw_oled()
        time.sleep(0.5)

except Exception as e:
    if svxlog:
        svxlog.stop_monitoring()
    raise

import schedule
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime, time as dt_time
import os
from pprint import pprint
import random

DEBUG=False
RAMADAN=False
ADHANS = []
FAJR = []
LIST=[]
FAJR_LIST=[]
NAMES = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha']
FAJR_DIR = 'mp3/fajr/'
NON_FAJR_DIR = 'mp3/non-fajr/'
DUA_PATH = 'mp3/dua/dua.mp3'

fajr_ramadan = [
  "05:33", "05:31", "05:30", "05:28", "05:26",
  "05:24", "05:23", "05:21", "06:20", "06:19",
  "06:17", "06:15", "06:14", "06:12", "06:10",
  "06:08", "06:06", "06:04", "06:02", "06:00",
  "05:59", "05:57", "05:55", "05:53", "05:51",
  "05:49", "05:47", "05:45", "05:43", "05:41"
];

magrib_ramadan = [
  "18:06", "18:07", "18:08", "18:10", "18:11",
  "18:12", "18:13", "18:15", "19:15", "19:16",
  "19:17", "19:18", "19:20", "19:21", "19:22",
  "19:23", "19:25", "19:26", "19:27", "19:28",
  "19:29", "19:31", "19:32", "19:33", "19:34",
  "19:36", "19:37", "19:38", "19:39", "19:40"
];

def update_adhans():
    global ADHANS, FAJR
    ADHANS = [f for f in os.listdir('./mp3/non-fajr') if os.path.isfile(os.path.join('./mp3/non-fajr', f))]
    ADHANS = [file for file in ADHANS if file.endswith('.mp3')]
    FAJR = [f for f in os.listdir('./mp3/fajr') if os.path.isfile(os.path.join('./mp3/fajr', f))]
    FAJR = [file for file in FAJR if file.endswith('.mp3')]
 
def get_updated_times():
    url = 'https://www.islamicfinder.org/prayer-widget/5882873/hanfi/5/0/15.0/15.0'
    while True:
        try:
            # import os
            # SSID = "Tell My WiFi Love Her"
            # Connect to the WiFi network (Replace "en0" with your WiFi adapter name if different)
            # os.system(f'networksetup -setairportnetwork en0 "{SSID}"')

            page = requests.get(url, timeout=10)  # Added timeout for better handling
            page.raise_for_status()  # Raise an exception for HTTP errors (4xx, 5xx)
            soup = BeautifulSoup(page.content, 'html.parser')
            prayer_times_div = soup.find('div', id='calendar-slider')
            prayer_times = []
            prayer_times_raw = prayer_times_div.find_all('div', {
                'class': 'd-flex flex-direction-row flex-justify-sb pad-top-sm pad-left-sm pad-right-sm'
            })
            
            for prayer_time in prayer_times_raw:
                time_raw = prayer_time.find_all('p')[1].get_text()
                dt = datetime.strptime(time_raw.strip(), "%I:%M %p")
                prayer_times.append(dt.strftime("%H:%M"))
            
            prayer_times.pop(1)  # remove sunrise
            
            if (RAMADAN):
                current_day = datetime.now().day # worked only because islamic and gregorian were in sync
                prayer_times[0] = fajr_ramadan[current_day]
                prayer_times[3] = magrib_ramadan[current_day]
            
            return prayer_times  # Success, return the times

        except (requests.RequestException, AttributeError, IndexError) as e:
            print(e)
            print("Error fetching prayer times")
            os.system('networksetup -setairportpower en1 off')
            time.sleep(10)
            os.system('networksetup -setairportpower en1 on')
            print("Retrying in 1 hour...")
            time.sleep(3600)

def play_adhan():
    if (len(LIST) == 0):
        LIST.extend(ADHANS)
    
    if (len(FAJR_LIST) == 0):
        FAJR_LIST.extend(FAJR)

    print(LIST)
    print(FAJR_LIST)

    if (time.strftime('%p') == "PM"):
        x=LIST.pop(random.randrange(len(LIST)))
        command = "afplay " + NON_FAJR_DIR + str(x)
    else:
        x=FAJR_LIST.pop(random.randrange(len(FAJR_LIST)))
        command = "afplay " + FAJR_DIR + str(x)

    print("playing adhan: "+str(x))
    print("Time is " + time.strftime('%I:%M %p'))
    print("\n")
    os.system(command)
    command = "afplay " + DUA_PATH
    os.system(command)
    return schedule.CancelJob
 
 
def schedule_prayer_times(prayer_times = None):
    if (DEBUG):
        # while len(LIST) != 1: play_adhan()
        play_adhan()
        return
    
    if not prayer_times: prayer_times = get_updated_times()
    for pt in prayer_times:
        schedule.every().day.at(pt).do(play_adhan)
        prayer_name = NAMES[prayer_times.index(pt) + (5 - len(prayer_times))]
        print("Scheduled " + prayer_name + " to play at " + pt)

def scheduled_job():
    update_adhans()
    schedule_prayer_times()

# Main process
print('DEBUG: ' + str(DEBUG))
if DEBUG:
    scheduled_job()
else:
    update_adhans()

    # schedule any adhans left for the day
    prayer_times = get_updated_times()
    pending_prayer_times = []
    now = datetime.now().time()
    midnight = dt_time(23, 59, 59)
    for prayer_time in prayer_times:
        pt = datetime.strptime(prayer_time, "%H:%M").time()
        if (now < pt <= midnight):
            pending_prayer_times.append(prayer_time)
    schedule_prayer_times(pending_prayer_times)

    # auto schedule all adhans after today every night
    schedule.every().day.at('00:30').do(scheduled_job)
 
while True:
    schedule.run_pending()
    time.sleep(1)
 

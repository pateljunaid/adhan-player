import schedule
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime, time as dt_time
import os
from pprint import pprint
import random

DEBUG=False
ADHANS = []
FAJR = []
LIST=[]
FAJR_LIST=[]
NAMES = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha']
FAJR_DIR = 'mp3/fajr/'
NON_FAJR_DIR = 'mp3/non-fajr/'

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

            # Alternative:
            # networksetup -setairportpower en0 off # turn off device en0
            # networksetup -setairportpower en0 on  # turn on device en0

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
            return prayer_times  # Success, return the times

        except (requests.RequestException, AttributeError, IndexError) as e:
            print(f"Error fetching prayer times: {e}")
            print("Retrying in 1 hour...")
            time.sleep(3600)  # Wait for 1 hour before retrying

def play_adhan():
    if (len(LIST) == 0):
        LIST.extend(ADHANS)
    
    if (len(FAJR_LIST) == 0):
        FAJR_LIST.extend(FAJR)

    print("\n".join(LIST))

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
 

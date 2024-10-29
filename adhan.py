import schedule
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import os
from pprint import pprint
import random
 
ADHANS=[1,2,3,4,5,6,7,8,9]
FAJR=[1,2,3,4]
LIST=[]
DEBUG=False
NAMES = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha']
FAJR_DIR = 'mp3/fajr/'
NON_FAJR_DIR = 'mp3/non-fajr/'

def get_updated_times():
    # make a request to the website
    url = 'https://www.islamicfinder.org/prayer-widget/5882873/hanfi/5/0/15.0/15.0'
    page = requests.get(url)
 
    # parse the HTML using BeautifulSoup
    soup = BeautifulSoup(page.content, 'html.parser')
 
    # find the div containing the prayer times
    prayer_times_div = soup.find('div', id='calendar-slider')
    # extract the prayer times
    prayer_times = []
    prayer_times_raw = prayer_times_div.find_all('div', {
        'class': 'd-flex flex-direction-row flex-justify-sb pad-top-sm pad-left-sm pad-right-sm'})
    for prayer_time in prayer_times_raw:
        time_raw = prayer_time.find_all('p')[1].get_text()
        dt = datetime.strptime(time_raw.strip(), "%I:%M %p")
        prayer_times.append(dt.strftime("%H:%M"))
 
    prayer_times.pop(1)  # remove sunrise
    return prayer_times
 
 
def play_adhan():
    if (len(LIST) == 0):
        LIST.extend(ADHANS)
    print(LIST)

    if (time.strftime('%p') == "PM"):
        x=LIST.pop(random.randrange(len(LIST)))
        command = "afplay " + NON_FAJR_DIR + "adhan-" + str(x) + ".mp3"
    else:
        x=random.randrange(len(FAJR))+1
        command = "afplay " + FAJR_DIR + "fajr-" + str(x) + ".mp3"

    print("playing adhan-"+str(x))
    print("Time is " + time.strftime('%I:%M %p'))
    os.system(command)
    return schedule.CancelJob
 
 
def schedule_prayer_times():    
    if (DEBUG): 
        play_adhan()
        return

    prayer_times = get_updated_times()
    for pt in prayer_times:
        schedule.every().day.at(pt).do(play_adhan)
        prayer_name = NAMES[prayer_times.index(pt)]
        print("Scheduled " + prayer_name + " to play at " + pt)

# schedule_prayer_times()
schedule.every().day.at('00:30').do(schedule_prayer_times)
print('DEBUG: ' + str(DEBUG))
 
while True:
    schedule.run_pending()
    time.sleep(1)
 

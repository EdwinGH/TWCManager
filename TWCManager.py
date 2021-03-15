#! /usr/bin/python3

################################################################################
# Code and TWC protocol reverse engineering by Chris Dragon.
#
# Additional logs and hints provided by Teslamotorsclub.com users:
#   TheNoOne, IanAmber, and twc.
# Thank you!
#
# For support and information, please read through this thread:
# https://teslamotorsclub.com/tmc/threads/new-wall-connector-load-sharing-protocol.72830
#
# Report bugs at https://github.com/cdragon/TWCManager/issues
#
# This software is released under the "Unlicense" model: http://unlicense.org
# This means source code and TWC protocol knowledge are released to the general
# public free for personal or commercial use. I hope the knowledge will be used
# to increase the use of green energy sources by controlling the time and power
# level of car charging.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# For more information, please visit http://unlicense.org

import commentjson
import json
import os.path
import math
import queue
import random
import re
import serial
import subprocess
import struct
import sys
import sysv_ipc
import time
import traceback
from datetime import datetime
import threading
from lib.TWCManager.TWCSlave import TWCSlave
from lib.TWCManager.EMS.Fronius import Fronius
from lib.TWCManager.EMS.HASS import HASS
from lib.TWCManager.Status.HASSStatus import HASSStatus
from lib.TWCManager.Status.MQTTStatus import MQTTStatus
from lib.TWCManager.Vehicle.TeslaAPI import CarApi

##########################
# Load Configuration File
config = None
jsonconfig = None
if (os.path.isfile('/etc/twcmanager/config.json')):
    jsonconfig = open('/etc/twcmanager/config.json')
else:
    if (os.path.isfile('config.json')):
        jsonconfig = open('config.json')

if (jsonconfig):
    config = commentjson.load(jsonconfig)
else:
    print("Unable to find a configuration file.")
    sys.exit()

# All TWCs ship with a random two-byte TWCID. We default to using 0x7777 as our
# fake TWC ID. There is a 1 in 64535 chance that this ID will match each real
# TWC on the network, in which case you should pick a different random id below.
# This isn't really too important because even if this ID matches another TWC on
# the network, that TWC will pick its own new random ID as soon as it sees ours
# conflicts.
fakeTWCID = bytearray(b'\x77\x77')

# TWCs send a seemingly-random byte after their 2-byte TWC id in a number of
# messages. I call this byte their "Sign" for lack of a better term. The byte
# never changes unless the TWC is reset or power cycled. We use hard-coded
# values for now because I don't know if there are any rules to what values can
# be chosen. I picked 77 because it's easy to recognize when looking at logs.
# These shouldn't need to be changed.
masterSign = bytearray(b'\x77')
slaveSign = bytearray(b'\x77')


#
# End configuration parameters
#
##############################


##############################
#
# Begin functions
#

def hex_str(s:str):
    return " ".join("{:02X}".format(ord(c)) for c in s)

def hex_str(ba:bytearray):
    return " ".join("{:02X}".format(c) for c in ba)

def run_process(cmd):
    result = None
    try:
        result = subprocess.check_output(cmd, shell=True)
    except subprocess.CalledProcessError:
        # We reach this point if the process returns a non-zero exit code.
        result = b''

    return result

def time_now():
    global config
    return(datetime.now().strftime("%H:%M:%S" + (
        ".%f" if config['config']['displayMilliseconds'] else "")))

def load_settings():
    global config, nonScheduledAmpsMax, scheduledAmpsMax, \
           scheduledAmpsStartHour, scheduledAmpsEndHour, \
           scheduledAmpsDaysBitmap, hourResumeTrackGreenEnergy, kWhDelivered, \
           carapi, carApiTokenExpireTime, homeLat, homeLon

    try:
        fh = open(config['config']['settingsPath'] + "TWCManager.settings", 'r')

        for line in fh:
            m = re.search(r'^\s*nonScheduledAmpsMax\s*=\s*([-0-9.]+)', line, re.MULTILINE)
            if(m):
                nonScheduledAmpsMax = int(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: nonScheduledAmpsMax set to " + str(nonScheduledAmpsMax))
                continue

            m = re.search(r'^\s*scheduledAmpsMax\s*=\s*([-0-9.]+)', line, re.MULTILINE)
            if(m):
                scheduledAmpsMax = int(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: scheduledAmpsMax set to " + str(scheduledAmpsMax))
                continue

            m = re.search(r'^\s*scheduledAmpsStartHour\s*=\s*([-0-9.]+)', line, re.MULTILINE)
            if(m):
                scheduledAmpsStartHour = float(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: scheduledAmpsStartHour set to " + str(scheduledAmpsStartHour))
                continue

            m = re.search(r'^\s*scheduledAmpsEndHour\s*=\s*([-0-9.]+)', line, re.MULTILINE)
            if(m):
                scheduledAmpsEndHour = float(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: scheduledAmpsEndHour set to " + str(scheduledAmpsEndHour))
                continue

            m = re.search(r'^\s*scheduledAmpsDaysBitmap\s*=\s*([-0-9.]+)', line, re.MULTILINE)
            if(m):
                scheduledAmpsDaysBitmap = int(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: scheduledAmpsDaysBitmap set to " + str(scheduledAmpsDaysBitmap))
                continue

            m = re.search(r'^\s*hourResumeTrackGreenEnergy\s*=\s*([-0-9.]+)', line, re.MULTILINE)
            if(m):
                hourResumeTrackGreenEnergy = float(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: hourResumeTrackGreenEnergy set to " + str(hourResumeTrackGreenEnergy))
                continue

            m = re.search(r'^\s*kWhDelivered\s*=\s*([-0-9.]+)', line, re.MULTILINE)
            if(m):
                kWhDelivered = float(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: kWhDelivered set to " + str(kWhDelivered))
                continue

            m = re.search(r'^\s*carApiBearerToken\s*=\s*(.+)', line, re.MULTILINE)
            if(m):
                carapi.setCarApiBearerToken(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: carApiBearerToken set to " + str(m.group(1)))
                continue

            m = re.search(r'^\s*carApiRefreshToken\s*=\s*(.+)', line, re.MULTILINE)
            if(m):
                carapi.setCarApiRefreshToken(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: carApiRefreshToken set to " + str(m.group(1)))
                continue

            m = re.search(r'^\s*carApiTokenExpireTime\s*=\s*(.+)', line, re.MULTILINE)
            if(m):
                carApiTokenExpireTime = float(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: carApiTokenExpireTime set to " + str(carApiTokenExpireTime))
                continue

            m = re.search(r'^\s*homeLat\s*=\s*(.+)', line, re.MULTILINE)
            if(m):
                homeLat = float(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: homeLat set to " + str(homeLat))
                continue

            m = re.search(r'^\s*homeLon\s*=\s*(.+)', line, re.MULTILINE)
            if(m):
                homeLon = float(m.group(1))
                if(config['config']['debugLevel'] >= 10):
                    print("load_settings: homeLon set to " + str(homeLon))
                continue

            print(time_now() + ": load_settings: Unknown setting " + line)

        fh.close()

    except FileNotFoundError:
        pass

def save_settings():
    global config, nonScheduledAmpsMax, scheduledAmpsMax, \
           scheduledAmpsStartHour, scheduledAmpsEndHour, \
           scheduledAmpsDaysBitmap, hourResumeTrackGreenEnergy, kWhDelivered, \
           carapi, carApiTokenExpireTime, homeLat, homeLon

    fh = open(config['config']['settingsPath'] + "TWCManager.settings", 'w')
    fh.write('nonScheduledAmpsMax=' + str(nonScheduledAmpsMax) +
            '\nscheduledAmpsMax=' + str(scheduledAmpsMax) +
            '\nscheduledAmpsStartHour=' + str(scheduledAmpsStartHour) +
            '\nscheduledAmpsEndHour=' + str(scheduledAmpsEndHour) +
            '\nscheduledAmpsDaysBitmap=' + str(scheduledAmpsDaysBitmap) +
            '\nhourResumeTrackGreenEnergy=' + str(hourResumeTrackGreenEnergy) +
            '\nkWhDelivered=' + str(kWhDelivered) +
            '\ncarApiBearerToken=' + str(carapi.getCarApiBearerToken()) +
            '\ncarApiRefreshToken=' + str(carapi.getCarApiRefreshToken()) +
            '\ncarApiTokenExpireTime=' + str(int(carApiTokenExpireTime)) +
            '\nhomeLat=' + str(homeLat) +
            '\nhomeLon=' + str(homeLon)
            )

    fh.close()

def trim_pad(s:bytearray, makeLen):
    # Trim or pad s with zeros so that it's makeLen length.
    while(len(s) < makeLen):
        s += b'\x00'

    if(len(s) > makeLen):
        s = s[0:makeLen]

    return s


def send_msg(msg):
    # Send msg on the RS485 network. We'll escape bytes with a special meaning,
    # add a CRC byte to the message end, and add a C0 byte to the start and end
    # to mark where it begins and ends.
    global ser, timeLastTx, config

    msg = bytearray(msg)
    checksum = 0
    for i in range(1, len(msg)):
        checksum += msg[i]

    msg.append(checksum & 0xFF)

    # Escaping special chars:
    # The protocol uses C0 to mark the start and end of the message.  If a C0
    # must appear within the message, it is 'escaped' by replacing it with
    # DB and DC bytes.
    # A DB byte in the message is escaped by replacing it with DB DD.
    #
    # User FuzzyLogic found that this method of escaping and marking the start
    # and end of messages is based on the SLIP protocol discussed here:
    #   https://en.wikipedia.org/wiki/Serial_Line_Internet_Protocol
    i = 0
    while(i < len(msg)):
        if(msg[i] == 0xc0):
            msg[i:i+1] = b'\xdb\xdc'
            i = i + 1
        elif(msg[i] == 0xdb):
            msg[i:i+1] = b'\xdb\xdd'
            i = i + 1
        i = i + 1

    msg = bytearray(b'\xc0' + msg + b'\xc0')

    if(config['config']['debugLevel'] >= 9):
        print("Tx@" + time_now() + ": " + hex_str(msg))

    ser.write(msg)

    timeLastTx = time.time()

def unescape_msg(msg:bytearray, msgLen):
    # Given a message received on the RS485 network, remove leading and trailing
    # C0 byte, unescape special byte values, and verify its data matches the CRC
    # byte.
    msg = msg[0:msgLen]

    # See notes in send_msg() for the way certain bytes in messages are escaped.
    # We basically want to change db dc into c0 and db dd into db.
    # Only scan to one less than the length of the string to avoid running off
    # the end looking at i+1.
    i = 0
    while i < len(msg):
        if(msg[i] == 0xdb):
            if(msg[i+1] == 0xdc):
                # Replace characters at msg[i] and msg[i+1] with 0xc0,
                # shortening the string by one character. In Python, msg[x:y]
                # refers to a substring starting at x and ending immediately
                # before y. y - x is the length of the substring.
                msg[i:i+2] = [0xc0]
            elif(msg[i+1] == 0xdd):
                msg[i:i+2] = [0xdb]
            else:
                print(time_now(), "ERROR: Special character 0xDB in message is " \
                  "followed by invalid character 0x%02X.  " \
                  "Message may be corrupted." %
                  (msg[i+1]))

                # Replace the character with something even though it's probably
                # not the right thing.
                msg[i:i+2] = [0xdb]
        i = i+1

    # Remove leading and trailing C0 byte.
    msg = msg[1:len(msg)-1]
    return msg


def send_master_linkready1():
    global config
    
    if(config['config']['debugLevel'] >= 1):
        print(time_now() + ": Send master linkready1")

    # When master is powered on or reset, it sends 5 to 7 copies of this
    # linkready1 message followed by 5 copies of linkready2 (I've never seen
    # more or less than 5 of linkready2).
    #
    # This linkready1 message advertises master's TWCID to other slaves on the
    # network.
    # If a slave happens to have the same id as master, it will pick a new
    # random TWCID. Other than that, slaves don't seem to respond to linkready1.

    # linkready1 and linkready2 are identical except FC E1 is replaced by FB E2
    # in bytes 2-3. Both messages will cause a slave to pick a new id if the
    # slave's id conflicts with master.
    # If a slave stops sending heartbeats for awhile, master may send a series
    # of linkready1 and linkready2 messages in seemingly random order, which
    # means they don't indicate any sort of startup state.

    # linkready1 is not sent again after boot/reset unless a slave sends its
    # linkready message.
    # At that point, linkready1 message may start sending every 1-5 seconds, or
    # it may not be sent at all.
    # Behaviors I've seen:
    #   Not sent at all as long as slave keeps responding to heartbeat messages
    #   right from the start.
    #   If slave stops responding, then re-appears, linkready1 gets sent
    #   frequently.

    # One other possible purpose of linkready1 and/or linkready2 is to trigger
    # an error condition if two TWCs on the network transmit those messages.
    # That means two TWCs have rotary switches setting them to master mode and
    # they will both flash their red LED 4 times with top green light on if that
    # happens.

    # Also note that linkready1 starts with FC E1 which is similar to the FC D1
    # message that masters send out every 4 hours when idle. Oddly, the FC D1
    # message contains all zeros instead of the master's id, so it seems
    # pointless.

    # I also don't understand the purpose of having both linkready1 and
    # linkready2 since only two or more linkready2 will provoke a response from
    # a slave regardless of whether linkready1 was sent previously. Firmware
    # trace shows that slaves do something somewhat complex when they receive
    # linkready1 but I haven't been curious enough to try to understand what
    # they're doing. Tests show neither linkready1 or 2 are necessary. Slaves
    # send slave linkready every 10 seconds whether or not they got master
    # linkready1/2 and if a master sees slave linkready, it will start sending
    # the slave master heartbeat once per second and the two are then connected.
    send_msg(bytearray(b'\xFC\xE1') + fakeTWCID + masterSign + bytearray(b'\x00\x00\x00\x00\x00\x00\x00\x00'))


def send_master_linkready2():
    global config
    
    if(config['config']['debugLevel'] >= 1):
        print(time_now() + ": Send master linkready2")

    # This linkready2 message is also sent 5 times when master is booted/reset
    # and then not sent again if no other TWCs are heard from on the network.
    # If the master has ever seen a slave on the network, linkready2 is sent at
    # long intervals.
    # Slaves always ignore the first linkready2, but respond to the second
    # linkready2 around 0.2s later by sending five slave linkready messages.
    #
    # It may be that this linkready2 message that sends FB E2 and the master
    # heartbeat that sends fb e0 message are really the same, (same FB byte
    # which I think is message type) except the E0 version includes the TWC ID
    # of the slave the message is intended for whereas the E2 version has no
    # recipient TWC ID.
    #
    # Once a master starts sending heartbeat messages to a slave, it
    # no longer sends the global linkready2 message (or if it does,
    # they're quite rare so I haven't seen them).
    send_msg(bytearray(b'\xFB\xE2') + fakeTWCID + masterSign + bytearray(b'\x00\x00\x00\x00\x00\x00\x00\x00'))

def send_slave_linkready():
    # In the message below, \x1F\x40 (hex 0x1f40 or 8000 in base 10) refers to
    # this being a max 80.00Amp charger model.
    # EU chargers are 32A and send 0x0c80 (3200 in base 10).
    #
    # I accidentally changed \x1f\x40 to \x2e\x69 at one point, which makes the
    # master TWC immediately start blinking its red LED 6 times with top green
    # LED on. Manual says this means "The networked Wall Connectors have
    # different maximum current capabilities".
    msg = bytearray(b'\xFD\xE2') + fakeTWCID + slaveSign + bytearray(b'\x1F\x40\x00\x00\x00\x00\x00\x00')
    if(self.protocolVersion == 2):
        msg += bytearray(b'\x00\x00')

    send_msg(msg)

def master_id_conflict():
    # We're playing fake slave, and we got a message from a master with our TWCID.
    # By convention, as a slave we must change our TWCID because a master will not.
    fakeTWCID[0] = random.randint(0, 0xFF)
    fakeTWCID[1] = random.randint(0, 0xFF)

    # Real slaves change their sign during a conflict, so we do too.
    slaveSign[0] = random.randint(0, 0xFF)

    print(time_now() + ": Master's TWCID matches our fake slave's TWCID.  " \
        "Picked new random TWCID %02X%02X with sign %02X" % \
        (fakeTWCID[0], fakeTWCID[1], slaveSign[0]))

def num_cars_charging_now():
    global config, hassstatus, mqttstatus, slaveTWCRoundRobin

    carsCharging = 0
    for slaveTWC in slaveTWCRoundRobin:
        if(slaveTWC.reportedAmpsActual >= 1.0):
            carsCharging += 1
            if(config['config']['debugLevel'] >= 10):
                print("BUGFIX: Number of cars charging now: " + str(carsCharging))
            hassstatus.setStatus(slaveTWC.TWCID, "cars_charging", carsCharging)
            mqttstatus.setStatus(slaveTWC.TWCID, "carsCharging", carsCharging)
    return carsCharging

def new_slave(newSlaveID, maxAmps):
    global slaveTWCs, slaveTWCRoundRobin, carapi

    try:
        slaveTWC = slaveTWCs[newSlaveID]
        # We didn't get KeyError exception, so this slave is already in
        # slaveTWCs and we can simply return it.
        return slaveTWC
    except KeyError:
        pass

    slaveTWC = TWCSlave(newSlaveID, maxAmps, carapi)
    slaveTWCs[newSlaveID] = slaveTWC
    slaveTWCRoundRobin.append(slaveTWC)

    if(len(slaveTWCRoundRobin) > 3):
        print("WARNING: More than 3 slave TWCs seen on network.  " \
            "Dropping oldest: " + hex_str(slaveTWCRoundRobin[0].TWCID) + ".")
        delete_slave(slaveTWCRoundRobin[0].TWCID)

    return slaveTWC

def delete_slave(deleteSlaveID):
    global slaveTWCs, slaveTWCRoundRobin

    for i in range(0, len(slaveTWCRoundRobin)):
        if(slaveTWCRoundRobin[i].TWCID == deleteSlaveID):
            del slaveTWCRoundRobin[i]
            break
    try:
        del slaveTWCs[deleteSlaveID]
    except KeyError:
        pass

def total_amps_actual_all_twcs():
    global config, master, slaveTWCRoundRobin

    totalAmps = 0
    for slaveTWC in slaveTWCRoundRobin:
        totalAmps += slaveTWC.reportedAmpsActual
        hassstatus.setStatus(slaveTWC.TWCID, "amps_in_use", slaveTWC.reportedAmpsActual)
        mqttstatus.setStatus(slaveTWC.TWCID, "ampsInUse", slaveTWC.reportedAmpsActual)
        master.setTotalAmpsInUse(totalAmps)

    if(config['config']['debugLevel'] >= 10):
        print("Total amps all slaves are using: " + str(totalAmps))
        hassstatus.setStatus(bytes("all", 'UTF-8'), "total_amps_in_use", totalAmps)
        mqttstatus.setStatus(bytes("all", 'UTF-8'), "totalAmpsInUse", totalAmps)
    return totalAmps


def car_api_available(email = None, password = None, charge = None):
    global config, carApiLastErrorTime, carapi, carApiTokenExpireTime

    now = time.time()
    apiResponseDict = {}

    if(now - carApiLastErrorTime < (carapi.getCarApiErrorRetryMins()*60)):
        # It's been under carApiErrorRetryMins minutes since the car API
        # generated an error. To keep strain off Tesla's API servers, wait
        # carApiErrorRetryMins mins till we try again. This delay could be
        # reduced if you feel the need. It's mostly here to deal with unexpected
        # errors that are hopefully transient.
        # https://teslamotorsclub.com/tmc/threads/model-s-rest-api.13410/page-114#post-2732052
        # says he tested hammering the servers with requests as fast as possible
        # and was automatically blacklisted after 2 minutes. Waiting 30 mins was
        # enough to clear the blacklist. So at this point it seems Tesla has
        # accepted that third party apps use the API and deals with bad behavior
        # automatically.
        if(config['config']['debugLevel'] >= 11):
            print(time_now() + ': Car API disabled for ' +
                  str(int(carapi.getCarApiErrorRetryMins()*60 - (now - carApiLastErrorTime))) +
                  ' more seconds due to recent error.')
        return False

    # Tesla car API info comes from https://timdorr.docs.apiary.io/
    if(carapi.getCarApiBearerToken() == '' or carApiTokenExpireTime - now < 30*24*60*60):
        cmd = None
        apiResponse = b''

        # If we don't have a bearer token or our refresh token will expire in
        # under 30 days, get a new bearer token.  Refresh tokens expire in 45
        # days when first issued, so we'll get a new token every 15 days.
        if(carapi.getCarApiRefreshToken() != ''):
            cmd = 'curl -s -m 60 -X POST -H "accept: application/json" -H "Content-Type: application/json" -d \'' + \
                  json.dumps({'grant_type': 'refresh_token', \
                              'client_id': '81527cff06843c8634fdc09e8ac0abefb46ac849f38fe1e431c2ef2106796384', \
                              'client_secret': 'c7257eb71a564034f9419ee651c7d0e5f7aa6bfbd18bafb5c5c033b093bb2fa3', \
                              'refresh_token': carapi.getCarApiRefreshToken() }) + \
                  '\' "https://owner-api.teslamotors.com/oauth/token"'
        elif(email != None and password != None):
            cmd = 'curl -s -m 60 -X POST -H "accept: application/json" -H "Content-Type: application/json" -d \'' + \
                  json.dumps({'grant_type': 'password', \
                              'client_id': '81527cff06843c8634fdc09e8ac0abefb46ac849f38fe1e431c2ef2106796384', \
                              'client_secret': 'c7257eb71a564034f9419ee651c7d0e5f7aa6bfbd18bafb5c5c033b093bb2fa3', \
                              'email': email, 'password': password }) + \
                  '\' "https://owner-api.teslamotors.com/oauth/token"'

        if(cmd != None):
            if(config['config']['debugLevel'] >= 2):
                # Hide car password in output
                cmdRedacted = re.sub(r'("password": )"[^"]+"', r'\1[HIDDEN]', cmd)
                print(time_now() + ': Car API cmd', cmdRedacted)
            apiResponse = run_process(cmd)
            # Example response:
            # b'{"access_token":"4720d5f980c9969b0ca77ab39399b9103adb63ee832014fe299684201929380","token_type":"bearer","expires_in":3888000,"refresh_token":"110dd4455437ed351649391a3425b411755a213aa815171a2c6bfea8cc1253ae","created_at":1525232970}'

        try:
            apiResponseDict = json.loads(apiResponse.decode('ascii'))
        except json.decoder.JSONDecodeError:
            pass

        try:
            if(config['config']['debugLevel'] >= 4):
                print(time_now() + ': Car API auth response', apiResponseDict, '\n')
            carapi.setCarApiBearerToken(apiResponseDict['access_token'])
            carapi.setCarApiRefreshToken(apiResponseDict['refresh_token'])
            carApiTokenExpireTime = now + apiResponseDict['expires_in']
        except KeyError:
            print(time_now() + ": ERROR: Can't access Tesla car via API.  Please log in again via web interface.")
            carApiLastErrorTime = now
            # Instead of just setting carApiLastErrorTime, erase tokens to
            # prevent further authorization attempts until user enters password
            # on web interface. I feel this is safer than trying to log in every
            # ten minutes with a bad token because Tesla might decide to block
            # remote access to your car after too many authorization errors.
            carapi.setCarApiBearerToken()
            carapi.setCarApiRefreshToken()

        save_settings()

    if(carapi.getCarApiBearerToken() != ''):
        if(carapi.getVehicleCount() < 1):
            cmd = 'curl -s -m 60 -H "accept: application/json" -H "Authorization:Bearer ' + \
                  carapi.getCarApiBearerToken() + \
                  '" "https://owner-api.teslamotors.com/api/1/vehicles"'
            if(config['config']['debugLevel'] >= 8):
                print(time_now() + ': Car API cmd', cmd)
            try:
                apiResponseDict = json.loads(run_process(cmd).decode('ascii'))
            except json.decoder.JSONDecodeError:
                pass

            try:
                if(config['config']['debugLevel'] >= 4):
                    print(time_now() + ': Car API vehicle list', apiResponseDict, '\n')

                for i in range(0, apiResponseDict['count']):
                    carapi.addVehicle(apiResponseDict['response'][i]['id'])
            except (KeyError, TypeError):
                # This catches cases like trying to access
                # apiResponseDict['response'] when 'response' doesn't exist in
                # apiResponseDict.
                print(time_now() + ": ERROR: Can't get list of vehicles via Tesla car API.  Will try again in "
                      + str(carapi.getCarApiErrorRetryMins()) + " minutes.")
                carApiLastErrorTime = now
                return False

        if(carapi.getVehicleCount() > 0):
            # Wake cars if needed
            needSleep = False
            for vehicle in carapi.getCarApiVehicles():
                if(charge == True and vehicle.stopAskingToStartCharging):
                    if(config['config']['debugLevel'] >= 8):
                        print(time_now() + ": Don't charge vehicle " + str(vehicle.ID)
                              + " because vehicle.stopAskingToStartCharging == True")
                    continue

                if(now - vehicle.lastErrorTime < (carapi.getCarApiErrorRetryMins()*60)):
                    # It's been under carApiErrorRetryMins minutes since the car
                    # API generated an error on this vehicle. Don't send it more
                    # commands yet.
                    if(config['config']['debugLevel'] >= 8):
                        print(time_now() + ": Don't send commands to vehicle " + str(vehicle.ID)
                              + " because it returned an error in the last "
                              + str(carapi.getCarApiErrorRetryMins()) + " minutes.")
                    continue

                if(vehicle.ready()):
                    continue

                if(now - vehicle.lastWakeAttemptTime <= vehicle.delayNextWakeAttempt):
                    if(config['config']['debugLevel'] >= 10):
                        print(time_now() + ": car_api_available returning False because we are still delaying "
                              + str(delayNextWakeAttempt) + " seconds after the last failed wake attempt.")
                    return False

                # It's been delayNextWakeAttempt seconds since we last failed to
                # wake the car, or it's never been woken. Wake it.
                vehicle.lastWakeAttemptTime = now
                cmd = 'curl -s -m 60 -X POST -H "accept: application/json" -H "Authorization:Bearer ' + \
                      carapi.getCarApiBearerToken() + \
                      '" "https://owner-api.teslamotors.com/api/1/vehicles/' + \
                      str(vehicle.ID) + '/wake_up"'
                if(config['config']['debugLevel'] >= 8):
                    print(time_now() + ': Car API cmd', cmd)

                try:
                    apiResponseDict = json.loads(run_process(cmd).decode('ascii'))
                except json.decoder.JSONDecodeError:
                    pass

                state = 'error'
                try:
                    if(config['config']['debugLevel'] >= 4):
                        print(time_now() + ': Car API wake car response', apiResponseDict, '\n')

                    state = apiResponseDict['response']['state']

                except (KeyError, TypeError):
                    # This catches unexpected cases like trying to access
                    # apiResponseDict['response'] when 'response' doesn't exist
                    # in apiResponseDict.
                    state = 'error'

                if(state == 'online'):
                    # With max power saving settings, car will almost always
                    # report 'asleep' or 'offline' the first time it's sent
                    # wake_up.  Rarely, it returns 'online' on the first wake_up
                    # even when the car has not been contacted in a long while.
                    # I suspect that happens when we happen to query the car
                    # when it periodically awakens for some reason.
                    vehicle.firstWakeAttemptTime = 0
                    vehicle.delayNextWakeAttempt = 0
                    # Don't alter vehicle.lastWakeAttemptTime because
                    # vehicle.ready() uses it to return True if the last wake
                    # was under 2 mins ago.
                    needSleep = True
                else:
                    if(vehicle.firstWakeAttemptTime == 0):
                        vehicle.firstWakeAttemptTime = now

                    if(state == 'asleep' or state == 'waking'):
                        if(now - vehicle.firstWakeAttemptTime <= 10*60):
                            # http://visibletesla.com has a 'force wakeup' mode
                            # that sends wake_up messages once every 5 seconds
                            # 15 times. This generally manages to wake my car if
                            # it's returning 'asleep' state, but I don't think
                            # there is any reason for 5 seconds and 15 attempts.
                            # The car did wake in two tests with that timing,
                            # but on the third test, it had not entered online
                            # mode by the 15th wake_up and took another 10+
                            # seconds to come online. In general, I hear relays
                            # in the car clicking a few seconds after the first
                            # wake_up but the car does not enter 'waking' or
                            # 'online' state for a random period of time. I've
                            # seen it take over one minute, 20 sec.
                            #
                            # I interpret this to mean a car in 'asleep' mode is
                            # still receiving car API messages and will start
                            # to wake after the first wake_up, but it may take
                            # awhile to finish waking up. Therefore, we try
                            # waking every 30 seconds for the first 10 mins.
                            vehicle.delayNextWakeAttempt = 30;
                        elif(now - vehicle.firstWakeAttemptTime <= 70*60):
                            # Cars in 'asleep' state should wake within a
                            # couple minutes in my experience, so we should
                            # never reach this point. If we do, try every 5
                            # minutes for the next hour.
                            vehicle.delayNextWakeAttempt = 5*60;
                        else:
                            # Car hasn't woken for an hour and 10 mins. Try
                            # again in 15 minutes. We'll show an error about
                            # reaching this point later.
                            vehicle.delayNextWakeAttempt = 15*60;
                    elif(state == 'offline'):
                        if(now - vehicle.firstWakeAttemptTime <= 31*60):
                            # A car in offline state is presumably not connected
                            # wirelessly so our wake_up command will not reach
                            # it. Instead, the car wakes itself every 20-30
                            # minutes and waits some period of time for a
                            # message, then goes back to sleep. I'm not sure
                            # what the period of time is, so I tried sending
                            # wake_up every 55 seconds for 16 minutes but the
                            # car failed to wake.
                            # Next I tried once every 25 seconds for 31 mins.
                            # This worked after 19.5 and 19.75 minutes in 2
                            # tests but I can't be sure the car stays awake for
                            # 30secs or if I just happened to send a command
                            # during a shorter period of wakefulness.
                            vehicle.delayNextWakeAttempt = 25;

                            # I've run tests sending wake_up every 10-30 mins to
                            # a car in offline state and it will go hours
                            # without waking unless you're lucky enough to hit
                            # it in the brief time it's waiting for wireless
                            # commands. I assume cars only enter offline state
                            # when set to max power saving mode, and even then,
                            # they don't always enter the state even after 8
                            # hours of no API contact or other interaction. I've
                            # seen it remain in 'asleep' state when contacted
                            # after 16.5 hours, but I also think I've seen it in
                            # offline state after less than 16 hours, so I'm not
                            # sure what the rules are or if maybe Tesla contacts
                            # the car periodically which resets the offline
                            # countdown.
                            #
                            # I've also seen it enter 'offline' state a few
                            # minutes after finishing charging, then go 'online'
                            # on the third retry every 55 seconds.  I suspect
                            # that might be a case of the car briefly losing
                            # wireless connection rather than actually going
                            # into a deep sleep.
                            # 'offline' may happen almost immediately if you
                            # don't have the charger plugged in.
                    else:
                        # Handle 'error' state.
                        if(now - vehicle.firstWakeAttemptTime <= 60*60):
                            # We've tried to wake the car for less than an
                            # hour.
                            foundKnownError = False
                            if('error' in apiResponseDict):
                                error = apiResponseDict['error']
                                for knownError in carapi.getCarApiTransientErrors():
                                    if(knownError == error[0:len(knownError)]):
                                        foundKnownError = True
                                        break

                            if(foundKnownError):
                                # I see these errors often enough that I think
                                # it's worth re-trying in 1 minute rather than
                                # waiting 5 minutes for retry in the standard
                                # error handler.
                                vehicle.delayNextWakeAttempt = 60;
                            else:
                                # We're in an unexpected state. This could be caused
                                # by the API servers being down, car being out of
                                # range, or by something I can't anticipate. Try
                                # waking the car every 5 mins.
                                vehicle.delayNextWakeAttempt = 5*60;
                        else:
                            # Car hasn't woken for over an hour. Try again
                            # in 15 minutes. We'll show an error about this
                            # later.
                            vehicle.delayNextWakeAttempt = 15*60;

                    if(config['config']['debugLevel'] >= 1):
                        if(state == 'error'):
                            print(time_now() + ": Car API wake car failed with unknown response.  " \
                                "Will try again in "
                                + str(vehicle.delayNextWakeAttempt) + " seconds.")
                        else:
                            print(time_now() + ": Car API wake car failed.  State remains: '"
                                + state + "'.  Will try again in "
                                + str(vehicle.delayNextWakeAttempt) + " seconds.")

                if(vehicle.firstWakeAttemptTime > 0
                   and now - vehicle.firstWakeAttemptTime > 60*60):
                    # It should never take over an hour to wake a car.  If it
                    # does, ask user to report an error.
                    print(time_now() + ": ERROR: We have failed to wake a car from '"
                        + state + "' state for %.1f hours.\n" \
                          "Please private message user CDragon at " \
                          "http://teslamotorsclub.com with a copy of this error. " \
                          "Also include this: %s" % (
                          ((now - vehicle.firstWakeAttemptTime) / 60 / 60),
                          str(apiResponseDict)))

    if(now - carApiLastErrorTime < (carapi.getCarApiErrorRetryMins()*60) or carapi.getCarApiBearerToken() == ''):
        if(config['config']['debugLevel'] >= 8):
            print(time_now() + ": car_api_available returning False because of recent carApiLasterrorTime "
                + str(now - carApiLastErrorTime) + " or empty carApiBearerToken '"
                + carapi.getCarApiBearerToken() + "'")
        return False

    if(config['config']['debugLevel'] >= 8):
        # We return True to indicate there was no error that prevents running
        # car API commands and that we successfully got a list of vehicles.
        # True does not indicate that any vehicle is actually awake and ready
        # for commands.
        print(time_now() + ": car_api_available returning True")

    if(needSleep):
        # If you send charge_start/stop less than 1 second after calling
        # update_location(), the charge command usually returns:
        #   {'response': {'result': False, 'reason': 'could_not_wake_buses'}}
        # I'm not sure if the same problem exists when sending commands too
        # quickly after we send wake_up.  I haven't seen a problem sending a
        # command immediately, but it seems safest to sleep 5 seconds after
        # waking before sending a command.
        time.sleep(5);

    return True

def car_api_charge(charge):
    # Do not call this function directly.  Call by using background thread:
    # queue_background_task({'cmd':'charge', 'charge':<True/False>})
    global carApiLastErrorTime, carapi, homeLat, homeLon, config

    now = time.time()
    apiResponseDict = {}
    if(not charge):
        # Whenever we are going to tell vehicles to stop charging, set
        # vehicle.stopAskingToStartCharging = False on all vehicles.
        for vehicle in carapi.getCarApiVehicles():
            vehicle.stopAskingToStartCharging = False

    if(now - carapi.getLastStartOrStopChargeTime() < 60):
        # Don't start or stop more often than once a minute
        if(config['config']['debugLevel'] >= 8):
            print(time_now() + ': car_api_charge return because under 60 sec since last carApiLastStartOrStopChargeTime')
        return 'error'

    if(car_api_available(charge = charge) == False):
        if(config['config']['debugLevel'] >= 8):
            print(time_now() + ': car_api_charge return because car_api_available() == False')
        return 'error'

    startOrStop = 'start' if charge else 'stop'
    result = 'success'
    if(config['config']['debugLevel'] >= 8):
        print("startOrStop is set to " + str(startOrStop))
        
    for vehicle in carapi.getCarApiVehicles():
        if(charge and vehicle.stopAskingToStartCharging):
            if(config['config']['debugLevel'] >= 8):
                print(time_now() + ": Don't charge vehicle " + str(vehicle.ID)
                      + " because vehicle.stopAskingToStartCharging == True")
            continue

        if(vehicle.ready() == False):
            continue

        # Only update carApiLastStartOrStopChargeTime if car_api_available() managed
        # to wake cars.  Setting this prevents any command below from being sent
        # more than once per minute.
        carapi.updateLastStartOrStopChargeTime()

        if(config['config']['onlyChargeMultiCarsAtHome'] and carapi.getVehicleCount() > 1):
            # When multiple cars are enrolled in the car API, only start/stop
            # charging cars parked at home.

            if(vehicle.update_location() == False):
                result = 'error'
                continue

            if(homeLat == 10000):
                if(config['config']['debugLevel'] >= 1):
                    print(time_now() + ": Home location for vehicles has never been set.  " +
                        "We'll assume home is where we found the first vehicle currently parked.  " +
                        "Home set to lat=" + str(vehicle.lat) + ", lon=" +
                        str(vehicle.lon))
                homeLat = vehicle.lat
                homeLon = vehicle.lon
                save_settings()

            # 1 lat or lon = ~364488.888 feet. The exact feet is different depending
            # on the value of latitude, but this value should be close enough for
            # our rough needs.
            # 1/364488.888 * 10560 = 0.0289.
            # So if vehicle is within 0289 lat and lon of homeLat/Lon,
            # it's within ~10560 feet (2 miles) of home and we'll consider it to be
            # at home.
            # I originally tried using 0.00548 (~2000 feet) but one night the car
            # consistently reported being 2839 feet away from home despite being
            # parked in the exact spot I always park it.  This is very odd because
            # GPS is supposed to be accurate to within 12 feet.  Tesla phone app
            # also reports the car is not at its usual address.  I suspect this
            # is another case of a bug that's been causing car GPS to freeze  the
            # last couple months.
            if(abs(homeLat - vehicle.lat) > 0.0289
               or abs(homeLon - vehicle.lon) > 0.0289):
                # Vehicle is not at home, so don't change its charge state.
                if(config['config']['debugLevel'] >= 1):
                    print(time_now() + ': Vehicle ID ' + str(vehicle.ID) +
                          ' is not at home.  Do not ' + startOrStop + ' charge.')
                continue

            # If you send charge_start/stop less than 1 second after calling
            # update_location(), the charge command usually returns:
            #   {'response': {'result': False, 'reason': 'could_not_wake_buses'}}
            # Waiting 2 seconds seems to consistently avoid the error, but let's
            # wait 5 seconds in case of hardware differences between cars.
            time.sleep(5)

        cmd = 'curl -s -m 60 -X POST -H "accept: application/json" -H "Authorization:Bearer ' + \
              carapi.getCarApiBearerToken() + \
              '" "https://owner-api.teslamotors.com/api/1/vehicles/' + \
            str(vehicle.ID) + '/command/charge_' + startOrStop + '"'

        # Retry up to 3 times on certain errors.
        for retryCount in range(0, 3):
            if(config['config']['debugLevel'] >= 8):
                print(time_now() + ': Car API cmd', cmd)

            try:
                apiResponseDict = json.loads(run_process(cmd).decode('ascii'))
            except json.decoder.JSONDecodeError:
                pass

            try:
                if(config['config']['debugLevel'] >= 4):
                    print(time_now() + ': Car API TWC ID: ' + str(vehicle.ID) + ": " + startOrStop + \
                          ' charge response', apiResponseDict, '\n')
                # Responses I've seen in apiResponseDict:
                # Car is done charging:
                #   {'response': {'result': False, 'reason': 'complete'}}
                # Car wants to charge but may not actually be charging. Oddly, this
                # is the state reported when car is not plugged in to a charger!
                # It's also reported when plugged in but charger is not offering
                # power or even when the car is in an error state and refuses to
                # charge.
                #   {'response': {'result': False, 'reason': 'charging'}}
                # Car not reachable:
                #   {'response': None, 'error_description': '', 'error': 'vehicle unavailable: {:error=>"vehicle unavailable:"}'}
                # This weird error seems to happen randomly and re-trying a few
                # seconds later often succeeds:
                #   {'response': {'result': False, 'reason': 'could_not_wake_buses'}}
                # I've seen this a few times on wake_up, charge_start, and drive_state:
                #   {'error': 'upstream internal error', 'response': None, 'error_description': ''}
                # I've seen this once on wake_up:
                #   {'error': 'operation_timedout for txid `4853e3ad74de12733f8cc957c9f60040`}', 'response': None, 'error_description': ''}
                # Start or stop charging success:
                #   {'response': {'result': True, 'reason': ''}}
                if(apiResponseDict['response'] == None):
                    if('error' in apiResponseDict):
                        foundKnownError = False
                        error = apiResponseDict['error']
                        for knownError in carapi.getCarApiTransientErrors():
                            if(knownError == error[0:len(knownError)]):
                                # I see these errors often enough that I think
                                # it's worth re-trying in 1 minute rather than
                                # waiting carApiErrorRetryMins minutes for retry
                                # in the standard error handler.
                                if(config['config']['debugLevel'] >= 1):
                                    print(time_now() + ": Car API returned '"
                                          + error
                                          + "' when trying to start charging.  Try again in 1 minute.")
                                time.sleep(60)
                                foundKnownError = True
                                break
                        if(foundKnownError):
                            continue

                    # This generally indicates a significant error like 'vehicle
                    # unavailable', but it's not something I think the caller can do
                    # anything about, so return generic 'error'.
                    result = 'error'
                    # Don't send another command to this vehicle for
                    # carApiErrorRetryMins mins.
                    vehicle.lastErrorTime = now
                elif(apiResponseDict['response']['result'] == False):
                    if(charge):
                        reason = apiResponseDict['response']['reason']
                        if(reason == 'complete' or reason == 'charging'):
                            # We asked the car to charge, but it responded that
                            # it can't, either because it's reached target
                            # charge state (reason == 'complete'), or it's
                            # already trying to charge (reason == 'charging').
                            # In these cases, it won't help to keep asking it to
                            # charge, so set vehicle.stopAskingToStartCharging =
                            # True.
                            #
                            # Remember, this only means at least one car in the
                            # list wants us to stop asking and we don't know
                            # which car in the list is connected to our TWC.
                            if(config['config']['debugLevel'] >= 1):
                                print(time_now() + ': Vehicle ' + str(vehicle.ID)
                                      + ' is done charging or already trying to charge.  Stop asking to start charging.')
                            vehicle.stopAskingToStartCharging = True
                        else:
                            # Car was unable to charge for some other reason, such
                            # as 'could_not_wake_buses'.
                            if(reason == 'could_not_wake_buses'):
                                # This error often happens if you call
                                # charge_start too quickly after another command
                                # like drive_state. Even if you delay 5 seconds
                                # between the commands, this error still comes
                                # up occasionally. Retrying often succeeds, so
                                # wait 5 secs and retry.
                                # If all retries fail, we'll try again in a
                                # minute because we set
                                # carApiLastStartOrStopChargeTime = now earlier.
                                time.sleep(5)
                                continue
                            else:
                                # Start or stop charge failed with an error I
                                # haven't seen before, so wait
                                # carApiErrorRetryMins mins before trying again.
                                print(time_now() + ': ERROR "' + reason + '" when trying to ' +
                                      startOrStop + ' car charging via Tesla car API.  Will try again later.' +
                                      "\nIf this error persists, please private message user CDragon at http://teslamotorsclub.com " \
                                      "with a copy of this error.")
                                result = 'error'
                                vehicle.lastErrorTime = now

            except (KeyError, TypeError):
                # This catches cases like trying to access
                # apiResponseDict['response'] when 'response' doesn't exist in
                # apiResponseDict.
                print(time_now() + ': ERROR: Failed to ' + startOrStop
                      + ' car charging via Tesla car API.  Will try again later.')
                vehicle.lastErrorTime = now
            break

    if(config['config']['debugLevel'] >= 1 and carapi.getLastStartOrStopChargeTime() == now):
        print(time_now() + ': Car API ' + startOrStop + ' charge result: ' + result)

    return result


def queue_background_task(task):
    global backgroundTasksQueue, backgroundTasksCmds
    if(task['cmd'] in backgroundTasksCmds):
        # Some tasks, like cmd='charge', will be called once per second until
        # a charge starts or we determine the car is done charging.  To avoid
        # wasting memory queing up a bunch of these tasks when we're handling
        # a charge cmd already, don't queue two of the same task.
        return

    # Insert task['cmd'] in backgroundTasksCmds to prevent queuing another
    # task['cmd'] till we've finished handling this one.
    backgroundTasksCmds[task['cmd']] = True

    # Queue the task to be handled by background_tasks_thread.
    backgroundTasksQueue.put(task)


def background_tasks_thread():
    global backgroundTasksQueue, backgroundTasksCmds, carApiLastErrorTime

    while True:
        task = backgroundTasksQueue.get()

        if(task['cmd'] == 'charge'):
            # car_api_charge does nothing if it's been under 60 secs since it
            # was last used so we shouldn't have to worry about calling this
            # too frequently.
            car_api_charge(task['charge'])
        elif(task['cmd'] == 'carApiEmailPassword'):
            carApiLastErrorTime = 0
            car_api_available(task['email'], task['password'])
        elif(task['cmd'] == 'checkGreenEnergy'):
            check_green_energy()

        # Delete task['cmd'] from backgroundTasksCmds such that
        # queue_background_task() can queue another task['cmd'] in the future.
        del backgroundTasksCmds[task['cmd']]

        # task_done() must be called to let the queue know the task is finished.
        # backgroundTasksQueue.join() can then be used to block until all tasks
        # in the queue are done.
        backgroundTasksQueue.task_done()

def check_green_energy():
    global maxAmpsToDivideAmongSlaves, config, hass, backgroundTasksLock

    # Check solar panel generation using an API exposed by
    # the HomeAssistant API.
    #
    # You may need to customize the sensor entity_id values
    # to match those used in your environment. This is configured
    # in the config section at the top of this file.
    #
    master.setConsumption('Manual', (config['config']['greenEnergyAmpsOffset'] * 240))
    master.setConsumption('Fronius', fronius.getConsumption())
    master.setGeneration('Fronius', fronius.getGeneration())
    master.setConsumption('HomeAssistant', hass.getConsumption())
    master.setGeneration('HomeAssistant', hass.getGeneration())

    # Use backgroundTasksLock to prevent changing maxAmpsToDivideAmongSlaves
    # if the main thread is in the middle of examining and later using
    # that value.
    backgroundTasksLock.acquire()
    maxAmpsToDivideAmongSlaves = master.getMaxAmpsToDivideAmongSlaves()
    
    if(config['config']['debugLevel'] >= 1):
        print("%s: Solar generating %dW, Consumption %dW, Charger Load %dW" % (time_now(), master.getGeneration(), master.getConsumption(), master.getChargerLoad()))
        print("          Limiting car charging to %.2fA - %.2fA = %.2fA." % ((master.getGeneration() / 240), (master.getConsumption() / 240), maxAmpsToDivideAmongSlaves))
        print("          Charge when above %.0fA (minAmpsPerTWC)." % (config['config']['minAmpsPerTWC']))
        
    backgroundTasksLock.release()

    # Update HASS sensors with min/max amp values
    hassstatus.setStatus(bytes("config", 'UTF-8'), "min_amps_per_twc", config['config']['minAmpsPerTWC'])
    mqttstatus.setStatus(bytes("config", 'UTF-8'), "minAmpsPerTWC", config['config']['minAmpsPerTWC'])
    hassstatus.setStatus(bytes("all", 'UTF-8'), "max_amps_for_slaves", maxAmpsToDivideAmongSlaves)
    mqttstatus.setStatus(bytes("all", 'UTF-8'), "maxAmpsForSlaves", maxAmpsToDivideAmongSlaves)

#
# End functions
#
##############################

class TWCMaster:

  consumptionValues   = {}
  generationValues    = {}
  subtractChargerLoad = False
  totalAmpsInUse      = 0
  TWCID               = None

  def __init__(self, TWCID, config):
    self.TWCID = TWCID
    self.subtractChargerLoad = config['config']['subtractChargerLoad']

  def getChargerLoad(self):
    # Calculate in watts the load that the charger is generating so
    # that we can exclude it from the consumption if necessary
    return (self.getTotalAmpsInUse() * 240)

  def getConsumption(self):
    consumptionVal = 0

    for key in self.consumptionValues:
      consumptionVal += float(self.consumptionValues[key])

    if (consumptionVal < 0):
      consumptionVal = 0

    return float(consumptionVal)

  def getGeneration(self):
    generationVal = 0

    # Currently, our only logic is to add all of the values together
    for key in self.generationValues:
      generationVal += float(self.generationValues[key])

    if (generationVal < 0):
      generationVal = 0

    return float(generationVal)

  def getGenerationOffset(self):
    # Returns the number of watts to subtract from the solar generation stats
    # This is consumption + charger load if subtractChargerLoad is enabled
    # Or simply consumption if subtractChargerLoad is disabled
    generationOffset = self.getConsumption()
    if (self.subtractChargerLoad):
      generationOffset -= self.getChargerLoad()
    if (generationOffset < 0):
      generationOffset = 0
    return float(generationOffset)

  def getMaxAmpsToDivideAmongSlaves(self):
    # Watts = Volts * Amps
    # Car charges at 240 volts in North America so we figure
    # out how many amps * 240 = solarW and limit the car to
    # that many amps.

    # Calculate our current generation and consumption in watts
    solarW = float(self.getGeneration() - self.getGenerationOffset())

    # Generation may be below zero if consumption is greater than generation
    if solarW < 0:
        solarW = 0

    # Watts = Volts * Amps
    # Car charges at 240 volts in North America so we figure
    # out how many amps * 240 = solarW and limit the car to
    # that many amps.
    maxAmpsToDivideAmongSlaves = (solarW / 240)
    return maxAmpsToDivideAmongSlaves

  def getTotalAmpsInUse(self):
    # Returns the number of amps currently in use by all TWCs
    return self.totalAmpsInUse

  def setConsumption(self, source, value):
    # Accepts consumption values from one or more data sources
    # For now, this gives a sum value of all, but in future we could
    # average across sources perhaps, or do a primary/secondary priority
    self.consumptionValues[source] = value

  def setGeneration(self, source, value):
    self.generationValues[source] = value

  def setTotalAmpsInUse(self, amps):
    self.totalAmpsInUse = amps

##############################
#
# Begin global vars
#

data = ''
dataLen = 0
ignoredData = bytearray()
msg = bytearray()
msgLen = 0
lastTWCResponseMsg = None
overrideMasterHeartbeatData = b''

masterTWCID = ''
slaveHeartbeatData = bytearray([0x01,0x0F,0xA0,0x0F,0xA0,0x00,0x00,0x00,0x00])
numInitMsgsToSend = 10
msgRxCount = 0
timeLastTx = 0

slaveTWCs = {}
slaveTWCRoundRobin = []
idxSlaveToSendNextHeartbeat = 0

maxAmpsToDivideAmongSlaves = 0
scheduledAmpsMax = -1
scheduledAmpsStartHour = -1
scheduledAmpsEndHour = -1
scheduledAmpsDaysBitmap = 0x7F

chargeNowAmps = 0
chargeNowTimeEnd = 0

spikeAmpsToCancel6ALimit = 16
timeLastGreenEnergyCheck = 0
hourResumeTrackGreenEnergy = -1
kWhDelivered = 119
timeLastkWhDelivered = time.time()
timeLastkWhSaved = time.time()

# __FILE__ contains the path to the running script. Replace the script name with
# TWCManagerSettings.txt. This gives us a path that will always locate
# TWCManagerSettings.txt in the same directory as the script even when pwd does
# not match the script directory.
nonScheduledAmpsMax = -1
timeLastHeartbeatDebugOutput = 0

webMsgPacked = ''
webMsgMaxSize = 300
webMsgResult = 0

timeTo0Aafter06 = 0
timeToRaise2A = 0

homeLat = 10000
homeLon = 10000

backgroundTasksQueue = queue.Queue()
backgroundTasksCmds = {}
backgroundTasksLock = threading.Lock()

ser = None
ser = serial.Serial(config['config']['rs485adapter'], config['config']['baud'], timeout=0)

#
# End global vars
#
##############################


##############################
#
# Begin main program
#

load_settings()


# Create a background thread to handle tasks that take too long on the main
# thread.  For a primer on threads in Python, see:
# http://www.laurentluce.com/posts/python-threads-synchronization-locks-rlocks-semaphores-conditions-events-and-queues/
backgroundTasksThread = threading.Thread(target=background_tasks_thread, args = ())
backgroundTasksThread.daemon = True
backgroundTasksThread.start()


# Create an IPC (Interprocess Communication) message queue that we can
# periodically check to respond to queries from the TWCManager web interface.
#
# These messages will contain commands like "start charging at 10A" or may ask
# for information like "how many amps is the solar array putting out".
#
# The message queue is identified by a numeric key. This script and the web
# interface must both use the same key. The "ftok" function facilitates creating
# such a key based on a shared piece of information that is not likely to
# conflict with keys chosen by any other process in the system.
#
# ftok reads the inode number of the file or directory pointed to by its first
# parameter. This file or dir must already exist and the permissions on it don't
# seem to matter. The inode of a particular file or dir is fairly unique but
# doesn't change often so it makes a decent choice for a key.  We use the parent
# directory of the TWCManager script.
#
# The second parameter to ftok is a single byte that adds some additional
# uniqueness and lets you create multiple queues linked to the file or dir in
# the first param. We use 'T' for Tesla.
#
# If you can't get this to work, you can also set key = <some arbitrary number>
# and in the web interface, use the same arbitrary number. While that could
# conflict with another process, it's very unlikely to.
webIPCkey = sysv_ipc.ftok(re.sub('/[^/]+$', '/', __file__), ord('T'), True)

# Use the key to create a message queue with read/write access for all users.
webIPCqueue = sysv_ipc.MessageQueue(webIPCkey, sysv_ipc.IPC_CREAT, 0o666)
if(webIPCqueue == None):
    print("ERROR: Can't create Interprocess Communication message queue to communicate with web interface.")

# After the IPC message queue is created, if you type 'sudo ipcs -q' on the
# command like, you should see something like:
# ------ Message Queues --------
# key        msqid      owner      perms      used-bytes   messages
# 0x5402ed16 491520     pi         666        0            0
#
# Notice that we've created the only IPC message queue in the system. Apparently
# default software on the pi doesn't use IPC or if it does, it creates and
# deletes its message queues quickly.
#
# If you want to get rid of all queues because you created extras accidentally,
# reboot or type 'sudo ipcrm -a msg'.  Don't get rid of all queues if you see
# ones you didn't create or you may crash another process.
# Find more details in IPC here:
# http://www.onlamp.com/pub/a/php/2004/05/13/shared_memory.html


print("TWC Manager starting as fake %s with id %02X%02X and sign %02X" \
    % ( ("Master" if config['config']['fakeMaster'] else "Slave"), \
    ord(fakeTWCID[0:1]), ord(fakeTWCID[1:2]), ord(slaveSign)))

# Instantiate necessary classes
master = TWCMaster(fakeTWCID, config)
carapi = CarApi(config)
fronius = Fronius(config['config']['debugLevel'], config['sources']['Fronius'])
hass = HASS(config['config']['debugLevel'], config['sources']['HASS'])
hassstatus = HASSStatus(config['config']['debugLevel'],config['status']['HASS'])
mqttstatus = MQTTStatus(config['config']['debugLevel'],config['status']['MQTT'])

while True:
    try:
        # In this area, we always send a linkready message when we first start.
        # Whenever there is no data available from other TWCs to respond to,
        # we'll loop back to this point to send another linkready or heartbeat
        # message. By only sending our periodic messages when no incoming
        # message data is available, we reduce the chance that we will start
        # transmitting a message in the middle of an incoming message, which
        # would corrupt both messages.

        # Add a 25ms sleep to prevent pegging pi's CPU at 100%. Lower CPU means
        # less power used and less waste heat.
        time.sleep(0.025)

        now = time.time()

        if(config['config']['fakeMaster'] == 1):
            # A real master sends 5 copies of linkready1 and linkready2 whenever
            # it starts up, which we do here.
            # It doesn't seem to matter if we send these once per second or once
            # per 100ms so I do once per 100ms to get them over with.
            if(numInitMsgsToSend > 5):
                send_master_linkready1()
                time.sleep(0.1) # give slave time to respond
                numInitMsgsToSend -= 1
            elif(numInitMsgsToSend > 0):
                send_master_linkready2()
                time.sleep(0.1) # give slave time to respond
                numInitMsgsToSend = numInitMsgsToSend - 1
            else:
                # After finishing the 5 startup linkready1 and linkready2
                # messages, master will send a heartbeat message to every slave
                # it's received a linkready message from. Do that here.
                # A real master would keep sending linkready messages periodically
                # as long as no slave was connected, but since real slaves send
                # linkready once every 10 seconds till they're connected to a
                # master, we'll just wait for that.
                if(time.time() - timeLastTx >= 1.0):
                    # It's been about a second since our last heartbeat.
                    if(len(slaveTWCRoundRobin) > 0):
                        slaveTWC = slaveTWCRoundRobin[idxSlaveToSendNextHeartbeat]
                        if(time.time() - slaveTWC.timeLastRx > 26):
                            # A real master stops sending heartbeats to a slave
                            # that hasn't responded for ~26 seconds. It may
                            # still send the slave a heartbeat every once in
                            # awhile but we're just going to scratch the slave
                            # from our little black book and add them again if
                            # they ever send us a linkready.
                            print(time_now() + ": WARNING: We haven't heard from slave " \
                                "%02X%02X for over 26 seconds.  " \
                                "Stop sending them heartbeat messages." % \
                                (slaveTWC.TWCID[0], slaveTWC.TWCID[1]))
                            delete_slave(slaveTWC.TWCID)
                        else:
                            slaveTWC.send_master_heartbeat()

                        idxSlaveToSendNextHeartbeat = idxSlaveToSendNextHeartbeat + 1
                        if(idxSlaveToSendNextHeartbeat >= len(slaveTWCRoundRobin)):
                            idxSlaveToSendNextHeartbeat = 0
                        time.sleep(0.1) # give slave time to respond
        else:
            # As long as a slave is running, it sends link ready messages every
            # 10 seconds. They trigger any master on the network to handshake
            # with the slave and the master then sends a status update from the
            # slave every 1-3 seconds. Master's status updates trigger the slave
            # to send back its own status update.
            # As long as master has sent a status update within the last 10
            # seconds, slaves don't send link ready.
            # I've also verified that masters don't care if we stop sending link
            # ready as long as we send status updates in response to master's
            # status updates.
            if(config['config']['fakeMaster'] != 2 and time.time() - timeLastTx >= 10.0):
                if(config['config']['debugLevel'] >= 1):
                    print("Advertise fake slave %02X%02X with sign %02X is " \
                          "ready to link once per 10 seconds as long as master " \
                          "hasn't sent a heartbeat in the last 10 seconds." % \
                        (ord(fakeTWCID[0:1]), ord(fakeTWCID[1:2]), ord(slaveSign)))
                send_slave_linkready()


        ########################################################################
        # See if there's any message from the web interface.
        # If the message is longer than msgMaxSize, MSG_NOERROR tells it to
        # return what it can of the message and discard the rest.
        # When no message is available, IPC_NOWAIT tells msgrcv to return
        # msgResult = 0 and $! = 42 with description 'No message of desired
        # type'.
        # If there is an actual error, webMsgResult will be -1.
        # On success, webMsgResult is the length of webMsgPacked.
        try:
            webMsgRaw = webIPCqueue.receive(False, 2)
            if(len(webMsgRaw[0]) > 0):
                webMsgType = webMsgRaw[1]
                unpacked = struct.unpack('=LH', webMsgRaw[0][0:6])
                webMsgTime = unpacked[0]
                webMsgID = unpacked[1]
                webMsg = webMsgRaw[0][6:len(webMsgRaw[0])]

                if(config['config']['debugLevel'] >= 1):
                    webMsgRedacted = webMsg

                    # Hide car password in web request to send password to Tesla
                    m = re.search(b'^(carApiEmailPassword=[^\n]+\n)', webMsg, re.MULTILINE)
                    if(m):
                        webMsgRedacted = m.group(1) + b'[HIDDEN]'
                    print(time_now() + ": Web query: '" + str(webMsgRedacted) + "', id " + str(webMsgID) +
                                       ", time " + str(webMsgTime) + ", type " + str(webMsgType))
                webResponseMsg = ''
                numPackets = 0
                if(webMsg == b'getStatus'):
                    needCarApiBearerToken = False
                    if(carapi.getCarApiBearerToken() == ''):
                        for i in range(0, len(slaveTWCRoundRobin)):
                            if(slaveTWCRoundRobin[i].protocolVersion == 2):
                                needCarApiBearerToken = True

                    webResponseMsg = (
                        "%.2f" % (maxAmpsToDivideAmongSlaves) +
                        '`' + "%.2f" % (config['config']['wiringMaxAmpsAllTWCs']) +
                        '`' + "%.2f" % (config['config']['minAmpsPerTWC']) +
                        '`' + "%.2f" % (chargeNowAmps) +
                        '`' + str(nonScheduledAmpsMax) +
                        '`' + str(scheduledAmpsMax) +
                        '`' + "%02d:%02d" % (int(scheduledAmpsStartHour),
                                             int((scheduledAmpsStartHour % 1) * 60)) +
                        '`' + "%02d:%02d" % (int(scheduledAmpsEndHour),
                                             int((scheduledAmpsEndHour % 1) * 60)) +
                        '`' + str(scheduledAmpsDaysBitmap) +
                        '`' + "%02d:%02d" % (int(hourResumeTrackGreenEnergy),
                                             int((hourResumeTrackGreenEnergy % 1) * 60)) +
                        # Send 1 if we need an email/password entered for car api, otherwise send 0
                        '`' + ('1' if needCarApiBearerToken else '0') +
                        '`' + str(len(slaveTWCRoundRobin))
                        )

                    for i in range(0, len(slaveTWCRoundRobin)):
                        webResponseMsg += (
                            '`' + "%02X%02X" % (slaveTWCRoundRobin[i].TWCID[0],
                                                              slaveTWCRoundRobin[i].TWCID[1]) +
                            '~' + str(slaveTWCRoundRobin[i].maxAmps) +
                            '~' + "%.2f" % (slaveTWCRoundRobin[i].reportedAmpsActual) +
                            '~' + str(slaveTWCRoundRobin[i].lastAmpsOffered) +
                            '~' + str(slaveTWCRoundRobin[i].reportedState)
                            )

                elif(webMsg[0:20] == b'setNonScheduledAmps='):
                    m = re.search(b'([-0-9]+)', webMsg[19:len(webMsg)])
                    if(m):
                        nonScheduledAmpsMax = int(m.group(1))

                        # Save nonScheduledAmpsMax to SD card so the setting
                        # isn't lost on power failure or script restart.
                        save_settings()
                elif(webMsg[0:17] == b'setScheduledAmps='):
                    m = re.search(b'([-0-9]+)\nstartTime=([-0-9]+):([0-9]+)\nendTime=([-0-9]+):([0-9]+)\ndays=([0-9]+)', \
                                  webMsg[17:len(webMsg)], re.MULTILINE)
                    if(m):
                        scheduledAmpsMax = int(m.group(1))
                        scheduledAmpsStartHour = int(m.group(2)) + (int(m.group(3)) / 60)
                        scheduledAmpsEndHour = int(m.group(4)) + (int(m.group(5)) / 60)
                        scheduledAmpsDaysBitmap = int(m.group(6))
                        save_settings()
                elif(webMsg[0:30] == b'setResumeTrackGreenEnergyTime='):
                    m = re.search(b'([-0-9]+):([0-9]+)', webMsg[30:len(webMsg)], re.MULTILINE)
                    if(m):
                        hourResumeTrackGreenEnergy = int(m.group(1)) + (int(m.group(2)) / 60)
                        save_settings()
                elif(webMsg[0:11] == b'sendTWCMsg='):
                    m = re.search(b'([0-9a-fA-F]+)', webMsg[11:len(webMsg)], re.MULTILINE)
                    if(m):
                        twcMsg = trim_pad(bytearray.fromhex(m.group(1).decode('ascii')),
                                          15 if len(slaveTWCRoundRobin) == 0 \
                                          or slaveTWCRoundRobin[0].protocolVersion == 2 else 13)
                        if((twcMsg[0:2] == b'\xFC\x19') or (twcMsg[0:2] == b'\xFC\x1A')):
                            print("\n*** ERROR: Web interface requested sending command:\n"
                                  + hex_str(twcMsg)
                                  + "\nwhich could permanently disable the TWC.  Aborting.\n")
                        elif((twcMsg[0:2] == b'\xFB\xE8')):
                            print("\n*** ERROR: Web interface requested sending command:\n"
                                  + hex_str(twcMsg)
                                  + "\nwhich could crash the TWC.  Aborting.\n")
                        else:
                            lastTWCResponseMsg = bytearray();
                            send_msg(twcMsg)
                elif(webMsg == b'getLastTWCMsgResponse'):
                    if(lastTWCResponseMsg != None and lastTWCResponseMsg != b''):
                        webResponseMsg = hex_str(lastTWCResponseMsg)
                    else:
                        webResponseMsg = 'None'
                elif(webMsg[0:20] == b'carApiEmailPassword='):
                    m = re.search(b'([^\n]+)\n([^\n]+)', webMsg[20:len(webMsg)], re.MULTILINE)
                    if(m):
                        queue_background_task({'cmd':'carApiEmailPassword',
                                                  'email':m.group(1).decode('ascii'),
                                                  'password':m.group(2).decode('ascii')})
                elif(webMsg[0:23] == b'setMasterHeartbeatData='):
                    m = re.search(b'([0-9a-fA-F]*)', webMsg[23:len(webMsg)], re.MULTILINE)
                    if(m):
                        if(len(m.group(1)) > 0):
                            overrideMasterHeartbeatData = trim_pad(bytearray.fromhex(m.group(1).decode('ascii')),
                                                                   9 if slaveTWCRoundRobin[0].protocolVersion == 2 else 7)
                        else:
                            overrideMasterHeartbeatData = b''
                elif(webMsg == b'chargeNow'):
                    chargeNowAmps = config['config']['wiringMaxAmpsAllTWCs']
                    chargeNowTimeEnd = now + 60*60*24
                elif(webMsg == b'chargeNowCancel'):
                    chargeNowAmps = 0
                    chargeNowTimeEnd = 0
                elif(webMsg == b'dumpState'):
                    # dumpState commands are used for debugging. They are called
                    # using a web page:
                    # http://(Pi address)/index.php?submit=1&dumpState=1
                    webResponseMsg = ('time=' + str(now) + ', fakeMaster='
                        + str(config['config']['fakeMaster']) + ', rs485Adapter=' + config['config']['rs485adapter']
                        + ', baud=' + str(config['config']['baud'])
                        + ', wiringMaxAmpsAllTWCs=' + str(config['config']['wiringMaxAmpsAllTWCs'])
                        + ', wiringMaxAmpsPerTWC=' + str(config['config']['wiringMaxAmpsPerTWC'])
                        + ', minAmpsPerTWC=' + str(config['config']['minAmpsPerTWC'])
                        + ', greenEnergyAmpsOffset=' + str(config['config']['greenEnergyAmpsOffset'])
                        + ', debugLevel=' + str(config['config']['debugLevel'])
                        + '\n')
                    webResponseMsg += (
                        'carApiStopAskingToStartCharging=' + str(carApiStopAskingToStartCharging)
                        + '\ncarApiLastStartOrStopChargeTime=' + str(time.strftime("%m-%d-%y %H:%M:%S", time.localtime(carapi.getLastStartOrStopChargeTime())))
                        + '\ncarApiLastErrorTime=' + str(time.strftime("%m-%d-%y %H:%M:%S", time.localtime(carApiLastErrorTime)))
                        + '\ncarApiTokenExpireTime=' + str(time.strftime("%m-%d-%y %H:%M:%S", time.localtime(carApiTokenExpireTime)))
                        + '\n'
                        )

                    for vehicle in carapi.getCarApiVehicles():
                        webResponseMsg += str(vehicle.__dict__) + '\n'

                    webResponseMsg += 'slaveTWCRoundRobin:\n'
                    for slaveTWC in slaveTWCRoundRobin:
                        webResponseMsg += str(slaveTWC.__dict__) + '\n'

                    numPackets = math.ceil(len(webResponseMsg) / 290)
                elif(webMsg[0:14] == b'setDebugLevel='):
                    m = re.search(b'([-0-9]+)', webMsg[14:len(webMsg)], re.MULTILINE)
                    if(m):
                        config['config']['debugLevel'] = int(m.group(1))
                else:
                    print(time_now() + ": Unknown IPC request from web server: " + str(webMsg))

                if(len(webResponseMsg) > 0):
                    if(config['config']['debugLevel'] >= 5):
                        print(time_now() + ": Web query response: '" + webResponseMsg + "'")

                    try:
                        if(numPackets == 0):
                            if(len(webResponseMsg) > 290):
                                webResponseMsg = webResponseMsg[0:290]

                            webIPCqueue.send(struct.pack('=LH' + str(len(webResponseMsg)) + 's', webMsgTime, webMsgID,
                                   webResponseMsg.encode('ascii')), block=False)
                        else:
                            # In this case, block=False prevents blocking if the message
                            # queue is too full for our message to fit. Instead, an
                            # error is returned.
                            msgTemp = struct.pack('=LH1s', webMsgTime, webMsgID, bytearray([numPackets]))
                            webIPCqueue.send(msgTemp, block=False)
                            for i in range(0, numPackets):
                                packet = webResponseMsg[i*290:i*290+290]
                                webIPCqueue.send(struct.pack('=LH' + str(len(packet)) + 's', webMsgTime, webMsgID,
                                   packet.encode('ascii')), block=False)

                    except sysv_ipc.BusyError:
                        print(time_now() + ": Error: IPC queue full when trying to send response to web interface.")

        except sysv_ipc.BusyError:
            # No web message is waiting.
            pass

        ########################################################################
        # See if there's an incoming message on the RS485 interface.

        timeMsgRxStart = time.time()
        while True:
            now = time.time()
            dataLen = ser.inWaiting()
            if(dataLen == 0):
                if(msgLen == 0):
                    # No message data waiting and we haven't received the
                    # start of a new message yet. Break out of inner while
                    # to continue at top of outer while loop where we may
                    # decide to send a periodic message.
                    break
                else:
                    # No message data waiting but we've received a partial
                    # message that we should wait to finish receiving.
                    if(now - timeMsgRxStart >= 2.0):
                        if(config['config']['debugLevel'] >= 9):
                            print(time_now() + ": Msg timeout (" + hex_str(ignoredData) +
                                  ') ' + hex_str(msg[0:msgLen]))
                        msgLen = 0
                        ignoredData = bytearray()
                        break

                    time.sleep(0.025)
                    continue
            else:
                dataLen = 1
                data = ser.read(dataLen)

            if(dataLen != 1):
                # This should never happen
                print("WARNING: No data available.")
                break

            timeMsgRxStart = now
            timeLastRx = now
            if(msgLen == 0 and data[0] != 0xc0):
                # We expect to find these non-c0 bytes between messages, so
                # we don't print any warning at standard debug levels.
                if(config['config']['debugLevel'] >= 11):
                    print("Ignoring byte %02X between messages." % (data[0]))
                ignoredData += data
                continue
            elif(msgLen > 0 and msgLen < 15 and data[0] == 0xc0):
                # If you see this when the program is first started, it
                # means we started listening in the middle of the TWC
                # sending a message so we didn't see the whole message and
                # must discard it. That's unavoidable.
                # If you see this any other time, it means there was some
                # corruption in what we received. It's normal for that to
                # happen every once in awhile but there may be a problem
                # such as incorrect termination or bias resistors on the
                # rs485 wiring if you see it frequently.
                if(config['config']['debugLevel'] >= 10):
                    print("Found end of message before full-length message received.  " \
                          "Discard and wait for new message.")

                msg = data
                msgLen = 1
                continue

            if(msgLen == 0):
                msg = bytearray()
            msg += data
            msgLen += 1

            # Messages are usually 17 bytes or longer and end with \xc0\xfe.
            # However, when the network lacks termination and bias
            # resistors, the last byte (\xfe) may be corrupted or even
            # missing, and you may receive additional garbage bytes between
            # messages.
            #
            # TWCs seem to account for corruption at the end and between
            # messages by simply ignoring anything after the final \xc0 in a
            # message, so we use the same tactic. If c0 happens to be within
            # the corrupt noise between messages, we ignore it by starting a
            # new message whenever we see a c0 before 15 or more bytes are
            # received.
            #
            # Uncorrupted messages can be over 17 bytes long when special
            # values are "escaped" as two bytes. See notes in send_msg.
            #
            # To prevent most noise between messages, add a 120ohm
            # "termination" resistor in parallel to the D+ and D- lines.
            # Also add a 680ohm "bias" resistor between the D+ line and +5V
            # and a second 680ohm "bias" resistor between the D- line and
            # ground. See here for more information:
            #   https://www.ni.com/support/serial/resinfo.htm
            #   http://www.ti.com/lit/an/slyt514/slyt514.pdf
            # This explains what happens without "termination" resistors:
            #   https://e2e.ti.com/blogs_/b/analogwire/archive/2016/07/28/rs-485-basics-when-termination-is-necessary-and-how-to-do-it-properly
            if(msgLen >= 16 and data[0] == 0xc0):
                break

        if(msgLen >= 16):
            msg = unescape_msg(msg, msgLen)
            # Set msgLen = 0 at start so we don't have to do it on errors below.
            # len($msg) now contains the unescaped message length.
            msgLen = 0

            msgRxCount += 1

            # When the sendTWCMsg web command is used to send a message to the
            # TWC, it sets lastTWCResponseMsg = b''.  When we see that here,
            # set lastTWCResponseMsg to any unusual message received in response
            # to the sent message.  Never set lastTWCResponseMsg to a commonly
            # repeated message like master or slave linkready, heartbeat, or
            # voltage/kWh report.
            if(lastTWCResponseMsg == b''
               and msg[0:2] != b'\xFB\xE0' and msg[0:2] != b'\xFD\xE0'
               and msg[0:2] != b'\xFC\xE1' and msg[0:2] != b'\xFB\xE2'
               and msg[0:2] != b'\xFD\xE2' and msg[0:2] != b'\xFB\xEB'
               and msg[0:2] != b'\xFD\xEB' and msg[0:2] != b'\xFD\xE0'
            ):
                lastTWCResponseMsg = msg

            if(config['config']['debugLevel'] >= 9):
                print("Rx@" + time_now() + ": (" + hex_str(ignoredData) + ') ' \
                      + hex_str(msg) + "")

            ignoredData = bytearray()

            # After unescaping special values and removing the leading and
            # trailing C0 bytes, the messages we know about are always 14 bytes
            # long in original TWCs, or 16 bytes in newer TWCs (protocolVersion
            # == 2).
            if(len(msg) != 14 and len(msg) != 16):
                print(time_now() + ": ERROR: Ignoring message of unexpected length %d: %s" % \
                       (len(msg), hex_str(msg)))
                continue

            checksumExpected = msg[len(msg) - 1]
            checksum = 0
            for i in range(1, len(msg) - 1):
                checksum += msg[i]

            if((checksum & 0xFF) != checksumExpected):
                print("ERROR: Checksum %X does not match %02X.  Ignoring message: %s" %
                    (checksum, checksumExpected, hex_str(msg)))
                continue

            if(config['config']['fakeMaster'] == 1):
                ############################
                # Pretend to be a master TWC

                foundMsgMatch = False
                # We end each regex message search below with \Z instead of $
                # because $ will match a newline at the end of the string or the
                # end of the string (even without the re.MULTILINE option), and
                # sometimes our strings do end with a newline character that is
                # actually the CRC byte with a value of 0A or 0D.
                msgMatch = re.search(b'^\xfd\xe2(..)(.)(..)\x00\x00\x00\x00\x00\x00.+\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle linkready message from slave.
                    #
                    # We expect to see one of these before we start sending our
                    # own heartbeat message to slave.
                    # Once we start sending our heartbeat to slave once per
                    # second, it should no longer send these linkready messages.
                    # If slave doesn't hear master's heartbeat for around 10
                    # seconds, it sends linkready once per 10 seconds and starts
                    # flashing its red LED 4 times with the top green light on.
                    # Red LED stops flashing if we start sending heartbeat
                    # again.
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    sign = msgMatch.group(2)
                    maxAmps = ((msgMatch.group(3)[0] << 8) + msgMatch.group(3)[1]) / 100

                    if(config['config']['debugLevel'] >= 1):
                        print(time_now() + ": %.2f amp slave TWC %02X%02X is ready to link.  Sign: %s" % \
                            (maxAmps, senderID[0], senderID[1],
                            hex_str(sign)))

                    if(maxAmps >= 80):
                        # U.S. chargers need a spike to 21A to cancel a 6A
                        # charging limit imposed in an Oct 2017 Tesla car
                        # firmware update. See notes where
                        # spikeAmpsToCancel6ALimit is used.
                        spikeAmpsToCancel6ALimit = 21
                    else:
                        # EU chargers need a spike to only 16A.  This value
                        # comes from a forum post and has not been directly
                        # tested.
                        spikeAmpsToCancel6ALimit = 16

                    if(senderID == fakeTWCID):
                        print(time_now + ": Slave TWC %02X%02X reports same TWCID as master.  " \
                              "Slave should resolve by changing its TWCID." % \
                              (senderID[0], senderID[1]))
                        # I tested sending a linkready to a real master with the
                        # same TWCID as master and instead of master sending back
                        # its heartbeat message, it sent 5 copies of its
                        # linkready1 and linkready2 messages. Those messages
                        # will prompt a real slave to pick a new random value
                        # for its TWCID.
                        #
                        # We mimic that behavior by setting numInitMsgsToSend =
                        # 10 to make the idle code at the top of the for()
                        # loop send 5 copies of linkready1 and linkready2.
                        numInitMsgsToSend = 10
                        continue

                    # We should always get this linkready message at least once
                    # and generally no more than once, so this is a good
                    # opportunity to add the slave to our known pool of slave
                    # devices.
                    slaveTWC = new_slave(senderID, maxAmps)

                    if(slaveTWC.protocolVersion == 1 and slaveTWC.minAmpsTWCSupports == 6):
                        if(len(msg) == 14):
                            slaveTWC.protocolVersion = 1
                            slaveTWC.minAmpsTWCSupports = 5
                        elif(len(msg) == 16):
                            slaveTWC.protocolVersion = 2
                            slaveTWC.minAmpsTWCSupports = 6

                        if(config['config']['debugLevel'] >= 1):
                            print(time_now() + ": Set slave TWC %02X%02X protocolVersion to %d, minAmpsTWCSupports to %d." % \
                                 (senderID[0], senderID[1], slaveTWC.protocolVersion, slaveTWC.minAmpsTWCSupports))

                    # We expect maxAmps to be 80 on U.S. chargers and 32 on EU
                    # chargers. Either way, don't allow
                    # slaveTWC.wiringMaxAmps to be greater than maxAmps.
                    if(slaveTWC.wiringMaxAmps > maxAmps):
                        print("\n\n!!! DANGER DANGER !!!\nYou have set wiringMaxAmpsPerTWC to "
                              + str(config['config']['wiringMaxAmpsPerTWC'])
                              + " which is greater than the max "
                              + str(maxAmps) + " amps your charger says it can handle.  " \
                              "Please review instructions in the source code and consult an " \
                              "electrician if you don't know what to do.")
                        slaveTWC.wiringMaxAmps = maxAmps / 4

                    # Make sure we print one SHB message after a slave
                    # linkready message is received by clearing
                    # lastHeartbeatDebugOutput. This helps with debugging
                    # cases where I can't tell if we responded with a
                    # heartbeat or not.
                    slaveTWC.lastHeartbeatDebugOutput = ''

                    slaveTWC.timeLastRx = time.time()
                    slaveTWC.send_master_heartbeat()
                else:
                    msgMatch = re.search(b'\A\xfd\xe0(..)(..)(.......+?).\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle heartbeat message from slave.
                    #
                    # These messages come in as a direct response to each
                    # heartbeat message from master. Slave does not send its
                    # heartbeat until it gets one from master first.
                    # A real master sends heartbeat to a slave around once per
                    # second, so we do the same near the top of this for()
                    # loop. Thus, we should receive a heartbeat reply from the
                    # slave around once per second as well.
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    receiverID = msgMatch.group(2)
                    heartbeatData = msgMatch.group(3)

                    try:
                        slaveTWC = slaveTWCs[senderID]
                    except KeyError:
                        # Normally, a slave only sends us a heartbeat message if
                        # we send them ours first, so it's not expected we would
                        # hear heartbeat from a slave that's not in our list.
                        print(time_now() + ": ERROR: Received heartbeat message from " \
                                "slave %02X%02X that we've not met before." % \
                                (senderID[0], senderID[1]))
                        continue

                    if(fakeTWCID == receiverID):
                        slaveTWC.receive_slave_heartbeat(heartbeatData)
                    else:
                        # I've tried different fakeTWCID values to verify a
                        # slave will send our fakeTWCID back to us as
                        # receiverID. However, I once saw it send receiverID =
                        # 0000.
                        # I'm not sure why it sent 0000 and it only happened
                        # once so far, so it could have been corruption in the
                        # data or an unusual case.
                        if(config['config']['debugLevel'] >= 1):
                            print(time_now() + ": WARNING: Slave TWC %02X%02X status data: " \
                                  "%s sent to unknown TWC %02X%02X." % \
                                (senderID[0], senderID[1],
                                hex_str(heartbeatData), receiverID[0], receiverID[1]))
                else:
                    msgMatch = re.search(b'\A\xfd\xeb(..)(..)(.+?).\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle kWh total and voltage message from slave.
                    #
                    # This message can only be generated by TWCs running newer
                    # firmware.  I believe it's only sent as a response to a
                    # message from Master in this format:
                    #   FB EB <Master TWCID> <Slave TWCID> 00 00 00 00 00 00 00 00 00
                    # Since we never send such a message, I don't expect a slave
                    # to ever send this message to us, but we handle it just in
                    # case.
                    # According to FuzzyLogic, this message has the following
                    # format on an EU (3-phase) TWC:
                    #   FD EB <Slave TWCID> 00000038 00E6 00F1 00E8 00
                    #   00000038 (56) is the total kWh delivered to cars
                    #     by this TWC since its construction.
                    #   00E6 (230) is voltage on phase A
                    #   00F1 (241) is voltage on phase B
                    #   00E8 (232) is voltage on phase C
                    #
                    # I'm guessing in world regions with two-phase power that
                    # this message would be four bytes shorter, but the pattern
                    # above will match a message of any length that starts with
                    # FD EB.
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    receiverID = msgMatch.group(2)
                    data = msgMatch.group(3)

                    if(config['config']['debugLevel'] >= 1):
                        print(time_now() + ": Slave TWC %02X%02X unexpectedly reported kWh and voltage data: %s." % \
                            (senderID[0], senderID[1],
                            hex_str(data)))
                        
                else:
                    msgMatch = re.search(b'\A\xfd\xee(..)(.+?).\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                        # Get last 7 characters of VIN from slave. (usually only 3 digits)
                        #
                        # This message can only be generated by TWCs running newer
                        # firmware.  I believe it's only sent as a response to a
                        # message from Master in this format:
                        #   FB EE <Master TWCID> <Slave TWCID> 00 00 00 00 00 00 00 00 00

                        # Response message is FD EE <Slave TWCID> VV VV VV VV VV VV VV where VV is an ascii character code 
                        # representing a letter or number. VV will be all zero when car CAN communication is disabled 
                        # (DIP switch 2 down) or when a non-Tesla vehicle is plugged in using something like a JDapter.

                        foundMsgMatch = True
                        senderID = msgMatch.group(1)
                        data = msgMatch.group(2)

                        if(config['config']['debugLevel'] >= 1):
                            print(time_now() + ": Slave TWC %02X%02X reported VIN data: %s." % \
                                (senderID[0], senderID[1], hex_str(data)))

                else:
                    msgMatch = re.search(b'\A\xfc(\xe1|\xe2)(..)(.)\x00\x00\x00\x00\x00\x00\x00\x00.+\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    foundMsgMatch = True
                    print(time_now() + " ERROR: TWC is set to Master mode so it can't be controlled by TWCManager.  " \
                           "Search installation instruction PDF for 'rotary switch' and set " \
                           "switch so its arrow points to F on the dial.")
                if(foundMsgMatch == False):
                    print(time_now() + ": *** UNKNOWN MESSAGE FROM SLAVE:" + hex_str(msg)
                          + "\nPlease private message user CDragon at http://teslamotorsclub.com " \
                          "with a copy of this error.")
            else:
                ###########################
                # Pretend to be a slave TWC

                foundMsgMatch = False
                msgMatch = re.search(b'\A\xfc\xe1(..)(.)\x00\x00\x00\x00\x00\x00\x00\x00+?.\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle linkready1 from master.
                    # See notes in send_master_linkready1() for details.
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    sign = msgMatch.group(2)

                    masterTWCID = senderID

                    # This message seems to always contain seven 00 bytes in its
                    # data area. If we ever get this message with non-00 data
                    # we'll print it as an unexpected message.
                    if(config['config']['debugLevel'] >= 1):
                        print(time_now() + ": Master TWC %02X%02X Linkready1.  Sign: %s" % \
                            (senderID[0], senderID[1], hex_str(sign)))

                    if(senderID == fakeTWCID):
                        master_id_conflict()

                    # Other than picking a new fakeTWCID if ours conflicts with
                    # master, it doesn't seem that a real slave will make any
                    # sort of direct response when sent a master's linkready1 or
                    # linkready2.

                else:
                    msgMatch = re.search(b'\A\xfb\xe2(..)(.)\x00\x00\x00\x00\x00\x00\x00\x00+?.\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle linkready2 from master.
                    # See notes in send_master_linkready2() for details.
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    sign = msgMatch.group(2)

                    masterTWCID = senderID

                    # This message seems to always contain seven 00 bytes in its
                    # data area. If we ever get this message with non-00 data
                    # we'll print it as an unexpected message.

                    if(config['config']['debugLevel'] >= 1):
                        print(time_now() + ": Master TWC %02X%02X Linkready2.  Sign: %s" % \
                            (senderID[0], senderID[1], hex_str(sign)))

                    if(senderID == fakeTWCID):
                        master_id_conflict()
                else:
                    msgMatch = re.search(b'\A\xfb\xe0(..)(..)(.......+?).\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle heartbeat message from Master.
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    receiverID = msgMatch.group(2)
                    heartbeatData = msgMatch.group(3)

                    masterTWCID = senderID
                    try:
                        slaveTWC = slaveTWCs[receiverID]
                    except KeyError:
                        slaveTWC = new_slave(receiverID, 80)

                    slaveTWC.masterHeartbeatData = heartbeatData

                    if(receiverID != fakeTWCID):
                        # This message was intended for another slave.
                        # Ignore it.
                        if(config['config']['debugLevel'] >= 11):
                            print(time_now() + ": Master %02X%02X sent " \
                                "heartbeat message %s to receiver %02X%02X " \
                                "that isn't our fake slave." % \
                                (senderID[0], senderID[1],
                                hex_str(heartbeatData),
                                receiverID[0], receiverID[1]))
                        continue

                    amps = (slaveHeartbeatData[1] << 8) + slaveHeartbeatData[2]
                    kWhDelivered += (((240 * (amps/100)) / 1000 / 60 / 60) * (now - timeLastkWhDelivered))
                    timeLastkWhDelivered = now
                    if(time.time() - timeLastkWhSaved >= 300.0):
                        timeLastkWhSaved = now
                        if(config['config']['debugLevel'] >= 9):
                            print(time_now() + ": Fake slave has delivered %.3fkWh" % \
                               (kWhDelivered))
                        save_settings()

                    if(heartbeatData[0] == 0x07):
                        # Lower amps in use (not amps allowed) by 2 for 10
                        # seconds. Set state to 07.
                        slaveHeartbeatData[0] = heartbeatData[0]
                        timeToRaise2A = now + 10
                        amps -= 280
                        slaveHeartbeatData[3] = ((amps >> 8) & 0xFF)
                        slaveHeartbeatData[4] = (amps & 0xFF)
                    elif(heartbeatData[0] == 0x06):
                        # Raise amp setpoint by 2 permanently and reply with
                        # state 06.  After 44 seconds, report state 0A.
                        timeTo0Aafter06 = now + 44
                        slaveHeartbeatData[0] = heartbeatData[0]
                        amps += 200
                        slaveHeartbeatData[1] = ((amps >> 8) & 0xFF)
                        slaveHeartbeatData[2] = (amps & 0xFF)
                        amps -= 80
                        slaveHeartbeatData[3] = ((amps >> 8) & 0xFF)
                        slaveHeartbeatData[4] = (amps & 0xFF)
                    elif(heartbeatData[0] == 0x05 or heartbeatData[0] == 0x08 or heartbeatData[0] == 0x09):
                        if(((heartbeatData[1] << 8) + heartbeatData[2]) > 0):
                            # A real slave mimics master's status bytes [1]-[2]
                            # representing max charger power even if the master
                            # sends it a crazy value.
                            slaveHeartbeatData[1] = heartbeatData[1]
                            slaveHeartbeatData[2] = heartbeatData[2]

                            ampsUsed = (heartbeatData[1] << 8) + heartbeatData[2]
                            ampsUsed -= 80
                            slaveHeartbeatData[3] = ((ampsUsed >> 8) & 0xFF)
                            slaveHeartbeatData[4] = (ampsUsed & 0xFF)
                    elif(heartbeatData[0] == 0):
                        if(timeTo0Aafter06 > 0 and timeTo0Aafter06 < now):
                            timeTo0Aafter06 = 0
                            slaveHeartbeatData[0] = 0x0A
                        elif(timeToRaise2A > 0 and timeToRaise2A < now):
                            # Real slave raises amps used by 2 exactly 10
                            # seconds after being sent into state 07. It raises
                            # a bit slowly and sets its state to 0A 13 seconds
                            # after state 07. We aren't exactly emulating that
                            # timing here but hopefully close enough.
                            timeToRaise2A = 0
                            amps -= 80
                            slaveHeartbeatData[3] = ((amps >> 8) & 0xFF)
                            slaveHeartbeatData[4] = (amps & 0xFF)
                            slaveHeartbeatData[0] = 0x0A
                    elif(heartbeatData[0] == 0x02):
                        print(time_now() + ": Master heartbeat contains error %ld: %s" % \
                                (heartbeatData[1], hex_str(heartbeatData)))
                    else:
                        print(time_now() + ": UNKNOWN MHB state %s" % \
                                (hex_str(heartbeatData)))

                    # Slaves always respond to master's heartbeat by sending
                    # theirs back.
                    slaveTWC.send_slave_heartbeat(senderID)
                    slaveTWC.print_status(slaveHeartbeatData)
                else:
                    msgMatch = re.search(b'\A\xfc\x1d\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00+?.\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle 2-hour idle message
                    #
                    # This message is sent from a Master TWC three times in a
                    # row every 2 hours:
                    #   c0 fc 1d 00 00 00 00 00 00 00 00 00 00 00 1d c0
                    #
                    # I'd say this is used to indicate the master is still
                    # alive, but it doesn't contain the Master's TWCID or any other
                    # data so I don't see what any receiving TWC can do with it.
                    #
                    # I suspect this message is only sent when the master
                    # doesn't see any other TWCs on the network, so I don't
                    # bother to have our fake master send these messages being
                    # as there's no point in playing a fake master with no
                    # slaves around.
                    foundMsgMatch = True
                    if(config['config']['debugLevel'] >= 1):
                        print(time_now() + ": Received 2-hour idle message from Master.")
                else:
                    msgMatch = re.search(b'\A\xfd\xe2(..)(.)(..)\x00\x00\x00\x00\x00\x00.+\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle linkready message from slave on network that
                    # presumably isn't us.
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    sign = msgMatch.group(2)
                    maxAmps = ((msgMatch.group(3)[0] << 8) + msgMatch.group(3)[1]) / 100
                    if(config['config']['debugLevel'] >= 1):
                        print(time_now() + ": %.2f amp slave TWC %02X%02X is ready to link.  Sign: %s" % \
                            (maxAmps, senderID[0], senderID[1],
                            hex_str(sign)))
                    if(senderID == fakeTWCID):
                        print(time_now() + ": ERROR: Received slave heartbeat message from " \
                                "slave %02X%02X that has the same TWCID as our fake slave." % \
                                (senderID[0], senderID[1]))
                        continue

                    new_slave(senderID, maxAmps)
                else:
                    msgMatch = re.search(b'\A\xfd\xe0(..)(..)(.......+?).\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle heartbeat message from slave on network that
                    # presumably isn't us.
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    receiverID = msgMatch.group(2)
                    heartbeatData = msgMatch.group(3)

                    if(senderID == fakeTWCID):
                        print(time_now() + ": ERROR: Received slave heartbeat message from " \
                                "slave %02X%02X that has the same TWCID as our fake slave." % \
                                (senderID[0], senderID[1]))
                        continue

                    try:
                        slaveTWC = slaveTWCs[senderID]
                    except KeyError:
                        # Slave is unlikely to send another linkready since it's
                        # already linked with a real Master TWC, so just assume
                        # it's 80A.
                        slaveTWC = new_slave(senderID, 80)

                    slaveTWC.print_status(heartbeatData)
                else:
                    msgMatch = re.search(b'\A\xfb\xeb(..)(..)(\x00\x00\x00\x00\x00\x00\x00\x00\x00+?).\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle voltage request message.  This is only supported in
                    # Protocol 2 so we always reply with a 16-byte message.
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    receiverID = msgMatch.group(2)

                    if(senderID == fakeTWCID):
                        print(time_now() + ": ERROR: Received voltage request message from " \
                                "TWC %02X%02X that has the same TWCID as our fake slave." % \
                                (senderID[0], senderID[1]))
                        continue

                    if(config['config']['debugLevel'] >= 8):
                        print(time_now() + ": VRQ from %02X%02X to %02X%02X" % \
                            (senderID[0], senderID[1], receiverID[0], receiverID[1]))

                    if(receiverID == fakeTWCID):
                        kWhCounter = int(kWhDelivered)
                        kWhPacked = bytearray([((kWhCounter >> 24) & 0xFF),
                                      ((kWhCounter >> 16) & 0xFF),
                                      ((kWhCounter >> 8) & 0xFF),
                                      (kWhCounter & 0xFF)])
                        print(time_now() + ": VRS %02X%02X: %dkWh (%s) %dV %dV %dV" % \
                            (fakeTWCID[0], fakeTWCID[1],
                            kWhCounter, hex_str(kWhPacked), 240, 0, 0))
                        send_msg(bytearray(b'\xFD\xEB') + fakeTWCID
                                 + kWhPacked
                                 + bytearray(b'\x00\xF0\x00\x00\x00\x00\x00'))
                else:
                    msgMatch = re.search(b'\A\xfd\xeb(..)(.........+?).\Z', msg, re.DOTALL)
                if(msgMatch and foundMsgMatch == False):
                    # Handle voltage response message.
                    # Example US value:
                    #   FD EB 7777 00000014 00F6 0000 0000 00
                    # EU value (3 phase power):
                    #   FD EB 7777 00000038 00E6 00F1 00E8 00
                    foundMsgMatch = True
                    senderID = msgMatch.group(1)
                    data = msgMatch.group(2)
                    kWhCounter = (data[0] << 24) + (data[1] << 16) + (data[2] << 8) + data[3]
                    voltsPhaseA = (data[4] << 8) + data[5]
                    voltsPhaseB = (data[6] << 8) + data[7]
                    voltsPhaseC = (data[8] << 8) + data[9]

                    if(senderID == fakeTWCID):
                        print(time_now() + ": ERROR: Received voltage response message from " \
                                "TWC %02X%02X that has the same TWCID as our fake slave." % \
                                (senderID[0], senderID[1]))
                        continue

                    if(config['config']['debugLevel'] >= 1):
                        print(time_now() + ": VRS %02X%02X: %dkWh %dV %dV %dV" % \
                            (senderID[0], senderID[1],
                            kWhCounter, voltsPhaseA, voltsPhaseB, voltsPhaseC))

                if(foundMsgMatch == False):
                    print(time_now() + ": ***UNKNOWN MESSAGE from master: " + hex_str(msg))

    except KeyboardInterrupt:
        print("Exiting after background tasks complete...")
        break

    except Exception as e:
        # Print info about unhandled exceptions, then continue.  Search for
        # 'Traceback' to find these in the log.
        traceback.print_exc()

        # Sleep 5 seconds so the user might see the error.
        time.sleep(5)


# Wait for background tasks thread to finish all tasks.
# Note that there is no such thing as backgroundTasksThread.stop(). Because we
# set the thread type to daemon, it will be automatically killed when we exit
# this program.
backgroundTasksQueue.join()

ser.close()

#
# End main program
#
##############################

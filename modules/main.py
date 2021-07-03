import datetime
import easygui as eg
import os
import struct
import subprocess
import sys
import threading
import time

from argparse import ArgumentParser
from common import Device
from handshake import handshake
from load_payload import load_payload
from logger import log

def check_modemmanager():
    pids = [pid for pid in os.listdir('/proc') if pid.isdigit()]

    for pid in pids:
        try:
            args = open(os.path.join('/proc', pid, 'cmdline'), 'rb').read().decode("utf-8").split('\0')
            if len(args) > 0 and "modemmanager" in args[0].lower():
                print("You need to temporarily disable/uninstall ModemManager before this script can proceed")
                sys.exit(1)
        except IOError:
            continue

def switch_boot0(dev, unbrick = False):
    dev.emmc_switch(1)
    block = dev.emmc_read(0)
    if not unbrick:
        if block[0:9] != b"EMMC_BOOT":
            dev.reboot()
            raise RuntimeError("what's wrong with your BOOT0?")
    dev.kick_watchdog()

def calculate_time_left(time_passed, done, left):
    time_left = int(((left - done - 1) * time_passed / (done + 1)).total_seconds())
    if time_left >= 604800:
        return str(round(time_left / 604800, 1))  + "w"
    if time_left >= 86400:
        return str(round(time_left / 86400, 1))  + "d"
    if time_left >= 3600:
        return str(round(time_left / 3600, 1))  + "h"
    if time_left >= 60:
        return str(round(time_left / 60, 1))  + "m"
    return str(time_left) + "s"

def flash_data(dev, data, start_block, max_size=0):
    while len(data) % 0x200 != 0:
        data += b"\x00"

    if max_size and len(data) > max_size:
        raise RuntimeError("data too big to flash")

    blocks = len(data) // 0x200
    start_time = datetime.datetime.now()
    for x in range(blocks):
        time_passed = datetime.datetime.now() - start_time
        print('\033[K', end='')
        print("[{} / {}, {}, time left = {}, time passed = {}]".format(x + 1, blocks, \
                                                                       str(int((x  + 1) / (blocks - 1) * 100)) + "%", \
                                                                       calculate_time_left(time_passed, x, blocks), \
                                                                       str(time_passed)[:-7]), end='\r')
        dev.emmc_write(start_block + x, data[x * 0x200:(x + 1) * 0x200])
        if x % 10 == 0:
            dev.kick_watchdog()
    print("")

def read_file(path):
    with open(path, "rb") as fin:
        data = fin.read()
    return data

def flash_binary(dev, path, start_block, max_size=0):
    flash_data(dev, read_file(path), start_block, max_size)

def dump_binary(dev, path, start_block, max_size=0):
    with open(path, "w+b") as fout:
        blocks = max_size // 0x200
        start_time = datetime.datetime.now()
        for x in range(blocks):
            time_passed = datetime.datetime.now() - start_time
            print('\033[K', end='')
            print("[{} / {}, {}, time left = {}, time passed = {}]".format(x + 1, blocks, \
                                                                           str(int((x  + 1) / (blocks - 1) * 100)) + "%", \
                                                                           calculate_time_left(time_passed, x, blocks), \
                                                                           str(time_passed)[:-7]), end='\r')
            fout.write(dev.emmc_read(start_block + x))
            if x % 10 == 0:
                dev.kick_watchdog()
    print("")

def switch_user(dev, partitiontable = False):
    dev.emmc_switch(0)
    block = dev.emmc_read(0)
    if not partitiontable:
        if block[510:512] != b"\x55\xAA":
            dev.reboot()
            raise RuntimeError("what's wrong with your GPT? try to flash partition table")
    dev.kick_watchdog()

def parse_gpt(dev):
    data = dev.emmc_read(0x400 // 0x200) + dev.emmc_read(0x600 // 0x200) \
         + dev.emmc_read(0x800 // 0x200) + dev.emmc_read(0xA00 // 0x200) \
         + dev.emmc_read(0xC00 // 0x200) + dev.emmc_read(0xE00 // 0x200)
    num = len(data) // 0x80
    parts = dict()
    for x in range(num):
        part = data[x * 0x80:(x + 1) * 0x80]
        part_name = part[0x38:].decode("utf-16le").rstrip("\x00")
        part_start = struct.unpack("<Q", part[0x20:0x28])[0]
        part_end = struct.unpack("<Q", part[0x28:0x30])[0]
        parts[part_name] = (part_start, part_end - part_start + 1)
    return parts

class UserInputThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.done = False

    def run(self):
        log("Hold volume down button and press enter")
        log("Don't release volume button until you see FASTBOOT mode... on phone screen")
        input()
        self.done = True

def main():
    parser = ArgumentParser()
    parser.add_argument("-b", "--backupemmc", action='store_true', dest='backupemmc', default=False,
                        help="backup selected emmc partitions to dumps folder")
    parser.add_argument("-d", "--dumpbootrom", action='store_true', dest='dumpbootrom', default=False,
                        help="dump device bootrom to dumps folder")
    parser.add_argument("-m", "--ignoremodem", action='store_true', dest='ignoremodem', default=False,
                        help="ignore ModemManager checking")
    parser.add_argument("-p", "--parttablebackup", action='store_true', dest='parttablebackup',
                        default=False, help="backup partition table to dumps folder")
    parser.add_argument("-r", "--restoreemmc", action='store_true', dest='restoreemmc', default=False,
                        help="restore selected emmc partitions from dumps folder")
    parser.add_argument("-s", "--parttablerestore", action='store_true', dest='parttablerestore',
                        default=False, help="flash partition table to emmc")
    parser.add_argument("-u", "--unbrick", action='store_true', dest='unbrick', default=False,
                        help="flash stock partitions to the device(flyme 6.2.2.0G, except flyme4 lk)")
    args = parser.parse_args()

    if sys.platform.startswith("linux") and not args.ignoremodem:
        check_modemmanager()

    dev = Device()
    dev.find_device()

    # Handshake
    handshake(dev, args.dumpbootrom)

    # Load brom payload
    load_payload(dev, "../brom-payload/build/payload.bin", args.dumpbootrom)
    dev.kick_watchdog()

    # Partition table
    if args.parttablebackup:
        log("Backuping up partition table")
        if not os.path.exists("../dumps"):
            os.mkdir("../dumps")
        switch_user(dev, args.parttablebackup)
        dump_binary(dev, "../dumps/gpt_part.bin", 0, 1024 * 0x200)

    # Partition table
    if args.parttablerestore:
        log("Restoring partition table")
        if not os.path.exists("../dumps"):
            raise RuntimeError("Can't find partition table backup")
        switch_user(dev, args.parttablerestore)
        flash_binary(dev, "../dumps/gpt_part.bin", 0, 1024 * 0x200)

    # Sanity check GPT
    log("Check GPT")
    switch_user(dev)

    # Parse gpt
    gpt = parse_gpt(dev)
    log("== GPT start ==")
    for partition_name, partition_parameters in gpt.items():
        log("{} {}".format(partition_name, partition_parameters))
    log("== GPT end ==")
    if "lk" not in gpt or "tee1" not in gpt or "boot" not in gpt or "recovery" not in gpt:
        raise RuntimeError("bad gpt")

    # Sanity check boot0
    log("Check boot0")
    switch_boot0(dev, args.unbrick)

    # Unbrick
    if args.unbrick:
        dev.kick_watchdog()
        for partition in eg.multchoicebox("What files do you want to flash?", "Unbrick",
                                          ["preloader", "lk", "tee", "logo", "boot", "recovery"]):
            log("Flashing " + partition)
            if partition != "preloader":
                switch_user(dev)
                if partition != "tee":
                    flash_binary(dev, "../bin/" + partition + ".img", gpt[partition][0],
                                 gpt[partition][1] * 0x200)
                else:
                    flash_binary(dev, "../bin/" + partition + ".img", gpt[partition + "1"][0],
                                 gpt[partition + "1"][1] * 0x200)
                    flash_binary(dev, "../bin/" + partition + ".img", gpt[partition + "2"][0],
                                 gpt[partition + "2"][1] * 0x200)
            else:
                switch_boot0(dev, args.unbrick)
                flash_binary(dev, "../bin/" + partition + ".img", 0)


    # Test R/W
    if False:
        switch_user(dev)
        dump_binary(dev, "../boot.img", gpt["boot"][0], gpt["boot"][1] * 0x200)
        flash_binary(dev, "../boot.img", gpt["boot"][0], gpt["boot"][1] * 0x200)
        print(dev.rpmb_read().hex())

    # Backup EMMC
    if args.backupemmc:
        if not os.path.exists("../dumps"):
            os.mkdir("../dumps")
        dev.kick_watchdog()
        partitionsToBackup = eg.multchoicebox("What partitons do you want to backup?", "Backup", gpt)
        log("Backuping up EMMC partitions")
        switch_user(dev)
        for partition in gpt:
            if partition in partitionsToBackup:
                dump_binary(dev, "../dumps/" + partition + ".img", gpt[partition][0],
                            gpt[partition][1] * 0x200)

    # Restore EMMC
    if args.restoreemmc:
        if not os.path.exists("../dumps"):
            raise RuntimeError("Can't find EMMC backup")
        backupedUpPartitions = []
        for r, d, f in os.walk("../dumps"):
            for file in f:
                if '.img' in file:
                    if os.path.splitext(file)[0] in gpt:
                        backupedUpPartitions.append(os.path.splitext(file)[0])
        dev.kick_watchdog()
        partitionsToRestore = eg.multchoicebox("What partitons do you want to restore?",
                                               "Restore", backupedUpPartitions)
        log("Restoring EMMC partitions")
        switch_user(dev)
        for partition in partitionsToRestore:
            flash_binary(dev, "../dumps/" + partition + ".img", gpt[partition][0],
                         gpt[partition][1] * 0x200)

    # Reboot
    log("Reboot")
    dev.reboot()

if __name__ == "__main__":
    main()

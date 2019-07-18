import datetime
import os
import struct
import subprocess
import sys
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
    print("")

def switch_user(dev, partitiontable = False):
    dev.emmc_switch(0)
    block = dev.emmc_read(0)
    if not partitiontable:
        if block[510:512] != b"\x55\xAA":
            dev.reboot()
            raise RuntimeError("what's wrong with your GPT? try to flash partition table")

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

def main():
    parser = ArgumentParser()
    parser.add_argument("-l", "--unlock", action='store_true', dest='unlock', default=False,
                        help="patch frp and flash flyme4 lk to allow bootloader unlock")
    parser.add_argument("-m", "--ignoremodem", action='store_true', dest='ignoremodem', default=False,
                        help="ignore ModemManager checking")
    parser.add_argument("-p", "--partitiontable", action='store_true', dest='partitiontable', default=False,
                        help="flash stock partition table to emmc")
    parser.add_argument("-t", "--testrw", action='store_true', dest='testrw', default=False,
                        help="dump, flash boot partition and read rpmb")
    parser.add_argument("-u", "--unbrick", action='store_true', dest='unbrick', default=False,
                        help="flash stock partitions to the device(flyme 6.2.2.0G, except flyme4 lk)")
    args = parser.parse_args()

    if sys.platform.startswith("linux") and not args.ignoremodem:
        check_modemmanager()

    dev = Device()
    dev.find_device()

    # Handshake
    handshake(dev)

    # Load brom payload
    load_payload(dev, "../brom-payload/build/payload.bin")

    # Partition table
    if args.partitiontable:
        log("Flashing partition table")
        switch_user(dev, args.partitiontable)
        flash_binary(dev, "../bin/gpt_part.bin", 0, 1024 * 0x200)

    # Sanity check GPT
    log("Check GPT")
    switch_user(dev)

    # Parse gpt
    gpt = parse_gpt(dev)
    log("gpt_parsed = {}".format(gpt))
    if "lk" not in gpt or "tee1" not in gpt or "boot" not in gpt or "recovery" not in gpt:
        raise RuntimeError("bad gpt")

    # Sanity check boot0
    log("Check boot0")
    switch_boot0(dev, args.unbrick)

    # Unbrick
    if args.unbrick:
        log("Flashing preloader")
        switch_boot0(dev, args.unbrick)
        flash_binary(dev, "../bin/boot0.img", 0)
        log("Flashing lk")
        switch_user(dev)
        flash_binary(dev, "../bin/uboot.img", gpt["lk"][0], gpt["lk"][1] * 0x200)
        log("Flashing tee1 and tee2")
        switch_user(dev)
        flash_binary(dev, "../bin/mobicore.bin", gpt["tee1"][0], gpt["tee1"][1] * 0x200)
        flash_binary(dev, "../bin/mobicore.bin", gpt["tee2"][0], gpt["tee2"][1] * 0x200)
        log("Flashing logo")
        switch_user(dev)
        flash_binary(dev, "../bin/logo.img", gpt["logo"][0], gpt["logo"][1] * 0x200)
        log("Flashing boot")
        switch_user(dev)
        flash_binary(dev, "../bin/boot.img", gpt["boot"][0], gpt["boot"][1] * 0x200)
        log("Flashing recovery")
        switch_user(dev)
        flash_binary(dev, "../bin/recovery.img", gpt["recovery"][0], gpt["recovery"][1] * 0x200)

    # Unlock
    if args.unlock:
        if not os.path.exists("../backup"):
            os.mkdir("../backup")
        log("Dumping frp")
        switch_user(dev)
        dump_binary(dev, "../backup/frp_orig.img", gpt["frp"][0], gpt["frp"][1] * 0x200)
        log("Patching frp")
        if subprocess.call(["./unlock_bootloader.sh", "../backup/frp_orig.img"]) == 2: # port to python?
            log("frp is already unlocked")
        else:
            if not os.path.exists("../backup/frp_unlocked.img"):
                raise RuntimeError("Can't find patched frp")
            log("Flashing patched frp")
            switch_user(dev)
            flash_binary(dev, "../backup/frp_unlocked.img", gpt["frp"][0], gpt["frp"][1] * 0x200)
        log("Flashing lk")
        switch_user(dev)
        flash_binary(dev, "../bin/uboot.img", gpt["lk"][0], gpt["lk"][1] * 0x200)
        
    # Test R/W
    if args.testrw:
        switch_user(dev)
        dump_binary(dev, "../boot.img", gpt["boot"][0], gpt["boot"][1] * 0x200)
        flash_binary(dev, "../boot.img", gpt["boot"][0], gpt["boot"][1] * 0x200)
        print(dev.rpmb_read().hex())

    # Reboot
    if args.unlock:
        log("Hold volume down button and press enter")
        log("Don't release volume button until you see FASTBOOT mode... on phone screen")
        input()
    log("Reboot")
    dev.reboot()
    if args.unlock:
        while not b'fastboot' in subprocess.check_output(["fastboot", "devices"]):
            time.sleep(0.5)
        if b'unlocked: yes' in subprocess.run(["fastboot", "getvar", "all"], stderr=subprocess.PIPE).stderr:
            log("Bootloader already unlocked")
            log("Hold power for 12s to reboot")
        elif b'unlocked: no' in subprocess.run(["fastboot", "getvar", "all"], stderr=subprocess.PIPE).stderr:
            log("Click volume up button to unlock")
            subprocess.call(["fastboot", "oem", "unlock"])
            log("Hold power for 12s to reboot")
        else:
            raise RuntimeError("Can't read getvar all")

if __name__ == "__main__":
    main()

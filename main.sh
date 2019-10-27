#!/bin/bash

set -e

for arg in "$@"; do arguments+=" $arg"; done

python3 -c 'import easygui' 2> /dev/null || pip3 install easygui
python3 -c 'import serial; import serial.tools.list_ports' 2> /dev/null || pip3 install pyserial

cd modules
python3 main.py $arguments
cd ..

#!/bin/bash

set -e

cd modules
python3 main.py $1
cd ..

#!/bin/sh
set -e

FRP=$1
FRPSIZE=$(wc -c $FRP | awk '{print $1}')

if [ -z "$FRP" ] || [ -z "$FRPSIZE" ] ; then
    echo "FRP not found, or FRP length is invalid"
    exit 1
fi

if [ $(dd if=$FRP bs=1 count=1 skip=$((FRPSIZE - 1)) 2>/dev/null | hexdump -e '"%X"') == 1 ]; then
    exit 2
fi

cp $FRP ../backup/frp_unlocked.img

# replace digest with 32 zero bytes
for i in $(seq 0 31) ; do echo -e '\x00' | dd of=../backup/frp_unlocked.img bs=1 count=1 seek=$i conv=notrunc 2> /dev/null ; done

# set unlock flag in FRP
echo -ne '\x01' | dd of=../backup/frp_unlocked.img bs=1 count=1 seek=$((FRPSIZE - 1)) conv=notrunc 2> /dev/null

# re-calculate digest
DIGEST=$(sha256sum ../backup/frp_unlocked.img | awk '{print $1}')

TMPFILE=$(mktemp XXXXXXXX)
for i in $(seq 1 2 64) ; do
    echo -n "\x""$(expr substr $DIGEST $i 2)" >> "$TMPFILE"
done

# place unlocked digest back to FRP
dd if="$TMPFILE" of=../backup/frp_unlocked.img bs=1 count=32 conv=notrunc 2> /dev/null
rm -f "$TMPFILE"

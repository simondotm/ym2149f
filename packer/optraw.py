#!/usr/bin/env python
# Experiments with SN raw data

import functools
import itertools
import struct
import sys
import time
import binascii
import math

filename = "VE3.raw"
RLE = False

fh = open(filename, 'rb')
data_block = bytearray(fh.read())
fh.close()	

#print len(input_block)

if False:
    # parse the header
    header_size = data_block[0]       # header size

    play_rate = data_block[1]       # play rate
    packet_count = data_block[2] + data_block[3]*256       # packet count LO
    duration_mm = data_block[4]       # duration mm
    duration_ss = data_block[5]       # duration ss

        

    print header_size
    print play_rate
    print packet_count
    print duration_mm
    print duration_ss

# 0,1 = tone1
# 2,3 = tone2
# 4,5 = tone3
# 6 = tone4
# 7,8,9,10 - volume 1,2,3,4
registers = [ 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
latched_channel = -1

output_block = bytearray()
output_blocks = []
for o in xrange(11):
    output_blocks.append( bytearray() )

n = 0
Packet = True
while (Packet):
    packet_size = data_block[n]
    print packet_size
    if packet_size == 255:
        Packet = False
    else:
        for x in range(packet_size):
            print " " +str(x)
            d = data_block[n+x]
            if d & 128:
                # latch
                c = (d>>5)&3
                latched_channel = c
                if d & 16:
                    # tone
                    print " tone on channel " + str(c)
                    registers[c*2+0] = d & 15

                else:
                    # volume
                    print " volume on channel " + str(c)
                    registers[c+7] = d & 15
            else:
                print " tone data on latched channel "
                registers[latched_channel*2+1] = d & 63

        for x in xrange(11):
			output_block.append(struct.pack('B', registers[x]))
			output_blocks[x].append(struct.pack('B', registers[x]))
		
			
        n += packet_size + 1

		
# calc diffs
if RLE:
    print 'run length encoding'
    for x in xrange(11):
        print 'register block ' + str(x)
        input_block = output_blocks[x]
        diff_block = bytearray()
        for n in xrange(len(input_block)):
            if n == 0:
                diff_block.append(input_block[0])
            else:
                if input_block[n] == input_block[n-1]:
                    diff_block.append(255)
                else:
                    diff_block.append(input_block[n])

        output_blocks[x] = diff_block

        # RLE
        if False:
            rle_block = bytearray()
            n = 0
            while (n < len(diff_block)):
                print 'offset ' + str(n)
                if (n < len(diff_block)-1) and diff_block[n+1] == 255:
                    offset = n
                    count = 1
                    while ((offset < len(diff_block)-1) and (count < 127)):
                        print 'diff[' + str(offset+1) + ']='+str(diff_block[offset+1])
                        if diff_block[offset+1] == 255:
                            count += 1 
                            offset += 1
                        else:
                            print 'ack'
                            break

                    rle_block.append(count+128)
                    rle_block.append(diff_block[n])
                    n += count
                    print 'run length ' + str(count)
                else:
                    rle_block.append(diff_block[n])
                    n += 1

            output_blocks[x] = rle_block
            print 'rle block size ' + str(x) + ' = ' + str(len(rle_block))
        
            

		

# write to output file
bin_file = open("z.bin", 'wb')
if True:
    # write separate blocks - compresses MUCH better than interleaved
    if False or RLE:
        for x in xrange(11):
            bin_file.write(output_blocks[x])
            print 'block size ' + str(x) + ' = ' + str(len(output_blocks[x]))
    else:
        # wont work if RLE compressed since block sizes will vary
        # try chunked non-interleaved
        # chunk_size is the amount off buffered music data
        # buffer size will be a multiple of 11 times this number
        offset = 0
        data_size = len(output_blocks[0])
        CHUNK_SIZE =  data_size  # 372 = 4096 byte buffer, 93 = 1023 byte buffer

        while data_size > 0:
            n = CHUNK_SIZE
            if data_size < n:
                n = data_size
            chunk = bytearray()
            for x in xrange(11):
                for y in xrange(n):
                    chunk.append(output_blocks[x][offset+y])
            bin_file.write(chunk)
            offset += n
            data_size -= n

else:
    # write interleaved blocks
    bin_file.write(output_block)
bin_file.close()	

if True:
    for x in xrange(11):
        bin_file = open("z_"+str(x)+".bin", 'wb')
        bin_file.write(output_blocks[x])
        bin_file.close()
 
#!/usr/bin/env python
# ymdump.py
# .YM files data dump utility
# was originated based on code from https://github.com/FlorentFlament/ym2149-streamer
# Almost completely rewritten by https://github.com/simondotm/
#
# Copyright (c) 2019 Simon Morris. All rights reserved.
#
# "MIT License":
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the Software
# is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import functools
import itertools
import struct
import sys
import time
import binascii
import math
import os

ENABLE_DEBUG = False        # enable this to have ALL the info spitting out. This is more than ENABLE_VERBOSE
ENABLE_VERBOSE = False


# R00 = Channel A Pitch LO (8 bits)
# R01 = Channel A Pitch HI (4 bits)
# R02 = Channel B Pitch LO (8 bits)
# R03 = Channel B Pitch HI (4 bits)
# R04 = Channel C Pitch LO (8 bits)
# R05 = Channel C Pitch HI (4 bits)
# R06 = Noise Frequency    (5 bits)
# R07 = I/O & Mixer        (IOB|IOA|NoiseC|NoiseB|NoiseA|ToneC|ToneB|ToneA)
# R08 = Channel A Level    (M | 4 bits) (where M is mode)
# R09 = Channel B Level    (M | 4 bits)
# R10 = Channel C Level    (M | 4 bits)
# R11 = Envelope Freq LO   (8 bits)
# R12 = Envelope Freq HI   (8 bits)
# R13 = Envelope Shape     (CONT|ATT|ALT|HOLD)

class YmReader(object):


    def __init__(self, fd):

        print "Parsing YM file..."

        self.__fd = fd
        self.__filename = fd.name
        self.__filesize = os.path.getsize(fd.name) 
        self.__parse_header()
        self.__data = []
        if not self.__data:
            self.__read_data()
            self.__check_eof()        

    def __parse_extra_infos(self):
        if self.__header['id'] == 'YM2!' or self.__header['id'] == 'YM3!' or  self.__header['id'] == 'YM3b':
            self.__header['song_name'] = self.__filename
            self.__header['author_name'] = ''
            self.__header['song_comment'] = ''
        else:
            # YM6!
            # Thanks http://stackoverflow.com/questions/32774910/clean-way-to-read-a-null-terminated-c-style-string-from-a-file
            toeof = iter(functools.partial(self.__fd.read, 1), '')
            def readcstr():
                return ''.join(itertools.takewhile('\0'.__ne__, toeof))
            self.__header['song_name'] = readcstr()
            self.__header['author_name'] = readcstr()
            self.__header['song_comment'] = readcstr()

    def __parse_header(self):
        # See:
        # http://leonard.oxg.free.fr/ymformat.html
        # ftp://ftp.modland.com/pub/documents/format_documentation/Atari%20ST%20Sound%20Chip%20Emulator%20YM1-6%20(.ay,%20.ym).txt

        # Parse the YM file format identifier first
        ym_format = self.__fd.read(4)
        print "YM Format: " + ym_format

        # we support YM2, YM3, YM5 and YM6
        
        d = {}
        if ym_format == 'YM2!' or ym_format == 'YM3!' or ym_format == 'YM3b':
            print "Version 2"
            d['id'] = ym_format
            d['check_string'] = 'LeOnArD!'
            d['nb_frames'] = (self.__filesize-4)/14
            d['song_attributes'] = 1 # interleaved
            d['nb_digidrums'] = 0
            d['chip_clock'] = 2000000
            d['frames_rate'] = 50
            d['loop_frame'] = 0
            d['extra_data'] = 0
            d['nb_registers'] = 14
        else:
            if ym_format == 'YM6!' or ym_format == 'YM5!':
                # Then parse the rest based on version
                ym_header = '> 8s I I H I H I H'
                s = self.__fd.read(struct.calcsize(ym_header))
                (d['check_string'],
                d['nb_frames'],
                d['song_attributes'],
                d['nb_digidrums'],
                d['chip_clock'],
                d['frames_rate'],
                d['loop_frame'],
                d['extra_data'],
                ) = struct.unpack(ym_header, s)

                d['id'] = ym_format
                d['nb_registers'] = 16
            else:
                if ym_format == '!C-l':
                    print('This is an LHA compressed YM file. Please extract the inner YM file first using 7zip or similar.')
                    sys.exit()
                else:
                    raise Exception('Unknown or Unsupported file format: ' + ym_format)

        # ok, carry on.
        #b0:     Set if Interleaved data block.
        #b1:     Set if the digi-drum samples are signed data.
        #b2:     Set if the digidrum is already in ST 4 bits format.
        d['interleaved'] = d['song_attributes'] & 0x01 != 0
        d['dd_signed'] = d['song_attributes'] & 0x02 != 0
        d['dd_stformat'] = d['song_attributes'] & 0x04 != 0
        self.__header = d

        if d['interleaved']:
            print "YM File is Interleaved format"


        # read any DD samples
        num_dd = self.__header['nb_digidrums']
        if num_dd != 0:

            print "Music contains " + str(num_dd) + " digi drum samples"

            # info
            if d['dd_stformat']:
                print " Samples are 4-bit ST format" # TODO: what does this mean?!
            else:
                print " Samples are UNKNOWN FORMAT" # TODO:so what format is it exactly?! 

            if d['dd_signed']:
                print " Samples are SIGNED"
            else:
                print " Samples are UNSIGNED"


            for i in xrange(num_dd):
                # skip over the digidrums sample file data section for now
                #print self.__fd.tell()
                sample_size = struct.unpack('>I', self.__fd.read(4))[0]   # get sample size

                print "Found DigiDrums sample " + str(i) + ", " + str(sample_size) + " bytes, loading data..."

                #print sample_size
                #print self.__fd.tell()
                #print "sample " + str(i) + " size="+str(sample_size)
                self.__fd.seek(sample_size, 1)      # skip the sample data (for now)
                #print self.__fd.tell()            


            #raise Exception('Unsupported file format: Digidrums are not supported')

        self.__parse_extra_infos()

        #print "file offset="
        #print self.__fd.tell()  
        #       self.dump_header()



    def __read_data_interleaved(self):
        #print "__read_data_interleaved"
        #print "file offset=" + str(self.__fd.tell())  


        cnt  = self.__header['nb_frames']

        regs = []
        for i in xrange( self.__header['nb_registers']):
            regs.append(self.__fd.read(cnt))            

        # support output of just the intro (for tunes with looping sections)       
        loop_frame = self.__header['loop_frame']


        if ENABLE_DEBUG:
            print " Loaded " + str(len(regs)) + " register data chunks"
            for r in xrange( self.__header['nb_registers']):
                print " Register " + str(r) + " entries = " + str(len(regs[r]))

        self.__data = regs



    def __read_data(self):
        if not self.__header['interleaved']:
            raise Exception(
                'Unsupported file format: Only interleaved data are supported')
        #print "file offset=" + str(self.__fd.tell())  
        self.__read_data_interleaved()
        #print "file offset=" + str(self.__fd.tell())  

    def __check_eof(self):
        if self.__fd.read(4) != 'End!':
            print '*Warning* End! marker not found after frames'


    def dump_header(self):
        for k in ('id','check_string', 'nb_frames', 'nb_registers', 'song_attributes',
                  'nb_digidrums', 'chip_clock', 'frames_rate', 'loop_frame',
                  'extra_data', 'song_name', 'author_name', 'song_comment'):
            print "{}: {}".format(k, self.__header[k])

    def get_header(self):
        return self.__header

    def get_data(self):
        return self.__data

    def write_raw(self, filename):
        regs = self.__data
        cnt  = self.__header['nb_frames']
        raw_data = bytearray()

        for n in xrange(cnt):
            for r in xrange(14):
                raw_data.append( regs[r][n] )

        # write to output file
        raw_file = open(filename, 'wb')
        raw_file.write(raw_data)
        raw_file.close()        



#------------------------------------------------------------------------
# Main()
#------------------------------------------------------------------------

import argparse

# Determine if running as a script
if __name__ == '__main__':

    print("ymdump.py : Convert Atari ST .YM files to RAW interleaved data files")
    print("Written in 2019 by Simon Morris, https://github.com/simondotm/ym2149f")
    print("")

    epilog_string = "Notes:\n"
    epilog_string += " This tool does not support LHA compressed YM files, so\n"
    epilog_string += "  you must extract the archived YM file first.\n"

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_string)

    parser.add_argument("input", help="YM source file (must be extracted from within the original YM file) [input]")
    parser.add_argument("-o", "--output", metavar="<output>", help="write RAW file <output> (default is '[input].ymr')")
    args = parser.parse_args()


    src = args.input
    dst = args.output
    if dst == None:
        dst = os.path.splitext(src)[0] + ".ymr"
    print "output file=" + dst


    # check for missing files
    if not os.path.isfile(src):
        print("ERROR: File '" + src + "' not found")
        sys.exit()

    with open(src, "rb") as fd:
        ym = YmReader(fd)
        fd.close()  
        
        ym.dump_header()
        header = ym.get_header()
        data = ym.get_data()

        print "Loaded YM File."
        print "Output file: '" + dst + "'"
        ym.write_raw( dst )



#!/usr/bin/env python
# ym2sn.py
# .YM files (YM2149 sound chip) to SN76489 .VGM music file format conversion utility
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
from os.path import basename


# Command line can override these defaults
SN_CLOCK = 4000000              # set this to the target SN chip clock speed
LFSR_BIT = 15                   # set this to either 15 or 16 depending on which bit of the LFSR is tapped in the SN chip

ENABLE_ENVELOPES = True     # enable this to simulate envelopes in the output
ENABLE_ATTENUATION = False  # enables conversion of YM to SN attenuation. In theory a better matching of volume in the output.

FILTER_CHANNEL_A = False
FILTER_CHANNEL_B = False
FILTER_CHANNEL_C = False
FILTER_CHANNEL_N = False    # Noise channel

ENABLE_DEBUG = False        # enable this to have ALL the info spitting out. This is more than ENABLE_VERBOSE
ENABLE_VERBOSE = False

ARDUINO_BIN = False

# Percussive Noise has a dedicated channel and attenuator on the SN
# whereas on the YM, the noise waveform is logically OR'd with the squarewave
# Therefore each YM channel contributes 1/3 of the overall noise volume
# As a result SN percussion adds 25% extra volume in the mix unless we compensate for it.
# This scaler allows that to be balanced
# This can be tweaked if necessary
NOISE_MIX_SCALE = 1.0 / 3.0 

# Runtime options (not command line options)
ENABLE_NOISE = True         # enables noises to be processed
ENABLE_NOISE_PITCH = True   # enables 'nearest match' fixed white noise frequency selection rather than fixed single frequency
ENABLE_ENVELOPE_MIX_HACK = True # wierd oddity fix where tone mix is disabled, but envelopes are enabled - EXPERIMENTAL
OPTIMIZE_VGM = True         # outputs delta register updates in the vgm rather than 1:1 register dumps
SAMPLE_RATE = 1             # number of volume frames to process per YM frame (1=50Hz, 2=100Hz, 63=700Hz, 126=6300Hz (GOOD!) 147=7350Hz, 294=14700Hz, 441=22050Hz, 882=44100Hz)

# legacy/non working debug flags
SIM_ENVELOPES = True       # set to true to use full volume for envelepe controlled sounds


# For testing only 
ENABLE_BIN = False          # enable output of a test 'bin' file (ie. the raw SN data file)

#--------------------------------------------------------------------
# Bass frequency processing settings
#--------------------------------------------------------------------
# Since the hardware SN76489 chip is limited to a frequency range determined by its clock rate, and also 10-bit precision,
# so some low-end frequencies cannot be reproduced to match the 12-bit precision YM chip.
#
# To remedy this we have two techniques available:
#   1. Use the periodic noise feature of the SN76489 to 'simulate' these lower frequencies (at the cost of interleaving percussion and some approximation because we can only have one channel playing PN)
#   2. Simulate the low-end frequencies in software by implementing a low frequency square wave using software timers to manipulate attenuation on the tone channels

# Periodic noise based bass settings (default)
ENABLE_BASS_TONES = True    # enables low frequency tones to be simulated with periodic noise
ENABLE_BASS_BIAS = True     # enables bias to the most active bass channel when more than one low frequency tone is playing at once.
FORCE_BASS_CHANNEL = -1 #-1     # set this to 0,1 or 2 (A/B/C) or -1, to make a specific channel always take the bass frequency. Not an elegant or useful approach.


# Software bass settings (overrides periodic noise bass)
# Enabling this setting will create output register data that is not hardware compliant, so any decoder must interpret the data correctly to synthesize bass frequencies.
# The output VGM file is VGM compatible, but it will not sound correct when played due to the data modifications.
# 
# The approach is as follows:
#   For any frequency on a tone channel that is below the SN76489 hardware tone frequency range (ie. value > 10-bits) 
#    We divide the tone register value by 4, store that in the 10-bit output, but set bit 6 in the high byte DATA register.
#    The decoder must check for this bit being set and interpet the tone register value as the duty cycle time for a software generated squarewave. 

ENABLE_SOFTWARE_BASS = False
if ENABLE_SOFTWARE_BASS:
    TONE_RANGE = 4095
    ENABLE_BASS_TONES = False
else:
    TONE_RANGE = 1023


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

# Chip specs:
# 3 x Squarewave tone oscillators and 1 x Noise generator
# 1 x Envelope driver
# 1 x Mixer

# Pitch oscillation frequency is (Clock / 16 x TP) [TP is tone pitch]
# Noise frequency is (Clock / 16 x NP) [NP is noise pitch R6]
# Noise and/or Tone is output when Mixer flag is set to 0 for a channel
# Mode [M] is 1, then envelope drives volume, when 0, the 4 bit value drives attenuation
# Envelope repetition frequency (fE) is (Clock / 256 x EP) [EP is envelope frequency]
# Envelope shape has 10 valid settings - see data sheet for details

# Envelope Generator
# The envelope generator is a simple 5-bit counter, that can be incremented, decremented, reset or stopped
# Control of it's behaviour is via R13
# The output of the counter drives the attenuation of the output signal (in 5 bit precision rather than 4 bit normally)
# The counter increments once every fE/32

# By calculating the envelope frequency we can determine how fast any software simulation of the waveform would need to be
# Writing to register 13 resets the envelope clock
# r13 has a particular status. If the value stored in the file is 0xff, YM emulator will not reset the waveform position.

# To get envelopes working on an SN chip we'd have to simulate the envelopes
# by reprogramming attenuation registers at the correct frequency
# Note that since the SN only has four bit of precision for volume, 
# it is already half the required update frequency

# Effects & Digidrums
# Digidrums are 4-bit samples played on one of the 3 voices
# Information for playback is encoded into the spare bits of the YM register data
# Plus 2 'virtual' registers (14+15)
# See ftp://ftp.modland.com/pub/documents/format_documentation/Atari%20ST%20Sound%20Chip%20Emulator%20YM1-6%20(.ay,%20.ym).txt

# r1 free bits are used to code TS:
# r1 bits b5-b4 is a 2bits code wich means:
# 
# 00:     No TS.
# 01:     TS running on voice A
# 10:     TS running on voice B
# 11:     TS running on voice C

# r1 bit b6 is only used if there is a TS running. If b6 is set, YM emulator must restart
# the TIMER to first position (you must be VERY sound-chip specialist to hear the difference).
# 
# 
# r3 free bits are used to code a DD start.
# r3 b5-b4 is a 2bits code wich means:
# 
# 00:     No DD
# 01:     DD starts on voice A
# 10:     DD starts on voice B
# 11:     DD starts on voice C


# Setup 6522 VIA to tick over a counter at the freq we need
# Then setup an interrupt at whatever period we can afford
# Load VIA counter, look up in envelope table, set volume on channels with envelopes enabled
# Could possibly do the digidrums this way too.


# Setup attenuation mapping tables


# SN attenuates in steps of 2dB, whereas YM steps in 1.5dB steps, so find the nearest volume in this table.
# Not sure this is ideal, because without envelopes, some tunes only use the 4 bit levels, and they dont use all 16-levels of the output SN
# but technically is a better representation? hmm.... revist
# from https://www.smspower.org/Development/SN76489
sn_volume_table= [ 0, 1304, 1642, 2067, 2603, 3277, 4125, 5193, 6568, 8231, 10362, 13045, 16422, 20675, 26028, 32767 ]


# When we average volumes (eg. for noise mixing or envelope sampling) we cant just average the linear value because they represent logarithmic attenuation
# So we use tables to convert between the logarithmic attenuation and the linear output
# YM Attentuation at normalized 1V amplitude according to the datasheet is -0.75 dB per step for 5-bit envelopes and -1.5 dB per step for the 4-bit fixed levels

# map 4 or 5-bit logarithmic volume to 8-bit linear volume
#ym_amplitude_table = [ 0x00, 0x01, 0x02, 0x02, 0x03, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0D, 0x10, 0x13, 0x16, 0x1A, 0x1F, 0x25, 0x2C, 0x34, 0x3D, 0x48, 0x54, 0x63, 0x74, 0x88, 0x9F, 0xBA, 0xD9, 0xFF ]
#sn_amplitude_table = [ 4096, 3254, 2584, 2053, 1631, 1295, 1029, 817, 649, 516, 410, 325, 258, 205, 163, 0 ]

# Taken from: https://github.com/true-grue/ayumi/blob/master/ayumi.c
# However, it doesn't marry with the YM2149 spec sheet, nor with the anecdotal reports that the YM attentuation steps in -1.5dB increments. 
# There are also other measurements here (for AY) https://github.com/mamedev/mame/blob/master/src/devices/sound/ay8910.cpp
# It looks like it might just be a difference in output levels between AY and YM
# So, I'm gonna run with the YM datasheet version.
# This may need further consideration if we're importing a YM file originally from a CPC/AY
YM_AMPLITUDE_TABLE_EMU = [
    0.0, 0.0,
    0.00465400167849, 0.00772106507973,
    0.0109559777218, 0.0139620050355,
    0.0169985503929, 0.0200198367285,
    0.024368657969, 0.029694056611,
    0.0350652323186, 0.0403906309606,
    0.0485389486534, 0.0583352407111,
    0.0680552376593, 0.0777752346075,
    0.0925154497597, 0.111085679408,
    0.129747463188, 0.148485542077,
    0.17666895552, 0.211551079576,
    0.246387426566, 0.281101701381,
    0.333730067903, 0.400427252613,
    0.467383840696, 0.53443198291,
    0.635172045472, 0.75800717174,
    0.879926756695, 1.0 ]

# calculate datasheet spec YM amplitude table from logs
YM_AMPLITUDE_TABLE_DS = []
for v in range(32):
    if v == 0:
        a = 0.0
    else:
        a = math.pow(10, ((-0.75*(31-v))/10) )
    #print " Amplitude of volume " + str(v) + " is " + str(a)
    a = min(1.0, a)
    a = max(0.0, a)
    YM_AMPLITUDE_TABLE_DS.append( a )


# use the datasheet table for YM amplitudes
ym_amplitude_table = YM_AMPLITUDE_TABLE_DS






# Define if we'll use table based mapping for YM amplitude calcs, or Logarithmic mapping
USE_YM_AMPLITUDE_TABLE = True

# get the normalized linear (float) amplitude for a 5 bit level
def get_ym_amplitude(v):
    if USE_YM_AMPLITUDE_TABLE:
        return ym_amplitude_table[v]
    else:
        if v == 0:
            a = 0.0
        else:
            a = math.pow(10, ((-0.75*(31-v))/10) )
        #print " Amplitude of volume " + str(v) + " is " + str(a)
        a = min(1.0, a)
        a = max(0.0, a)
        return a

# given an amplitude, return the nearest 5-bit volume level
def get_ym_volume(amplitude):
    if USE_YM_AMPLITUDE_TABLE:
        best_dist = 1<<31
        index = 0
        for n in range(32):
            ym_amplitude = ym_amplitude_table[n]
            p = amplitude - ym_amplitude
            dist = p * p
            # we always round to the nearest louder level (so we are never quieter than target level)
            if dist < best_dist and ym_amplitude >= amplitude:
                best_dist = dist
                index = n


        return index
    else:        
        if (a == 0.0):
            v = 0
        else:
            v = int( 31 - ( (10*math.log(a, 10)) / -0.75 ) )
        #print "  Volume of amplitude " + str(a) + " is " + str(v)
        if v > 31:
            print("RANGE ERROR > 5 bits in get_ym_volume()")
        v = min(31, v)
        v = max(0, v)
        return v      




# table to map ym volumes from 5-bit to 4-bit SN volumes
ym_sn_volume_table = []
for n in range(32):
    a = int(get_ym_amplitude(n) * 32767.0)
    dist = 1<<31
    index = 0
    for i in range(16):
        l = sn_volume_table[i]
        p = a - l
        d = p * p
        # we always round to the nearest louder level (so we are never quieter than target level)
        if d < dist and l >= a:
            dist = d
            index = i

    ym_sn_volume_table.append(index)



# get the nearest 4-bit logarithmic volume to the given 5-bit ym_volume
def get_sn_volume(ym_volume):
    if ym_volume > 31:
        print("RANGE ERROR > 5 bits in get_sn_volume()")
    if ENABLE_ATTENUATION:
        # use lookup table
        return ym_sn_volume_table[ym_volume]
    else:
        # simple attenuation map
        return (ym_volume >> 1) & 15




# print the tables
if True:

    print("ym_sn_volume_table:")
    print(ym_sn_volume_table)

    #def get_ym_amplitude2(v):
    #    if v == 0:
    #        a = 0.0
    #    else:
    #        a = math.pow(10, ((-0.75*(31-v))/10) )
    #    #print " Amplitude of volume " + str(v) + " is " + str(a)
    #    a = min(1.0, a)
    #    a = max(0.0, a)
    #    return a

    #ym_amplitude_table2 = []
    #for n in range(32):
    #    ym_amplitude_table2.append( get_ym_amplitude2(n) )

    print("attenuation tables:")
    print("{: >2} {: >20} {: >20} {: >20}".format("level", "ymtable_emu", "logtable_datasheet", "sntable"))
    for n in range(32):
        print("{: >2} {: >20} {: >20} {: >20}".format(n, YM_AMPLITUDE_TABLE_EMU[n], YM_AMPLITUDE_TABLE_DS[n], sn_volume_table[n>>1]/32767.0))





			
#---- FAST VERSION
	

# Class to emulate the YM2149 HW envelope generator
# see http://www.cpcwiki.eu/index.php/Ym2149 for the FPGA logic
class YmEnvelope():

    ENV_MASTER_CLOCK = 2000000
    ENV_CLOCK_DIVIDER = 8
    ENV_CONT = (1<<3)
    ENV_ATT = (1<<2)
    ENV_ALT = (1<<1)
    ENV_HOLD = (1<<0)

    #-- envelope shapes
    #-- CONT|ATT|ALT|HOLD
    #-- 0 0 x x  \___
    #-- 0 1 x x  /___
    #-- 1 0 0 0  \\\\
    #-- 1 0 0 1  \___
    #-- 1 0 1 0  \/\/
    #--           ___
    #-- 1 0 1 1  \
    #-- 1 1 0 0  ////
    #--           ___
    #-- 1 1 0 1  /
    #-- 1 1 1 0  /\/\
    #-- 1 1 1 1  /___    

	
	# Envelopes can be implemented much faster as lookup tables.
	# We use a 64 entry tables set based on the envelope shape (there are 16 possible envelope mode settings)
	# with variants as:
	# Ramp Up / Hold High
	# Ramp Up / Hold Low
	# Ramp Down / Hold Low
	# Ramp Down / Hold High
	# Ramp Up / Ramp Down (Loop)
	# Ramp Up / Ramp Up (Loop) 
	# Ramp Down / Ramp Down (Loop)
	# 
	# Pre-create the 16 shapes as an array of 64 values from 0-31 (output V) 
	# Select appropriate active table as ETABLE when env shape is set
	# ELOOP on, if bit 0 (HOLD) is 1 OR bit 3 (CONT) is 0
	# Each update, add N envelope cycles to envelope counter
	# if ELOOP: ECNT &= 63 else ECNT = MAX(ECNT,63)
	# V = ETABLE[ECNT]

    # This approach also opens up the possibility for digi drums, since they are effectively just attentuation tables too (just longer ones!)
	
    def __init__(self):
        self.__clock_cnt = 0    # clock cycle counter

        # Initialise the envelope shape tables
        # YM envelopes are very basic and comprise 4 basic types of waveform ramp(up/down) hold(high/low)
        ramp_up = []
        ramp_dn = []
        hold_hi = []
        hold_lo = []

        # populate these waveforms as 32 5-bit volume levels, where 0 is silent, 31 is full volume
        for x in range(0,32):
            ramp_up.append(x)
            ramp_dn.append(31-x)
            hold_hi.append(31)
            hold_lo.append(0)
            
        # Now create each shape by copying two combinations of the above 4 shapes into 16 different 64-value arrays.
        self.__envelope_shapes = []
            
        def createShape(phase1, phase2):
            temp = []
            temp.extend(phase1)
            temp.extend(phase2)
            self.__envelope_shapes.append( temp )
            
        # shapes 0-3
        createShape(ramp_dn, hold_lo)
        createShape(ramp_dn, hold_lo)
        createShape(ramp_dn, hold_lo)
        createShape(ramp_dn, hold_lo)

        # shapes 4-7
        createShape(ramp_up, hold_lo)
        createShape(ramp_up, hold_lo)
        createShape(ramp_up, hold_lo)
        createShape(ramp_up, hold_lo)

        # shapes 8-15
        createShape(ramp_dn, ramp_dn)
        createShape(ramp_dn, hold_lo)
        createShape(ramp_dn, ramp_up)
        createShape(ramp_dn, hold_hi)
        createShape(ramp_up, ramp_up)
        createShape(ramp_up, hold_hi)
        createShape(ramp_up, ramp_dn)
        createShape(ramp_up, hold_lo)

        self.reset()

    # reset the chip logic state
    def reset(self):
        self.__rb = 0   # Envelope frequency, 8-bit fine adjustment
        self.__rc = 0   # Envelope frequency, 8-bit rough adjustment   
        self.__rd = 0   # shape of envelope (CONT|ATT|ALT|HOLD)

        self.__env_cnt = 0      # envelope period counter (index into the table)
        self.__env_table = []	# current envelope shape table

        # initialise shape
        self.set_envelope_shape(self.__rd)


    # set envelope shape register 13
    def set_envelope_shape(self, r):
        # stash register setting
        self.__rd = r
        self.__env_cnt = 0

        # load initial shape table
        self.__env_table = self.__envelope_shapes[r & 15]

        # determine if it is a looped shape / mode
        if (r & self.ENV_HOLD) == self.ENV_HOLD or (r & self.ENV_CONT) == 0:
            self.__env_hold = True
        else:
            self.__env_hold = False

        # set the current envelope volume
        self.__env_volume = self.__env_table[0]
        if ENABLE_DEBUG:
            print("  ENV: set shape " + str(r) + " hold=" + str(self.__env_hold) + " " + str(self.__env_table) )


    # set the YM chip envelope frequency registers
    def set_envelope_freq(self, hi, lo):
        self.__rb = lo
        self.__rc = hi

    # get the current YM chip envelope frequency as a 16-bit interval/counter value
    def get_envelope_period(self):
        return self.__rc * 256 + self.__rb

    # get the current 5-bit envelope volume, 0-31
    def get_envelope_volume(self):
        return self.__env_volume
        #return (self.__env_table[self.__env_cnt])# & 31)


    # advance the envelope emulator by provided number of clock cycles
    def tick(self, clocks):

        #print "tick(" + str(clocks) + ")"
        # advance the clock cycles
        self.__clock_cnt += clocks

        # get the currently set envelope frequency and scale up by the clock divider, since we're working in cpu clocks rather than envelope clocks
        f = self.get_envelope_period() * self.ENV_CLOCK_DIVIDER
        #print " f=" + str(f)

        a = get_ym_amplitude( self.__env_table[self.__env_cnt] )
        n = 1
        if (f > 0):
            # the envelope logic runs every ENV_CLOCK_DIVIDER clock cycles
            # iterate correct number of envelope periods based on the current envelope frequency
            # we average the outputs processed, as a simple low pass filter to compensate for larger values of clocks and create a sampled output volume
            # TODO: a better resampling filter 
            while (self.__clock_cnt >= f):	

                self.__env_cnt += 1
                self.__clock_cnt -= f


                # if looping, mask the bottom 6 bits, otherwise clamp at 63
                if self.__env_hold:
                    self.__env_cnt = min(63, self.__env_cnt)
                else:
                    self.__env_cnt &= 63

                # increase number of envelope samples
                n += 1
                # add the envelope volume to the sampled volume
                a += get_ym_amplitude( self.__env_table[self.__env_cnt] )
                #print "  __env_cnt=" + str(self.__env_cnt) + ", __clock_cnt=" + str(self.__clock_cnt)

        # output volume is the average volume for the elapsed number of clocks
        self.__env_volume = get_ym_volume( a / n )
        #print "   Finished Tick __env_cnt=" + str(self.__env_cnt)

    # perform a check that the logic is working correctly
    def test(self):
        self.reset()
        print('default volume - ' + str(self.get_envelope_volume()))

        for m in range(16):
            self.reset()
            self.set_envelope_shape(m)
            self.set_envelope_freq(0,1) # interval of 1
            vs = ''
            for n in range(128):
                v = self.get_envelope_volume() >> 1
                vs += format(v, 'x')
                self.tick(8)

            print('output volume M=' + str(format(m, 'x')) + ' - ' + vs)

        #stop
			

class YmReader(object):

    # these are set only when outputting looped files
    OUTPUT_LOOP_INTRO = False
    OUTPUT_LOOP_SECTION = False

    def __init__(self, fd):

        # create instance of YM envelope generator
        self.__ymenv = YmEnvelope()
        #self.__ymenv.test()

        print("Parsing YM file...")

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
            #print("here")
            # YM6!
            # Thanks http://stackoverflow.com/questions/32774910/clean-way-to-read-a-null-terminated-c-style-string-from-a-file
            #toeof = iter(functools.partial(self.__fd.read, 1), '')
            
            #print("here2")
            #def readcstr():
            #    return ''.join(itertools.takewhile('\0'.__ne__, toeof))

            def readcstr():
                chars = []
                while True:
                    c = self.__fd.read(1)
                    if c == b'\x00': #chr(0):
                        return "".join(chars)
                    #print(c)
                    chars.append(c.decode("utf-8") )

            #print("here3")
            self.__header['song_name'] = readcstr()
            #print("here4")
            self.__header['author_name'] = readcstr()
            #print("here5")
            self.__header['song_comment'] = readcstr()
            #print("here6")

    def __parse_header(self):
        # See:
        # http://leonard.oxg.free.fr/ymformat.html
        # ftp://ftp.modland.com/pub/documents/format_documentation/Atari%20ST%20Sound%20Chip%20Emulator%20YM1-6%20(.ay,%20.ym).txt

        # Parse the YM file format identifier first
        ym_format = self.__fd.read(4)
        ym_format = ym_format.decode("utf-8") 
        print("YM Format: " + ym_format)

        # we support YM2, YM3, YM5 and YM6
        
        d = {}
        if ym_format == 'YM2!' or ym_format == 'YM3!' or ym_format == 'YM3b':
            print("Version 2")
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
            print("YM File is Interleaved format")


        # read any DD samples
        num_dd = self.__header['nb_digidrums']
        if num_dd != 0:

            print("Music contains " + str(num_dd) + " digi drum samples")

            # info
            if d['dd_stformat']:
                print(" Samples are 4-bit ST format") # TODO: what does this mean?!
            else:
                print(" Samples are UNKNOWN FORMAT") # TODO:so what format is it exactly?! 

            if d['dd_signed']:
                print(" Samples are SIGNED")
            else:
                print(" Samples are UNSIGNED")


            for i in range(num_dd):
                # skip over the digidrums sample file data section for now
                #print self.__fd.tell()
                sample_size = struct.unpack('>I', self.__fd.read(4))[0]   # get sample size

                print("Found DigiDrums sample " + str(i) + ", " + str(sample_size) + " bytes, loading data...")

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
        for i in range( self.__header['nb_registers']):
            regs.append(self.__fd.read(cnt))            

        # support output of just the intro (for tunes with looping sections)       
        loop_frame = self.__header['loop_frame']

        if self.OUTPUT_LOOP_INTRO and loop_frame != 0:
            self.__header['nb_frames'] = loop_frame
            for i in range(self.__header['nb_registers']):
                regs[i] = regs[i][0:loop_frame-1]

        if self.OUTPUT_LOOP_SECTION and loop_frame != 0:
            self.__header['nb_frames'] = cnt - loop_frame
            for i in range(self.__header['nb_registers']):
                regs[i] = regs[i][loop_frame:]


        if ENABLE_DEBUG:
            print(" Loaded " + str(len(regs)) + " register data chunks")
            for r in range( self.__header['nb_registers']):
                print(" Register " + str(r) + " entries = " + str(len(regs[r])))




        #self.__data=[''.join(f) for f in zip(*regs)]
        self.__data = regs
        #print self.__data



    def __read_data(self):
        if not self.__header['interleaved']:
            raise Exception(
                'Unsupported file format: Only interleaved data are supported')
        #print "file offset=" + str(self.__fd.tell())  
        self.__read_data_interleaved()
        #print "file offset=" + str(self.__fd.tell())  

    def __check_eof(self):
        if self.__fd.read(4) != 'End!':
            print('*Warning* End! marker not found after frames')


    def dump_header(self):
        for k in ('id','check_string', 'nb_frames', 'nb_registers', 'song_attributes',
                  'nb_digidrums', 'chip_clock', 'frames_rate', 'loop_frame',
                  'extra_data', 'song_name', 'author_name', 'song_comment'):
            print("{}: {}".format(k, self.__header[k]))

    def get_header(self):
        return self.__header

    def get_data(self):
        return self.__data

    def write_vgm(self, vgm_filename):

        # prepare the YM file parser
        clock = self.__header['chip_clock']
        cnt  = self.__header['nb_frames']
        frame_rate = self.__header['frames_rate']

        regs = self.__data

        digi_drums = self.__header['nb_digidrums']


        print("Analysing & Converting YM file...")

        # prepare the vgm output
        #vgm_filename = "test.vgm"
        print("   VGM Processing : Writing output VGM file '" + vgm_filename + "'")

        print("---")

        vgm_stream = bytearray()
        vgm_time = 0
        vgm_clock = SN_CLOCK # SN clock speed

        # prepare the raw output
        raw_stream = bytearray()

        # YM has 12 bits of precision
        # Lower values correspond to higher frequencies - see http://poi.ribbon.free.fr/tmp/freq2regs.htm
        ym_freq_hi = (float(clock) / 16.0) / float(1)
        ym_freq_lo = (float(clock) / 16.0) / float(4095)

        # SN has 10 bits of precision vs YM's 12 bits
        sn_freq_hi = float(vgm_clock) / (2.0 * float(1) * 16.0)
        sn_freq_lo = float(vgm_clock) / (2.0 * float(TONE_RANGE) * 16.0)

        # SN can generate periodic noise in the lower Hz range
        sn_pfreq_hi = float(vgm_clock) / (2.0 * float(1) * 16.0 * float(LFSR_BIT))
        sn_pfreq_lo = float(vgm_clock) / (2.0 * float(TONE_RANGE) * 16.0 * float(LFSR_BIT))

        print(" YM clock is " + str(clock))
        print(" SN clock is " + str(vgm_clock))

        print(" YM Tone Frequency range from " + str(ym_freq_lo) + "Hz to " + str(ym_freq_hi) + "Hz")
        print(" SN Tone Frequency range from " + str(sn_freq_lo) + "Hz to " + str(sn_freq_hi) + "Hz")
        print(" SN Bass Frequency range from " + str(sn_pfreq_lo) + "Hz to " + str(sn_pfreq_hi) + "Hz")

        if ENABLE_SOFTWARE_BASS:
            print(" + Software Bass is ENABLED")
        if ENABLE_BASS_TONES:
            print(" + Periodic Noise Bass is ENABLED")
        if ENABLE_ENVELOPES:
            print(" + Envelope Emulation is ENABLED")
        if ENABLE_ATTENUATION:
            print(" + Volume Attenuation is ENABLED")

        def get_register_data(register, frame):
            return int(binascii.hexlify(regs[register][frame]), 16)
            
        print("---")

        #print get_register_data(0,0)
        #print get_register_data(1,0)

        # set default volumes at the start of the tune for all channels
        if not self.OUTPUT_LOOP_SECTION:
            dv = 15 # default volume is 15 (silent)
            vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
            vgm_stream.extend( struct.pack('B', 128+(0<<5)+16+dv) ) # LATCH VOLUME C0
            vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
            vgm_stream.extend( struct.pack('B', 128+(1<<5)+16+dv) ) # LATCH VOLUME C1
            vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
            vgm_stream.extend( struct.pack('B', 128+(2<<5)+16+dv) ) # LATCH VOLUME C2
            vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
            vgm_stream.extend( struct.pack('B', 128+(3<<5)+16+dv) ) # LATCH VOLUME C3 to SILENT

            # set periodic noise on channel 3
            vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
            vgm_stream.extend( struct.pack('B', 128 + (3 << 5) + 3) ) # LATCH PERIODIC TONE on channel 3    

        # stats for tracking frequency ranges within music
        ym_tone_a_max = 0
        ym_tone_b_max = 0
        ym_tone_c_max = 0

        ym_tone_a_min = 65536
        ym_tone_b_min = 65536
        ym_tone_c_min = 65536

        # range of noise frequencies
        ym_noise_max = 0
        ym_noise_min = 63356
        
        # range of digidrum playback frequencies
        ym_dd_freq_min = 65536
        ym_dd_freq_max = 0        

        # range of envelope frequencies
        ym_env_freq_min = 65536
        ym_env_freq_max = 0        

        # number of frames using envelopes
        ym_env_count = 0 

        # latch data for optimizing the output vgm to only output changes in register state
        # make sure the initial values are guaranteed to get an output on first run
        sn_attn_latch = [ -1, -1, -1, -1 ]
        sn_tone_latch = [ -1, -1, -1, -1 ]




                        

        # Helper functions
        def get_register_byte(r):
            # some tunes have incorrect data stream lengths, handle that here.
            if r < len(regs) and i < self.__header['nb_frames'] and i < len(regs[r]) :
                n = regs[r][i]
                return int(n) #int( struct.unpack('B', n)[0] ) #int(n) #int(binascii.hexlify(n), 16)
            else:
                print("ERROR: Register out of range - bad sample ID or corrupt file?")
                return 0

        def get_register_word(r):
            return get_register_byte(r) + get_register_byte(r+1)*256

        def getregisterflag(data,bit,key0,key1):
            if data & (1<<bit):
                return key1
            else:
                return key0      

        #--------------------------------------------------------------
        # return frequency in hz of a given YM tone/noise pitch
        #--------------------------------------------------------------
        def get_ym_frequency(v):
            if v < 1:
                v = 1
            return clock / (16 * v)


        #--------------------------------------------------------------
        # given a YM tone period, return the equivalent SN tone register period
        #--------------------------------------------------------------
        def ym_to_sn(ym_tone, is_periodic = False):

            transposed = 0
            # Adjust freq scale & baseline range if periodic noise selected
            baseline_freq = sn_freq_lo
            sn_freq_scale = 1.0
            if is_periodic:
                sn_freq_scale = float(LFSR_BIT)
                baseline_freq = sn_pfreq_lo

            # tones should never exceed 12-bit range
            # but some YM files encode extra info
            # into the top 4 bits

            if ym_tone > 4095:
                print(" ERROR: tone data ("+str(ym_tone)+") is out of range (0-4095)")
                ym_tone = ym_tone & 4095
                
            # If the tone is 0, it's probably because
            # there's a digidrum being played on this voice
            if ym_tone == 0:
                if ENABLE_VERBOSE:
                    print(" ERROR: ym tone is 0")
                ym_freq = 0
                target_freq = 0
            else:
                ym_freq = (float(clock) / 16.0) / float(ym_tone)



                # if the frequency goes below the range
                # of the SN capabilities, add an octave
                target_freq = ym_freq
                while target_freq < baseline_freq:
                    #if ENABLE_DEBUG:
                    #    print " WARNING: Freq too low - Added an octave - from " + str(ym_freq) + " to " + str(ym_freq*2.0) + "Hz"
                    target_freq *= 2.0
                    transposed +=  1

            # calculate the appropriate SN tone register value
            if target_freq == 0:
                sn_tone = 0
                sn_freq = 0
            else:
                sn_tone = float(vgm_clock) / (2.0 * target_freq * 16.0 * sn_freq_scale )
                # due to the integer maths, some precision is lost at the lower end
                sn_tone = int(round(sn_tone))	# using round minimizes error margin at lower precision

                # clamp range to 10 bits (or adjust if bit bass enabled)
                if ENABLE_SOFTWARE_BASS:
                    if sn_tone > 1023:
                        sn_tone >>= 2       # reduce tone frequency by 2 octaves
                        sn_tone |= 16384    # set bit 15 (bit 6 of DATA byte)
                        #print " INFO: Exported bit bass tone (target_freq="+str(target_freq)+" Hz)"
                else:
                    if sn_tone > 1023:
                        sn_tone = 1023
                        print(" WARNING: Clipped SN tone to 1023 (target_freq="+str(target_freq)+" Hz)")
                        # this could result in bad tuning, depending on why it occurred. better to reduce freq?



                if sn_tone < 1:
                    sn_tone = 1
                    print(" WARNING: Clipped SN tone to 1 (target_freq="+str(target_freq)+" Hz)")

                sn_freq = float(vgm_clock) / (2.0 * float(sn_tone) * 16.0 * sn_freq_scale)

            if ENABLE_DEBUG:
                sp = ""
                if is_periodic:
                    sp = " (PERIODIC)"
                print("   ym_tone=" + str(ym_tone) + " ym_freq="+str(ym_freq) + ", sn_tone="+str(sn_tone) + " sn_freq="+str(sn_freq) + ", transposed " + str(transposed) + " octaves" + sp)

            hz_err = sn_freq - ym_freq
            if hz_err > 2.0 or hz_err < -2.0:
                if ENABLE_VERBOSE:
                    print(" WARNING: Large error transposing tone! [" + str(hz_err) + " Hz ], Periodic=" + str(is_periodic))

            return sn_tone

        #--------------------------------------------------------------
        # As above, but for periodic white noise
        #--------------------------------------------------------------
        def ym_to_sn_periodic(ym_tone):

            # tones should never exceed 12-bit range
            # but some YM files encode extra info
            # into the top 4 bits
            if ym_tone > 4095:
                print(" ERROR: tone data ("+str(ym_tone)+") is out of range (0-4095)")
                ym_tone = ym_tone & 4095

            # If the tone is 0, it's probably because
            # there's a digidrum being played on this voice
            if ym_tone == 0:
                if ENABLE_VERBOSE:
                    print(" ERROR: ym tone is 0")
                ym_freq = 0
            else:
                ym_freq = (float(clock) / 16.0) / float(ym_tone)

            # if the frequency goes below the range
            # of the SN capabilities, add an octave
            while ym_freq < sn_pfreq_lo:
                ym_freq *= 2.0
                if ENABLE_VERBOSE:
                    print(" WARNING: Freq too low - Added an octave - now " + str(ym_freq) + "Hz")

            sn_tone = float(vgm_clock) / (2.0 * ym_freq * 16.0 * float(LFSR_BIT) )
            
            # due to the integer maths, some precision is lost at the lower end
            sn_tone = int(round(sn_tone))	# using round minimizes error margin at lower precision

            # clamp range to 10 bits (or adjust if bit bass enabled)
            if ENABLE_SOFTWARE_BASS:
                if sn_tone > 1023:
                    sn_tone >>= 2
                    sn_tone |= 16384 
                    print(" WARNING: Exported bit bass tone in periodic noise ?? (target_freq="+str(target_freq)+" Hz)")
                    # this could result in bad tuning, depending on why it occurred. better to reduce freq?
            else:
                if sn_tone > 1023:
                    sn_tone = 1023
                    print(" WARNING: Clipped SN tone to 1023 (ym_freq="+str(ym_freq)+" Hz)")



            if sn_tone < 1:
                sn_tone = 1
                print(" WARNING: Clipped SN tone to 1 (ym_freq="+str(ym_freq)+" Hz)")

            sn_freq = float(vgm_clock) / (2.0 * float(sn_tone) * 16.0 * float(LFSR_BIT))

            #print "ym_tone=" + str(ym_tone) + " ym_freq="+str(ym_freq) + " sn_tone="+str(sn_tone) + " sn_freq="+str(sn_freq)

            hz_err = sn_freq - ym_freq
            if hz_err > 2.0 or hz_err < -2.0:
                if ENABLE_VERBOSE:
                    print(" WARNING: Large error transposing tone! [" + str(hz_err) + " Hz ] ")

            return sn_tone

        #--------------------------------------------------------------
        # given a channel and tone value, output vgm command
        #--------------------------------------------------------------
        def output_sn_tone(channel, tone):
            #if tone & 16384:
            #    print " BIT BANGED TONE: " + str(tone)
            if ENABLE_SOFTWARE_BASS:
                #if tone > 4095:
                #    print " ERROR (output_sn_tone): tone > 4095"
                tone_mask = 127
            else: 
                if tone > 1023:
                    print(" ERROR (output_sn_tone): tone > 1023")
                tone_mask = 63

            if tone < 0:
                print(" ERROR (output_sn_tone): tone < 0")

            r_lo = 128 + (channel << 5) + (tone & 15)    # bit 4 clear for tone

            # If software bass is enabled, the output tone will have a flag set if "low frequency"
            if ENABLE_SOFTWARE_BASS:
                # check for "low frequency" flag
                if tone & 16384:
                    r_hi = ((tone & 1023) >> 4) & tone_mask
                    r_hi |= 64  # set bit 6 of the hi (DATA) byte
                else:
                    r_hi = (tone >> 4) & tone_mask
            else:
                r_hi = (tone >> 4) & tone_mask




            #if r_hi & 64:
            #    print "DEFINITELY BIT BANGED OUTPUT"

            vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
            vgm_stream.extend( struct.pack('B', r_lo) ) # LATCH TONE
            vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
            vgm_stream.extend( struct.pack('B', r_hi) ) # DATA TONE

            raw_stream.extend( struct.pack('B', r_lo) )
            raw_stream.extend( struct.pack('B', r_hi) )

        #--------------------------------------------------------------
        # output a noise tone on channel 3
        #--------------------------------------------------------------
        def output_sn_noise(tone):

            # We would not expect to see tones other than 3 (tuned periodic noise) or 4,5,6 (fixed frequency white noise)
            # so flag if so.
            if (tone != 4) and (tone != 3) and (tone != 5) and (tone != 6):
                print("WARNING: Detected unusual noise note - " + str(tone))

            r_lo = 128 + (3 << 5) + (tone & 15)

            vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
            vgm_stream.extend( struct.pack('B', r_lo) ) # LATCH TONE

            raw_stream.extend( struct.pack('B', r_lo) ) # LATCH TONE
        

        #--------------------------------------------------------------
        # given a channel and volume value, output vgm command
        #--------------------------------------------------------------
        def output_sn_volume(channel, volume):

            r_lo = 128 + (channel << 5) + 16 + (15 - (volume & 15))    # bit 4 set for volume, SN volumes are inverted

            vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
            vgm_stream.extend( struct.pack('B', r_lo) ) # LATCH VOLUME

            raw_stream.extend( struct.pack('B', r_lo) ) # LATCH VOLUME

        #--------------------------------------------------------------
        # YM stream pre-processing code
        #--------------------------------------------------------------
        channel_lof_a = 0
        channel_lof_b = 0
        channel_lof_c = 0
        channel_lof_multi2 = 0
        channel_lof_multi3 = 0
        envelope_count = 0

        for i in range(cnt):
            ym_tone_a = get_register_word(0) & 4095
            ym_tone_b = get_register_word(2) & 4095
            ym_tone_c = get_register_word(4) & 4095

            ym_freq_a = get_ym_frequency(ym_tone_a)
            ym_freq_b = get_ym_frequency(ym_tone_b)
            ym_freq_c = get_ym_frequency(ym_tone_c)

            # envelope attentuation mode flags
            ym_envelope_a = get_register_byte( 8) & 16
            ym_envelope_b = get_register_byte( 9) & 16
            ym_envelope_c = get_register_byte(10) & 16

            # low freq analysis
            lof_channels = 0
            if (ym_freq_a < sn_freq_lo):
                channel_lof_a += 1
                lof_channels += 1
            if (ym_freq_b < sn_freq_lo):
                channel_lof_b += 1
                lof_channels += 1                
            if (ym_freq_c < sn_freq_lo):
                channel_lof_c += 1
                lof_channels += 1  

            if lof_channels == 2:
                channel_lof_multi2 += 1    
            if lof_channels == 3:
                channel_lof_multi3 += 1    

            # envelope usage analysis
            if ym_envelope_a or ym_envelope_b or ym_envelope_c:
                envelope_count += 1

        print(" Song analysis over " + str(cnt) + " frames:")
        if envelope_count:
            print(" There were " + str(envelope_count) + " frames that used envelopes")
        else:
            print(" This tune does not use envelopes.")
        print(" ")
        print("  Channel A has " + str(channel_lof_a) + " low frequency tones")
        print("  Channel B has " + str(channel_lof_b) + " low frequency tones")
        print("  Channel C has " + str(channel_lof_c) + " low frequency tones")
        print("  There were " + str(channel_lof_multi2) + " frames where 2 channels were simultaneous low frequency tones")
        print("  There were " + str(channel_lof_multi3) + " frames where 3 channels were simultaneous low frequency tones")

        bass_channel_bias = 0 # a
        if channel_lof_b > channel_lof_a and channel_lof_b > channel_lof_c:
            bass_channel_bias = 1 # b
        if channel_lof_c > channel_lof_a and channel_lof_c > channel_lof_b:
            bass_channel_bias = 2 # b

        if (FORCE_BASS_CHANNEL >= 0):
            bass_channel_bias = FORCE_BASS_CHANNEL

        print("    Selecting channel " + str(bass_channel_bias) + " as the priority bass channel")
        print("---")


        # Initialise these outside of the main loop so that their state persists across frame
        # otherwise we can end up with data being incorrectly reset - particularly on the noise channel
        sn_attn_out = [0, 0, 0, 0]
        sn_tone_out = [0, 0, 0, 0]

        #--------------------------------------------------------------
        # YM stream processing code
        #--------------------------------------------------------------
        # Scan the YM stream one frame at a time
        for i in range(cnt):
            secs = int(i / frame_rate)
            mins = int(secs / 60)
            secs = secs % 60
            cycle = int(i % frame_rate)

            s = "Frame="
            s += '{:05d}'.format(i)+" "
            s += "("
            s += '{:02d}'.format(mins)+":"
            s += '{:02d}'.format(secs)+"."
            s += '{:02d}'.format(cycle)+")"



            #------------------------------------------------
            # Conversion Logic
            #------------------------------------------------

            if ENABLE_DEBUG:
                print("--- ")		

            #------------------------------------------------
            # extract the YM register values for this frame
            #------------------------------------------------

            # volume attenuation level (if bit 4 is clear)
            # we convert the 4-bit value to 5-bits so we're always working in higher precision
            ym_volume_a = (get_register_byte(8) & 15) << 1 
            ym_volume_b = (get_register_byte(9) & 15) << 1
            ym_volume_c = (get_register_byte(10) & 15) << 1

            # ensure the new 5-bit value occupies the full 5-bit range (0-31 instead of 0-30) by OR'ing bit 1 to bit 0
            # by duplicating the least significant bit
            ym_volume_a |= (ym_volume_a >> 1 & 1)
            ym_volume_b |= (ym_volume_b >> 1 & 1)
            ym_volume_c |= (ym_volume_c >> 1 & 1)




            # envelope attentuation mode flags
            ym_envelope_a = get_register_byte( 8) & 16
            ym_envelope_b = get_register_byte( 9) & 16
            ym_envelope_c = get_register_byte(10) & 16

            # Tone registers
            # Have to properly mask these registers
            # r1 bits 4-6 are used for TS info
            # r3 bits 4-5 are used for DD info
            ym_tone_a = get_register_word(0) & 4095
            ym_tone_b = get_register_word(2) & 4095
            ym_tone_c = get_register_word(4) & 4095

            # Noise register
            # R6 bits 5-6 are used for TP for TS setting
            ym_noise = get_register_byte(6) & 31

            # envelope frequency register
            ym_envelope_f = get_register_word(11)   

            # envelope shape register (YM format stores 255 if this register should not be updated this frame)
            ym_envelope_shape = get_register_byte(13)

            # mixer flag registers
            # output is on when mix bit is clear. 
            # we invert it though for easier code readibility 
            ym_mixer = get_register_byte(7)
            ym_mix_tone_a = (ym_mixer & (1<<0)) == 0
            ym_mix_tone_b = (ym_mixer & (1<<1)) == 0
            ym_mix_tone_c = (ym_mixer & (1<<2)) == 0

            ym_mix_noise_a = (ym_mixer & (1<<3)) == 0
            ym_mix_noise_b = (ym_mixer & (1<<4)) == 0
            ym_mix_noise_c = (ym_mixer & (1<<5)) == 0

            # oddity with "nd-ui.ym" requires this. data shows envelopes enabled on a channel, but tone mixer disabled. a wierd/useless combination. 
            if ENABLE_ENVELOPE_MIX_HACK:
                if ym_envelope_a:
                    ym_mix_tone_a = True
                if ym_envelope_b:
                    ym_mix_tone_b = True
                if ym_envelope_c:
                    ym_mix_tone_c = True


            if True:   # does not work
                if FILTER_CHANNEL_A:
                    ym_mix_tone_a = False
                    ym_mix_noise_a = False
                if FILTER_CHANNEL_B:
                    ym_mix_tone_b = False
                    ym_mix_noise_b = False
                if FILTER_CHANNEL_C:
                    ym_mix_tone_c = False
                    ym_mix_noise_c = False
                if FILTER_CHANNEL_N:
                    ym_mix_noise_a = False
                    ym_mix_noise_b = False
                    ym_mix_noise_c = False







 



            #--------------------------------------------------------------------
            # Process YM file-specific attributes (not YM2149 chip features)
            #--------------------------------------------------------------------
            ts_on = 0
            dd_on = 0

            if self.__header['nb_registers'] == 16:
                    
                # digi drums - in YM format, DD triggers are encoded into bits 4+5 of R3
                # only 1 DD can be triggered per frame, on a specific voice
                # TS = Timer Synth
                # DD = Digidrums
                # 

                # trigger flags for TS and DD
                # 2 bits where 00=No TS/DD 01=VoiceA 10=VoiceB 11=VoiceC
                ts_on = (get_register_byte(1) >> 4) & 3
                dd_on = (get_register_byte(3) >> 4) & 3

                #r1 bit b6 is only used if there is a TS running. If b6 is set, YM emulator must restart
                # the TIMER to first position (you must be VERY sound-chip specialist to hear the difference).

                if ts_on:
                    if ENABLE_DEBUG:
                        print(" ERROR: Timer Synth Trigger - Not handled yet")


                # timer/sample rate encodings
                # TC = Timer Count  (8-bits)
                # TP = Timer Prediv (3-bits)
                ts_tp = (get_register_byte(6) >> 5) & 7
                ts_tc = get_register_byte(14) & 255

                dd_tp = (get_register_byte(8) >> 5) & 7
                dd_tc = get_register_byte(15) & 255

    # 4bits volume value (vmax) for TS is stored in the 4 free bits of r5 (b7-b4)

                MFP_FREQ = 2457600
                MFP_TABLE = [ 1, 4, 10, 16, 50, 64, 100, 200]

                # Handle DD frequency
                dd_freq = 0
                if dd_on:

                    if ENABLE_DEBUG:
                        print("  dd_tp=" + str(dd_tp))
                        print("  dd_tc=" + str(dd_tc))

                    if dd_tc == 0:
                        print(" ERROR: Digidrum TC value is 0 - unexpected & unhandled")
                    else:             
                        dd_freq = int( (MFP_FREQ / MFP_TABLE[dd_tp]) / dd_tc )

                # Handle TS frequency
                ts_freq = 0
                if ts_on:
                    if ts_tc == 0:
                        print(" ERROR: Timer Synth TC value is 0 - unexpected & unhandled")
                    else:
                        ts_freq = int( (MFP_FREQ / MFP_TABLE[ts_tp]) / ts_tc )

                # If a DD is triggered on a voice, the volume register for that channel
                # should be interpreted as a 5-bit sample number rather than a volume


            



            #------------------------------------------------
            # show YM frame info
            #------------------------------------------------

            # pitch
            s += ", Tone ["
            s += " " + '{:6d}'.format( ym_tone_a )
            s += " " + '{:6d}'.format( ym_tone_b )
            s += " " + '{:6d}'.format( ym_tone_c )


            # noise
            #s += ", Noise"
            s += ", " + '{:3d}'.format( ym_noise )
            s += " ]"

            # volume
            s += ", Vol ["
            s += " " + '{:2d}'.format( ym_volume_a >> 1 )
            s += " " + '{:2d}'.format( ym_volume_b >> 1 )
            s += " " + '{:2d}'.format( ym_volume_c >> 1 )
            s += " ]"

            # mixer
            m = get_register_byte(7)

            # output is on when mix bit is clear
            s += ", Tone Mix ["
            s += " " + getregisterflag(m,0, "a", "-")
            s += " " + getregisterflag(m,1, "b", "-")
            s += " " + getregisterflag(m,2, "c", "-")
            s += " ]"

            s += ", Noise Mix ["
            s += " " + getregisterflag(m,3, "a", "-")
            s += " " + getregisterflag(m,4, "b", "-")
            s += " " + getregisterflag(m,5, "c", "-")
            s += " ]"
            


            # envelope
            s += ", Env ["
            s += " " + getregisterflag(get_register_byte( 8), 4, "-", "a")
            s += " " + getregisterflag(get_register_byte( 9), 4, "-", "b")
            s += " " + getregisterflag(get_register_byte(10), 4, "-", "c")
            s += " ]"

            # Envelope shape
            env_shapes = [ "\\___", "\\___", "\\___", "\\___", "/___", "/___", "/___", "/___", "\\\\\\\\", "\\___", "\\/\\/", "\\---", "////", "/---", "/\\/\\", "/___"]


            s += ", Env Shape ["
            if ym_envelope_shape != 255:
                s += " " + env_shapes[ym_envelope_shape & 15]
            else:
                s += " ----"
            #s += " " + getregisterflag(ym_envelope_shape,2, "----", " ATT")
            #s += " " + getregisterflag(ym_envelope_shape,1, "----", " ALT")
            #s += " " + getregisterflag(ym_envelope_shape,0, "----", "HOLD")
            s += " ]"

            # Envelope frequency - this is the frequency that the counters will update
            # and therefore the frequency that a CPU would have to simulate them for accurate sound
            if ym_envelope_f == 0:
                #print "WARNING: Envelope frequency is 0 - unexpected & unhandled"
                # It's ok, happens when no envelope being used
                ehz = 0
            else:
                ehz = (float(clock) / 8.0) / float(ym_envelope_f)
            
            s += ", Env Freq ["
            s += " " + '{:6d}'.format( ym_envelope_f ) + " (" + '{:9.2f}'.format( ehz ) + "Hz)"            
            s += " ]"


            # Digi drums extended info (not chip-related, YM format only)
            if dd_on:
                s += ", Digidrum ["
                s += " " + str(dd_on)
                s += " ]"

                # Sample ID is whatever is in the volume register for the channel
                s += ", Sample ["
                s += " " +  '{:2d}'.format(get_register_byte(7+dd_on))
                s += " ]"
            
                # Sample freq is whatever is in R14 (not sure what R15 is as DD2)
                s += ", Sample Freq ["
                s += " " +  '{:6d}'.format(dd_freq)
                s += " ]"

            # output YM frame data before we mess with it.
            if ENABLE_VERBOSE:
                print(s)  

            #---------------------------------------------------
            # Stats
            #---------------------------------------------------
            # calculate some additional variables
            ym_tone_a_max = max(ym_tone_a_max, ym_tone_a)
            ym_tone_b_max = max(ym_tone_b_max, ym_tone_b)
            ym_tone_c_max = max(ym_tone_c_max, ym_tone_c)

            ym_tone_a_min = min(ym_tone_a_min, ym_tone_a)
            ym_tone_b_min = min(ym_tone_b_min, ym_tone_b)
            ym_tone_c_min = min(ym_tone_c_min, ym_tone_c)

            ym_freq_a = get_ym_frequency(ym_tone_a)
            ym_freq_b = get_ym_frequency(ym_tone_b)
            ym_freq_c = get_ym_frequency(ym_tone_c)

            ym_env_freq_min = min(ym_env_freq_min, ehz)
            ym_env_freq_max = max(ym_env_freq_max, ehz)

            if dd_on:

                ym_dd_freq_min = min(ym_dd_freq_min, dd_freq)
                ym_dd_freq_max = max(ym_dd_freq_max, dd_freq)

 
            #------------------------------------------------
            # output VGM SN76489 equivalent data
            #------------------------------------------------



            # load the current tones & volumes
            # ym_volumes are 5 bit, so we must map to 4 bits.
            # SM: changed this so it isn't misleading us to think we're using a crappy shift scale for volume
            # sn_attn_out[] is always set further down after the various envelope & mix parameters have been processed
            sn_attn_out[0] = -1 #ym_volume_a >> 1
            sn_attn_out[1] = -1 #ym_volume_b >> 1
            sn_attn_out[2] = -1 #ym_volume_c >> 1
            sn_attn_out[3] = -1

            #--------------------------------------------------
            # Process noise mixers
            #--------------------------------------------------
            # determine how many channels have the noise mixer enabled
            # noise & tone mixers are separate
            noise_active = 0
            if ENABLE_NOISE:
                if (ym_mix_noise_a or ym_mix_noise_b or ym_mix_noise_c):

                    if ym_mix_noise_a:# and ym_volume_a > 0: # and not ym_mix_tone_a:
                        noise_active += 1
                    if ym_mix_noise_b:# and ym_volume_b > 0: # and not ym_mix_tone_b:
                        noise_active += 1
                    if ym_mix_noise_c: # and ym_volume_c > 0: # and not ym_mix_tone_c:
                        noise_active += 1

                    if ENABLE_DEBUG:
                        print("  Noise active on " + str(noise_active) + " channels, ym_noise=" + str(ym_noise))

                    if ENABLE_DEBUG:
                        # some debug code to see if there really are
                        # occasions where noise is active but volume for the channel is 0
                        # this is often because envelopes are being used
                        if ym_volume_a == 0 and ym_mix_noise_a:
                            print(" - Channel A has noise enabled but no channel volume")
                        if ym_volume_b == 0 and ym_mix_noise_b:
                            print(" - Channel B has noise enabled but no channel volume")
                        if ym_volume_c == 0 and ym_mix_noise_c:
                            print(" - Channel C has noise enabled but no channel volume")


            #--------------------------------------------------
            # Process tones 
            #--------------------------------------------------

            # rather than send data to exact channels we
            # create maps, this way we can track which YM tone channel maps to which SN tone channel, since we might switch some if bass tones are present
            channel_map_a = 0
            channel_map_b = 1
            channel_map_c = 2

            # next, take a look at the channel frequencies, if any are too low for the SN to play
            # we can swap one channel over to a periodic noise sound to simulate the bass frequency we need.
            # NOTE: since we are sharing noise channel on SN with periodic noise, the noise takes priority over bass, but we still do the bass
            # processing so that theres continuity of the bass effect (otherwise when we switch to a noise sound, channel 2 would become audible, but at a higher frequency)
            bass_active = False
            bass_channel = 0 # the channel that bass is active on (0=a 1=b 2=c)
            tone_sent = False

            lo_count = 0
            if ENABLE_BASS_TONES:

                if ym_mix_tone_a and (ym_freq_a < sn_freq_lo):
                    lo_count += 1
                if ym_mix_tone_b and (ym_freq_b < sn_freq_lo):
                    lo_count += 1
                if ym_mix_tone_c and (ym_freq_c < sn_freq_lo):
                    lo_count += 1

                if ENABLE_DEBUG:
                    print("  " + str(lo_count) + " channels detected out of SN frequency range, adjusting...")
                    if noise_active:
                        print("  - Bass tone disabled because noise is active, bass may be transposed.")



            if ENABLE_BASS_TONES and not noise_active:
                # if at least one channel is an out of range frequency
                # adjust for periodic noise bass
                if lo_count:
                    bass_active = True


                    # Find the channel with the lowest frequency
                    # And move it over to SN Periodic noise channel instead
                    
                    bass_channel = -1

                    # if ENABLE_BASS_BIAS is set, and more than one channel is competing for the bass channel, we'll bias to this channel, otherwise its channel A < B < C 
                    if ENABLE_BASS_BIAS and (lo_count > 1):
                        # we know the mix is active since lo_count only includes channels with mix on.
                        # so double check the selected bias channel is currently one of the actives bass frequencies and force that channel to be bass
                        if ym_mix_tone_a and bass_channel_bias == 0 and (ym_freq_a < sn_freq_lo):
                            bass_channel = bass_channel_bias
                        if ym_mix_tone_b and bass_channel_bias == 1 and (ym_freq_b < sn_freq_lo):
                            bass_channel = bass_channel_bias
                        if ym_mix_tone_c and bass_channel_bias == 2 and (ym_freq_c < sn_freq_lo):
                            bass_channel = bass_channel_bias

                    if bass_channel >= 0:
                        if ENABLE_DEBUG:
                            print(" Selected bass channel " + str(bass_channel) + " as the bias due to multiple bass tones")
                    else:
                        # only one channel is low frequency, find it.
                        # pick the bass channel
                        if ym_mix_tone_a and (ym_freq_a < sn_freq_lo): #(ym_freq_a <= ym_freq_b and ym_freq_a <= ym_freq_c):
                            bass_channel = 0
                        else:
                            if ym_mix_tone_b and (ym_freq_b < sn_freq_lo): #(ym_freq_b <= ym_freq_a and ym_freq_b <= ym_freq_c):
                                bass_channel = 1
                            else:
                                if ym_mix_tone_c and (ym_freq_c < sn_freq_lo): #(ym_freq_c <= ym_freq_a and ym_freq_c <= ym_freq_b):
                                    bass_channel = 2
                                else:
                                    print(" ERROR: no bass channel assigned - should not happen!")
                                    if not ym_mix_tone_a and not ym_mix_tone_b and not ym_mix_tone_c:
                                        print("  - Because No Tone Channels have Mix Enabled.")
                                    if ENABLE_DEBUG:
                                        print("   lo_count=" + str(lo_count) + " ym_freq_a="+str(ym_freq_a)+" ym_freq_b="+str(ym_freq_b)+" ym_freq_c="+str(ym_freq_c))


                    #if (FORCE_BASS_CHANNEL >= 0):
                    #    bass_channel = FORCE_BASS_CHANNEL
                    
                    # Swap channels according to bass preference
                    if bass_channel == 0:
                        # it's A
                        if ENABLE_DEBUG:
                            print("  Channel A -> Bass ")

                        channel_map_a = 2
                        channel_map_b = 1
                        channel_map_c = 0

                        sn_tone_out[0] = ym_to_sn(ym_tone_c)
                        sn_tone_out[1] = ym_to_sn(ym_tone_b)
                        sn_tone_out[2] = ym_to_sn(ym_tone_a, True)
                        tone_sent = True


                    else:
                        if bass_channel == 1:
                            # it's B
                            if ENABLE_DEBUG:
                                print("  Channel B -> Bass ")

                            channel_map_a = 0
                            channel_map_b = 2
                            channel_map_c = 1

                            sn_tone_out[0] = ym_to_sn(ym_tone_a)
                            sn_tone_out[1] = ym_to_sn(ym_tone_c)
                            sn_tone_out[2] = ym_to_sn(ym_tone_b, True)
                            tone_sent = True

                        else:
                            if bass_channel == 2:
                                # it's C  
                                if ENABLE_DEBUG:  
                                    print("  Channel C -> Bass ")

                                channel_map_a = 0
                                channel_map_b = 1
                                channel_map_c = 2

                                sn_tone_out[0] = ym_to_sn(ym_tone_a)
                                sn_tone_out[1] = ym_to_sn(ym_tone_b)
                                sn_tone_out[2] = ym_to_sn(ym_tone_c, True)
                                tone_sent = True
                            else:
                                # same as above
                                print(" ERROR2: no bass channel assigned - should not happen!")

                     

            # write tones without modification if no bass adjustment happened
            if not tone_sent:
                
                # tones get written anyway, if there's some low frequency tones detected, they'll get modded
                sn_tone_out[0] = ym_to_sn(ym_tone_a)
                sn_tone_out[1] = ym_to_sn(ym_tone_b)
                sn_tone_out[2] = ym_to_sn(ym_tone_c)



            #--------------------------------------------------
            # Process noise tones
            #--------------------------------------------------

            if noise_active:
                sn_noise_freq_0 = float(vgm_clock) / (32.0 * 16.0) # 15.6 Khz @ 4Mhz
                sn_noise_freq_1 = float(vgm_clock) / (32.0 * 32.0) #  7.8 Khz @ 4Mhz
                sn_noise_freq_2 = float(vgm_clock) / (32.0 * 64.0) #  3.9 Khz @ 4Mhz

                noise_freq = 0
                if ym_noise == 0:
                    print(" ERROR: Noise is enabled at frequency 0 - unexpected")
                    noise_freq = sn_noise_freq_0
                else:
                    noise_freq = float(clock) / (16.0 * ym_noise)

                    ym_noise_min = min(ym_noise, ym_noise_min)
                    ym_noise_max = max(ym_noise, ym_noise_max)

                #snf = float(vgm_clock) / (16.0 * ym_noise)
                
                if ENABLE_DEBUG:
                    print("   noise_freq=" + str(noise_freq) + "Hz")
                    print("   SN 0 = " + str(sn_noise_freq_0))
                    print("   SN 1 = " + str(sn_noise_freq_1))
                    print("   SN 2 = " + str(sn_noise_freq_2))
                
                # SN internal clock is 1/16 of external clock
                # white noise on the SN has 4 frequencies
                # 0 - clock/512  (7812) 32Hz (emit random output every 16 cycles)
                # 1 - clock/1024 (3906) 21Hz (emit random output every 32 cycles)
                # 2 - clock/2048 (1953) 18Hz (emit random output every 64 cycles)
                # 3 - tone 2 frequency
                # The SN uses the counter as usual,

                # for noise we are looking at the duty cycle rate that
                # the output is pulsed - this is the same as the YM chip:
                # clock / 16N

                # find the closest noise frequency on SN to match YM noise frequency
                def get_freq_dist(f1, f2):
                    d = f1 - f2
                    return math.sqrt(d*d)

                sn_noise = 0
                if ENABLE_NOISE_PITCH:
                    min_dist = 1<<31
                    dist = get_freq_dist(noise_freq, sn_noise_freq_0)
                    if ENABLE_DEBUG:
                        print("dist " + str(dist) + " min_dist " + str(min_dist))
                    if dist < min_dist:
                        if ENABLE_DEBUG:
                            print(" 0 " + str(dist))
                        min_dist = dist
                        sn_noise = 0
                    dist = get_freq_dist(noise_freq, sn_noise_freq_1)
                    if dist < min_dist:
                        if ENABLE_DEBUG:
                            print(" 1 " + str(dist))
                        min_dist = dist
                        sn_noise = 1
                    dist = get_freq_dist(noise_freq, sn_noise_freq_2)
                    if dist < min_dist:
                        if ENABLE_DEBUG:
                            print(" 2 " + str(dist))
                        min_dist = dist
                        sn_noise = 2
                    

                    if False:
                        if noise_freq > (sn_noise_freq_0 - ((sn_noise_freq_0-sn_noise_freq_1) * 0.75)):
                            sn_noise = 0
                        else:
                            if noise_freq > (sn_noise_freq_1 - ((sn_noise_freq_1-sn_noise_freq_2) * 0.75)):
                                sn_noise = 1
                            else:
                                sn_noise = 2

                if ENABLE_DEBUG:
                    print('   sn_noise = ' + str(sn_noise))


                sn_tone_out[3] = 4 + sn_noise # bit 2 selects White noise, 0/1/2 are fixed low frequency (16 cycle) (3 is the tuned white noise)
                # most tunes dont seem to change the noise frequency much
            else:
                if bass_active:
                    sn_tone_out[3] = 3 # Tuned Periodic noise

            # sanity check. was previously a bug.
            if sn_tone_out[3] == 0 and sn_attn_out[3] != 0:
                print("WARNING: Noise tone 0 - should not happen! attenuation=" + str(sn_attn_out[3]) )


            #--------------------------------------------------
            # Process volumes
            #--------------------------------------------------

            # update envelopes
            # apply volumes to mapped channels as per mixer, and envelopes
            # compute noise volume also, to include envelopes mix
            # do these in a loop based on envelope sampling rate (default 50Hz)


            #------------------------------------------------
            # envelope processing
            #------------------------------------------------

            # prior to envelope processing, we have the ym_volumes for each channel as output per frame
            # envelopes override these.

            # Each frame, we'll process output in 2 steps
            # 1. The tone & noise registers
            # 2. The attenuation registers
            # Attenuation can be sampled at a higher rate than 50Hz to enable sampling of envelopes/digi drums

            # count occurrences of frames that are using envelopes 
            if ym_envelope_a or ym_envelope_b or ym_envelope_c:
                ym_env_count += 1




            # emulate the YM envelope logic if required
            if (ENABLE_ENVELOPES):
                # process envelopes
                # first set the envelope frequency
                self.__ymenv.set_envelope_freq(get_register_byte(12), get_register_byte(11))

                # next set the envelope shape, but only if it is set in the YM stream
                # (since setting this register resets the envelope state)

                if (ym_envelope_shape != 255):
                    if ENABLE_DEBUG:
                        print('  setting envelope shape ' + format(ym_envelope_shape, '#004b'))
                    self.__ymenv.set_envelope_shape(ym_envelope_shape)

            # Now we sample the volume repeatedly at the rate given. This has the effect of simulating the envelopes at a better resolution.
            sample_rate = SAMPLE_RATE # 1 # 441 # / SAMPLE_RATE # 63 # 700Hz
            sample_interval = 882 / sample_rate
            for sample_loops in range(0,sample_rate):


                if (ENABLE_ENVELOPES):
                    # use the envelope volume if M is set for any channel
                    if ym_envelope_a:
                        ym_volume_a = self.__ymenv.get_envelope_volume()
                        if ENABLE_DEBUG:
                            print('  envelope on A')
                    if ym_envelope_b:
                        if ENABLE_DEBUG:
                            print('  envelope on B')
                        ym_volume_b = self.__ymenv.get_envelope_volume()
                    if ym_envelope_c:
                        if ENABLE_DEBUG:
                            print('  envelope on C')
                        ym_volume_c = self.__ymenv.get_envelope_volume()
                else:
                    # if envelopes are not enabled and we want to simulate envelopes, just use max volume
                    # it's not a great simulation, but prevents some audio being muted
                    if SIM_ENVELOPES:
                        if ym_envelope_a:
                            ym_volume_a = 31
                        if ym_envelope_b:
                            ym_volume_b = 31
                        if ym_envelope_c:
                            ym_volume_c = 31

                #ym_volume_a = 31
                #ym_mix_tone_a = True

                #------------------------------------------------
                # noise volume calculation
                # noise mixer is independent of tone mixer
                #------------------------------------------------

                # determine which channels have the noise mixer enabled
                # then calculate a volume which is the average level
                # nope - each channel mixer can contribute 1/3 of the overall noise mix
                # we do this work in normalized amplitude space for better precision
                noise_volume = 0
                if ENABLE_NOISE and noise_active:

                    noise_amplitude = 0.0
                    if ym_mix_noise_a:
                        noise_amplitude += get_ym_amplitude(ym_volume_a) * NOISE_MIX_SCALE
                    if ym_mix_noise_b:
                        noise_amplitude += get_ym_amplitude(ym_volume_b) * NOISE_MIX_SCALE
                    if ym_mix_noise_c:
                        noise_amplitude += get_ym_amplitude(ym_volume_c) * NOISE_MIX_SCALE

                    # average the noise amplitude based on number of active noise channels
                    #noise_amplitude /= noise_active
                    if (noise_amplitude > 1.0):
                        print("ERROR: Noise Amplitude is distorted > 1.0 " + str(noise_amplitude))

                    # scale the overall mix of the noise
                    # to reflect the fact that we have a dedicated channel on SN for noise
                    # whereas on YM noise is logically OR'd with the squarewave
                    #noise_amplitude *= 0.75 #NOISE_MIX_SCALE

                    # amplitude back to 5-bit volume
                    noise_volume = get_ym_volume(noise_amplitude)

                    

                    #print "  Noise Average from volume=" + str(nv) + ", new method average=" + str(noise_volume)
                    #noise_volume = 15



                #------------------------------------------------
                # Apply tone mixer settings (will mute channels if not enabled)
                #------------------------------------------------

                if not ym_mix_tone_a:
                    ym_volume_a = 0
                if not ym_mix_tone_b:
                    ym_volume_b = 0
                if not ym_mix_tone_c:
                    ym_volume_c = 0

                #------------------------------------------------
                # final output mix of volumes to SN (for tones, bass & noise)
                #------------------------------------------------



                # tone volumes first. we're converting the 5-bit YM volumes to 4-bit SN volumes.
                sn_attn_out[channel_map_a] = get_sn_volume(ym_volume_a)
                sn_attn_out[channel_map_b] = get_sn_volume(ym_volume_b)
                sn_attn_out[channel_map_c] = get_sn_volume(ym_volume_c)

                # then noise or bass
                if noise_active:
                    # active noise overrides bass
                    sn_attn_out[3] = get_sn_volume(noise_volume)
                    if bass_active:
                        sn_attn_out[2] = 0 # turn off tone2 while noise is playing if bass is active
                else:
                    # no noise active, so check if bass is active (since thats emulated on SN noise channel using tuned periodic noise)
                    if bass_active:
                        # no noise, just bass. turn off tone2, apply bass volume to channel 3  
                        # we need to determine the volume of the simulated bass so we can set the correct volume for the SN noise channel
                        if bass_channel == 0:
                            bass_volume = ym_volume_a
                        else:
                            if bass_channel == 1: # b
                                bass_volume = ym_volume_b
                            else:
                                if bass_channel == 2: # c
                                    bass_volume = ym_volume_c
                                else:
                                    print("ERROR: No bass channel set when setting bass volume.")

                        # output bass settings to SN
                        sn_attn_out[3] = get_sn_volume(bass_volume)
                        sn_attn_out[2] = 0 # turn off tone2 while bass effect is playing
                        
                    else:
                        # no noise, no bass, so just turn off noise channel
                        sn_attn_out[3] = 0


                # Double check that all attenuation channels have been processed correctly
                if (sn_attn_out[0] < 0 or sn_attn_out[1] < 0 or sn_attn_out[2] < 0 or sn_attn_out[3] < 0):
                    print("ERROR: Attenuation was not correctly applied to one of the channels, " + str(sn_attn_out))


                #-------------------------------------------------
                # output the final data to VGM
                #-------------------------------------------------
                if OPTIMIZE_VGM:
                    # only output register values that have changed since last frame
                    if sn_tone_out[0] != sn_tone_latch[0]:
                        sn_tone_latch[0] = sn_tone_out[0]
                        output_sn_tone(0, sn_tone_latch[0])

                    if sn_tone_out[1] != sn_tone_latch[1]:
                        sn_tone_latch[1] = sn_tone_out[1]              
                        output_sn_tone(1, sn_tone_latch[1])

                    if sn_tone_out[2] != sn_tone_latch[2]:
                        sn_tone_latch[2] = sn_tone_out[2]              
                        output_sn_tone(2, sn_tone_latch[2])

                    # for the noise channel, only output register writes
                    # if the noise tone has changed, so that we dont unnecessarily
                    # reset the LFSR
                    if sn_tone_out[3] != sn_tone_latch[3]:
                        sn_tone_latch[3] = sn_tone_out[3]
                        output_sn_noise(sn_tone_latch[3])

                    # volumes
                    if sn_attn_out[0] != sn_attn_latch[0]:
                        sn_attn_latch[0] = sn_attn_out[0]              
                        output_sn_volume(0, sn_attn_latch[0])
                        
                    if sn_attn_out[1] != sn_attn_latch[1]:
                        sn_attn_latch[1] = sn_attn_out[1]              
                        output_sn_volume(1, sn_attn_latch[1])

                    if sn_attn_out[2] != sn_attn_latch[2]:
                        sn_attn_latch[2] = sn_attn_out[2]              
                        output_sn_volume(2, sn_attn_latch[2])

                    if sn_attn_out[3] != sn_attn_latch[3]:
                        sn_attn_latch[3] = sn_attn_out[3]              
                        output_sn_volume(3, sn_attn_latch[3])

                else:                
                    # otherwise output ALL register writes
                    # decoder must delta check for changes in tone3 (noise) to ensure LFSR isn't always reset otherwise artifacts will appear
                    output_sn_tone(0, sn_tone_out[0])
                    output_sn_tone(1, sn_tone_out[1])
                    output_sn_tone(2, sn_tone_out[2])
                    output_sn_noise(sn_tone_out[3])

                    output_sn_volume(0, sn_attn_out[0])
                    output_sn_volume(1, sn_attn_out[1])
                    output_sn_volume(2, sn_attn_out[2])
                    output_sn_volume(3, sn_attn_out[3])

                # now output to vgm
                # so, for a higher res output we could output the volume here.
                if True:

                    if (ENABLE_ENVELOPES):
                        # update the envelope cpu emulation
                        self.__ymenv.tick( self.__header['chip_clock'] / (50*sample_rate) )
                    if sample_interval == 882:
                        vgm_stream.extend( struct.pack('B', 0x63) ) 
                    else:
                        vgm_stream.extend( struct.pack('B', 0x61) ) 
                        vgm_stream.extend( struct.pack('B', sample_interval % 256) ) 
                        vgm_stream.extend( struct.pack('B', sample_interval / 256) )                         

                else:
                    if False and ENABLE_ENVELOPES:

                        for n in range(882):
                            # update the envelope cpu emulation
                            self.__ymenv.tick( self.__header['chip_clock'] / 44100 )
                            vgm_stream.extend( struct.pack('B', 0x61) ) 
                            vgm_stream.extend( struct.pack('B', 0x01) ) 
                            vgm_stream.extend( struct.pack('B', 0x00) ) 
                    else:
                        if (ENABLE_ENVELOPES):
                            # update the envelope cpu emulation
                            self.__ymenv.tick( self.__header['chip_clock'] / 50 )
                        vgm_stream.extend( struct.pack('B', 0x63) ) # WAIT50, or 882 samples (44100/50), short for 0x61 0x72 0x03

        #--------------------------------------------
        # Information
        #--------------------------------------------
        print("")
        print("Info:")
        print("         Num Frames - " + str(self.__header['nb_frames']))
        print(" Channel A Hz range - " + str( get_ym_frequency(ym_tone_a_max) ) + "Hz to " + str( get_ym_frequency(ym_tone_a_min) ) + "Hz")
        print(" Channel B Hz range - " + str( get_ym_frequency(ym_tone_b_max) ) + "Hz to " + str( get_ym_frequency(ym_tone_b_min) ) + "Hz")
        print(" Channel C Hz range - " + str( get_ym_frequency(ym_tone_c_max) ) + "Hz to " + str( get_ym_frequency(ym_tone_c_min) ) + "Hz")
        print("      Num Digidrums - " + str(self.__header['nb_digidrums']))
        print("  Digidrum Hz range - " + str( ym_dd_freq_min ) + "Hz to " + str( ym_dd_freq_max ) + "Hz")
        print("   Enveloped Frames - " + str( ym_env_count ) + " (" + str( ym_env_count*100.0/self.__header['nb_frames'] ) + "%)")

        print("  Envelope Hz range - " + str( ym_env_freq_min ) + "Hz to " + str( ym_env_freq_max ) + "Hz")
        print("        Noise range - " + str( ym_noise_min ) + " to " + str( ym_noise_max ) + " ")


        #--------------------------------------------
        # Output the VGM
        #--------------------------------------------

        vgm_stream.extend( struct.pack('B', 0x66) ) # VGM END command
        vgm_stream_length = len(vgm_stream)		

        # build the GD3 data block
        gd3_data = bytearray()
        gd3_stream = bytearray()	
        gd3_stream_length = 0
        
        gd3_offset = 0

        STRIP_GD3 = False
        VGM_FREQUENCY = 44100
        if not STRIP_GD3: # disable for no GD3 tag
            # note that GD3 requires two-byte characters
            # title
            gd3_data.extend( self.__header['song_name'].encode("utf_16le") + b'\x00' + b'\x00') #title_eng' + b'\x00\x00') # title_eng
            gd3_data.extend( b'\x00\x00') # title_jap
            # game
            gd3_data.extend( self.__filename.encode("utf_16le") + b'\x00\x00') # game_eng
            gd3_data.extend( b'\x00\x00') # game_jap
            # console
            gd3_data.extend( 'YM2149F'.encode("utf_16le") + b'\x00\x00') # console_eng
            gd3_data.extend( b'\x00\x00') # console_jap
            # author
            gd3_data.extend( self.__header['author_name'].encode("utf_16le") + b'\x00\x00') # artist_eng
            gd3_data.extend( b'\x00\x00') # artist_jap
            # date
            gd3_data.extend( b'\x00\x00') # date
            # vgm creator
            gd3_data.extend( 'github.com/simondotm/ym2149f'.encode("utf_16le") + b'\x00\x00')# vgm_creator
            # notes
            gd3_data.extend( self.__header['song_comment'].encode("utf_16le") + b'\x00\x00') # notes
            
            gd3_stream.extend(b'Gd3 ')
            gd3_stream.extend(struct.pack('I', 0x100))				# GD3 version
            gd3_stream.extend(struct.pack('I', len(gd3_data)))		# GD3 length		
            gd3_stream.extend(gd3_data)		
            
            gd3_offset = (64-20) + vgm_stream_length
            gd3_stream_length = len(gd3_stream)
        else:
            print("   VGM Processing : GD3 tag was stripped")
        
        # build the full VGM output stream		
        vgm_data = bytearray()
        vgm_data.extend(b'Vgm ')    # VGM Magic number
        vgm_data.extend(struct.pack('I', 64 + vgm_stream_length + gd3_stream_length - 4))				# EoF offset
        vgm_data.extend(struct.pack('I', 0x00000151))		# Version
        vgm_data.extend(struct.pack('I', vgm_clock)) #self.metadata['sn76489_clock']))
        vgm_data.extend(struct.pack('I', 0))#self.metadata['ym2413_clock']))
        vgm_data.extend(struct.pack('I', gd3_offset))				# GD3 offset
        vgm_data.extend(struct.pack('I', int(cnt*VGM_FREQUENCY/50))) #self.metadata['total_samples']))				# total samples
        vgm_data.extend(struct.pack('I', 0)) #self.metadata['loop_offset']))				# loop offset
        vgm_data.extend(struct.pack('I', 0)) #self.metadata['loop_samples']))				# loop # samples
        vgm_data.extend(struct.pack('I', 50))#self.metadata['rate']))				# rate
        vgm_data.extend(struct.pack('H', 0x0003))	# 0x0003 for BBC configuration of SN76489 self.metadata['sn76489_feedback']))				# sn fb
        vgm_data.extend(struct.pack('B', LFSR_BIT)) #self.metadata['sn76489_shift_register_width']))				# SNW	
        vgm_data.extend(struct.pack('B', 0))				# SN Flags			
        vgm_data.extend(struct.pack('I', 0))#self.metadata['ym2612_clock']))		
        vgm_data.extend(struct.pack('I', 0))#self.metadata['ym2151_clock']))	
        vgm_data.extend(struct.pack('I', 12))				# VGM data offset
        vgm_data.extend(struct.pack('I', 0))				# SEGA PCM clock	
        vgm_data.extend(struct.pack('I', 0))				# SPCM interface	

        # attach the vgm data
        vgm_data.extend(vgm_stream)

        # attach the vgm gd3 tag if required
        if STRIP_GD3 == False:
            vgm_data.extend(gd3_stream)
        
        # write to output file
        vgm_file = open(vgm_filename, 'wb')
        vgm_file.write(vgm_data)
        vgm_file.close()
        
        print("   VGM Processing : Written " + str(int(len(vgm_data))) + " bytes, GD3 tag used " + str(gd3_stream_length) + " bytes")

        if ENABLE_BIN:
            # write an example SN data BIN format output file
            frame_size = 11 #16
            frame_total = len(raw_stream) / frame_size
            fh = open(  vgm_filename.rsplit( ".", 1 )[ 0 ] +".bin", 'wb')
    #        fh.write(raw_stream)

            frame_count = frame_total #16 # frame_total #33 # number of frames to package at a time
            #offs = 0
            for c in range(frame_total / frame_count):
                for r in range(frame_size):
                    rdata = bytearray()
                    for n in range(frame_count):
                        rdata.append(raw_stream[c*frame_count*frame_size + n*frame_size + r])
                    #offs += frame_size
                    fh.write(rdata)
    
            fh.close()

            # 16 bytes per frame
            # 16 frames per 256 byte page
            # unpack first frame on init
            # unpack remaining data
            # or 256 x 11 = 2816 bytes, 1 page per register = 256

            print("   BIN Processing : Written " + str(int(len(raw_stream))) + " bytes")


        # write a binary file out for the Arduino project, containing raw YM & SN register data
        if ARDUINO_BIN:


            header_block = bytearray()
            play_rate = self.__header['frames_rate']
            packet_count = self.__header['nb_frames']
            # emit the play rate & packet count          
            print("play rate is " + str(play_rate))
            header_block.append(struct.pack('B', play_rate & 0xff))
            header_block.append(struct.pack('B', packet_count & 0xff))		
            header_block.append(struct.pack('B', (packet_count >> 8) & 0xff))	

            print("    Num packets " + str(packet_count))
            duration = packet_count / play_rate
            duration_mm = int(duration / 60.0)
            duration_ss = int(duration % 60.0)
            print("    Song duration " + str(duration) + " seconds, " + str(duration_mm) + "m" + str(duration_ss) + "s")
            header_block.append(struct.pack('B', duration_mm))	# minutes		
            header_block.append(struct.pack('B', duration_ss))	# seconds

            # output the final byte stream
            output_block = bytearray()	

            # send header
            output_block.append(struct.pack('B', len(header_block)))
            output_block.extend(header_block)

            # send title
            title = self.__header['song_name']
            if len(title) > 254:
                title = title[:254]
            output_block.append(struct.pack('B', len(title) + 1))	# title string length
            output_block.extend(title)
            output_block.append(struct.pack('B', 0))				# zero terminator

            # send author
            author = self.__header['author_name']

            # use filename if no author listed
            if len(author) == 0:
                author = basename(vgm_filename)

            if len(author) > 254:
                author = author[:254]
            output_block.append(struct.pack('B', len(author) + 1))	# author string length
            output_block.extend(author)
            output_block.append(struct.pack('B', 0))				# zero terminator

            # now send the raw data
            print(packet_count)
            print(len(raw_stream)/11)

            for c in range(packet_count):
                # SN data first
                for r in range(11):
                    output_block.append(raw_stream[c*11 + r])
                # YM data next
                for r in range(14):
                    output_block.append(regs[r][c])
    
            fh = open( vgm_filename.rsplit( ".", 1 )[ 0 ] + ".bin", 'wb')
            fh.write(output_block)
            fh.close()

            print("   ARDUINO BIN Processing : Written " + str(int(len(output_block))) + " bytes")

        print("All done.")


def to_minsec(frames, frames_rate):
    secs = frames / frames_rate
    mins = secs / 60
    secs = secs % 60
    return (mins, secs)

#------------------------------------------------------------------------
# Main()
#------------------------------------------------------------------------

import argparse

# Determine if running as a script
if __name__ == '__main__':

    print("ym2sn.py : Convert Atari ST .YM files to SN76489 VGM music files")
    print("Written in 2019 by Simon Morris, https://github.com/simondotm/ym2149f")
    print("")

    epilog_string = "Notes:\n"
    epilog_string += " This tool does not support LHA compressed YM files, so\n"
    epilog_string += "  you must extract the archived YM file first.\n"

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_string)

    parser.add_argument("input", help="YM source file (must be extracted from within the original YM file) [input]")
    parser.add_argument("-o", "--output", metavar="<output>", help="write VGM file <output> (default is '[input].vgm')")
    parser.add_argument("-c", "--clock", type=float, default=4.0, metavar="<n>", help="Set target SN76489 clock rate to <n> Mhz, default: 4.0 (4Mhz)")
    parser.add_argument("-s", "--shift", type=int, default=15, metavar="<n>", help="Set target SN76489 LFSR bit to <n> 15 or 16, default: 15 (BBC Micro)")
    parser.add_argument("-r", "--rate", type=int, default=50, metavar="<n>", help="Set envelope sample rate to <n> Hz (must be divisble by 50!), default: 50Hz")
    parser.add_argument("-f", "--filter", default='', metavar="<s>", help="Filter channels A,B,C,N <s> is a string, eg. -f AB")
    parser.add_argument("-b", "--bass", help="Enable software bass (output VGM will not be hardware compliant for bass tones)", default=False, action="store_true")
    parser.add_argument("-t", "--attenuation", help="Force SN76489 attentuation mapping (volumes scaled from YM dB to SN dB) [Experimental]", default=False, action="store_true")
    parser.add_argument("-n", "--noenvelopes", help="Disable envelope simulation", default=False, action="store_true")
    parser.add_argument("-l", "--loops", help="Export two VGM files, one for intro and one for looping section", default=False, action="store_true")
    parser.add_argument("-a", "--arduino", help="Export Arduino BIN file", default=False, action="store_true")
    parser.add_argument("-v", "--verbose", help="Enable verbose mode", action="store_true")
    parser.add_argument("-d", "--debug", help="Enable debug mode", action="store_true")
    args = parser.parse_args()


    src = args.input
    dst = args.output
    if dst == None:
        dst = os.path.splitext(src)[0] + ".vgm"

    print("output file=" + dst)

    # check for missing files
    if not os.path.isfile(src):
        print("ERROR: File '" + src + "' not found")
        sys.exit()

    # Set options
    ENABLE_VERBOSE = args.verbose
    ENABLE_DEBUG = args.debug
    SN_CLOCK = int(args.clock * 1000000.0)
    LFSR_BIT = args.shift
    ENABLE_ATTENUATION = args.attenuation

    # check for Arduino output mode
    if args.arduino:
        ARDUINO_BIN = True
        OPTIMIZE_VGM = False # we have to turn off the VGM optimization otherwise not all SN register is output.

    if (args.noenvelopes):
        ENABLE_ENVELOPES = False
        SIM_ENVELOPES = False

    if (args.bass):
        ENABLE_SOFTWARE_BASS = True
        TONE_RANGE = 4095
        ENABLE_BASS_TONES = False

    if (args.rate % 50) != 0:
        print("ERROR: Envelope sample rate must be divisible by 50Hz.")
        sys.exit()

    SAMPLE_RATE = int(args.rate / 50)
    
    if (len(args.filter) != 0):
        FILTER_CHANNEL_A = str.find(args.filter, 'A') != -1
        FILTER_CHANNEL_B = str.find(args.filter, 'B') != -1
        FILTER_CHANNEL_C = str.find(args.filter, 'C') != -1
        FILTER_CHANNEL_N = str.find(args.filter, 'N') != -1


    header = None
    data = None


    ym_filename = src

    if args.loops:
        print("Exporting loop intro...")
        YmReader.OUTPUT_LOOP_INTRO = True

    with open(ym_filename, "rb") as fd:
        ym = YmReader(fd)
        fd.close()  
        
        ym.dump_header()
        header = ym.get_header()
        data = ym.get_data()
            

        print("Loaded YM File.")

        vgm_filename = dst[:dst.rfind('.')]
        if YmReader.OUTPUT_LOOP_INTRO:
            vgm_filename += ".intro"

        vgm_filename += ".vgm"
        print("Output file: '" + vgm_filename + "'")
        ym.write_vgm( vgm_filename )

    # export the looping section
    if args.loops:
        print("Exporting loop section...")

        YmReader.OUTPUT_LOOP_INTRO = False
        YmReader.OUTPUT_LOOP_SECTION = True
        with open(ym_filename, "rb") as fd:
            ym = YmReader(fd)
            fd.close()  
            
            ym.dump_header()
            header = ym.get_header()
            data = ym.get_data()
                

            print("Loaded YM File.")
            vgm_filename = ym_filename[:ym_filename.rfind('.')] + ".loop.vgm"
            print(vgm_filename)
            ym.write_vgm( vgm_filename )





    song_min, song_sec = to_minsec(header['nb_frames'], header['frames_rate'])
    print("")
    #print data

    if False:
        with open(sys.argv[1], 'w') as fd:
            time.sleep(2) # Wait for Arduino reset
            frame_t = time.time()
            for i in range(header['nb_frames']):
                # Substract time spent in code
                time.sleep(1./header['frames_rate'] - (time.time() - frame_t))
                frame_t = time.time()
                fd.write(data[i])
                fd.flush()
                i+= 1

                # Additionnal processing
                cur_min, cur_sec = to_minsec(i, header['frames_rate'])
                sys.stdout.write(
                    "\x1b[2K\rPlaying {0:02}:{1:02} / {2:02}:{3:02}".format(
                    cur_min, cur_sec, song_min, song_sec))
                sys.stdout.flush()

            # Clear YM2149 registers
            fd.write('\x00'*16)
            fd.flush()

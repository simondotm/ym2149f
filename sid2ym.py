#!/usr/bin/env python
# sid2ym.py
# SID to .YM files (YM2149 sound chip) music file format conversion utility
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
import subprocess
from os.path import basename
from string import Formatter


ENABLE_DEBUG = False        # enable this to have ALL the info spitting out. This is more than ENABLE_VERBOSE
ENABLE_VERBOSE = False

# SID
#
# Registers
# Voice 1
# 00 - Frequency Lo - 8 bits
# 01 - Frequency Hi - 8 bits
# 02 - Pulse Width Lo - 8 bits
# 03 - Pulse Width Hi - 4 bits
# 04 - Control - 8 bits [NOISE|SQUARE|SAW|TRI|TEST|RINGMOD|SYNC|GATE]
# 05 - Attack/Decay - 8 bits [ATTACK(4)|DECAY(4)]
# 06 - Sustain/Release - 8 bits [SUSTAIN(4)|RELEASE(4)]
# Voice 2
# 07-13
# Voice 3
# 14-20
# Filter
# 21 - FC LO - 3 bits
# 22 - FC HI - 8 bits
# 34 - Res/Filt [RES(4)|FILT EX|FILT(3)]
# 35 - Mode/Vol [3OFF|HP|BP|LP|VOL(4)]

# Fout = (Fn * FCLK / 16777216) Hz

# Control register
# 

# ADSR
attack_table = [ 2, 8, 16, 24, 38, 56, 68, 80, 100, 250, 500, 800, 1000, 3000, 5000, 8000]

# 0
# 1
# 2
# 3

SID_CLOCK = 1000000
YM_CLOCK = 2000000

# YM REGISTERS

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

# Pitch oscillation frequency is (Clock / 16 x TP) [TP is tone pitch]
# Noise frequency is (Clock / 16 x NP) [NP is noise pitch R6]
# Noise and/or Tone is output when Mixer flag is set to 0 for a channel
# Mode [M] is 1, then envelope drives volume, when 0, the 4 bit value drives attenuation
# Envelope repetition frequency (fE) is (Clock / 256 x EP) [EP is envelope frequency]
# Envelope shape has 10 valid settings - see data sheet for details

# SID internals http://forum.6502.org/viewtopic.php?f=8&t=4150
# SID internals from Bob Yannes Interview http://sid.kubarth.com/articles/interview_bob_yannes.html


# Class to manage simulated state of a SID voice based on register settings
class SidVoice(object):

    EnvelopeCycle_Inactive = 0
    EnvelopeCycle_Attack = 1
    EnvelopeCycle_Decay = 2
    EnvelopeCycle_Sustain = 3
    EnvelopeCycle_Release = 4

    def __init__(self, voiceid):
        self.__voiceid = voiceid
        self.reset()
        print("Sid voice " + str(voiceid) + " initialised.")

    def reset(self):
        ### internals

        # oscillator - 24-bit phase accumulator
        self.__accumulator = 0

        # waveform output level - 12-bits
        self.__waveform_level = 0

        # envelope counter - 16-bit counter for envelope period
        self.__envelope_counter = 0

        # envelope level - 24-bit value where top 8-bits are the 0-255 output level
        self.__envelope_level = 0
        # envelope Cycle - 0=inactive, 1=attack, 2=decay, 3=sustain, 4=release
        self.__envelope_cycle = SidVoice.EnvelopeCycle_Inactive

        ### registers
        self.__gate = False
        self.set_frequency(0, 0)
        self.set_pulsewidth(0, 0)
        self.set_control(0)
        self.set_envelope(0, 0)




    # registers 0,1 - frequency (16-bits)
    def set_frequency(self, lo, hi):
        f = lo + (hi * 256)
        self.__frequency = f

    # registers 2,3 - pulse width (12-bits)
    def set_pulsewidth(self, lo, hi):
        p = lo + (hi * 256)
        self.__pulsewidth = p

    def voiceId(self):
        return "V" + str(self.__voiceid) + " "

    # register 4 - control (8-bits)
    def set_control(self, c):

        last_gate = self.__gate

        # waveform generators enable flags
        self.__noise = ((c & 128) == 128)
        self.__pulse = ((c & 64) == 64)
        self.__triangle = ((c & 32) == 32)
        self.__sawtooth = ((c & 16) == 16)
        # control flags
        self.__test = ((c & 8) == 8)
        self.__ringmod = ((c & 4) == 4)
        self.__sync = ((c & 2) == 2)
        self.__gate = ((c & 1) == 1)

        # handle gate trigger state change
        if self.__gate != last_gate:

            if self.__gate:
                # gate on - attack cycle triggered 
                self.__envelope_cycle = SidVoice.EnvelopeCycle_Attack
            else:
                # gate cleareroff - release cycle triggered
                self.__envelope_cycle = SidVoice.EnvelopeCycle_Release

        s = ""
        s += "NOIS " if self.__noise else "---- "
        s += "PULS " if self.__pulse else "---- "
        s += "TRIN " if self.__triangle else "---- "
        s += "SAWT " if self.__sawtooth else "---- "
        s += "TEST " if self.__test else "---- "
        s += "SYNC " if self.__sync else "---- "
        s += "GATE " if self.__gate else "---- "

        print(self.voiceId() + "CONTROL set to: " + s)

    # register 5,6 - envelope (4x 4-bits)
    def set_envelope(self, r1, r2):
        self.__attack = (r1 >> 4) & 15
        self.__decay = (r1 & 15)
        self.__sustain = (r2 >> 4) & 15
        self.__release = (r2 & 15)
        print(self.voiceId() + "ADSR set to A="+str(self.__attack)+", D="+str(self.__decay)+", S="+str(self.__sustain)+", R="+str(self.__release))


    # get the current envelope level / amplitude for this voice (0-255)
    def get_envelope_level(self):
        return (self.__envelope_level >> 16) & 255

    def get_waveform_level(self):
        return self.__waveform_level

    # advance clock where t is 1/SID_CLOCK
    def tick(self, t):

        self.__accumulator += int(t * self.__frequency)

        # calculate the waveform D/A output (12-bit DAC)
        # sawtooth is the top 12 bits of the accumulator
        sawtooth_level = self.__accumulator >> 12

        # pulse output is the top 12 bits of the accumulator matching the pulsewidth register
        pulse_level = 4095 if ((self.__accumulator >>12 ) == self.__pulsewidth) else 0

        # triangle output is the top 12 bits, where the low 11 bits of this are inverted by the top bit, then shifted left
        triangle_invert = 2047 if (self.__accumulator & 8388608) else 0
        triangle_level = (((self.__accumulator >> 4) ^ triangle_invert) << 1) & 4095

        sawtooth_level = sawtooth_level if self.__sawtooth else 0
        pulse_level = pulse_level if self.__pulse else 0
        triangle_level = triangle_level if self.__triangle else 0

        # waveform generator outputs are AND'ed together
        self.__waveform_level = sawtooth_level & pulse_level & triangle_level

        #----------------------------
        # envelope generation
        #----------------------------

        # gate bit set to one triggers the ADSR cycle
        # attack phase rises from 0-255 at the ms rate specified by attack register
        # decay phase moves from 255 to the sustain register level
        # sustain level holds until gate bit is cleared
        # release phases moves from sustain level to 0 at the rate specified by release register
        # register can be changed during each phase, but only take effect if new value is possible depending on ramp up/ramp down mode
        # gate bit can be cleared at any time to trigger release phase, even if ads phase incomplete
        # if gate bit is set before release phase has completed, the envelope generator continues attack phase from current setting

        # these tables are mappings of register values to ms/step
        attack_table = [ 2, 8, 16, 24, 38, 56, 68, 80, 100, 250, 500, 800, 1000, 3000, 5000, 8000 ]
        decayrelease_table = [ 6, 24, 48, 72, 114, 168, 204, 240, 300, 750, 1500, 2400, 3000, 9000, 15000, 24000 ]
        sustain_table = [ 0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff ]

        # envelope process
        # see also https://sourceforge.net/p/vice-emu/code/HEAD/tree/trunk/vice/src/resid/envelope.cc

        precision = (2 ** 31)
        attack_rate = int(precision / (attack_table[self.__attack] * SID_CLOCK / 1000))
        decay_rate = int(precision / (decayrelease_table[self.__decay] * SID_CLOCK / 1000))
        release_rate = int(precision / (decayrelease_table[self.__release] * SID_CLOCK / 1000))
        sustain_target = sustain_table[self.__sustain] << 24

        # iterate the ADSR logic for each tick. Suboptimal for now.
        #iteration_count = t
        #if (self.__envelope_cycle == SidVoice.EnvelopeCycle_Attack) and ((self.__envelope_counter + attack_rate*t) <= precision):
        #    t = 1
        #elif (self.__envelope_cycle == SidVoice.EnvelopeCycle_Decay) and ((self.__envelope_counter - decay_rate*t) > 0):
        #    t = 1

        for n in range(t):
            if self.__envelope_cycle == SidVoice.EnvelopeCycle_Inactive:
                # nothing to do
                break
            # attack cycle
            elif self.__envelope_cycle == SidVoice.EnvelopeCycle_Attack:
                self.__envelope_counter += attack_rate
                self.__envelope_level = self.__envelope_counter >> 24
                if self.__envelope_level >= 255:
                    self.__envelope_level = 255
                    self.__envelope_cycle = SidVoice.EnvelopeCycle_Decay
            # decay cycle
            elif self.__envelope_cycle == SidVoice.EnvelopeCycle_Decay:
                self.__envelope_counter -= decay_rate
                self.__envelope_level = self.__envelope_counter >> 24
                if self.__envelope_level <= sustain_target:
                    self.__envelope_level = sustain_target 
                    self.__envelope_cycle = SidVoice.EnvelopeCycle_Sustain
            elif self.__envelope_cycle == SidVoice.EnvelopeCycle_Sustain:
                # sustain cycle
                # nothing to do
                # possibly check if sustain register has changed
                # cant change in this loop, so break
                break
                
            elif self.__envelope_cycle == SidVoice.EnvelopeCycle_Release:
                # release cycle
                self.__envelope_counter -= release_rate
                self.__envelope_level = self.__envelope_counter >> 24
                if self.__envelope_level <= 0:
                    self.__envelope_level = 0
                    self.__envelope_cycle = SidVoice.EnvelopeCycle_Inactive
                    break



 


        



# Class to manage simulated state of a SID chip
# 3 Voices are indexed as 0/1/2
class SidState(object):

    
    def __init__(self):
        print("Sid Emulator!")
        self.reset()

    def reset(self):
        #self.__registers[36] = [0,]
        self.__voices = [ SidVoice(1), SidVoice(2), SidVoice(3) ]

    def get_voice(self, voice):
        return self.__voices[voice]

    def tick(self, t):
        for voice in self.__voices:
            voice.tick(t)


    


class SidReader(object):


    def __init__(self, fd):


        print("Parsing SID DUMP file...")

        self.__fd = fd
        self.__filename = fd.name
        self.__filesize = os.path.getsize(fd.name) 

        self.__sid = SidState()

        self.read_dump()


    def read_dump(self):

        content = self.__fd.readlines()
        content = [x.strip() for x in content] 
        
        # Format
        # | Frame | Freq Note/Abs WF ADSR Pul | Freq Note/Abs WF ADSR Pul | Freq Note/Abs WF ADSR Pul | FCut RC Typ V |
        

        #--------------------------------------------------------------
        # return frequency in hz of a given SID tone/noise pitch
        #--------------------------------------------------------------
        def get_sid_frequency(v):
            # (Fn * FCLK / 16777216)
            if v < 1:
                v = 1
            return float(v) * float(SID_CLOCK) / 16777216.0

        #--------------------------------------------------------------
        # return frequency in hz of a given YM tone/noise pitch
        #--------------------------------------------------------------
        def get_ym_frequency(v):
            if v < 1:
                v = 1
            ym_freq = (float(YM_CLOCK) / 16.0) / float(v)
            return ym_freq

        #--------------------------------------------------------------
        # return YM tone from given frequency in hz
        #--------------------------------------------------------------
        def frequency_to_ym_tone(f):
            # f = (Clock / 16 x TP)
            tone = int( float(YM_CLOCK) / (float(f) * 16.0) )
            if (tone < 0):
                tone = 0
            if (tone > 4095):
                tone = 4095
                print("WARNING: Tone clipped to 4095")
            return int(tone)

        #--------------------------------------------------------------
        # return YM tone from given SID tone
        #--------------------------------------------------------------

        def sid_tone_to_ym_tone(v):
            f = get_sid_frequency(v)
            if (f < 30.0):
                print("SID frequency " + str(f) + "hz (tone=" + str(v) + ") too low for YM")

            t = frequency_to_ym_tone(f)
            yf = get_ym_frequency(t)

            if (v > 0):
                print("SID tone=" + str(v) + ", SID freq=" + str(f) + ", YM tone=" + str(t) + ", YM freq=" + str(yf))


            return t


        def parse_voice(channel):

            # replace periods with zero's
            # this might not be a good idea since we will end up writing registers that do not need to be written
            def hex2int(s):
                s = s.replace(".", "0")
                return int(s, 16)
            def isSet(s):
                return not "." in s

            # 16-bits frequency
            freq = channel[1:5]
            channel = channel[5:]

            # note - ignored
            note = channel[1:9]
            channel = channel[9:]

            # 8-bits control
            wf = channel[1:3]
            channel = channel[3:]
            
            # 16-bits ADSR
            adsr = channel[:5]
            channel = channel[5:]

            # 12-bits Pulse width
            pul = channel[1:4]


            # convert to data

            #freq_data = hex2int(freq_data)
            #wf_data = hex2int(wf_data)
            #adsr_data = hex2int(adsr_data)
            #pul_data = hex2int(pul_data)

            data = {}

            if isSet(freq):
                data["freq"] = hex2int(freq)

            if isSet(wf):
                data["wf"] = hex2int(wf)

            if isSet(adsr):
                data["adsr"] = hex2int(adsr)

            if isSet(pul):
                data["pul"] = hex2int(pul)


            print(data)
            return data


        # 16 register sets on the YM file
        self.__regs = []
        for i in range(16):
            self.__regs.append( bytearray() )            

        # 16 YM registers per frame

        #            PA--  PB--  PC--  NF MX VA VB VC ENVF- ES XX XX
        registers = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

        # get the virtual voices
        sid_voice1 = self.__sid.get_voice(0)
        sid_voice2 = self.__sid.get_voice(1)
        sid_voice3 = self.__sid.get_voice(2)

        # parse the SID register dump
        header = True
        for x in content:

            
            if not header:
                # split the frame text into text segments
                frame = x.split("|")

                frameId = int(frame[1])

                # test early out, 10seconds 
                if (frameId > 50*10):
                    break

                print("-------------------------------------------")
                print("Frame #" + str(frameId))
                print("| Frame | Freq Note/Abs WF ADSR Pul | Freq Note/Abs WF ADSR Pul | Freq Note/Abs WF ADSR Pul | FCut RC Typ V |")
                print(x)
                print("")

                # decode register data from each voice segment
                voice1 = parse_voice(frame[2]) # voice1
                voice2 = parse_voice(frame[3]) # voice2
                voice3 = parse_voice(frame[4]) # voice3

                # channel A
                
                # tones
                if "freq" in voice1: 
                    tone1 = sid_tone_to_ym_tone( voice1["freq"] )
                    registers[0] = tone1 & 255
                    registers[1] = (tone1 >> 8) & 255

                if "freq" in voice2: 
                    tone2 = sid_tone_to_ym_tone( voice2["freq"] )
                    registers[2] = tone2 & 255
                    registers[3] = (tone2 >> 8) & 255

                if "freq" in voice3: 
                    tone3 = sid_tone_to_ym_tone( voice3["freq"] )
                    registers[4] = tone3 & 255
                    registers[5] = (tone3 >> 8) & 255

                if "wf" in voice1:
                    sid_voice1.set_control( voice1["wf"] )

                if "wf" in voice2:
                    sid_voice2.set_control( voice2["wf"] )

                if "wf" in voice3:
                    sid_voice3.set_control( voice3["wf"] )

                if "adsr" in voice1:
                    sid_voice1.set_envelope( voice1["adsr"] >> 8, voice1["adsr"] & 255 )

                if "adsr" in voice2:
                    sid_voice2.set_envelope( voice2["adsr"] >> 8, voice2["adsr"] & 255 )

                if "adsr" in voice3:
                    sid_voice3.set_envelope( voice3["adsr"] >> 8, voice3["adsr"] & 255 )


                # volumes
                registers[8] = sid_voice1.get_envelope_level() >> 4 
                registers[9] = sid_voice2.get_envelope_level() >> 4 
                registers[10] = sid_voice3.get_envelope_level() >> 4 

                if True:
                    registers[8] = 15 
                    registers[9] = 15 
                    registers[10] = 15 

                registers[7] = 8+16+32 # only tones, no noise

                ticks = int(SID_CLOCK / 50)
                self.__sid.tick( ticks )



                #print(frame)
                for i in range(16):
                    self.__regs[i].append(registers[i])

            if ("+-------+" in x):
                header = False




    def write_ym(self, ym_filename):

        nb_frames = len(self.__regs[0])

        # build the full VGM output stream		
        ym_data = bytearray()
        ym_data.extend(b'YM6!')            # YM version
        ym_data.extend(b'LeOnArD!')         # check_string
        ym_data.extend(struct.pack('>I', nb_frames))	# nb_frames
        ym_data.extend(struct.pack('>I', 1))	# song_attributes
        ym_data.extend(struct.pack('>H', 0)) # nb_digidrums
        ym_data.extend(struct.pack('>I', YM_CLOCK)) # chip_clock
        ym_data.extend(struct.pack('>H', 50))	# frames_rate
        ym_data.extend(struct.pack('>I', 0)) # loop_frame
        ym_data.extend(struct.pack('>H', 0)) # extra_data
        ym_data.extend(b'name\0')            # Song name
        ym_data.extend(b'author\0')            # Author name
        ym_data.extend(b'comment\0')            # Song comment

        # YM6 requires 16 register sets
        for i in range(16):
            ym_data.extend(self.__regs[i])

        ym_data.extend(b'End!')            # EOF token


        # write to output file
        ym_file = open(ym_filename, 'wb')
        ym_file.write(ym_data)
        ym_file.close()

        print("All done.")

#------------------------------------------------------------------------
# Main()
#------------------------------------------------------------------------

import argparse

# Determine if running as a script
if __name__ == '__main__':

    print("sid2ym.py : Convert C64 SID dump files to Atari ST .YM files")
    print("Written in 2019 by Simon Morris, https://github.com/simondotm/ym2149f")
    print("")

    epilog_string = "Notes:\n"
    epilog_string += " This tool parses files output by SidDump.exe,\n"
    epilog_string += "  it does not parse .sid files directly.\n"

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_string)

    parser.add_argument("input", help="SID source file [input]")
    parser.add_argument("-o", "--output", metavar="<output>", help="write YM file <output> (default is '[input].ym')")
    parser.add_argument("-v", "--verbose", help="Enable verbose mode", action="store_true")
    parser.add_argument("-d", "--debug", help="Enable debug mode", action="store_true")
    args = parser.parse_args()


    src = args.input
    dst = args.output
    if dst == None:
        dst = os.path.splitext(src)[0] + ".ym"

    print("output file=" + dst)

    # check for missing files
    if not os.path.isfile(src):
        print("ERROR: File '" + src + "' not found")
        sys.exit()

    # Set options
    ENABLE_VERBOSE = args.verbose
    ENABLE_DEBUG = args.debug


    #subprocess.check_output(['siddump', '-l'])

    with open(src, "r") as fd:
        sidReader = SidReader(fd)
        fd.close()  
        
        print("Loaded SID File.")
        print("Output file: '" + dst + "'")
        sidReader.write_ym( dst )

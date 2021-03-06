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
from timeit import default_timer as timer

FIXED_LENGTH = 0 #50*10 #0 #50* 60
ENABLE_ADSR = True #False

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
#attack_table = [ 2, 8, 16, 24, 38, 56, 68, 80, 100, 250, 500, 800, 1000, 3000, 5000, 8000]

# 0
# 1
# 2
# 3

# https://codebase64.org/doku.php?id=base:cpu_clocking
# https://dustlayer.com/c64-architecture/2013/5/7/hardware-basics-part-1-tick-tock-know-your-clock

# 7.88MHz (PAL) respectively 8.18MHz (NTSC)
#NTSC_CLOCK = 7881984 
#PAL_CLOCK = 8181816 

SID_NTSC_CLOCK = 1022727 # 14318180 / 14
SID_PAL_CLOCK = 985248 # 17734475 / 18

SID_CLOCK = SID_PAL_CLOCK # set to SID_NTSC_CLOCK or SID_PAL_CLOCK
YM_CLOCK = 2000000
YM_RATE = 50 if SID_CLOCK == SID_PAL_CLOCK else 60

# TODO:
# linear-to-logarithmic volume ramp for YM
# check ADSR calcs are accurate
# what does TEST do?
# sampling rate
# wierd beeps
# odd tuning
# support noise
# waveform sim?


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


class Stats(object):

    def __init__(self):
        
        self.total_hz_error = 0.0
        self.worst_hz_error = 0.0
        self.total_large_error_tones = 0
        self.clipped_tones = 0
        self.total_frames = 0
        self.max_ym_tone = 0
        self.min_ym_tone = 65535
        self.uses_pulse_wave = 0
        self.uses_triangle_wave = 0
        self.uses_sawtooth_wave = 0
        self.uses_noise_wave = 0
        self.uses_test_bit = 0
        self.uses_sync_bit = 0
        self.uses_ringmod_bit = 0
        self.control_register_updates = 0
        self.pulsewidth_register_updates = 0
        self.min_sid_pulsewidth = 65536
        self.max_sid_pulsewidth = 0

    def report(self):
        print("")
        print("----------")
        print("Statistics")
        print("----------")
        print("Total frames: " + str(self.total_frames))
        print("Total Hz Error is " + str(self.total_hz_error))
        print("Worst Hz Error is " + str(self.worst_hz_error))
        print("Total tones with large Hz error: " + str(self.total_large_error_tones))
        print("Min YM Freq: " + str(self.max_ym_tone) + ", (" + str(get_ym_frequency(self.max_ym_tone)) + "Hz)" )
        print("Max YM Freq: " + str(self.min_ym_tone) + ", (" + str(get_ym_frequency(self.min_ym_tone)) + "Hz)" )
        print("Total clipped YM tones (>12-bit): " + str(self.clipped_tones))
        print("")
        print("SID Info")
        print("--------")
        print("Control register updates: " + str(self.control_register_updates))
        print("  Triangle wave settings: " + str(self.uses_triangle_wave))
        print("     Pulse wave settings: " + str(self.uses_pulse_wave))
        print("  Sawtooth wave settings: " + str(self.uses_sawtooth_wave))
        print("     Noise wave settings: " + str(self.uses_noise_wave))
        print("")
        print("       Sync bit settings: " + str(self.uses_sync_bit))
        print("       Test bit settings: " + str(self.uses_test_bit))
        print("    Ringmod bit settings: " + str(self.uses_ringmod_bit))
        print("")
        print(" Pulse width reg updates: " + str(self.pulsewidth_register_updates))
        print("Max Pulse width register: " + str(self.max_sid_pulsewidth))
        print("Min Pulse width register: " + str(self.min_sid_pulsewidth))
        print("----------")
        print("")

# global instance of stats class
stats = Stats()



# Taken from: https://github.com/true-grue/ayumi/blob/master/ayumi.c
# However, it doesn't marry with the YM2149 spec sheet, nor with the anecdotal reports that the YM attentuation steps in -1.5dB increments. Still, I'm gonna run with the emulator version.
ym_amplitude_table = [
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

### HELPER FUNCTIONS
### SHOULD PROBABLY PUT THESE IN A CLASS AT SOME POINT


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
    #global stats
    # f = (Clock / 16 x TP)
    tone = int( round( float(YM_CLOCK) / (float(f) * 16.0) ) )

    stats.max_ym_tone = max([stats.max_ym_tone, tone])
    stats.min_ym_tone = min([stats.min_ym_tone, tone])

    if (tone < 0):
        tone = 0
    if (tone > 4095):
        tone = 4095
        stats.clipped_tones += 1
        print("WARNING: Tone clipped to 4095")
    return int(tone)

#--------------------------------------------------------------
# return YM tone from given SID tone
#--------------------------------------------------------------

def sid_tone_to_ym_tone(v):
    #global stats # total_hz_error, worst_hz_error

    f = get_sid_frequency(v)
    if (f < 30.0):
        print("SID frequency " + str(f) + "hz (tone=" + str(v) + ") too low for YM")

    t = frequency_to_ym_tone(f)
    yf = get_ym_frequency(t)

    if (v > 0):
        error = abs(yf - f)
        stats.total_hz_error += error
        if error > stats.worst_hz_error:
            stats.worst_hz_error = error
        print("SID tone=" + str(v) + ", SID freq=" + str(f) + ", YM tone=" + str(t) + ", YM freq=" + str(yf) + ", (error=" + str(error) + "hz)" )
        if (error > 1):
            stats.total_large_error_tones += 1
            print("WARNING: LARGE ERROR IN FREQUENCY CONVERSION (" + str(error) + "hz)")
    return t



# get the normalized linear (float) amplitude for a 5 bit level
def get_ym_amplitude(v):
    if True:
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

# given an amplitude (0-1), return the closest matching YM 5-bit volume level
def get_ym_volume(a):
    if True:
        dist = 1<<31
        index = 0
        for n in range(32):
            ya = ym_amplitude_table[n]
            p = a - ya
            d = p * p
            # we always round to the nearest louder level (so we are never quieter than target level)
            if d < dist and ya >= a:
                dist = d
                index = n

        return index
    else:        
        if (a == 0.0):
            v = 0
        else:
            v = int( 31 - ( (10*math.log(a, 10)) / -0.75 ) )
        #print "  Volume of amplitude " + str(a) + " is " + str(v)
        #if v > 31:
        #    print "TITS"
        v = min(31, v)
        v = max(0, v)
        return v      


# Class to manage simulated state of a SID voice based on register settings
class SidVoice(object):

    # class statics

    # these tables are mappings of ADSR register values to ms/step
    attack_table = [ 2, 8, 16, 24, 38, 56, 68, 80, 100, 250, 500, 800, 1000, 3000, 5000, 8000 ]
    decayrelease_table = [ 6, 24, 48, 72, 114, 168, 204, 240, 300, 750, 1500, 2400, 3000, 9000, 15000, 24000 ]
    # sustain table maps S of the ADSR registers to a target 8-bit volume from a 4-bit setting
    sustain_table = [ 0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff ]

    # Envelope cycles
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
        self.set_frequency(0)#, 0)
        self.set_pulsewidth(0)#, 0)
        self.set_control(0)
        self.set_envelope(0, 0)

    # registers 0,1 - frequency (16-bits)
    def set_frequency(self, f):# lo, hi):
        #f = lo + (hi * 256)
        self.__frequency = f


    def get_frequency(self):
        return self.__frequency

    # registers 2,3 - pulse width (12-bits)
    def set_pulsewidth(self, p):#lo, hi):
        #p = lo + (hi * 256)
        self.__pulsewidth = p

        stats.pulsewidth_register_updates += 1
        stats.min_sid_pulsewidth = min([self.__pulsewidth, stats.min_sid_pulsewidth])
        stats.max_sid_pulsewidth = max([self.__pulsewidth, stats.max_sid_pulsewidth])

    def voiceId(self):
        return "V" + str(self.__voiceid) + " "

    def isNoise(self):
        return self.__noise == True

    def isPulse(self):
        return self.__pulse == True

    def isTriangle(self):
        return self.__triangle == True

    def isSaw(self):
        return self.__sawtooth == True

    def isTest(self):
        return self.__test == True

    def isSync(self):
        return self.__sync == True

    def isRingMod(self):
        return self.__ringmod == True

    def isMute(self):
        return self.__test or not self.__wave_active

    # TODO:
    # support mode & filter registers

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

        stats.control_register_updates += 1
        if self.__pulse:
            stats.uses_pulse_wave += 1
        if self.__triangle:
            stats.uses_triangle_wave += 1
        if self.__sawtooth:
            stats.uses_sawtooth_wave += 1
        if self.__noise:
            stats.uses_noise_wave += 1
        if self.__test:
            stats.uses_test_bit += 1
        if self.__sync:
            stats.uses_sync_bit += 1
        if self.__ringmod:
            stats.uses_ringmod_bit += 1

        # logic indicator if a waveform is active on this voice
        self.__wave_active = self.__noise or self.__pulse or self.__triangle or self.__sawtooth


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
        s += " (NO WAVEFORMS ACTIVE)" if not self.__wave_active else ""
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
        return self.__envelope_level #(self.__envelope_level >> 16) & 255
        #if self.__noise:
        #    return 0
        if self.__test:
            if self.__envelope_level > 0:
                print("NOTE: TEST bit set overrode ADSR volume")
            return 0
        else:
            return self.__envelope_level #(self.__envelope_level >> 16) & 255

    def get_waveform_level(self):
        #if self.__noise:
        #    return 0
        if self.___test:
            return 0
        else:
            return self.__waveform_level

    # advance envelope clock where t is 1/SID_CLOCK
    # returns true if ADSR is active
    def tick_envelope(self, t):

        #----------------------------
        # envelope generation
        #----------------------------

        adsr_active = True

        # Some early out scenarios can be handled here
        # ADSR doesn't need always updating so that's work we can detect & skip 
        if (self.__envelope_cycle == SidVoice.EnvelopeCycle_Sustain):
            # if we're in sustain cycle, its an early out because only Gate change will affect it
            # and that cannot happen within this logic
            print(" - Optimized sustain for voice " + str(self.__voiceid))
            adsr_active = False
        elif (self.__envelope_cycle == SidVoice.EnvelopeCycle_Inactive):
            print(" - Optimized ADSR for voice " + str(self.__voiceid) + " because Inactive")
            adsr_active = False

        # early out if inactive
        if not adsr_active:
            return adsr_active
        


        # gate bit set to one triggers the ADSR cycle
        # attack phase rises from 0-255 at the ms rate specified by attack register
        # decay phase moves from 255 to the sustain register level
        # sustain level holds until gate bit is cleared
        # release phases moves from sustain level to 0 at the rate specified by release register
        # register can be changed during each phase, but only take effect if new value is possible depending on ramp up/ramp down mode
        # gate bit can be cleared at any time to trigger release phase, even if ads phase incomplete
        # if gate bit is set before release phase has completed, the envelope generator continues attack phase from current setting


        # envelope process
        # see also https://sourceforge.net/p/vice-emu/code/HEAD/tree/trunk/vice/src/resid/envelope.cc

        precision = (2 ** 31)
        attack_rate = int( round( precision / (SidVoice.attack_table[self.__attack] * SID_CLOCK / 1000)))
        decay_rate = int( round( precision / (SidVoice.decayrelease_table[self.__decay] * SID_CLOCK / 1000)))
        release_rate = int( round( precision / (SidVoice.decayrelease_table[self.__release] * SID_CLOCK / 1000)))
        sustain_target = SidVoice.sustain_table[self.__sustain] << 24


        # iterate the ADSR logic for each tick. Suboptimal for now.
        iteration_count = t
        iteration_scale = 1
        if (self.__envelope_cycle == SidVoice.EnvelopeCycle_Attack) and ((self.__envelope_counter + attack_rate*t) <= precision):
            iteration_scale = t
            iteration_count = 1
            print(" - Optimized attack for voice " + str(self.__voiceid))
        elif (self.__envelope_cycle == SidVoice.EnvelopeCycle_Decay) and ((self.__envelope_counter - decay_rate*t) > sustain_target):
            iteration_scale = t
            iteration_count = 1
            print(" - Optimized decay for voice " + str(self.__voiceid))
        elif (self.__envelope_cycle == SidVoice.EnvelopeCycle_Release) and ((self.__envelope_counter - release_rate*t) >= 0):
            iteration_scale = t
            iteration_count = 1
            print(" - Optimized release for voice " + str(self.__voiceid))
        elif (self.__envelope_cycle == SidVoice.EnvelopeCycle_Sustain):
            # if we're in sustain cycle, its an early out because only Gate change will affect it
            iteration_count = 1
            adsr_active = False
            print(" - Optimized sustain for voice " + str(self.__voiceid))
        elif (self.__envelope_cycle == SidVoice.EnvelopeCycle_Inactive):
            iteration_count = 1
            adsr_active = False
            print(" - Optimized ADSR for voice " + str(self.__voiceid) + " because Inactive")
        else:
            print(" - " + str(iteration_count) + " ADSR Iterations for voice " + str(self.__voiceid) + ", cycle=" + str(self.__envelope_cycle))

        #elif (self.__envelope_cycle == SidVoice.EnvelopeCycle_Decay) and ((self.__envelope_counter - decay_rate*t) > 0):
        #    t = 1

        for n in range(iteration_count):
            #print("Iteration count=" + str(iteration_count) + ", n=" + str(n) + ", " #" + str(n))
            if self.__envelope_cycle == SidVoice.EnvelopeCycle_Inactive:
                # nothing to do
                break
            # attack cycle
            elif self.__envelope_cycle == SidVoice.EnvelopeCycle_Attack:
                self.__envelope_counter += attack_rate * iteration_scale
                self.__envelope_level = self.__envelope_counter >> 24
                if self.__envelope_level >= 255:
                    self.__envelope_level = 255
                    self.__envelope_cycle = SidVoice.EnvelopeCycle_Decay
            # decay cycle
            elif self.__envelope_cycle == SidVoice.EnvelopeCycle_Decay:
                self.__envelope_counter -= decay_rate * iteration_scale
                if self.__envelope_counter <= sustain_target:
                    self.__envelope_counter = sustain_target 
                    self.__envelope_cycle = SidVoice.EnvelopeCycle_Sustain
                self.__envelope_level = self.__envelope_counter >> 24
            elif self.__envelope_cycle == SidVoice.EnvelopeCycle_Sustain:
                # sustain cycle
                # nothing to do
                # possibly check if sustain register has changed
                # cant change in this loop, so break
                break
                
            elif self.__envelope_cycle == SidVoice.EnvelopeCycle_Release:
                # release cycle
                self.__envelope_counter -= release_rate * iteration_scale
                self.__envelope_level = self.__envelope_counter >> 24
                if self.__envelope_level <= 0:
                    self.__envelope_level = 0
                    self.__envelope_cycle = SidVoice.EnvelopeCycle_Inactive
                    break

        return adsr_active


    # advance clock where t is 1/SID_CLOCK
    def tick(self, t):

        self.__accumulator += int( round(t * self.__frequency))

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

        # update envelope generator



        # We sub divide the incoming tick interval
        # to optimize ADSR intervals for faster processing 
        # which improves performance by a significant factor
        et = t

        ETICK_RESOLUTION = SID_CLOCK / 8
        while (et > 0):

            lt = ETICK_RESOLUTION
            if (lt > et):
                lt = et

            et -= lt

            adsr_active = self.tick_envelope(lt)
            if not adsr_active:
                # ADSR is in a cycle where it is longer needing any updates
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

        self.set_filter_resonance(0)
        self.set_filter_control(0)
        self.set_master_volume(0)
        self.set_filter_cutoff(0)        

    def get_voice(self, voice):
        return self.__voices[voice]


    # filter cutoff frequency (16-bits)
    # bits 3-7 are not used?
    # registers $15-$16
    def set_filter_cutoff(self, c):
        self.__filter_cutoff = c



    # filter enable controls (4-bits)
    # register $17 (bits 0-3)
    def set_filter_control(self, fc):
        self.__filter_voice1 = (fc & 1) == 1
        self.__filter_voice2 = (fc & 2) == 2
        self.__filter_voice3 = (fc & 4) == 4
        self.__filter_ext = (fc & 8) == 8

        s = ""
        s += "V1 " if self.__filter_voice1 else "-- "
        s += "V2 " if self.__filter_voice2 else "-- "
        s += "V3 " if self.__filter_voice3 else "-- "
        s += "EX " if self.__filter_ext else "-- "

        print("FILTER CONTROL set to: " + s)


    # filter resonance (4-bits) (0-15) where 0 is no resonance
    # register $17 (bits 4-7)
    def set_filter_resonance(self, r):
        self.__filter_resonance = r


    # master volume (4-bits) (0-16) where 0 is no volume
    # register $18 (bits 0-3)
    def set_master_volume(self, v):
        self.__master_volume = v

    def get_master_volume(self):
        return self.__master_volume

    # filter mode (4-bits)
    # register $18 (bits 4-7)
    def set_filter_mode(self, m):
        self.__filter_lo_pass = (m & 1) == 1
        self.__filter_bn_pass = (m & 2) == 2
        self.__filter_hi_pass = (m & 4) == 4
        self.__filter_3_off = (m & 8) == 8

        s = ""
        s += "LO-P " if self.__filter_lo_pass else "---- "
        s += "BN-P " if self.__filter_bn_pass else "---- "
        s += "HI-P " if self.__filter_hi_pass else "---- "
        s += "3OFF " if self.__filter_3_off else "---- "

        print("FILTER MODE set to: " + s)



    def is3off(self):
        return self.__filter_3_off

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
        


        def hex2int(s):
            #s = s.replace(".", "0")
            return int(s, 16)

        def isSet(s):
            return not "." in s

        def parse_voice(segment):


            # 16-bits frequency
            freq = segment[1:5]
            segment = segment[5:]

            # note - ignored
            note = segment[1:9]
            segment = segment[9:]

            # 8-bits control
            wf = segment[1:3]
            segment = segment[3:]
            
            # 16-bits ADSR
            adsr = segment[:5]
            segment = segment[5:]

            # 12-bits Pulse width
            pul = segment[1:4]


            # convert to data

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

        def parse_common(segment):
            
            data = {}

            # 16-bits frequency cutoff register ($15,$16)
            # bits 3-7 off lo are not used
            cutoff = segment[1:5]
            segment = segment[5:]
            if isSet(cutoff):
                data["cutoff"] = hex2int(cutoff)


            # 8bit RES/Filter register ($17)
            rc = segment[1:3]
            segment = segment[3:]
            if isSet(rc):
                rc_v = hex2int(rc)
                # resonance (bits 4-7 of register $17)
                data["res"] = rc_v >> 4
                # filter enable (bits 0-3 of register $17)
                data["filter"] = rc_v & 15


            #4bit mode filter output register ($18 bits 4-7)
            # "Off" / "Hi " / "Low" / "B+H" / "L+B"
            # Low = 
            typ = segment[1:4]
            segment = segment[4:]


            # 4 - Low pass output
            # 5 - Band pass output
            # 6 - High pass output
            # 7 - 3 Off (voice 3 muted)
            typ_map = {
                "Low" : 1,
                "Hi " : 4,
                "B+H" : 6,
                "L+B" : 3,
                "Off" : 8
            }
            if isSet(typ):
                if typ in typ_map:
                    value = typ_map[typ]
                    data["typ"] = value
                else:
                    print("WARNING: Unhandled filter output mode '" + str(typ) + "'")


            # master volume (register $18 bits 0-3)
            v = segment[1:2]
            if isSet(v):
                data["v"] = hex2int(v)

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
        sid = self.__sid
        sid_voice1 = sid.get_voice(0)
        sid_voice2 = sid.get_voice(1)
        sid_voice3 = sid.get_voice(2)

        # stats
        #stats = {}
        #stats["pulse_frames"] = 0
        #stats["triangle_frames"] = 0
        #stats["noise_frames"]

        print("")
        print("Processing...")
        print("")

        middle_c_tone = 0x1168
        middle_c = get_sid_frequency(middle_c_tone) # should be 261.6Hz
        print("Middle C is " + str(middle_c) + "(" + str(middle_c_tone) + ")" )
        if int(middle_c*10.0) != 2616:
            print("WARNING: SID CLOCK DOES NOT MATCH TUNING") 
        else:
            print("SID CLOCK MATCHED OK.")
        print("SID CLOCK IS " + str(SID_CLOCK))
        print("YM RATE IS " + str(YM_RATE) + "hz")
        print("YM CLOCK IS " + str(YM_CLOCK))

        # parse the SID register dump
        header = True
        for x in content:

            
            if not header:

                stats.total_frames += 1

                # split the frame text into text segments
                frame = x.split("|")

                frameId = int(frame[1])

                # test early out, 10seconds 
                if (FIXED_LENGTH > 0) and (frameId > FIXED_LENGTH):
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

                common = parse_common(frame[5]) # common data


                # common control registers
                if "cutoff" in common:
                    sid.set_filter_cutoff( common["cutoff"] )

                if "res" in common:
                    sid.set_filter_resonance( common["res"] )

                if "v" in common:
                    sid.set_master_volume( common["v"] )

                if "filter" in common:
                    sid.set_filter_control( common["filter"] )

                if "typ" in common:
                    sid.set_filter_mode( common["typ"] )



                # control registers
                if "wf" in voice1:
                    sid_voice1.set_control( voice1["wf"] )

                if "wf" in voice2:
                    sid_voice2.set_control( voice2["wf"] )

                if "wf" in voice3:
                    sid_voice3.set_control( voice3["wf"] )

                # pulse widths
                if "pul" in voice1:
                    #sid_voice1.set_pulsewidth( voice1["pul"] >> 8, voice1["pul"] & 255 )
                    sid_voice1.set_pulsewidth( voice1["pul"] )

                if "pul" in voice2:
                    #sid_voice2.set_pulsewidth( voice2["pul"] >> 8, voice2["pul"] & 255 )
                    sid_voice2.set_pulsewidth( voice2["pul"] )

                if "pul" in voice3:
                    #sid_voice3.set_pulsewidth( voice3["pul"] >> 8, voice3["pul"] & 255 )
                    sid_voice3.set_pulsewidth( voice3["pul"] )


                # tones
                if "freq" in voice1: # and not sid_voice1.isNoise(): 
                    sid_voice1.set_frequency( voice1["freq"] )
                    tone1 = sid_tone_to_ym_tone( voice1["freq"] )
                    registers[0] = tone1 & 255
                    registers[1] = (tone1 >> 8) & 255

                if "freq" in voice2: # and not sid_voice2.isNoise(): 
                    sid_voice2.set_frequency( voice2["freq"] )
                    tone2 = sid_tone_to_ym_tone( voice2["freq"] )
                    registers[2] = tone2 & 255
                    registers[3] = (tone2 >> 8) & 255

                if "freq" in voice3: # and not sid_voice3.isNoise():  
                    sid_voice3.set_frequency( voice3["freq"] )
                    tone3 = sid_tone_to_ym_tone( voice3["freq"] )
                    registers[4] = tone3 & 255
                    registers[5] = (tone3 >> 8) & 255


                if "adsr" in voice1:
                    sid_voice1.set_envelope( voice1["adsr"] >> 8, voice1["adsr"] & 255 )

                if "adsr" in voice2:
                    sid_voice2.set_envelope( voice2["adsr"] >> 8, voice2["adsr"] & 255 )

                if "adsr" in voice3:
                    sid_voice3.set_envelope( voice3["adsr"] >> 8, voice3["adsr"] & 255 )

                # select appropriate waveform on YM, noise or square wave
                ym_mixer_1 = 1 if sid_voice1.isNoise() else 8
                ym_mixer_2 = 2 if sid_voice2.isNoise() else 16
                ym_mixer_3 = 4 if sid_voice3.isNoise() else 32

                # TODO: weight average by volume?
                sid_noise_avg_freq = 0
                sid_noise_channels_active = 0
                if sid_voice1.isNoise():
                    sid_noise_avg_freq += get_sid_frequency( sid_voice1.get_frequency() )
                    sid_noise_channels_active += 1
                if sid_voice2.isNoise():
                    sid_noise_avg_freq += get_sid_frequency( sid_voice2.get_frequency() )
                    sid_noise_channels_active += 1
                if sid_voice3.isNoise():
                    sid_noise_avg_freq += get_sid_frequency( sid_voice3.get_frequency() )
                    sid_noise_channels_active += 1


                if sid_noise_channels_active > 0:
                    #fn = CLOCK / 16NP
                    # 16NP * fn = CLOCK
                    # 16NP = CLOCK / fn
                    # NP = (CLOCK / fn) / 16

                    sid_noise_avg_freq = sid_noise_avg_freq / sid_noise_channels_active
                    sid_noise_avg_freq *= 16.0 # hack because SID noise is much lower freq than YM
                    ym_noise_value = int((YM_CLOCK / sid_noise_avg_freq) / 16.0)
                    print(str(sid_noise_avg_freq))
                    print("YM Noise value=" + str(ym_noise_value) + ", from " + str(sid_noise_channels_active) + " active noise voices")
                    if ym_noise_value > 31:
                        ym_noise_value = 31
                        print("YM Noise value CLIPPED to 31")
                    
                    
                    ym_noise_value = 2
                    registers[6] = ym_noise_value

                # TODO: handle noise frequency. Problematic if three voices have different noise frequencies set
                # since YM has only one noise frequency.

                # override waveform on YM if channels are mute
                # channels are mute if either test bit set or no waveform output
                ym_mixer_1 = (1+8) if sid_voice1.isMute() else ym_mixer_1
                ym_mixer_2 = (2+16) if sid_voice2.isMute() else ym_mixer_2
                ym_mixer_3 = (4+32) if sid_voice3.isMute() else ym_mixer_3

                # handle 3off case
                if sid.is3off():
                    ym_mixer_3 = (4+32)
                    print("SID 3OFF SET, so Voice 3 is mute")

                # handle test bit as an override on the waveform output
                #ym_mixer_1 = (1+8) if sid_voice1.isTest() else ym_mixer_1
                #ym_mixer_2 = (2+16) if sid_voice2.isTest() else ym_mixer_2
                #ym_mixer_3 = (4+32) if sid_voice3.isTest() else ym_mixer_3


                registers[7] = ym_mixer_1 | ym_mixer_2 | ym_mixer_3 #8+16+32 # only tones, no noise


                def sid_to_ym_volume(v):
                    # scale by master volume current set on the SID
                    sv = (v * sid.get_master_volume() ) / 15.0
                    # normalise volume
                    fv = float(sv) / 255.0
                    # convert to logarithmic YM volume
                    ymv = get_ym_volume(fv)
                    print("SID Volume=" + str(v) + ", YM Volume=" + str(ymv) + ", Linear YM Volume=" + str(v>>3))
                    return ymv

                # volumes
                if ENABLE_ADSR:
                    # linear sid volume output from the envelope generator is
                    # converted to logarithmic 4-bit amplitude on the YM


                    registers[8] = sid_to_ym_volume( sid_voice1.get_envelope_level() ) >> 1
                    registers[9] = sid_to_ym_volume( sid_voice2.get_envelope_level() ) >> 1
                    registers[10] = sid_to_ym_volume( sid_voice3.get_envelope_level() ) >> 1
                    # advance virtual SID clock
                    # ADSR resolution is 2ms, so ticks slower than this will 
                    # be an approximate rendering of the envelope.
                    # Only other option is to run the playback at 500Hz instead of 50Hz
                    ticks = int(SID_CLOCK / YM_RATE)
                    self.__sid.tick( ticks )
                else:
                    registers[8] = 15 
                    registers[9] = 15 
                    registers[10] = 15 



                #print(frame)
                for i in range(16):
                    self.__regs[i].append(registers[i])

            if ("+-------+" in x):
                header = False


        stats.report()




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
        ym_data.extend(struct.pack('>H', YM_RATE))	# frames_rate
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

        process_time = timer()        
        sidReader = SidReader(fd)
        process_time = timer() - process_time
        fd.close()  

        
        print("Loaded SID File.")
        print("Output file: '" + dst + "'")
        print("Conversion took " + str(process_time) + "s")
        sidReader.write_ym( dst )

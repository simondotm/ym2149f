#!/usr/bin/env python
# YM file format parser, and conversion routines for SN sound chip VGM files
# https://github.com/simondotm/ym2149f
# based on original code from https://github.com/FlorentFlament/ym2149-streamer

import functools
import itertools
import struct
import sys
import time
import binascii
import math

# Runtime vars
SN_CLOCK = 4000000 # SN target clock speed
LSFR_TAP_BIT = 15 # which bit of the SN Noise LFSR is tapped (15 or 16) 

DEBUG_LEVEL = 0

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


# Class to emulate the YM2149 HW envelope generator
class YmEnvelope():
    # see http://www.cpcwiki.eu/index.php/Ym2149 for FPGA logic

    ENV_MASTER_CLOCK = 2000000
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

    def __init__(self):
        self.__rb = 0   # Envelope frequency, 8-bit fine adjustment
        self.__rc = 0   # Envelope frequency, 8-bit rough adjustment   
        self.__rd = 0   # shape of envelope (CONT|ATT|ALT|HOLD)

        self.__cnt_div = 0      # clock divider
        self.__env_gen_cnt = 0  # envelope counter

        #self.__env_reset = 0
        #self.__env_ena = 1      # enable envelopes always

    # set envelope shape register 13
    def set_envelope_shape(r):
        self.__rd = r
        # reset state
        #self.__env_reset = 1

        # load initial state
        if (r & ENV_ATT) == 0:  # attack
            self.__env_vol = 31
            self.__env_inc = 0      # -1
        else:
            self.__env_vol = 0
            self.__env_inc = 1      # +1

        self.__env_hold = 0

    # set the YM chip envelope frequency registers
    def set_envelope_freq(hi, lo):
        self.__rb = lo
        self.__rc = hi


    # CONT = If 0, preset modes
    # ATT = Attack (=0 starts high decrement to low, =1 starts low incrementing to high)
    # ALT = Alernate (invert incrementor at top or bottom)
    # HOLD = Hold (freeze counter at top or bottom)

    def get_envelope_period():
        return self.__rc * 256 + self.__rb

    # get the current 5-bit envelope volume, 0-31
    def get_envelope_volume():
        return self.__env_vol

    # advance emulation by 1 system cycle
    def emulate_cycle():

        # emulate the clock divider
        #-- / 8 when SEL is high and /16 when SEL is low

        ena_div = 0:
        if (self.__cnt_div == 0):
            self.__cnt_div = 7 #(not I_SEL_L) & "111";
            ena_div = 1
        else:
            self.__cnt_div = cnt_div - 1

      

        # handle the envelope frequency counter
        env_gen_freq = self.rc & self.rb
        #-- envelope freqs 1 and 0 are the same.
        if (env_gen_freq == 0):
            env_gen_comp = 0
        else:
            env_gen_comp = (env_gen_freq - 1)


        env_ena = 0
        if (ena_div == 1): #-- divider ena
            if (self.__env_gen_cnt >= env_gen_comp):
                self.__env_gen_cnt = 0
                env_ena = 1
            else:
                self.__env_gen_cnt = (self.__env_gen_cnt + 1)




        # update envelope if the envelope frequency counter has signalled
        if (env_ena == 1):

            is_bot      = (self.__env_vol = 0)
            is_bot_p1   = (self.__env_vol = 1)
            is_top_m1   = (self.__env_vol = 30)
            is_top      = (self.__env_vol = 31)

            # process hold
            if (self.__env_hold == 0):
                if (self.__env_inc == 1):
                    self.__env_vol = (self.__env_vol + 1)
                else:
                    self.__env_vol = (self.__env_vol + 31)


            #-- envelope shape control.
            if (self.__rd & ENV_CONT) == 0:
                if (self.__env_inc == 0): #-- down
                    if is_bot_p1:
                        self.__env_hold = 1
                else:
                    if is_top:
                        self.__env_hold = 1
            else:
                if (self.__rd & ENV_HOLD) == 1: #-- hold = 1
                    if (self.__env_inc == 0): #-- down
                        if (self.__rd & ENV_ALT == 1): #-- alt
                            if is_bot:
                                self.__env_hold = 1
                        else:
                            if is_bot_p1:
                                self.__env_hold = 1
                    else:
                        if (__self.rd & ENV_ALT) == 1): #-- alt
                            if is_top:
                                self.__env_hold = 1
                        else:
                            if is_top_m1:
                                self.__env_hold = 1

                else:
                    if (self.__rd & ENV_ALT == 1): #-- alternate
                        if (self.__env_inc == 0): #-- down
                            if is_bot_p1:
                                self.__env_hold = 1
                            if is_bot:
                                self.__env_hold = 0
                                self.__env_inc = 1
                        else:
                            if is_top_m1:
                                self.__env_hold = 1
                            if is_top:
                                self.__env_hold = 0
                                self.__env_inc = 0

    # advance the envelope emulator by provided number of clock cycles
    def tick(clocks):
        for x in xrange(clocks):
            emulate_cycle()




        


class YmReader(object):

    def __parse_extra_infos(self):
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
        ym_header = '> 4s 8s I I H I H I H'
        s = self.__fd.read(struct.calcsize(ym_header))
        d = {}
        (d['id'],
         d['check_string'],
         d['nb_frames'],
         d['song_attributes'],
         d['nb_digidrums'],
         d['chip_clock'],
         d['frames_rate'],
         d['loop_frame'],
         d['extra_data'],
        ) = struct.unpack(ym_header, s)

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
        #regs = [self.__fd.read(cnt) for i in xrange(16)]
        regs = []
        for i in xrange(16):
            #print "file offset=" + str(self.__fd.tell())  
            regs.append(self.__fd.read(cnt))

        print " Loaded " + str(len(regs)) + " register data chunks"
        for r in xrange(16):
            print " Register " + str(r) + " entries = " + str(len(regs[r]))

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
            print '*Warning* End! marker not found after frames'

    def __init__(self, fd):

        # create instance of YM envelope generator
        self.__ymenv = YmEnvelope()

        print "Parsing YM file..."

        self.__fd = fd
        self.__parse_header()
        self.__data = []
        if not self.__data:
            self.__read_data()
            self.__check_eof()    

            

    def dump_header(self):
        for k in ('id','check_string', 'nb_frames', 'song_attributes',
                  'nb_digidrums', 'chip_clock', 'frames_rate', 'loop_frame',
                  'extra_data', 'song_name', 'author_name', 'song_comment'):
            print "{}: {}".format(k, self.__header[k])

    def get_header(self):
        return self.__header

    def get_data(self):
        return self.__data

    def write_vgm(self, vgm_filename):

        # prepare the YM file parser
        clock = self.__header['chip_clock']
        cnt  = self.__header['nb_frames']
        regs = self.__data

        digi_drums = self.__header['nb_digidrums']


        print "Analysing & Converting YM file..."

        # prepare the vgm output
        #vgm_filename = "test.vgm"
        print "   VGM Processing : Writing output VGM file '" + vgm_filename + "'"
        vgm_stream = bytearray()
        vgm_time = 0
        vgm_clock = SN_CLOCK

        # prepare the raw output
        raw_stream = bytearray()

        # YM has 12 bits of precision
        # Lower values correspond to higher frequencies - see http://poi.ribbon.free.fr/tmp/freq2regs.htm
        ym_freq_hi = (float(clock) / 16.0) / float(1)
        ym_freq_lo = (float(clock) / 16.0) / float(4095)

        # SN has 10 bits of precision vs YM's 12 bits
        sn_freq_hi = float(vgm_clock) / (2.0 * float(1) * 16.0)
        sn_freq_lo = float(vgm_clock) / (2.0 * float(1023) * 16.0)

        # SN can generate periodic noise in the lower Hz range
        sn_pfreq_hi = float(vgm_clock) / (2.0 * float(1) * 16.0 * float(LSFR_TAP_BIT))
        sn_pfreq_lo = float(vgm_clock) / (2.0 * float(1023) * 16.0 * float(LSFR_TAP_BIT))

        print "YM Tone Frequency range from " + str(ym_freq_lo) + "Hz to " + str(ym_freq_hi) + "Hz"
        print "SN Tone Frequency range from " + str(sn_freq_lo) + "Hz to " + str(sn_freq_hi) + "Hz"
        print "SN Bass Frequency range from " + str(sn_pfreq_lo) + "Hz to " + str(sn_pfreq_hi) + "Hz"


        def get_register_data(register, frame):
            return int(binascii.hexlify(regs[register][frame]), 16)
            


        print "---"
        #print get_register_data(0,0)
        #print get_register_data(1,0)

        # set default full volumes at the start of the tune for all channels
        vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
        vgm_stream.extend( struct.pack('B', 128+(0<<5)+16) ) # LATCH VOLUME
        vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
        vgm_stream.extend( struct.pack('B', 128+(1<<5)+16) ) # LATCH VOLUME
        vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
        vgm_stream.extend( struct.pack('B', 128+(2<<5)+16) ) # LATCH VOLUME
        vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
        vgm_stream.extend( struct.pack('B', 128+(3<<5)+16+15) ) # LATCH VOLUME to SILENT

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


        sn_attn_latch = [ 0, 0, 0, 0 ]
        sn_tone_latch = [ 0, 0, 0, 0 ]

        # my dump code
        for i in xrange(cnt):
            s = "Frame="+'{:05d}'.format(i)+" "

            def get_register_byte(r):
                return int(binascii.hexlify(regs[r][i]), 16)

            def get_register_word(r):
                return get_register_byte(r) + get_register_byte(r+1)*256

            def getregisterflag(data,bit,key0,key1):
                if data & (1<<bit):
                    return key1
                else:
                    return key0      

            # return frequency in hz of a given YM tone/noise pitch
            def get_ym_frequency(v):
                if v < 1:
                    v = 1
                return clock / (16 * v)


            # given a YM tone period, return the equivalent SN tone register period
            def ym_to_sn(ym_tone, is_periodic = False):

                # Adjust freq scale & baseline range if periodic noise selected
                baseline_freq = sn_freq_lo
                sn_freq_scale = 1.0
                if is_periodic:
                    sn_freq_scale = float(LSFR_TAP_BIT)
                    baseline_freq = sn_pfreq_lo

                # tones should never exceed 12-bit range
                # but some YM files encode extra info
                # into the top 4 bits

                if ym_tone > 4095:
                    print " ERROR: tone data ("+str(ym_tone)+") is out of range (0-4095)"
                    ym_tone = ym_tone & 4095
                    
                # If the tone is 0, it's probably because
                # there's a digidrum being played on this voice
                if ym_tone == 0:
                    print " ERROR: ym tone is 0"
                    ym_freq = 0
                else:
                    ym_freq = (float(clock) / 16.0) / float(ym_tone)



                    # if the frequency goes below the range
                    # of the SN capabilities, add an octave
                    while ym_freq < baseline_freq:
                        print " WARNING: Freq too low - Added an octave - from " + str(ym_freq) + " to " + str(ym_freq*2.0) + "Hz"
                        ym_freq *= 2.0

                # calculate the appropriate SN tone register value
                if ym_freq == 0:
                    sn_tone = 0
                    sn_freq = 0
                else:
                    sn_tone = float(vgm_clock) / (2.0 * ym_freq * 16.0 * sn_freq_scale )
                    # due to the integer maths, some precision is lost at the lower end
                    sn_tone = int(round(sn_tone))	# using round minimizes error margin at lower precision

                    # clamp range to 10 bits
                    if sn_tone > 1023:
                        sn_tone = 1023
                        print " WARNING: Clipped SN tone to 1023 (ym_freq="+str(ym_freq)+" Hz)"
                        # this could result in bad tuning, depending on why it occurred. better to reduce freq?
                    if sn_tone < 1:
                        sn_tone = 1
                        print " WARNING: Clipped SN tone to 1 (ym_freq="+str(ym_freq)+" Hz)"

                    sn_freq = float(vgm_clock) / (2.0 * float(sn_tone) * 16.0 * sn_freq_scale)

                #print "ym_tone=" + str(ym_tone) + " ym_freq="+str(ym_freq) + " sn_tone="+str(sn_tone) + " sn_freq="+str(sn_freq)

                hz_err = sn_freq - ym_freq
                if hz_err > 2.0 or hz_err < -2.0:
                    print " WARNING: Large error transposing tone! [" + str(hz_err) + " Hz ] "

                return sn_tone

            # As above, but for periodic white noise
            def ym_to_sn_periodic(ym_tone):

                # tones should never exceed 12-bit range
                # but some YM files encode extra info
                # into the top 4 bits
                if ym_tone > 4095:
                    print " ERROR: tone data ("+str(ym_tone)+") is out of range (0-4095)"
                    ym_tone = ym_tone & 4095

                # If the tone is 0, it's probably because
                # there's a digidrum being played on this voice
                if ym_tone == 0:
                    print " ERROR: ym tone is 0"
                    ym_freq = 0
                else:
                    ym_freq = (float(clock) / 16.0) / float(ym_tone)

                # if the frequency goes below the range
                # of the SN capabilities, add an octave
                while ym_freq < sn_pfreq_lo:
                    ym_freq *= 2.0
                    print " WARNING: Freq too low - Added an octave - now " + str(ym_freq) + "Hz"

                sn_tone = float(vgm_clock) / (2.0 * ym_freq * 16.0 * 15.0 )
                
                # due to the integer maths, some precision is lost at the lower end
                sn_tone = int(round(sn_tone))	# using round minimizes error margin at lower precision
                # clamp range to 10 bits
                if sn_tone > 1023:
                    sn_tone = 1023
                    print " WARNING: Clipped SN tone to 1023 (ym_freq="+str(ym_freq)+" Hz)"
                if sn_tone < 1:
                    sn_tone = 1
                    print " WARNING: Clipped SN tone to 1 (ym_freq="+str(ym_freq)+" Hz)"

                sn_freq = float(vgm_clock) / (2.0 * float(sn_tone) * 16.0 * 15.0)

                #print "ym_tone=" + str(ym_tone) + " ym_freq="+str(ym_freq) + " sn_tone="+str(sn_tone) + " sn_freq="+str(sn_freq)

                hz_err = sn_freq - ym_freq
                if hz_err > 2.0 or hz_err < -2.0:
                    print " WARNING: Large error transposing tone! [" + str(hz_err) + " Hz ] "

                return sn_tone

            # given a channel and tone value, output vgm command
            def output_sn_tone(channel, tone):
                
                # these shouldnt happen
                if tone > 1023:
                    print "ERROR: SN TONE OUT OF RANGE > 1023"
                if tone < 0:
                    print "ERROR: SN TONE OUT OF RANGE < 0"

                r_lo = 128 + (channel << 5) + (tone & 15)    # bit 4 clear for tone
                r_hi = (tone >> 4) & 63

                vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
                vgm_stream.extend( struct.pack('B', r_lo) ) # LATCH TONE
                vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
                vgm_stream.extend( struct.pack('B', r_hi) ) # DATA TONE

                raw_stream.extend( struct.pack('B', (tone & 15)) )
                raw_stream.extend( struct.pack('B', (tone >> 4) & 63) )

            # output a noise tone on channel 3
            def output_sn_noise(tone):

                r_lo = 128 + (3 << 5) + (tone & 15)

                vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
                vgm_stream.extend( struct.pack('B', r_lo) ) # LATCH TONE

                raw_stream.extend( struct.pack('B', (tone & 15)) ) # LATCH TONE
            

            # given a channel and volume value, output vgm command
            def output_sn_volume(channel, volume):

                r_lo = 128 + (channel << 5) + 16 + (15 - (volume & 15))    # bit 4 set for volume, SN volumes are inverted

                vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
                vgm_stream.extend( struct.pack('B', r_lo) ) # LATCH VOLUME

                raw_stream.extend( struct.pack('B', (15 - (volume & 15))) ) # LATCH VOLUME

            #------------------------------------------------
            # extract the YM register values for this frame
            #------------------------------------------------

            # volume attenuation level (if bit 4 is clear)
            ym_volume_a = get_register_byte(8) & 15
            ym_volume_b = get_register_byte(9) & 15
            ym_volume_c = get_register_byte(10) & 15

            # envelope attentuation mode flags
            ym_envelope_a = get_register_byte( 8) & 16
            ym_envelope_b = get_register_byte( 9) & 16
            ym_envelope_c = get_register_byte(10) & 16

            # process envelopes
            # first set the envelope frequency
            self.__ymenv.set_envelope_freq(get_register_byte(12), get_register_byte(11))

            # next set the envelope shape, but only if it is set in the YM stream
            # (since setting this register resets the envelope state)
            ym_envelope_shape = get_register_byte(13)
            if (ym_envelope_shape != 255):
                print 'setting envelope shape'
                self.__ymenv.set_envelope_shape(ym_envelope_shape)

            # use the envelope volume if M is set for any channel
            if ym_envelope_a:
                ym_volume_a = self.__ymenv.get_envelope_volume() / 2
                print 'envelope on A'
            if ym_envelope_b:
                print 'envelope on B'
                ym_volume_b = self.__ymenv.get_envelope_volume() / 2
            if ym_envelope_c:
                print 'envelope on C'
                ym_volume_c = self.__ymenv.get_envelope_volume() / 2

            # update the envelope cpu emulation
            self.__ymenv.tick( self.__header['chip_clock'] / 50 )
 
            # Have to properly mask these registers
            # r1 bits 4-6 are used for TS info
            # r3 bits 4-5 are used for DD info
            ym_tone_a = get_register_word(0) & 4095
            ym_tone_b = get_register_word(2) & 4095
            ym_tone_c = get_register_word(4) & 4095

            # R6 bits 5-6 are used for TP for TS setting
            ym_noise = get_register_byte(6) & 31


            ym_envelope_f = get_register_word(11)   # envelope frequency register


            # YM file specific attributes (not YM2149 chip features)
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
                print "ERROR: Timer Synth Trigger - Not handled yet"


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
                print " dd_tp=" + str(dd_tp)
                print " dd_tc=" + str(dd_tc)

                if dd_tc == 0:
                    print "ERROR: Digidrum TC value is 0 - unexpected & unhandled"
                else:             
                    dd_freq = (MFP_FREQ / MFP_TABLE[dd_tp]) / dd_tc

            # Handle TS frequency
            ts_freq = 0
            if ts_on:
                if ts_tc == 0:
                    print "ERROR: Timer Synth TC value is 0 - unexpected & unhandled"
                else:
                    ts_freq = (MFP_FREQ / MFP_TABLE[ts_tp]) / ts_tc

            # If a DD is triggered on a voice, the volume register for that channel
            # should be interpreted as a 5-bit sample number rather than a volume


            # output is on when mix bit is clear. 
            # we invert it though for easier code readibility 
            ym_mixer = get_register_byte(7)
            ym_mix_tone_a = (ym_mixer & (1<<0)) == 0
            ym_mix_tone_b = (ym_mixer & (1<<1)) == 0
            ym_mix_tone_c = (ym_mixer & (1<<2)) == 0

            ym_mix_noise_a = (ym_mixer & (1<<3)) == 0
            ym_mix_noise_b = (ym_mixer & (1<<4)) == 0
            ym_mix_noise_c = (ym_mixer & (1<<5)) == 0

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
            
 
            #------------------------------------------------
            # output VGM SN76489 equivalent data
            #------------------------------------------------

            sn_attn_out = [0, 0, 0, 0]
            sn_tone_out = [0, 0, 0, 0]

            # load the current tones & volumes
            sn_attn_out[0] = ym_volume_a
            sn_attn_out[1] = ym_volume_b
            sn_attn_out[2] = ym_volume_c
            sn_attn_out[3] = 0

            sn_tone_out[0] = ym_to_sn(ym_tone_a)
            sn_tone_out[1] = ym_to_sn(ym_tone_b)
            sn_tone_out[2] = ym_to_sn(ym_tone_c)

            # first, determine which channels have the noise mixer enabled
            # the calculate a volume which is the average level
            noise_volume = 0
            noise_active = 0
            if ym_mix_noise_a or ym_mix_noise_b or ym_mix_noise_c:

                if ym_mix_noise_a: # and not ym_mix_tone_a:
                    noise_volume += ym_volume_a
                    noise_active += 1
                if ym_mix_noise_b: # and not ym_mix_tone_b:
                    noise_volume += ym_volume_b
                    noise_active += 1
                if ym_mix_noise_c: # and not ym_mix_tone_c:
                    noise_volume += ym_volume_c
                    noise_active += 1

                # average the volume based on number of active noise channels
                noise_volume /= noise_active

                print "OUTPUT NOISE! " + str(noise_volume)            

            ENABLE_BASS_TONES = True
            bass_active = False
            if ENABLE_BASS_TONES and not noise_active:

                lo_count = 0
                if ym_freq_a < sn_freq_lo:
                    lo_count += 1
                if ym_freq_b < sn_freq_lo:
                    lo_count += 1
                if ym_freq_c < sn_freq_lo:
                    lo_count += 1
                    
                # if at least one channel is an out of range frequency
                # adjust for periodic noise bass
                if lo_count:
                    bass_active = True
                    print " " + str(lo_count) + " channels detected out of SN frequency range, adjusting..."
                    # mute channel 2
                    sn_attn_out[2] = 0

                    # Find the channel with the lowest frequency
                    # And move it over to SN Periodic noise channel instead
                    if ym_freq_a < ym_freq_b and ym_freq_a < ym_freq_c:
                        # it's A
                        print " Channel A -> Bass "                     

                        sn_attn_out[0] = ym_volume_c
                        sn_attn_out[1] = ym_volume_b
                        sn_attn_out[3] = ym_volume_a

                        sn_tone_out[0] = ym_to_sn(ym_tone_c)
                        sn_tone_out[1] = ym_to_sn(ym_tone_b)
                        sn_tone_out[2] = ym_to_sn(ym_tone_a, True)

                    else:
                        if ym_freq_b < ym_freq_a and ym_freq_b < ym_freq_c:
                            # it's B
                            print " Channel B -> Bass "                     

                            sn_attn_out[0] = ym_volume_a
                            sn_attn_out[1] = ym_volume_c
                            sn_attn_out[3] = ym_volume_b

                            sn_tone_out[0] = ym_to_sn(ym_tone_a)
                            sn_tone_out[1] = ym_to_sn(ym_tone_c)
                            sn_tone_out[2] = ym_to_sn(ym_tone_b, True)

                        else:
                            # it's C    
                            print " Channel C -> Bass "                     

                            sn_attn_out[0] = ym_volume_a
                            sn_attn_out[1] = ym_volume_b
                            sn_attn_out[3] = ym_volume_c

                            sn_tone_out[0] = ym_to_sn(ym_tone_a)
                            sn_tone_out[1] = ym_to_sn(ym_tone_b)
                            sn_tone_out[2] = ym_to_sn(ym_tone_c, True)

                           

            # Apply mixer settings (will mute channels when non-zero)
            # TODO: rename _mix_ to _mute_
            if not ym_mix_tone_a:
                sn_attn_out[0] = 0
            if not ym_mix_tone_b:
                sn_attn_out[1] = 0
            if not ym_mix_tone_c:
                sn_attn_out[2] = 0
                
            # handle noise. this will be interesting!
            # noise output will override any bass tones
            # TODO: detect if bass tone playing as well, and dont do bass adjustment if so





            if noise_active:
                noise_freq = 0
                if ym_noise == 0:
                    print "ERROR: Noise is enabled at frequency 0 - unexpected"
                else:
                    noise_freq = float(clock) / (16.0 * ym_noise)

                    ym_noise_min = min(ym_noise, ym_noise_min)
                    ym_noise_max = max(ym_noise, ym_noise_max)

                #snf = float(vgm_clock) / (16.0 * ym_noise)
                #print "noise_freq=" + str(noise_freq) + "Hz"
                #print "SN 0 = " + str(float(vgm_clock) / (16.0 * 16.0))
                #print "SN 1 = " + str(float(vgm_clock) / (16.0 * 32.0))
                #print "SN 2 = " + str(float(vgm_clock) / (16.0 * 64.0))
                
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

                sn_attn_out[3] = noise_volume
                sn_tone_out[3] = 4 # White noise, fixed low frequency (16 cycle)
                # most tunes dont seem to change the noise frequency much
            else:
                if bass_active:
                    sn_attn_out[2] = 0 # turn off tone2
                    sn_tone_out[3] = 3 # Periodic noise
                else:
                    sn_attn_out[3] = 0 # turn off noise
                

            # output the final data to VGM
            NO_DIFF = False

            if NO_DIFF:
                output_sn_tone(0, sn_tone_out[0])
                output_sn_tone(1, sn_tone_out[0])
                output_sn_tone(2, sn_tone_out[0])
                output_sn_noise(sn_tone_out[3])

                output_sn_volume(0, sn_attn_out[0])
                output_sn_volume(1, sn_attn_out[1])
                output_sn_volume(2, sn_attn_out[2])
                output_sn_volume(3, sn_attn_out[3])

                #output_sn_volume(3, 15)
                #output_sn_volume(3, 15)
                #output_sn_volume(3, 15)
                #output_sn_volume(3, 15)
                #output_sn_volume(3, 15)

            else:
                    
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
                # reset the LSFR
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


            #------------------------------------------------
            # show info
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
            s += " " + '{:2d}'.format( ym_volume_a )
            s += " " + '{:2d}'.format( ym_volume_b )
            s += " " + '{:2d}'.format( ym_volume_c )
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
            m = get_register_byte(13)  


            s += ", Env Shape ["
            s += " " + getregisterflag(m,3, "----", "CONT")
            s += " " + getregisterflag(m,2, "----", " ATT")
            s += " " + getregisterflag(m,1, "----", " ALT")
            s += " " + getregisterflag(m,0, "----", "HOLD")
            s += " ]"

            # Envelope frequency
            if ym_envelope_f == 0:
                #print "WARNING: Envelope frequency is 0 - unexpected & unhandled"
                # It's ok, happens when no envelope being used
                ehz = 0
            else:
                ehz = (float(clock) / (256.0 * float(ym_envelope_f))) * 32.0
            
            s += ", Env Freq ["
            s += " " + '{:6d}'.format( ym_envelope_f ) + " (" + '{:9.2f}'.format( ehz ) + "Hz)"            
            s += " ]"

            ym_env_freq_min = min(ym_env_freq_min, ehz)
            ym_env_freq_max = max(ym_env_freq_max, ehz)


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

                ym_dd_freq_min = min(ym_dd_freq_min, dd_freq)
                ym_dd_freq_max = max(ym_dd_freq_max, dd_freq)

            print s  

            # now output to vgm
            vgm_stream.extend( struct.pack('B', 0x63) ) # WAIT50

        #--------------------------------------------
        # Information
        #--------------------------------------------
        print ""
        print "Info:"
        print " Channel A Hz range - " + str( get_ym_frequency(ym_tone_a_max) ) + "Hz to " + str( get_ym_frequency(ym_tone_a_min) ) + "Hz"
        print " Channel B Hz range - " + str( get_ym_frequency(ym_tone_b_max) ) + "Hz to " + str( get_ym_frequency(ym_tone_b_min) ) + "Hz"
        print " Channel C Hz range - " + str( get_ym_frequency(ym_tone_c_max) ) + "Hz to " + str( get_ym_frequency(ym_tone_c_min) ) + "Hz"
        print "  Digidrum Hz range - " + str( ym_dd_freq_min ) + "Hz to " + str( ym_dd_freq_max ) + "Hz"
        print "  Envelope Hz range - " + str( ym_env_freq_min ) + "Hz to " + str( ym_env_freq_max ) + "Hz"
        print "        Noise range - " + str( ym_noise_min ) + " to " + str( ym_noise_max ) + " "


        #--------------------------------------------
        # Output the VGM
        #--------------------------------------------

        vgm_stream_length = len(vgm_stream)		

        # build the GD3 data block
        gd3_data = bytearray()
        gd3_stream = bytearray()	
        gd3_stream_length = 0
        
        gd3_offset = 0

        STRIP_GD3 = False
        VGM_FREQUENCY = 44100
        if not STRIP_GD3: # disable for no GD3 tag
            gd3_data.extend( self.__header['song_name'] + b'\x00\x00') #title_eng' + b'\x00\x00')
            gd3_data.extend('title_jap' + b'\x00\x00')
            gd3_data.extend('game_eng' + b'\x00\x00')
            gd3_data.extend('game_jap' + b'\x00\x00')
            gd3_data.extend('console_eng' + b'\x00\x00')
            gd3_data.extend('console_jap' + b'\x00\x00')
            gd3_data.extend('artist_eng' + b'\x00\x00')
            gd3_data.extend('artist_jap' + b'\x00\x00')
            gd3_data.extend('date' + b'\x00\x00')
            gd3_data.extend('vgm_creator' + b'\x00\x00')
            gd3_data.extend('notes' + b'\x00\x00')
            
            gd3_stream.extend('Gd3 ')
            gd3_stream.extend(struct.pack('I', 0x100))				# GD3 version
            gd3_stream.extend(struct.pack('I', len(gd3_data)))		# GD3 length		
            gd3_stream.extend(gd3_data)		
            
            gd3_offset = (64-20) + vgm_stream_length
            gd3_stream_length = len(gd3_stream)
        else:
            print "   VGM Processing : GD3 tag was stripped"
        
        # build the full VGM output stream		
        vgm_data = bytearray()
        vgm_data.extend(b'Vgm ')    # VGM Magic number
        vgm_data.extend(struct.pack('I', 64 + vgm_stream_length + gd3_stream_length - 4))				# EoF offset
        vgm_data.extend(struct.pack('I', 0x00000151))		# Version
        vgm_data.extend(struct.pack('I', vgm_clock)) #self.metadata['sn76489_clock']))
        vgm_data.extend(struct.pack('I', 0))#self.metadata['ym2413_clock']))
        vgm_data.extend(struct.pack('I', gd3_offset))				# GD3 offset
        vgm_data.extend(struct.pack('I', cnt*VGM_FREQUENCY/50)) #self.metadata['total_samples']))				# total samples
        vgm_data.extend(struct.pack('I', 0)) #self.metadata['loop_offset']))				# loop offset
        vgm_data.extend(struct.pack('I', 0)) #self.metadata['loop_samples']))				# loop # samples
        vgm_data.extend(struct.pack('I', 50))#self.metadata['rate']))				# rate
        vgm_data.extend(struct.pack('H', 0x0003))	# 0x0003 for BBC configuration of SN76489 self.metadata['sn76489_feedback']))				# sn fb
        vgm_data.extend(struct.pack('B', 15)) #self.metadata['sn76489_shift_register_width']))				# SNW	
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
        
        print "   VGM Processing : Written " + str(int(len(vgm_data))) + " bytes, GD3 tag used " + str(gd3_stream_length) + " bytes"

        # write to output file
        frame_size = 11 #16
        frame_total = len(raw_stream) / frame_size
        fh = open(vgm_filename+".bin", 'wb')
#        fh.write(raw_stream)

        frame_count = frame_total #16 # frame_total #33 # number of frames to package at a time
        #offs = 0
        for c in xrange(frame_total / frame_count):
            for r in xrange(frame_size):
                rdata = bytearray()
                for n in xrange(frame_count):
                    rdata.append(raw_stream[c*frame_count*frame_size + n*frame_size + r])
                #offs += frame_size
                fh.write(rdata)
 
        fh.close()

# 16 bytes per frame
# 16 frames per 256 byte page
# unpack first frame on init
# unpack remaining data
# or 256 x 11 = 2816 bytes, 1 page per register = 256

        print "   BIN Processing : Written " + str(int(len(raw_stream))) + " bytes"

        print "All done."                          


def to_minsec(frames, frames_rate):
    secs = frames / frames_rate
    mins = secs / 60
    secs = secs % 60
    return (mins, secs)

def main():
    header = None
    data = None

    if len(sys.argv) != 2:
        print "Syntax is: {} <ym_filepath>".format(sys.argv[0])
        exit(0)

    ym_filename = sys.argv[1]
    with open(ym_filename, "rb") as fd:
        ym = YmReader(fd)
        fd.close()  
        
        ym.dump_header()
        header = ym.get_header()
        data = ym.get_data()
            

        print "Loaded YM File."
        vgm_filename = ym_filename[:ym_filename.rfind('.')] + ".vgm"
        print vgm_filename
        ym.write_vgm( vgm_filename )

    song_min, song_sec = to_minsec(header['nb_frames'], header['frames_rate'])
    print ""
    #print data

    if False:
        with open(sys.argv[1], 'w') as fd:
            time.sleep(2) # Wait for Arduino reset
            frame_t = time.time()
            for i in xrange(header['nb_frames']):
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

if __name__ == '__main__':
    main()

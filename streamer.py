#!/usr/bin/env python
# YM file format parser
# based on original code from https://github.com/FlorentFlament/ym2149-streamer

import functools
import itertools
import struct
import sys
import time
import binascii
import math

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
# Envelope repetition frequency is (Clock / 256 x EP) [EP is envelope frequency]
# Envelope shape has 10 valid settings - see data sheet for details







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
        d['interleaved'] = d['song_attributes'] & 0x01 != 0
        self.__header = d


        if self.__header['nb_digidrums'] != 0:

            # skip the digidrums sections
            print self.__fd.tell()
            sample_size = struct.unpack('>I', self.__fd.read(4))[0]   # sample size
            print sample_size
            print self.__fd.tell()
            print "sample_size="+str(sample_size)
            self.__fd.seek(sample_size, 1)      # skip the sample data (for now)
            print self.__fd.tell()            


            #raise Exception('Unsupported file format: Digidrums are not supported')

        self.__parse_extra_infos()

        print "file offset="
        print self.__fd.tell()  
        #       self.dump_header()



    def __read_data_interleaved(self):
        print "__read_data_interleaved"
        print "file offset=" + str(self.__fd.tell())  
       
        cnt  = self.__header['nb_frames']
        #regs = [self.__fd.read(cnt) for i in xrange(16)]
        regs = []
        for i in xrange(16):
            print "file offset=" + str(self.__fd.tell())  
            regs.append(self.__fd.read(cnt))

        print len(regs)
        for r in xrange(16):
            print "Register " + str(r) + " entries = " + str(len(regs[r]))

        #self.__data=[''.join(f) for f in zip(*regs)]
        self.__data = regs
        #print self.__data



    def __read_data(self):
        if not self.__header['interleaved']:
            raise Exception(
                'Unsupported file format: Only interleaved data are supported')
        print "file offset=" + str(self.__fd.tell())  
        self.__read_data_interleaved()
        print "file offset=" + str(self.__fd.tell())  

    def __check_eof(self):
        if self.__fd.read(4) != 'End!':
            print '*Warning* End! marker not found after frames'

    def __init__(self, fd):
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
        print "Analysing"

        # prepare the vgm output
        #vgm_filename = "test.vgm"
        print "   VGM Processing : Writing output VGM file '" + vgm_filename + "'"
        vgm_stream = bytearray()
        vgm_time = 0
        vgm_clock = 4000000 # SN clock speed

        # YM has 12 bits of precision
        # Lower values correspond to higher frequencies - see http://poi.ribbon.free.fr/tmp/freq2regs.htm
        ym_freq_hi = (float(clock) / 16.0) / float(1)
        ym_freq_lo = (float(clock) / 16.0) / float(4095)

        # SN has 10 bits of precision vs YM's 12 bits
        sn_freq_hi = float(vgm_clock) / (2.0 * float(1) * 16.0)
        sn_freq_lo = float(vgm_clock) / (2.0 * float(1023) * 16.0)

        # SN can generate periodic noise in the lower Hz range
        sn_pfreq_hi = float(vgm_clock) / (2.0 * float(1) * 16.0 * 15.0)
        sn_pfreq_lo = float(vgm_clock) / (2.0 * float(1023) * 16.0 * 15.0)

        print "YM Tone Frequency range from " + str(ym_freq_lo) + "Hz to " + str(ym_freq_hi) + "Hz"
        print "SN Tone Frequency range from " + str(sn_freq_lo) + "Hz to " + str(sn_freq_hi) + "Hz"
        print "SN Bass Frequency range from " + str(sn_pfreq_lo) + "Hz to " + str(sn_pfreq_hi) + "Hz"


        def get_register_data(register, frame):
            return int(binascii.hexlify(regs[register][frame]), 16)
            


        print "---"
        print get_register_data(0,0)
        print get_register_data(1,0)

        # default volumes
        vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
        vgm_stream.extend( struct.pack('B', 128+(0<<5)+16) ) # LATCH VOLUME
        vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
        vgm_stream.extend( struct.pack('B', 128+(1<<5)+16) ) # LATCH VOLUME
        vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
        vgm_stream.extend( struct.pack('B', 128+(2<<5)+16) ) # LATCH VOLUME

        ym_tone_a_max = 0
        ym_tone_b_max = 0
        ym_tone_c_max = 0

        ym_tone_a_min = 65536
        ym_tone_b_min = 65536
        ym_tone_c_min = 65536
        

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
                return clock / (16 * v)


            # given a YM tone period, return the equivalent SN tone register period
            def ym_to_sn(ym_tone):

                if ym_tone > 4095:
                    print " ERROR: tone data ("+str(ym_tone)+") is out of range (0-4095)"
                if ym_tone == 0:
                    print " ERROR: ym tone is 0"
                    ym_freq = 1
                else:
                    ym_freq = (float(clock) / 16.0) / float(ym_tone)

                # if the frequency goes below the range
                # of the SN capabilities, add an octave
                while ym_freq < sn_freq_lo:
                    ym_freq *= 2.0
                    print " WARNING: Freq too low - Added an octave - now " + str(ym_freq) + "Hz"

                sn_tone = float(vgm_clock) / (2.0 * ym_freq * 16.0 )
                
                # due to the integer maths, some precision is lost at the lower end
                sn_tone = int(round(sn_tone))	# using round minimizes error margin at lower precision
                # clamp range to 10 bits
                if sn_tone > 1023:
                    sn_tone = 1023
                    print " WARNING: Clipped SN tone to 1023 (ym_freq="+str(ym_freq)+" Hz)"
                if sn_tone < 1:
                    sn_tone = 1
                    print " WARNING: Clipped SN tone to 1 (ym_freq="+str(ym_freq)+" Hz)"

                sn_freq = float(vgm_clock) / (2.0 * float(sn_tone) * 16.0)

                #print "ym_tone=" + str(ym_tone) + " ym_freq="+str(ym_freq) + " sn_tone="+str(sn_tone) + " sn_freq="+str(sn_freq)

                hz_err = sn_freq - ym_freq
                if hz_err > 2.0 or hz_err < -2.0:
                    print " WARNING: Large error transposing tone! [" + str(hz_err) + " Hz ] "

                return sn_tone

            # As above, but for periodic white noise
            def ym_to_sn_periodic(ym_tone):

                if ym_tone > 4095:
                    print " ERROR: tone data ("+str(ym_tone)+") is out of range (0-4095)"
                if ym_tone == 0:
                    print " ERROR: ym tone is 0"
                    ym_freq = 1
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

                r_lo = 128 + (channel << 5) + (tone & 15)    # bit 4 clear for tone
                r_hi = tone >> 4

                vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
                vgm_stream.extend( struct.pack('B', r_lo) ) # LATCH TONE
                vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
                vgm_stream.extend( struct.pack('B', r_hi) ) # DATA TONE

            # given a channel and volume value, output vgm command
            def output_sn_volume(channel, volume):

                r_lo = 128 + (channel << 5) + 16 + (15 - (volume & 15))    # bit 4 set for volume, SN volumes are inverted

                vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
                vgm_stream.extend( struct.pack('B', r_lo) ) # LATCH VOLUME


            #------------------------------------------------
            # extract the YM register values for this frame
            #------------------------------------------------
            ym_volume_a = get_register_byte(8) & 15
            ym_volume_b = get_register_byte(9) & 15
            ym_volume_c = get_register_byte(10) & 15

            ym_tone_a = get_register_word(0)
            ym_tone_b = get_register_word(2)
            ym_tone_c = get_register_word(4)

            ym_noise = get_register_byte(6)

            ym_envelope_a = get_register_byte( 8) & 4
            ym_envelope_b = get_register_byte( 9) & 4
            ym_envelope_c = get_register_byte(10) & 4

            # output is on when mix bit is clear
            ym_mixer = get_register_byte(7)
            ym_mix_tone_a = ym_mixer & (1<<0)
            ym_mix_tone_b = ym_mixer & (1<<1)
            ym_mix_tone_c = ym_mixer & (1<<2)

            ym_mix_noise_a = ym_mixer & (1<<3)
            ym_mix_noise_b = ym_mixer & (1<<4)
            ym_mix_noise_c = ym_mixer & (1<<5)


            ym_tone_a_max = max(ym_tone_a_max, ym_tone_a)
            ym_tone_b_max = max(ym_tone_b_max, ym_tone_b)
            ym_tone_c_max = max(ym_tone_c_max, ym_tone_c)

            ym_tone_a_min = min(ym_tone_a_min, ym_tone_a)
            ym_tone_b_min = min(ym_tone_b_min, ym_tone_b)
            ym_tone_c_min = min(ym_tone_c_min, ym_tone_c)




            #------------------------------------------------
            # output VGM SN76489 equivalent data
            #------------------------------------------------

            TEST_BASS = False
            BASS_CHANNEL = 1
            FILTER_A = True
            FILTER_B = False
            FILTER_C = True

            if TEST_BASS:
                if BASS_CHANNEL == 0:
                    temp = ym_volume_a
                    ym_volume_a = ym_volume_c
                    ym_volume_c = temp
                    temp = ym_tone_a
                    ym_tone_a = ym_tone_c
                    ym_tone_c = temp
                    

                if BASS_CHANNEL == 1:
                    temp = ym_volume_b
                    ym_volume_b = ym_volume_c
                    ym_volume_c = temp
                    temp = ym_tone_b
                    ym_tone_b = ym_tone_c
                    ym_tone_c = temp
                

            # channel A volume -> SN Channel 0
            if not FILTER_A:
                output_sn_volume(0, ym_volume_a)

            # channel B volume -> SN Channel 0
            if not FILTER_B:
                output_sn_volume(1, ym_volume_b)

            # channel C volume -> SN Channel 0
            if not FILTER_C:
                if TEST_BASS:
                    output_sn_volume(2, 0)
                    output_sn_volume(3, ym_volume_c)
                else:
                    output_sn_volume(2, ym_volume_c)



            # channel A -> SN Channel 0
            if not FILTER_A:
                if ym_mix_tone_a == 0:
                    sn_tone = ym_to_sn(ym_tone_a)
                    output_sn_tone(0, sn_tone)

            # channel B -> SN Channel 1
            if not FILTER_B:
                if ym_mix_tone_b == 0:
                    sn_tone = ym_to_sn(ym_tone_b)
                    output_sn_tone(1, sn_tone)

            # channel C -> SN Channel 2
            if not FILTER_C:
                if ym_mix_tone_c == 0:
                    if TEST_BASS:
                        print "PERIODIC"
                        sn_tone = ym_to_sn_periodic(ym_tone_c)
                        output_sn_tone(2, sn_tone)

                        r_lo = 128 + (3 << 5) + 3
                        vgm_stream.extend( struct.pack('B', 0x50) ) # COMMAND
                        vgm_stream.extend( struct.pack('B', r_lo) ) # LATCH TONE

                    else:
                        sn_tone = ym_to_sn(ym_tone_c)
                        output_sn_tone(2, sn_tone)

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

            # envelope
            s += ", Env ["
            s += " " + getregisterflag(get_register_byte( 8), 4, "-", "a")
            s += " " + getregisterflag(get_register_byte( 9), 4, "-", "b")
            s += " " + getregisterflag(get_register_byte(10), 4, "-", "c")
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
            
            # Envelope shape
            m = get_register_byte(13)  


            s += ", Env Shape ["
            s += " " + getregisterflag(m,3, "----", "CONT")
            s += " " + getregisterflag(m,2, "----", " ATT")
            s += " " + getregisterflag(m,1, "----", " ALT")
            s += " " + getregisterflag(m,0, "----", "HOLD")
            s += " ]"


            print s  

            # now output to vgm
            vgm_stream.extend( struct.pack('B', 0x63) ) # WAIT50

        #--------------------------------------------
        # Information
        #--------------------------------------------
        print "Channel A - " + str( get_ym_frequency(ym_tone_a_max) ) + "Hz to " + str( get_ym_frequency(ym_tone_a_min) ) + "Hz"
        print "Channel B - " + str( get_ym_frequency(ym_tone_b_max) ) + "Hz to " + str( get_ym_frequency(ym_tone_b_min) ) + "Hz"
        print "Channel C - " + str( get_ym_frequency(ym_tone_c_max) ) + "Hz to " + str( get_ym_frequency(ym_tone_c_min) ) + "Hz"
        
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
            gd3_data.extend('title_eng' + b'\x00\x00')
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

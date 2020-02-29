#!/usr/bin/env python
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




# Class to emulate the YM2149 HW envelope generator
# Based on http://www.cpcwiki.eu/index.php/Ym2149 FPGA logic
# This is very slow. No longer used.
class YmEnvelopeFPGA():

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
        self.reset()

    # reset the chip logic state
    def reset(self):
        self.__rb = 0   # Envelope frequency, 8-bit fine adjustment
        self.__rc = 0   # Envelope frequency, 8-bit rough adjustment   
        self.__rd = 0   # shape of envelope (CONT|ATT|ALT|HOLD)

        self.__cnt_div = 0      # clock divider
        self.__env_gen_cnt = 0  # envelope counter

        # initialise shape
        self.set_envelope_shape(self.__rd)


        #self.__env_reset = 0
        #self.__env_ena = 1      # enable envelopes always

    # set envelope shape register 13
    def set_envelope_shape(self, r):
        self.__rd = r
        # reset state
        #self.__env_reset = 1

        # load initial state
        if (r & self.ENV_ATT) == 0:  # attack
            self.__env_vol = 31
            self.__env_inc = 0      # -1
        else:
            self.__env_vol = 0
            self.__env_inc = 1      # +1

        self.__env_hold = 0

    # set the YM chip envelope frequency registers
    def set_envelope_freq(self, hi, lo):
        self.__rb = lo
        self.__rc = hi


    # CONT = If 0, preset modes
    # ATT = Attack (=0 starts high decrement to low, =1 starts low incrementing to high)
    # ALT = Alernate (invert incrementor at top or bottom)
    # HOLD = Hold (freeze counter at top or bottom)

    def get_envelope_period(self):
        return self.__rc * 256 + self.__rb

    # get the current 5-bit envelope volume, 0-31
    def get_envelope_volume(self):
        return (self.__env_vol)# & 31)

    # advance emulation by 1 envelope cycle
    # should be called 1/8 system clock cycles
    def envelope_cycle(self):

        # handle the envelope frequency counter
        env_gen_freq = (self.__rc * 256) + self.__rb
        #-- envelope freqs 1 and 0 are the same.
        if (env_gen_freq == 0):
            env_gen_comp = 0
        else:
            env_gen_comp = (env_gen_freq - 1)


        env_ena = 0
        if (self.__env_gen_cnt >= env_gen_comp):
            self.__env_gen_cnt = 0
            env_ena = 1
        else:
            self.__env_gen_cnt = (self.__env_gen_cnt + 1)




        # update envelope if the envelope frequency counter has signalled
        if (env_ena == 1):

            is_bot      = (self.__env_vol == 0)
            is_bot_p1   = (self.__env_vol == 1)
            is_top_m1   = (self.__env_vol == 30)
            is_top      = (self.__env_vol == 31)

            # process hold
            if (self.__env_hold == 0):
                if (self.__env_inc == 1):
                    self.__env_vol = (self.__env_vol + 1) & 31
                else:
                    self.__env_vol = (self.__env_vol + 31) & 31


            #-- envelope shape control.
            # CONT=0
            if (self.__rd & self.ENV_CONT) == 0:
                if (self.__env_inc == 0): #-- down
                    if is_bot_p1:
                        self.__env_hold = 1
                else:
                    if is_top:
                        self.__env_hold = 1
            else:
                # CONT = 1
                if (self.__rd & self.ENV_HOLD): #-- hold = 1
                    # CONT=1, HOLD=1
                    if (self.__env_inc == 0): #-- down
                        if (self.__rd & self.ENV_ALT): #-- alt
                            if is_bot:
                                self.__env_hold = 1
                        else:
                            if is_bot_p1:
                                self.__env_hold = 1
                    else:
                        if (self.__rd & self.ENV_ALT): #-- alt
                            if is_top:
                                self.__env_hold = 1
                        else:
                            if is_top_m1:
                                self.__env_hold = 1

                else:
                    # CONT=1, HOLD=0
                    if (self.__rd & self.ENV_ALT): #-- alternate
                        #print 'alt'
                        if (self.__env_inc == 0): #-- down
                            if is_bot_p1:
                                self.__env_hold = 1
                            if is_bot:
                                self.__env_hold = 0
                                self.__env_inc = 1
                                #print 'flip down up'
                        else:
                            if is_top_m1:
                                self.__env_hold = 1
                            if is_top:
                                self.__env_hold = 0
                                self.__env_inc = 0
                                #print 'flip up down'

    # advance the envelope emulator by provided number of clock cycles
    def tick(self, clocks):
        for x in range(clocks):
            # emulate the clock divider
            #-- / 8 when SEL is high and /16 when SEL is low

            ena_div = 0
            if (self.__cnt_div == 0):
                self.__cnt_div = 7 #(not I_SEL_L) & "111";
                ena_div = 1
            else:
                self.__cnt_div = self.__cnt_div - 1

            if (ena_div == 1): #-- divider ena
                self.envelope_cycle()               

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
                self.envelope_cycle()

            print('output volume M=' + str(format(m, 'x')) + ' - ' + vs)

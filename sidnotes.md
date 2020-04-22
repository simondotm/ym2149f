# Progress log

## Step 1
Decide on YM as target sound chip. It has a similar architecture to SID, but obviously lacks hardware capabilities.
First things first, we need to be able to convert a SID file, which is essentially bundled 6502 micro code, into a raw dump of register updates.
SidDump is our friend.

## Step 2
Now we have register dump data we can make a start on the conversion. This is quite fun - taking a text file dump from siddump and writing a frame-by-frame parser for it.

## Step 3
Now that we've parsed the raw register dump we can start work on a simple interpretation of the complex audio signals contained in the dump.
The simplest place to start is simply rendering frequencies in the SID tune as square waves on the YM.
The result sounds encouraging, it is vaguely like the tune.

## Step 4
The output we got from step 3 is messy because we're not making any effort to handle waveform types or envelopes/amplitudes, also the sid dump output indicates frames where some registers are not updated and we aren't handling that either, so we're getting 0 frequencies for that.

Since getting any kind of sensible voice amplitudes will require some simulation of the SID ADSR envelope generator, we might as well go for gold. A new class is created to simulate a SID voice and its associated register parameters and outputs - frequency, pulsewidth, waveform type, and ADSR emulation is the first job.

## Step 5
Ok the voice emulator first pass is in. It doesn't work because there's bugs in the ADSR state machine and precision calculations, so the output is always 0. Fixing this gives us a much clearer version of the music because sounds are now proportionally levelled rather than a cacaphony of sound. Its still messy though because we're not handling noise waveforms or empty frames properly so there's lots of low frequency sounds interleaved with the good stuff.

## Step 6
First fix, lets filter out any voices that are set to noise for a given frame. We'll come back to noise later. Result is much improved, no more high pitched clicks interleaved where percussion was triggered.

## Step 7
Next fix, lets filter out any voice updates where there's no change in register data. Again this results in a much improved output, no more bouncing to frequency 0 on the channel.

## Step 8
Now we have a clearer music track, but our YM levels need improving because the SID voice level is a linear 12-bit DAC output whereas the YM is a logarithmic 5-bit DAC. Adding a conversion table from linear to logarithm level is a big win, the music voice mix is much more accurate now.

With all of this done, its now far more obvious we have a tuning issue. The music sounds "about right" but there's a lot of off-tune tones. possibly rounding errors in our frequency conversions, or differences in precision between the SID oscillator and the YM frequency generator.

## Step 9
Took a bit of time to optimize the envelope ADSR process loop as it was rather brute force and slow. Subdividing the work and calculating if incremental counts were within range of the ADSR cycles for single iteration optimization seemed to do the trick, processing time reduced by a factor 4. 

## Step 10
Ok so it's clear to me now that SIDs might be running on PAL or NTSC derived clocks rather than the simple 1Mhz assumption I started with (based off the SID data sheet example clock).
SidDump gives us a middle C value rather than a clock rate, which I can see does not match my calcs so lets see if we can deduce PAL or NTSC from that.
https://dustlayer.com/c64-architecture/2013/5/7/hardware-basics-part-1-tick-tock-know-your-clock

Also, I've noticed that the playback rate of the YM version doesn't match the SID. Very odd.


# TODO
support mode register (especially voice 3 off & primary volume)
support filter register (if only to see what values are being sent)
investigate white noise, not right
check ADSR handling is accurate
find out if multiple voices play noise at once?
weight voice by volume if so
find out how many voices use PWM simultaneously. could average an envelope maybe
keep getting high beeps - some kind of voice not getting cleaned?
noise mixing seems very different to sid
support sid as parameter (route through siddump in script)
space debris runs slowly, why?
initial clock matching - check/improve this code
